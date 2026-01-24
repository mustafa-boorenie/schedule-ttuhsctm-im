"""
Amion scraper service for extracting call schedules and attending assignments.

Uses Playwright for browser automation to handle JavaScript-rendered content.
"""
import asyncio
import re
from datetime import date, datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = None
    Page = None

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Resident, Attending, AttendingAssignment, CallAssignment,
    AmionSyncLog, AcademicYear, DataSource, SyncStatus
)
from ..settings import settings


@dataclass
class ScrapedCallEntry:
    """A call assignment scraped from Amion."""
    resident_name: str
    date: date
    call_type: str  # 'on-call', 'pre-call', 'post-call'
    service: Optional[str] = None
    location: Optional[str] = None
    raw_text: Optional[str] = None


@dataclass
class ScrapedAttendingEntry:
    """An attending assignment scraped from Amion."""
    attending_name: str
    service: str
    date: date
    raw_text: Optional[str] = None


@dataclass
class NameMatch:
    """Result of matching a scraped name to a database resident."""
    scraped_name: str
    matched_resident_id: Optional[int]
    matched_resident_name: Optional[str]
    confidence: float  # 0.0 to 1.0
    needs_review: bool


class AmionScraper:
    """
    Scraper for Amion call schedules.

    Handles browser automation, data extraction, and name matching.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.browser: Optional[Browser] = None
        self._resident_cache: Dict[str, Resident] = {}
        self._attending_cache: Dict[str, Attending] = {}

    async def _get_browser(self) -> Browser:
        """Get or create browser instance."""
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright not available - using HTTP fallback")
        if not self.browser:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
        return self.browser

    async def close(self):
        """Close browser instance."""
        if self.browser:
            await self.browser.close()
            self.browser = None

    async def scrape_month(
        self,
        year: int,
        month: int,
        base_url: Optional[str] = None,
    ) -> Tuple[List[ScrapedCallEntry], List[ScrapedAttendingEntry]]:
        """
        Scrape call and attending data for a specific month.

        Returns tuple of (call_entries, attending_entries).
        """
        if not base_url:
            base_url = settings.amion_base_url

        if not base_url:
            raise ValueError("Amion base URL not configured")

        # Build URL with month parameter
        month_str = f"{year}-{month:02d}-01"

        # Parse and rebuild URL with correct parameters
        if "?" in base_url:
            url = f"{base_url}&month={month_str}"
        else:
            url = f"{base_url}?month={month_str}&assignment_kind=call&y_axis=names"

        # Try HTTP-based scraping first (faster, no browser needed)
        try:
            return await self._scrape_with_http(url, year, month)
        except Exception as http_error:
            print(f"HTTP scraping failed: {http_error}, trying Playwright...")

            # Fall back to Playwright if available
            if PLAYWRIGHT_AVAILABLE:
                try:
                    return await self._scrape_with_playwright(url, year, month)
                except Exception as pw_error:
                    raise RuntimeError(f"Both HTTP and Playwright scraping failed. HTTP: {http_error}, Playwright: {pw_error}")
            else:
                raise RuntimeError(f"HTTP scraping failed and Playwright not available: {http_error}")

    async def _scrape_with_http(
        self,
        url: str,
        year: int,
        month: int,
    ) -> Tuple[List[ScrapedCallEntry], List[ScrapedAttendingEntry]]:
        """Scrape using HTTP requests (no browser needed)."""
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text

        soup = BeautifulSoup(html, 'html.parser')

        call_entries = self._extract_call_entries_from_soup(soup, year, month)
        attending_entries = self._extract_attending_entries_from_soup(soup, year, month)

        return call_entries, attending_entries

    def _extract_call_entries_from_soup(
        self,
        soup: BeautifulSoup,
        year: int,
        month: int,
    ) -> List[ScrapedCallEntry]:
        """Extract call entries from parsed HTML."""
        entries = []

        # Look for table-based schedules (common in Amion)
        tables = soup.find_all('table')

        for table in tables:
            rows = table.find_all('tr')

            # Try to identify header row with dates
            header_dates = []
            for row in rows[:3]:  # Check first few rows for headers
                cells = row.find_all(['th', 'td'])
                for idx, cell in enumerate(cells):
                    text = cell.get_text(strip=True)
                    # Check if this looks like a date
                    if any(day in text for day in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']):
                        continue
                    if re.match(r'\d{1,2}[/-]\d{1,2}', text):
                        header_dates.append((idx, text))

            # Process data rows
            for row in rows:
                cells = row.find_all(['th', 'td'])
                if not cells:
                    continue

                # First cell might be resident name
                first_cell = cells[0].get_text(strip=True)

                # Skip header/label rows
                if first_cell.lower() in ('name', 'resident', 'date', '', 'call', 'schedule'):
                    continue
                if first_cell in ('TY', 'PGY1', 'PGY2', 'PGY3'):
                    continue

                # Check if this looks like a resident name (has at least 2 parts)
                if len(first_cell.split()) < 2 and len(first_cell) < 3:
                    continue

                resident_name = first_cell

                # Check remaining cells for call indicators
                for idx, cell in enumerate(cells[1:], 1):
                    cell_text = cell.get_text(strip=True).upper()

                    if not cell_text:
                        continue

                    # Identify call type
                    call_type = None
                    if any(x in cell_text for x in ['ON-CALL', 'ONCALL', 'ON CALL', 'CALL']):
                        call_type = 'on-call'
                    elif any(x in cell_text for x in ['PRE-CALL', 'PRECALL', 'PRE CALL', 'PRE']):
                        call_type = 'pre-call'
                    elif any(x in cell_text for x in ['POST-CALL', 'POSTCALL', 'POST CALL', 'POST']):
                        call_type = 'post-call'
                    elif cell_text in ['C', 'X', '*']:
                        call_type = 'on-call'

                    if call_type:
                        # Calculate date from column index
                        try:
                            entry_date = date(year, month, idx)
                            if entry_date.month == month:
                                entries.append(ScrapedCallEntry(
                                    resident_name=resident_name,
                                    date=entry_date,
                                    call_type=call_type,
                                    raw_text=cell_text,
                                ))
                        except ValueError:
                            continue

        return entries

    def _extract_attending_entries_from_soup(
        self,
        soup: BeautifulSoup,
        year: int,
        month: int,
    ) -> List[ScrapedAttendingEntry]:
        """Extract attending entries from parsed HTML."""
        entries = []

        # Look for attending sections
        for element in soup.find_all(['div', 'section', 'table']):
            text = element.get_text()
            if 'attending' in text.lower() or 'faculty' in text.lower():
                # Parse this section for attending assignments
                rows = element.find_all('tr') if element.name == 'table' else [element]

                for row in rows:
                    row_text = row.get_text(strip=True)
                    parsed = self._parse_attending_row(row_text, year, month)
                    entries.extend(parsed)

        return entries

    async def _scrape_with_playwright(
        self,
        url: str,
        year: int,
        month: int,
    ) -> Tuple[List[ScrapedCallEntry], List[ScrapedAttendingEntry]]:
        """Scrape using Playwright browser automation."""
        browser = await self._get_browser()
        page = await browser.new_page()

        try:
            # Navigate to page
            await page.goto(url, wait_until="networkidle", timeout=30000)

            # Wait for content to load
            await page.wait_for_timeout(2000)

            # Extract data
            call_entries = await self._extract_call_entries(page, year, month)
            attending_entries = await self._extract_attending_entries(page, year, month)

            return call_entries, attending_entries

        finally:
            await page.close()

    async def _extract_call_entries(
        self,
        page: Page,
        year: int,
        month: int,
    ) -> List[ScrapedCallEntry]:
        """Extract call assignments from the page."""
        entries = []

        # Try to find the calendar grid
        # Amion typically uses table-based layouts
        try:
            # Get all cells that might contain assignments
            # This selector may need adjustment based on actual Amion HTML structure
            cells = await page.query_selector_all('td.assignment, td[data-assignment], .calendar-cell, .schedule-cell')

            if not cells:
                # Fallback: try to get any table cells with content
                cells = await page.query_selector_all('table td')

            for cell in cells:
                text = await cell.text_content()
                if not text or not text.strip():
                    continue

                # Try to extract date from cell's position or data attributes
                date_attr = await cell.get_attribute('data-date')
                if date_attr:
                    try:
                        entry_date = date.fromisoformat(date_attr)
                    except:
                        continue
                else:
                    # Try to infer date from position
                    continue

                # Parse the cell content for names and call types
                parsed = self._parse_call_cell(text.strip(), entry_date)
                entries.extend(parsed)

            # Alternative: try to extract from a more structured format
            if not entries:
                entries = await self._extract_from_grid_view(page, year, month)

        except Exception as e:
            print(f"Error extracting call entries: {e}")

        return entries

    async def _extract_from_grid_view(
        self,
        page: Page,
        year: int,
        month: int,
    ) -> List[ScrapedCallEntry]:
        """Extract from grid view where rows are names and columns are dates."""
        entries = []

        try:
            # Get the page content for analysis
            content = await page.content()

            # Try to find rows with resident names
            rows = await page.query_selector_all('tr[data-resident], .resident-row, table tbody tr')

            for row in rows:
                # Get resident name from first cell
                name_cell = await row.query_selector('td:first-child, th:first-child')
                if not name_cell:
                    continue

                name = await name_cell.text_content()
                if not name or not name.strip():
                    continue

                name = name.strip()

                # Skip header rows
                if name.lower() in ('name', 'resident', 'date', ''):
                    continue

                # Get all assignment cells
                cells = await row.query_selector_all('td:not(:first-child)')

                for idx, cell in enumerate(cells):
                    cell_text = await cell.text_content()
                    if not cell_text or not cell_text.strip():
                        continue

                    # Calculate date based on column index
                    entry_date = date(year, month, 1) + timedelta(days=idx)
                    if entry_date.month != month:
                        continue

                    # Check if this cell indicates a call assignment
                    cell_text = cell_text.strip().upper()

                    call_type = None
                    if any(x in cell_text for x in ['CALL', 'ON', 'C']):
                        call_type = 'on-call'
                    elif any(x in cell_text for x in ['PRE', 'P']):
                        call_type = 'pre-call'
                    elif any(x in cell_text for x in ['POST', 'PO']):
                        call_type = 'post-call'

                    if call_type:
                        entries.append(ScrapedCallEntry(
                            resident_name=name,
                            date=entry_date,
                            call_type=call_type,
                            raw_text=cell_text,
                        ))

        except Exception as e:
            print(f"Error in grid extraction: {e}")

        return entries

    async def _extract_attending_entries(
        self,
        page: Page,
        year: int,
        month: int,
    ) -> List[ScrapedAttendingEntry]:
        """Extract attending assignments from the page."""
        entries = []

        try:
            # Look for attending section
            attending_sections = await page.query_selector_all(
                '.attending-section, [data-type="attending"], .faculty-schedule'
            )

            for section in attending_sections:
                rows = await section.query_selector_all('tr, .attending-row')

                for row in rows:
                    text = await row.text_content()
                    if not text:
                        continue

                    # Parse attending assignments
                    # Format might be: "Dr. Smith - ICU - Jan 1-7"
                    parsed = self._parse_attending_row(text.strip(), year, month)
                    entries.extend(parsed)

        except Exception as e:
            print(f"Error extracting attending entries: {e}")

        return entries

    def _parse_call_cell(self, text: str, entry_date: date) -> List[ScrapedCallEntry]:
        """Parse a cell's text content to extract call assignments."""
        entries = []

        # Split by common delimiters
        lines = re.split(r'[\n\r,;]', text)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Try to identify name and call type
            # Common patterns:
            # "John Smith (Call)"
            # "Call: John Smith"
            # "John Smith - On Call"

            call_type = 'on-call'  # default
            name = line

            # Extract call type indicators
            if re.search(r'\bpre[-\s]?call\b', line, re.I):
                call_type = 'pre-call'
                name = re.sub(r'\bpre[-\s]?call\b', '', line, flags=re.I)
            elif re.search(r'\bpost[-\s]?call\b', line, re.I):
                call_type = 'post-call'
                name = re.sub(r'\bpost[-\s]?call\b', '', line, flags=re.I)
            elif re.search(r'\bon[-\s]?call\b', line, re.I):
                call_type = 'on-call'
                name = re.sub(r'\bon[-\s]?call\b', '', line, flags=re.I)
            elif re.search(r'\bcall\b', line, re.I):
                call_type = 'on-call'
                name = re.sub(r'\bcall\b', '', line, flags=re.I)

            # Clean up name
            name = re.sub(r'[:\-\(\)]', ' ', name)
            name = ' '.join(name.split()).strip()

            if name and len(name) > 2:
                entries.append(ScrapedCallEntry(
                    resident_name=name,
                    date=entry_date,
                    call_type=call_type,
                    raw_text=line,
                ))

        return entries

    def _parse_attending_row(
        self,
        text: str,
        year: int,
        month: int,
    ) -> List[ScrapedAttendingEntry]:
        """Parse an attending row to extract assignments."""
        entries = []

        # Common patterns:
        # "Dr. Smith - ICU - Jan 1-7"
        # "ICU: Dr. Smith (1/1 - 1/7)"

        # Try to extract service, name, and dates
        parts = re.split(r'[\-:|]', text)
        if len(parts) < 2:
            return entries

        # Heuristic: identify which part is the name vs service
        name = None
        service = None

        for part in parts:
            part = part.strip()
            if re.match(r'^(Dr\.?|MD)\s', part, re.I) or len(part.split()) >= 2:
                if not name:
                    name = part
            elif part.upper() in ('ICU', 'ED', 'FLOOR', 'CLINIC', 'NIGHT'):
                service = part
            elif not service and len(part) < 20:
                service = part

        if name and service:
            # Try to extract date range
            date_match = re.search(r'(\d{1,2})[/\-](\d{1,2})', text)
            if date_match:
                start_day = int(date_match.group(1))
                end_day = int(date_match.group(2)) if date_match.group(2) else start_day

                # Create entry for each day in range
                for day in range(start_day, min(end_day + 1, 32)):
                    try:
                        entry_date = date(year, month, day)
                        entries.append(ScrapedAttendingEntry(
                            attending_name=name,
                            service=service,
                            date=entry_date,
                            raw_text=text,
                        ))
                    except ValueError:
                        continue

        return entries

    async def match_names(
        self,
        scraped_names: List[str],
        academic_year_id: Optional[int] = None,
    ) -> List[NameMatch]:
        """
        Match scraped names to residents in the database.

        Uses fuzzy matching for names that don't match exactly.
        """
        # Load residents
        query = select(Resident).where(Resident.is_active == True)
        if academic_year_id:
            query = query.where(Resident.academic_year_id == academic_year_id)

        result = await self.db.execute(query)
        residents = {r.name.lower(): r for r in result.scalars()}

        matches = []

        for scraped_name in scraped_names:
            scraped_lower = scraped_name.lower().strip()

            # Exact match
            if scraped_lower in residents:
                resident = residents[scraped_lower]
                matches.append(NameMatch(
                    scraped_name=scraped_name,
                    matched_resident_id=resident.id,
                    matched_resident_name=resident.name,
                    confidence=1.0,
                    needs_review=False,
                ))
                continue

            # Fuzzy match
            best_match = None
            best_score = 0.0

            for db_name, resident in residents.items():
                # Try different matching strategies
                score = self._calculate_name_similarity(scraped_lower, db_name)

                if score > best_score:
                    best_score = score
                    best_match = resident

            if best_match and best_score >= 0.8:
                matches.append(NameMatch(
                    scraped_name=scraped_name,
                    matched_resident_id=best_match.id,
                    matched_resident_name=best_match.name,
                    confidence=best_score,
                    needs_review=best_score < 0.95,
                ))
            elif best_match and best_score >= 0.6:
                matches.append(NameMatch(
                    scraped_name=scraped_name,
                    matched_resident_id=best_match.id,
                    matched_resident_name=best_match.name,
                    confidence=best_score,
                    needs_review=True,
                ))
            else:
                matches.append(NameMatch(
                    scraped_name=scraped_name,
                    matched_resident_id=None,
                    matched_resident_name=None,
                    confidence=0.0,
                    needs_review=True,
                ))

        return matches

    def _calculate_name_similarity(self, name1: str, name2: str) -> float:
        """Calculate similarity between two names."""
        # Direct sequence matching
        direct_score = SequenceMatcher(None, name1, name2).ratio()

        # Try matching with name parts reordered
        parts1 = name1.split()
        parts2 = name2.split()

        # Check if last names match
        if parts1 and parts2:
            last_name_score = SequenceMatcher(None, parts1[-1], parts2[-1]).ratio()

            # If last names are very similar, boost score
            if last_name_score > 0.9:
                direct_score = max(direct_score, 0.7 + last_name_score * 0.3)

        # Check reversed name order
        reversed_name = ' '.join(reversed(parts1))
        reversed_score = SequenceMatcher(None, reversed_name, name2).ratio()

        return max(direct_score, reversed_score)

    async def sync_to_database(
        self,
        call_entries: List[ScrapedCallEntry],
        attending_entries: List[ScrapedAttendingEntry],
        academic_year_id: Optional[int] = None,
        name_mappings: Optional[Dict[str, int]] = None,
    ) -> Dict:
        """
        Sync scraped data to the database.

        Args:
            call_entries: Scraped call assignments
            attending_entries: Scraped attending assignments
            academic_year_id: Academic year to associate data with
            name_mappings: Optional dict mapping scraped names to resident IDs

        Returns:
            Summary of sync results
        """
        results = {
            "call_created": 0,
            "call_updated": 0,
            "call_skipped": 0,
            "attending_created": 0,
            "attending_updated": 0,
            "errors": [],
            "unmatched_names": [],
        }

        # Get name mappings if not provided
        if not name_mappings:
            all_names = list(set(e.resident_name for e in call_entries))
            matches = await self.match_names(all_names, academic_year_id)
            name_mappings = {
                m.scraped_name: m.matched_resident_id
                for m in matches
                if m.matched_resident_id
            }
            results["unmatched_names"] = [
                m.scraped_name for m in matches if not m.matched_resident_id
            ]

        # Sync call entries
        for entry in call_entries:
            resident_id = name_mappings.get(entry.resident_name)
            if not resident_id:
                results["call_skipped"] += 1
                continue

            try:
                # Check for existing
                existing = await self.db.execute(
                    select(CallAssignment).where(
                        CallAssignment.resident_id == resident_id,
                        CallAssignment.date == entry.date,
                        CallAssignment.call_type == entry.call_type
                    )
                )
                existing = existing.scalar_one_or_none()

                if existing:
                    # Update
                    existing.service = entry.service
                    existing.location = entry.location
                    existing.source = DataSource.AMION
                    results["call_updated"] += 1
                else:
                    # Create
                    call = CallAssignment(
                        resident_id=resident_id,
                        call_type=entry.call_type,
                        date=entry.date,
                        service=entry.service,
                        location=entry.location,
                        academic_year_id=academic_year_id,
                        source=DataSource.AMION,
                    )
                    self.db.add(call)
                    results["call_created"] += 1

            except Exception as e:
                results["errors"].append(f"Call entry error: {e}")

        # Sync attending entries
        for entry in attending_entries:
            try:
                # Get or create attending
                existing_attending = await self.db.execute(
                    select(Attending).where(Attending.name == entry.attending_name)
                )
                attending = existing_attending.scalar_one_or_none()

                if not attending:
                    attending = Attending(
                        name=entry.attending_name,
                        service=entry.service,
                    )
                    self.db.add(attending)
                    await self.db.flush()

                # Check for existing assignment
                existing_assignment = await self.db.execute(
                    select(AttendingAssignment).where(
                        AttendingAssignment.service == entry.service,
                        AttendingAssignment.date == entry.date
                    )
                )
                existing_assignment = existing_assignment.scalar_one_or_none()

                if existing_assignment:
                    existing_assignment.attending_id = attending.id
                    results["attending_updated"] += 1
                else:
                    assignment = AttendingAssignment(
                        attending_id=attending.id,
                        service=entry.service,
                        date=entry.date,
                        academic_year_id=academic_year_id,
                        source=DataSource.AMION,
                    )
                    self.db.add(assignment)
                    results["attending_created"] += 1

            except Exception as e:
                results["errors"].append(f"Attending entry error: {e}")

        return results


async def run_amion_sync(
    db: AsyncSession,
    months_to_sync: int = 1,
    base_url: Optional[str] = None,
) -> Dict:
    """
    Run a full Amion sync for the specified number of months.

    Args:
        db: Database session
        months_to_sync: Number of months to sync (starting from current)
        base_url: Optional Amion URL override

    Returns:
        Summary of sync results
    """
    # Create sync log
    sync_log = AmionSyncLog(
        sync_type="full",
        status=SyncStatus.PARTIAL,
        started_at=datetime.utcnow(),
    )
    db.add(sync_log)
    await db.flush()

    scraper = AmionScraper(db)

    try:
        # Get current academic year
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.is_current == True)
        )
        academic_year = result.scalar_one_or_none()
        academic_year_id = academic_year.id if academic_year else None

        total_results = {
            "months_synced": 0,
            "call_created": 0,
            "call_updated": 0,
            "call_skipped": 0,
            "attending_created": 0,
            "attending_updated": 0,
            "errors": [],
            "unmatched_names": set(),
        }

        # Sync each month
        today = date.today()
        for i in range(months_to_sync):
            # Calculate month to sync
            month_date = today + timedelta(days=30 * i)
            year = month_date.year
            month = month_date.month

            try:
                call_entries, attending_entries = await scraper.scrape_month(
                    year, month, base_url
                )

                results = await scraper.sync_to_database(
                    call_entries,
                    attending_entries,
                    academic_year_id,
                )

                total_results["months_synced"] += 1
                total_results["call_created"] += results["call_created"]
                total_results["call_updated"] += results["call_updated"]
                total_results["call_skipped"] += results["call_skipped"]
                total_results["attending_created"] += results["attending_created"]
                total_results["attending_updated"] += results["attending_updated"]
                total_results["errors"].extend(results["errors"])
                total_results["unmatched_names"].update(results.get("unmatched_names", []))

            except Exception as e:
                total_results["errors"].append(f"Month {year}-{month}: {e}")

        # Update sync log
        sync_log.status = SyncStatus.SUCCESS if not total_results["errors"] else SyncStatus.PARTIAL
        sync_log.records_processed = (
            total_results["call_created"] + total_results["call_updated"] +
            total_results["attending_created"] + total_results["attending_updated"]
        )
        sync_log.completed_at = datetime.utcnow()
        sync_log.errors = {
            "messages": total_results["errors"],
            "unmatched_names": list(total_results["unmatched_names"]),
        }

        # Convert set to list for JSON serialization
        total_results["unmatched_names"] = list(total_results["unmatched_names"])

        return total_results

    except Exception as e:
        sync_log.status = SyncStatus.FAILED
        sync_log.completed_at = datetime.utcnow()
        sync_log.errors = {"fatal": str(e)}
        raise

    finally:
        await scraper.close()
