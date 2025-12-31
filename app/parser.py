"""
XLSX parser for residency rotation schedule.
Extracts resident names and their weekly rotation assignments.
"""
from __future__ import annotations

import pandas as pd
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator, Optional, Dict, Tuple, List, Union

from .config import SCHEDULE_START_YEAR, SCHEDULE_START_MONTH, SCHEDULE_START_DAY


class ScheduleParser:
    """Parser for the residency rotation XLSX schedule."""
    
    def __init__(self, xlsx_path: Union[str, Path]):
        self.xlsx_path = Path(xlsx_path)
        self._df: Optional[pd.DataFrame] = None
        self._residents: Optional[List[str]] = None
        self._week_dates: Optional[Dict[str, Tuple[date, date]]] = None
    
    @property
    def df(self) -> pd.DataFrame:
        """Lazy load the dataframe."""
        if self._df is None:
            self._df = pd.read_excel(self.xlsx_path)
        return self._df
    
    def get_residents(self) -> List[str]:
        """Get list of all resident names."""
        if self._residents is None:
            # Skip first row (header with dates) and filter out non-resident rows
            names = self.df["Resident Names"].dropna().tolist()
            
            # Entries to exclude (headers, categories, rotations, etc.)
            exclude_entries = {
                "TY", "PGY1", "PGY2", "PGY3", "Key:", "Resident Names", "Resident names",
                "CALL", "CC", "ED", "ICU", "ICUN", "NIGHT", "ORANGE", "RED", "PURPLE", 
                "GREEN", "VAC", "Backup", "Jeopardy", "Jeopardy ", "Away", "Neuro", "Geri"
            }
            
            # Filter out header rows, rotation names, and other non-resident entries
            self._residents = [
                name for name in names 
                if isinstance(name, str) 
                and name.strip() 
                and name.strip() not in exclude_entries
                and not name.startswith("Backup")  # Exclude "Backup" entries
                and len(name) > 2  # Exclude very short abbreviations
            ]
        return self._residents
    
    def _parse_week_dates(self) -> Dict[str, Tuple[date, date]]:
        """Parse week column headers into actual date ranges."""
        if self._week_dates is not None:
            return self._week_dates
        
        self._week_dates = {}
        
        # Get the first row which contains date ranges
        first_row = self.df.iloc[0]
        
        # Week columns are WEEK 1, WEEK 2, etc.
        week_columns = [col for col in self.df.columns if col.startswith("WEEK ")]
        
        # Parse the date ranges from the first row
        # Format examples: "July 1-4", "July 5-11", "July 12-18"
        current_year = SCHEDULE_START_YEAR
        current_month = SCHEDULE_START_MONTH
        
        for col in week_columns:
            date_range_str = first_row[col]
            if pd.isna(date_range_str):
                continue
                
            start_date, end_date = self._parse_date_range(
                str(date_range_str), 
                current_year, 
                current_month
            )
            
            self._week_dates[col] = (start_date, end_date)
            
            # Update current month/year for next iteration
            current_month = end_date.month
            current_year = end_date.year
        
        return self._week_dates
    
    def _parse_date_range(self, date_str: str, hint_year: int, hint_month: int) -> Tuple[date, date]:
        """
        Parse a date range string like "July 1-4" or "Dec 27-2" into actual dates.
        Handles month transitions like "July 26-1" (July 26 to Aug 1).
        """
        # Month name mapping
        months = {
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
        
        # Clean up the string
        date_str = date_str.strip().replace("- ", "-").replace(" -", "-")
        
        # Handle formats like "Aug 16- 22" (space after dash)
        date_str = date_str.replace("-  ", "-").replace("- ", "-")
        
        # Parse month and days
        parts = date_str.split()
        
        if len(parts) == 2:
            # Format: "July 1-4" or "Jan 31-Feb6"
            month_str = parts[0]
            day_range = parts[1]
        elif len(parts) == 1:
            # Format: "June27-Jul 3" - no space between month and day
            # Try to extract month name
            for m in months.keys():
                if date_str.startswith(m):
                    month_str = m
                    day_range = date_str[len(m):]
                    break
            else:
                # Fallback
                month_str = date_str[:3]
                day_range = date_str[3:]
        else:
            # Complex format like "Jan 31-Feb6" or "June27-Jul 3"
            month_str = parts[0]
            day_range = " ".join(parts[1:])
        
        # Get start month
        start_month = months.get(month_str, hint_month)
        
        # Determine year - if we go from Dec to Jan, increment year
        if hint_month > 6 and start_month < 6:
            start_year = hint_year + 1
        else:
            start_year = hint_year
        
        # Parse day range
        if "-" in day_range:
            day_parts = day_range.split("-")
            start_day_str = day_parts[0].strip()
            end_part = day_parts[1].strip() if len(day_parts) > 1 else start_day_str
            
            # Start day is just a number
            start_day = int("".join(c for c in start_day_str if c.isdigit()) or "1")
            
            # End part might include a month (e.g., "Feb6")
            end_month = start_month
            end_year = start_year
            
            # Check if end part has a month prefix
            end_day_str = end_part
            for m, m_num in months.items():
                if end_part.startswith(m):
                    end_month = m_num
                    end_day_str = end_part[len(m):]
                    # Handle year transition
                    if end_month < start_month:
                        end_year = start_year + 1
                    break
            
            end_day = int("".join(c for c in end_day_str if c.isdigit()) or "1")
            
            # Handle month rollover (e.g., "July 26-1" means July 26 to Aug 1)
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
        
        start_date = date(start_year, start_month, start_day)
        end_date = date(end_year, end_month, end_day)
        
        return start_date, end_date
    
    def get_resident_schedule(self, resident_name: str) -> Iterator[Tuple[str, date, date]]:
        """
        Get all rotation assignments for a resident.
        
        Yields tuples of (rotation_name, start_date, end_date) for each week.
        """
        # Find the resident's row
        resident_rows = self.df[self.df["Resident Names"] == resident_name]
        
        if resident_rows.empty:
            return
        
        resident_row = resident_rows.iloc[0]
        week_dates = self._parse_week_dates()
        
        for week_col, (start_date, end_date) in week_dates.items():
            rotation = resident_row.get(week_col)
            
            if pd.notna(rotation) and rotation:
                yield (str(rotation).strip(), start_date, end_date)


# Global parser instance (lazy loaded)
_parser: ScheduleParser | None = None


def get_parser(xlsx_path: Union[str, Path] = "schedule.xlsx") -> ScheduleParser:
    """Get or create the global parser instance."""
    global _parser
    if _parser is None:
        _parser = ScheduleParser(xlsx_path)
    return _parser


def reload_parser(xlsx_path: Union[str, Path] = "schedule.xlsx") -> ScheduleParser:
    """Force reload the parser with fresh data."""
    global _parser
    _parser = ScheduleParser(xlsx_path)
    return _parser

