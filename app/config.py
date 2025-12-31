"""
Configuration for rotation rules and schedule settings.
"""
from __future__ import annotations

from datetime import time
from typing import NamedTuple, Optional, Dict

# Schedule starts July 2025 (academic year 2025-2026)
# WEEK 1 starts Saturday July 5, 2025 (since July 1-4 is partial week)
# Actually looking at the data: WEEK 1 is "July 1-4" which is Tue-Fri
# So the schedule likely starts Tuesday July 1, 2025
# But weeks run Sat-Fri, so WEEK 1 is a partial week

SCHEDULE_START_YEAR = 2025
SCHEDULE_START_MONTH = 7
SCHEDULE_START_DAY = 1  # July 1, 2025 (Tuesday)

# Week structure: Saturday to Friday
# Each week column represents a Sat-Fri period
# Week 1: July 1-4 (partial - Tue to Fri since July 1 is Tuesday)
# Week 2: July 5-11 (Sat to Fri)


class RotationTimes(NamedTuple):
    """Time range for a rotation."""
    start: time
    end: time
    overnight: bool = False  # If True, end time is next day
    weekdays_only: bool = False  # If True, Mon-Fri only


# Rotation type definitions with their schedules
ROTATION_RULES: Dict[str, RotationTimes] = {
    # Hospital floors (colors) - daily 6:00 AM - 7:30 PM
    "ORANGE": RotationTimes(time(6, 0), time(19, 30)),
    "RED": RotationTimes(time(6, 0), time(19, 30)),
    "PURPLE": RotationTimes(time(6, 0), time(19, 30)),
    "GREEN": RotationTimes(time(6, 0), time(19, 30)),
    
    # ICU - daily 6:00 AM - 7:00 PM
    "ICU": RotationTimes(time(6, 0), time(19, 0)),
    
    # Night shift - 6:00 PM - 7:30 AM (overnight)
    "NIGHT": RotationTimes(time(18, 0), time(7, 30), overnight=True),
    
    # Clinic/Ambulatory - weekdays only 6:00 AM - 7:30 PM
    "AMBULAT": RotationTimes(time(6, 0), time(19, 30), weekdays_only=True),
    
    # Other rotations - default to daily 6:00 AM - 7:30 PM
    "ANESTH": RotationTimes(time(6, 0), time(19, 30)),
    "ED": RotationTimes(time(6, 0), time(19, 30)),
    "PULM": RotationTimes(time(6, 0), time(19, 30)),
    "CARDIO": RotationTimes(time(6, 0), time(19, 30)),
    "RENAL": RotationTimes(time(6, 0), time(19, 30)),
    "RHEUM": RotationTimes(time(6, 0), time(19, 30)),
    "GERI": RotationTimes(time(6, 0), time(19, 30)),
    "GI": RotationTimes(time(6, 0), time(19, 30)),
    "A/I": RotationTimes(time(6, 0), time(19, 30)),  # Allergy/Immunology
    "ENT": RotationTimes(time(6, 0), time(19, 30)),
    "RAD/IR": RotationTimes(time(6, 0), time(19, 30)),
    "RAD-A": RotationTimes(time(6, 0), time(19, 30)),
    "Surgery": RotationTimes(time(6, 0), time(19, 30)),
}

# Default rotation times for unknown rotations
DEFAULT_ROTATION = RotationTimes(time(6, 0), time(19, 30))

# Rotations to skip (no calendar events)
SKIP_ROTATIONS = {"VAC", "Research", "NaN", "", None}


def get_rotation_times(rotation: str) -> Optional[RotationTimes]:
    """Get the time rules for a rotation, or None if it should be skipped."""
    if rotation in SKIP_ROTATIONS or (isinstance(rotation, float) and str(rotation) == "nan"):
        return None
    
    # Check for exact match first
    if rotation in ROTATION_RULES:
        return ROTATION_RULES[rotation]
    
    # Check for partial match (case-insensitive)
    rotation_upper = rotation.upper() if isinstance(rotation, str) else ""
    for key, times in ROTATION_RULES.items():
        if key.upper() in rotation_upper or rotation_upper in key.upper():
            return times
    
    # Return default for unknown rotations
    return DEFAULT_ROTATION

