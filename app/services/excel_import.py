"""
Service for importing schedule data from Excel files.
"""
from datetime import date, time
from typing import Dict, List, Tuple, Optional
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Resident, Rotation, ScheduleAssignment, AcademicYear,
    DayOffType, PGYLevel, DataSource
)
from ..settings import settings


class ExcelImportService:
    """Service for importing Excel schedule files into the database."""

    # Entries to exclude (headers, categories, rotations, etc.)
    EXCLUDE_ENTRIES = {
        "TY", "PGY1", "PGY2", "PGY3", "Key:", "Resident Names", "Resident names",
        "CALL", "CC", "Backup", "Jeopardy", "Jeopardy ", "Away",
    }

    # Month name mapping
    MONTHS = {
        "Jan": 1, "January": 1,
        "Feb": 2, "February": 2,
        "Mar": 3, "March": 3,
        "Apr": 4, "April": 4,
        "May": 5,
        "Jun": 6, "June": 6,
        "Jul": 7, "July": 7,
        "Aug": 8, "August": 8,
        "Sep": 9, "September": 9,
        "Oct": 10, "October": 10,
        "Nov": 11, "November": 11,
        "Dec": 12, "December": 12,
    }

    # Default rotation configurations
    DEFAULT_ROTATIONS = {
        "ORANGE": {"color": "#F97316", "start": time(6, 0), "end": time(19, 30)},
        "RED": {"color": "#EF4444", "start": time(6, 0), "end": time(19, 30)},
        "PURPLE": {"color": "#A855F7", "start": time(6, 0), "end": time(19, 30)},
        "GREEN": {"color": "#22C55E", "start": time(6, 0), "end": time(19, 30)},
        "ICU": {"color": "#DC2626", "start": time(6, 0), "end": time(19, 0)},
        "ICUN": {"color": "#B91C1C", "start": time(18, 0), "end": time(7, 30), "overnight": True},
        "NIGHT": {"color": "#6366F1", "start": time(18, 0), "end": time(7, 30), "overnight": True},
        "ED": {"color": "#F59E0B", "start": time(6, 0), "end": time(19, 30)},
        "VAC": {"color": "#10B981", "generates_events": False},
        "Research": {"color": "#64748B", "generates_events": False},
        "AMBULAT": {"color": "#06B6D4", "start": time(6, 0), "end": time(19, 30), "weekdays_only": True},
        "Neuro": {"color": "#8B5CF6", "start": time(6, 0), "end": time(19, 30)},
        "Geri": {"color": "#EC4899", "start": time(6, 0), "end": time(19, 30)},
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self._rotation_cache: Dict[str, Rotation] = {}
        self._resident_cache: Dict[str, Resident] = {}

    async def import_excel(
        self,
        xlsx_path: Path,
        academic_year_id: Optional[int] = None,
        pgy_level_hint: Optional[PGYLevel] = None,
    ) -> Dict:
        """
        Import an Excel schedule file into the database.

        Returns a summary of what was imported.
        """
        df = pd.read_excel(xlsx_path)

        # Parse week dates from first row
        week_dates = self._parse_week_dates(df)

        # Get or create academic year
        if not academic_year_id:
            academic_year = await self._get_or_create_current_academic_year()
            academic_year_id = academic_year.id

        # Pre-load/create rotations
        await self._ensure_rotations_exist(df)

        # Process residents and their schedules
        results = {
            "residents_processed": 0,
            "weeks_processed": len(week_dates),
            "assignments_created": 0,
            "assignments_updated": 0,
            "errors": [],
        }

        # Find resident rows (skip header rows)
        current_pgy = pgy_level_hint
        for idx, row in df.iterrows():
            if idx == 0:  # Skip date header row
                continue

            name = row.get("Resident Names")
            if pd.isna(name) or not isinstance(name, str):
                continue

            name = name.strip()

            # Check for PGY level markers
            if name in ("TY", "PGY1", "PGY2", "PGY3"):
                current_pgy = PGYLevel(name)
                continue

            # Skip non-resident entries
            if name in self.EXCLUDE_ENTRIES or len(name) <= 2:
                continue

            # Determine PGY level
            pgy_level = current_pgy or self._guess_pgy_level(name, df, idx)

            # Get or create resident
            resident = await self._get_or_create_resident(name, pgy_level, academic_year_id)
            results["residents_processed"] += 1

            # Process schedule assignments for this resident
            for week_col, (week_start, week_end) in week_dates.items():
                rotation_name = row.get(week_col)

                if pd.isna(rotation_name) or not rotation_name:
                    continue

                rotation_name = str(rotation_name).strip()

                # Get or create rotation
                rotation = await self._get_or_create_rotation(rotation_name)

                # Create or update assignment
                created = await self._create_or_update_assignment(
                    resident.id,
                    rotation.id,
                    week_start,
                    week_end,
                    academic_year_id,
                )

                if created:
                    results["assignments_created"] += 1
                else:
                    results["assignments_updated"] += 1

        return results

    def _parse_week_dates(self, df: pd.DataFrame) -> Dict[str, Tuple[date, date]]:
        """Parse week column headers into actual date ranges."""
        week_dates = {}

        # Get the first row which contains date ranges
        first_row = df.iloc[0]

        # Week columns are WEEK 1, WEEK 2, etc.
        week_columns = [col for col in df.columns if str(col).startswith("WEEK ")]

        current_year = settings.schedule_start_year
        current_month = settings.schedule_start_month

        for col in week_columns:
            date_range_str = first_row[col]
            if pd.isna(date_range_str):
                continue

            start_date, end_date = self._parse_date_range(
                str(date_range_str),
                current_year,
                current_month
            )

            week_dates[col] = (start_date, end_date)

            # Update current month/year for next iteration
            current_month = end_date.month
            current_year = end_date.year

        return week_dates

    def _parse_date_range(
        self,
        date_str: str,
        hint_year: int,
        hint_month: int
    ) -> Tuple[date, date]:
        """Parse a date range string like 'July 1-4' into actual dates."""
        # Clean up the string
        date_str = date_str.strip().replace("- ", "-").replace(" -", "-").replace("-  ", "-")

        # Parse month and days
        parts = date_str.split()

        if len(parts) >= 2:
            month_str = parts[0]
            day_range = parts[1] if len(parts) == 2 else " ".join(parts[1:])
        elif len(parts) == 1:
            # Try to extract month name
            for m in self.MONTHS.keys():
                if date_str.startswith(m):
                    month_str = m
                    day_range = date_str[len(m):]
                    break
            else:
                month_str = date_str[:3]
                day_range = date_str[3:]
        else:
            return date(hint_year, hint_month, 1), date(hint_year, hint_month, 7)

        # Get start month
        start_month = self.MONTHS.get(month_str, hint_month)

        # Determine year
        if hint_month > 6 and start_month < 6:
            start_year = hint_year + 1
        else:
            start_year = hint_year

        # Parse day range
        if "-" in day_range:
            day_parts = day_range.split("-")
            start_day_str = day_parts[0].strip()
            end_part = day_parts[1].strip() if len(day_parts) > 1 else start_day_str

            start_day = int("".join(c for c in start_day_str if c.isdigit()) or "1")

            # End part might include a month
            end_month = start_month
            end_year = start_year
            end_day_str = end_part

            for m, m_num in self.MONTHS.items():
                if end_part.startswith(m):
                    end_month = m_num
                    end_day_str = end_part[len(m):]
                    if end_month < start_month:
                        end_year = start_year + 1
                    break

            end_day = int("".join(c for c in end_day_str if c.isdigit()) or "1")

            # Handle month rollover
            if end_day < start_day and end_month == start_month:
                end_month += 1
                if end_month > 12:
                    end_month = 1
                    end_year += 1
        else:
            start_day = int("".join(c for c in day_range if c.isdigit()) or "1")
            end_day = start_day
            end_month = start_month
            end_year = start_year

        return date(start_year, start_month, start_day), date(end_year, end_month, end_day)

    async def _get_or_create_current_academic_year(self) -> AcademicYear:
        """Get or create the current academic year."""
        result = await self.db.execute(
            select(AcademicYear).where(AcademicYear.is_current == True)
        )
        academic_year = result.scalar_one_or_none()

        if not academic_year:
            # Create default academic year
            start_year = settings.schedule_start_year
            academic_year = AcademicYear(
                name=f"{start_year}-{start_year + 1}",
                start_date=date(start_year, 7, 1),
                end_date=date(start_year + 1, 6, 30),
                is_current=True,
            )
            self.db.add(academic_year)
            await self.db.flush()

        return academic_year

    async def _ensure_rotations_exist(self, df: pd.DataFrame) -> None:
        """Ensure all rotations from the Excel file exist in the database."""
        # Collect unique rotation names
        rotation_names = set()
        week_columns = [col for col in df.columns if str(col).startswith("WEEK ")]

        for col in week_columns:
            for value in df[col].dropna():
                if isinstance(value, str) and value.strip():
                    rotation_names.add(value.strip())

        # Get existing rotations
        result = await self.db.execute(select(Rotation))
        existing = {r.name: r for r in result.scalars()}
        self._rotation_cache = existing

        # Create missing rotations
        for name in rotation_names:
            if name not in existing:
                await self._get_or_create_rotation(name)

    async def _get_or_create_rotation(self, name: str) -> Rotation:
        """Get or create a rotation by name."""
        if name in self._rotation_cache:
            return self._rotation_cache[name]

        result = await self.db.execute(select(Rotation).where(Rotation.name == name))
        rotation = result.scalar_one_or_none()

        if not rotation:
            # Create with defaults if available
            defaults = self.DEFAULT_ROTATIONS.get(name, {})
            rotation = Rotation(
                name=name,
                display_name=name,
                color=defaults.get("color", "#6B7280"),
                start_time=defaults.get("start"),
                end_time=defaults.get("end"),
                is_overnight=defaults.get("overnight", False),
                weekdays_only=defaults.get("weekdays_only", False),
                generates_events=defaults.get("generates_events", True),
            )
            self.db.add(rotation)
            await self.db.flush()

        self._rotation_cache[name] = rotation
        return rotation

    async def _get_or_create_resident(
        self,
        name: str,
        pgy_level: PGYLevel,
        academic_year_id: int,
    ) -> Resident:
        """Get or create a resident by name."""
        if name in self._resident_cache:
            return self._resident_cache[name]

        result = await self.db.execute(
            select(Resident).where(
                Resident.name == name,
                Resident.academic_year_id == academic_year_id
            )
        )
        resident = result.scalar_one_or_none()

        if not resident:
            resident = Resident(
                name=name,
                pgy_level=pgy_level,
                academic_year_id=academic_year_id,
                is_active=True,
            )
            self.db.add(resident)
            await self.db.flush()

        self._resident_cache[name] = resident
        return resident

    async def _create_or_update_assignment(
        self,
        resident_id: int,
        rotation_id: int,
        week_start: date,
        week_end: date,
        academic_year_id: int,
    ) -> bool:
        """Create or update a schedule assignment. Returns True if created, False if updated."""
        result = await self.db.execute(
            select(ScheduleAssignment).where(
                ScheduleAssignment.resident_id == resident_id,
                ScheduleAssignment.week_start == week_start
            )
        )
        assignment = result.scalar_one_or_none()

        if assignment:
            assignment.rotation_id = rotation_id
            assignment.week_end = week_end
            assignment.source = DataSource.EXCEL
            return False
        else:
            assignment = ScheduleAssignment(
                resident_id=resident_id,
                rotation_id=rotation_id,
                week_start=week_start,
                week_end=week_end,
                academic_year_id=academic_year_id,
                source=DataSource.EXCEL,
            )
            self.db.add(assignment)
            return True

    def _guess_pgy_level(self, name: str, df: pd.DataFrame, current_idx: int) -> PGYLevel:
        """Guess PGY level based on position in spreadsheet."""
        # Look backwards for PGY markers
        for idx in range(current_idx - 1, -1, -1):
            prev_name = df.iloc[idx].get("Resident Names")
            if pd.notna(prev_name) and str(prev_name).strip() in ("TY", "PGY1", "PGY2", "PGY3"):
                return PGYLevel(str(prev_name).strip())

        # Default to PGY1
        return PGYLevel.PGY1


async def seed_default_day_off_types(db: AsyncSession) -> None:
    """Seed default day off types."""
    default_types = [
        {"name": "Vacation", "color": "#10B981", "is_system": True},
        {"name": "Sick", "color": "#EF4444", "is_system": True},
        {"name": "Conference", "color": "#6366F1", "is_system": True},
        {"name": "Educational Leave", "color": "#8B5CF6", "is_system": True},
        {"name": "Personal", "color": "#F59E0B", "is_system": True},
    ]

    for type_data in default_types:
        result = await db.execute(
            select(DayOffType).where(DayOffType.name == type_data["name"])
        )
        if not result.scalar_one_or_none():
            db.add(DayOffType(**type_data))

    await db.flush()
