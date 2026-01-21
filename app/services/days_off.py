"""
Days Off Management Service.

Features:
- CSV upload with template
- LLM-powered natural language parsing
- CRUD operations for days off
"""
import csv
import io
import json
from datetime import date, datetime
from typing import List, Optional, Tuple
from dataclasses import dataclass

from sqlalchemy import select, and_, func
from sqlalchemy.ext.asyncio import AsyncSession
import openai

from ..models import Resident, DayOff, DayOffType, Admin, AuditLog, DataSource
from ..settings import settings


@dataclass
class DayOffEntry:
    """Represents a parsed day off entry."""
    resident_name: str
    start_date: date
    end_date: date
    day_off_type: str
    notes: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ParseResult:
    """Result of parsing days off from CSV or LLM."""
    entries: List[DayOffEntry]
    errors: List[str]
    warnings: List[str]


class DaysOffService:
    """Service for managing days off."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============== CSV Operations ==============

    def generate_csv_template(self) -> str:
        """Generate a CSV template for days off upload."""
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row
        writer.writerow([
            "resident_name",
            "start_date",
            "end_date",
            "type",
            "notes"
        ])

        # Example rows
        writer.writerow([
            "John Smith",
            "2026-01-15",
            "2026-01-17",
            "Vacation",
            "Family trip"
        ])
        writer.writerow([
            "Jane Doe",
            "2026-02-01",
            "2026-02-01",
            "Conference",
            "ACEP Conference"
        ])

        return output.getvalue()

    async def parse_csv(self, csv_content: str) -> ParseResult:
        """
        Parse a CSV file containing days off data.

        Returns parsed entries with validation results.
        """
        entries = []
        errors = []
        warnings = []

        # Get valid day off types
        result = await self.db.execute(select(DayOffType))
        day_off_types = {t.name.lower(): t for t in result.scalars().all()}

        # Get all residents for name matching
        result = await self.db.execute(
            select(Resident).where(Resident.is_active == True)
        )
        residents = {r.name.lower(): r for r in result.scalars().all()}

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_content))

        # Validate headers
        required_headers = {"resident_name", "start_date", "end_date", "type"}
        if not required_headers.issubset(set(reader.fieldnames or [])):
            missing = required_headers - set(reader.fieldnames or [])
            errors.append(f"Missing required columns: {', '.join(missing)}")
            return ParseResult(entries=[], errors=errors, warnings=[])

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (1-indexed + header)
            try:
                entry = await self._parse_csv_row(
                    row, row_num, residents, day_off_types
                )
                entries.append(entry)

                if entry.error:
                    errors.append(f"Row {row_num}: {entry.error}")

            except Exception as e:
                errors.append(f"Row {row_num}: Failed to parse - {str(e)}")

        return ParseResult(entries=entries, errors=errors, warnings=warnings)

    async def _parse_csv_row(
        self,
        row: dict,
        row_num: int,
        residents: dict,
        day_off_types: dict,
    ) -> DayOffEntry:
        """Parse a single CSV row."""
        resident_name = row.get("resident_name", "").strip()
        start_date_str = row.get("start_date", "").strip()
        end_date_str = row.get("end_date", "").strip()
        type_str = row.get("type", "").strip()
        notes = row.get("notes", "").strip() or None

        error = None

        # Validate resident
        if not resident_name:
            error = "Missing resident name"
        elif resident_name.lower() not in residents:
            error = f"Resident '{resident_name}' not found"

        # Parse dates
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            start_date = date.today()
            error = error or f"Invalid start date format: {start_date_str}"

        try:
            end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            end_date = date.today()
            error = error or f"Invalid end date format: {end_date_str}"

        # Validate date range
        if start_date > end_date:
            error = error or "Start date must be before or equal to end date"

        # Validate type
        if not type_str:
            error = error or "Missing day off type"
        elif type_str.lower() not in day_off_types:
            error = error or f"Unknown day off type: {type_str}"

        return DayOffEntry(
            resident_name=resident_name,
            start_date=start_date,
            end_date=end_date,
            day_off_type=type_str,
            notes=notes,
            error=error,
        )

    async def import_csv(
        self,
        csv_content: str,
        admin_id: int,
    ) -> dict:
        """
        Import days off from CSV content.

        Returns summary of import results.
        """
        result = await self.parse_csv(csv_content)

        if result.errors:
            # Filter to only valid entries
            valid_entries = [e for e in result.entries if not e.error]
        else:
            valid_entries = result.entries

        # Get mappings
        residents_result = await self.db.execute(
            select(Resident).where(Resident.is_active == True)
        )
        residents = {r.name.lower(): r for r in residents_result.scalars().all()}

        types_result = await self.db.execute(select(DayOffType))
        day_off_types = {t.name.lower(): t for t in types_result.scalars().all()}

        created_count = 0
        skipped_count = 0

        for entry in valid_entries:
            resident = residents.get(entry.resident_name.lower())
            day_off_type = day_off_types.get(entry.day_off_type.lower())

            if not resident or not day_off_type:
                skipped_count += 1
                continue

            # Check for duplicate
            existing = await self.db.execute(
                select(DayOff).where(
                    DayOff.resident_id == resident.id,
                    DayOff.start_date == entry.start_date,
                    DayOff.end_date == entry.end_date,
                    DayOff.type_id == day_off_type.id,
                )
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                continue

            # Create day off
            day_off = DayOff(
                resident_id=resident.id,
                type_id=day_off_type.id,
                start_date=entry.start_date,
                end_date=entry.end_date,
                notes=entry.notes,
                approved_by=admin_id,
                approved_at=datetime.utcnow(),
                source=DataSource.CSV,
            )
            self.db.add(day_off)
            created_count += 1

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="days_off_csv_import",
            entity_type="days_off",
            entity_id=None,
            old_value=None,
            new_value={
                "total_rows": len(result.entries),
                "created": created_count,
                "skipped": skipped_count,
                "errors": len(result.errors),
            },
        )
        self.db.add(audit)

        return {
            "total_rows": len(result.entries),
            "created": created_count,
            "skipped": skipped_count,
            "errors": result.errors,
            "warnings": result.warnings,
        }

    # ============== LLM Parsing ==============

    async def parse_text_with_llm(self, text: str) -> ParseResult:
        """
        Parse natural language text using OpenAI GPT.

        Returns structured days off entries.
        """
        if not settings.openai_api_key:
            return ParseResult(
                entries=[],
                errors=["OpenAI API key not configured"],
                warnings=[],
            )

        # Get valid day off types for the prompt
        result = await self.db.execute(select(DayOffType))
        day_off_types = [t.name for t in result.scalars().all()]

        # Get resident names for validation
        result = await self.db.execute(
            select(Resident).where(Resident.is_active == True)
        )
        residents = {r.name.lower(): r.name for r in result.scalars().all()}

        # Construct the prompt
        prompt = f"""Extract days off requests from the following text. Return a JSON array with objects containing:
- resident_name: string (the resident's full name)
- start_date: string in YYYY-MM-DD format
- end_date: string in YYYY-MM-DD format
- type: one of [{', '.join(day_off_types)}]
- notes: optional string with any additional context

If a single day is mentioned, use the same date for start_date and end_date.
If the year is not specified, assume the current year ({date.today().year}) or the next occurrence of that date.
Parse all days off mentioned, even if multiple residents or date ranges are in one sentence.

Text to parse:
{text}

Return ONLY valid JSON array, no other text."""

        try:
            client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a helpful assistant that extracts structured data from text. Always respond with valid JSON only."
                    },
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=2000,
            )

            content = response.choices[0].message.content.strip()

            # Clean up response (remove markdown code blocks if present)
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            # Parse JSON
            parsed_data = json.loads(content)

            if not isinstance(parsed_data, list):
                parsed_data = [parsed_data]

            entries = []
            errors = []
            warnings = []

            # Get day off types mapping
            types_result = await self.db.execute(select(DayOffType))
            day_off_types_map = {t.name.lower(): t.name for t in types_result.scalars().all()}

            for item in parsed_data:
                try:
                    # Parse dates
                    start_date = datetime.strptime(item["start_date"], "%Y-%m-%d").date()
                    end_date = datetime.strptime(item["end_date"], "%Y-%m-%d").date()

                    # Validate resident name
                    resident_name = item.get("resident_name", "")
                    error = None

                    if resident_name.lower() not in residents:
                        # Try fuzzy match
                        matched_name = self._fuzzy_match_name(resident_name, list(residents.values()))
                        if matched_name:
                            warnings.append(
                                f"'{resident_name}' matched to '{matched_name}'"
                            )
                            resident_name = matched_name
                        else:
                            error = f"Resident '{resident_name}' not found"
                    else:
                        # Use the canonical name from database
                        resident_name = residents[resident_name.lower()]

                    # Validate type
                    day_off_type = item.get("type", "")
                    if day_off_type.lower() not in day_off_types_map:
                        # Default to Personal if unknown
                        warnings.append(f"Unknown type '{day_off_type}', defaulting to 'Personal'")
                        day_off_type = "Personal"
                    else:
                        day_off_type = day_off_types_map[day_off_type.lower()]

                    entry = DayOffEntry(
                        resident_name=resident_name,
                        start_date=start_date,
                        end_date=end_date,
                        day_off_type=day_off_type,
                        notes=item.get("notes"),
                        error=error,
                    )
                    entries.append(entry)

                    if error:
                        errors.append(error)

                except Exception as e:
                    errors.append(f"Failed to parse entry: {str(e)}")

            return ParseResult(entries=entries, errors=errors, warnings=warnings)

        except json.JSONDecodeError as e:
            return ParseResult(
                entries=[],
                errors=[f"Failed to parse LLM response as JSON: {str(e)}"],
                warnings=[],
            )
        except Exception as e:
            return ParseResult(
                entries=[],
                errors=[f"LLM parsing failed: {str(e)}"],
                warnings=[],
            )

    def _fuzzy_match_name(self, name: str, candidates: List[str]) -> Optional[str]:
        """Try to fuzzy match a name to candidates."""
        from difflib import SequenceMatcher

        name_lower = name.lower()
        best_match = None
        best_ratio = 0.0

        for candidate in candidates:
            ratio = SequenceMatcher(None, name_lower, candidate.lower()).ratio()
            if ratio > best_ratio and ratio >= 0.7:
                best_ratio = ratio
                best_match = candidate

        return best_match

    async def import_from_llm(
        self,
        text: str,
        admin_id: int,
    ) -> dict:
        """
        Parse text with LLM and import valid days off.

        Returns summary of import results.
        """
        result = await self.parse_text_with_llm(text)

        if not result.entries:
            return {
                "total_parsed": 0,
                "created": 0,
                "skipped": 0,
                "errors": result.errors,
                "warnings": result.warnings,
            }

        # Get mappings
        residents_result = await self.db.execute(
            select(Resident).where(Resident.is_active == True)
        )
        residents = {r.name.lower(): r for r in residents_result.scalars().all()}

        types_result = await self.db.execute(select(DayOffType))
        day_off_types = {t.name.lower(): t for t in types_result.scalars().all()}

        created_count = 0
        skipped_count = 0

        for entry in result.entries:
            if entry.error:
                skipped_count += 1
                continue

            resident = residents.get(entry.resident_name.lower())
            day_off_type = day_off_types.get(entry.day_off_type.lower())

            if not resident or not day_off_type:
                skipped_count += 1
                continue

            # Check for duplicate
            existing = await self.db.execute(
                select(DayOff).where(
                    DayOff.resident_id == resident.id,
                    DayOff.start_date == entry.start_date,
                    DayOff.end_date == entry.end_date,
                    DayOff.type_id == day_off_type.id,
                )
            )
            if existing.scalar_one_or_none():
                skipped_count += 1
                result.warnings.append(
                    f"Duplicate: {entry.resident_name} {entry.start_date} - {entry.end_date}"
                )
                continue

            # Create day off
            day_off = DayOff(
                resident_id=resident.id,
                type_id=day_off_type.id,
                start_date=entry.start_date,
                end_date=entry.end_date,
                notes=entry.notes,
                approved_by=admin_id,
                approved_at=datetime.utcnow(),
                source=DataSource.LLM,
            )
            self.db.add(day_off)
            created_count += 1

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="days_off_llm_import",
            entity_type="days_off",
            entity_id=None,
            old_value={"original_text": text[:500]},  # Truncate for storage
            new_value={
                "total_parsed": len(result.entries),
                "created": created_count,
                "skipped": skipped_count,
            },
        )
        self.db.add(audit)

        return {
            "total_parsed": len(result.entries),
            "created": created_count,
            "skipped": skipped_count,
            "entries": [
                {
                    "resident_name": e.resident_name,
                    "start_date": e.start_date.isoformat(),
                    "end_date": e.end_date.isoformat(),
                    "type": e.day_off_type,
                    "notes": e.notes,
                    "error": e.error,
                }
                for e in result.entries
            ],
            "errors": result.errors,
            "warnings": result.warnings,
        }

    # ============== CRUD Operations ==============

    async def create_day_off(
        self,
        resident_id: int,
        type_id: int,
        start_date: date,
        end_date: date,
        notes: Optional[str] = None,
        admin_id: Optional[int] = None,
    ) -> DayOff:
        """Create a new day off entry."""
        day_off = DayOff(
            resident_id=resident_id,
            type_id=type_id,
            start_date=start_date,
            end_date=end_date,
            notes=notes,
            approved_by=admin_id,
            approved_at=datetime.utcnow() if admin_id else None,
            source=DataSource.MANUAL,
        )
        self.db.add(day_off)
        await self.db.flush()

        if admin_id:
            audit = AuditLog(
                admin_id=admin_id,
                action="days_off_create",
                entity_type="days_off",
                entity_id=day_off.id,
                old_value=None,
                new_value={
                    "resident_id": resident_id,
                    "type_id": type_id,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )
            self.db.add(audit)

        return day_off

    async def update_day_off(
        self,
        day_off_id: int,
        admin_id: int,
        type_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        notes: Optional[str] = None,
    ) -> Optional[DayOff]:
        """Update an existing day off entry."""
        result = await self.db.execute(
            select(DayOff).where(DayOff.id == day_off_id)
        )
        day_off = result.scalar_one_or_none()

        if not day_off:
            return None

        old_values = {
            "type_id": day_off.type_id,
            "start_date": day_off.start_date.isoformat(),
            "end_date": day_off.end_date.isoformat(),
            "notes": day_off.notes,
        }

        if type_id is not None:
            day_off.type_id = type_id
        if start_date is not None:
            day_off.start_date = start_date
        if end_date is not None:
            day_off.end_date = end_date
        if notes is not None:
            day_off.notes = notes

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="days_off_update",
            entity_type="days_off",
            entity_id=day_off_id,
            old_value=old_values,
            new_value={
                "type_id": day_off.type_id,
                "start_date": day_off.start_date.isoformat(),
                "end_date": day_off.end_date.isoformat(),
                "notes": day_off.notes,
            },
        )
        self.db.add(audit)

        return day_off

    async def delete_day_off(
        self,
        day_off_id: int,
        admin_id: int,
    ) -> bool:
        """Delete a day off entry."""
        result = await self.db.execute(
            select(DayOff).where(DayOff.id == day_off_id)
        )
        day_off = result.scalar_one_or_none()

        if not day_off:
            return False

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="days_off_delete",
            entity_type="days_off",
            entity_id=day_off_id,
            old_value={
                "resident_id": day_off.resident_id,
                "type_id": day_off.type_id,
                "start_date": day_off.start_date.isoformat(),
                "end_date": day_off.end_date.isoformat(),
            },
            new_value=None,
        )
        self.db.add(audit)

        await self.db.delete(day_off)
        return True

    async def get_days_off(
        self,
        resident_id: Optional[int] = None,
        type_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Tuple[List[DayOff], int]:
        """Get days off with optional filters."""
        query = select(DayOff)
        count_query = select(func.count(DayOff.id))

        if resident_id:
            query = query.where(DayOff.resident_id == resident_id)
            count_query = count_query.where(DayOff.resident_id == resident_id)

        if type_id:
            query = query.where(DayOff.type_id == type_id)
            count_query = count_query.where(DayOff.type_id == type_id)

        if start_date:
            query = query.where(DayOff.end_date >= start_date)
            count_query = count_query.where(DayOff.end_date >= start_date)

        if end_date:
            query = query.where(DayOff.start_date <= end_date)
            count_query = count_query.where(DayOff.start_date <= end_date)

        # Get total count
        count_result = await self.db.execute(count_query)
        total = count_result.scalar()

        # Get paginated results
        query = query.order_by(DayOff.start_date.desc()).limit(limit).offset(offset)
        result = await self.db.execute(query)
        days_off = result.scalars().all()

        return days_off, total
