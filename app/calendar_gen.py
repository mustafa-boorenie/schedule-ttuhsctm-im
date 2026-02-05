"""
ICS calendar generator for residency rotations.
Creates iCal files with proper event times based on rotation rules.
"""
from datetime import date, datetime, timedelta
from typing import Iterator
from icalendar import Calendar, Event

from .config import get_rotation_times, RotationTimes
from .parser import get_parser


def _slug(value: str) -> str:
    return "".join(ch.lower() for ch in value.strip() if ch.isalnum()) or "unknown"


def generate_calendar(resident_name: str) -> Calendar:
    """
    Generate an iCal calendar for a specific resident.
    
    Args:
        resident_name: The name of the resident
        
    Returns:
        An icalendar.Calendar object with all rotation events
    """
    cal = Calendar()
    
    # Required calendar properties
    cal.add("prodid", "-//Residency Rotation Calendar//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    cal.add("x-wr-calname", f"{resident_name} - Rotations")
    cal.add("x-wr-timezone", "America/New_York")  # Adjust as needed
    
    # Get the parser and generate events
    parser = get_parser()
    
    for rotation, week_start, week_end in parser.get_resident_schedule(resident_name):
        rotation_times = get_rotation_times(rotation)
        
        if rotation_times is None:
            # Skip this rotation (VAC, Research, etc.)
            continue
        
        # Generate individual day events for this week
        for event in generate_week_events(resident_name, rotation, week_start, week_end, rotation_times):
            cal.add_component(event)
    
    return cal


def generate_week_events(
    resident_name: str,
    rotation: str,
    week_start: date,
    week_end: date,
    times: RotationTimes
) -> Iterator[Event]:
    """
    Generate individual day events for a rotation week.
    
    Args:
        rotation: Name of the rotation (e.g., "ICU", "NIGHT")
        week_start: Start date of the week
        week_end: End date of the week
        times: RotationTimes with schedule rules
        
    Yields:
        Event objects for each day
    """
    current_date = week_start
    
    while current_date <= week_end:
        # Check if we should skip weekends for weekday-only rotations
        if times.weekdays_only and current_date.weekday() >= 5:  # 5=Sat, 6=Sun
            current_date += timedelta(days=1)
            continue
        
        # Create event for this day
        event = Event()
        
        # Generate unique ID
        # Stable UID is important for subscribed calendars (prevents duplicates on refresh).
        uid = f"rotation-{_slug(resident_name)}-{current_date.isoformat()}@rotation-calendar"
        event.add("uid", uid)
        
        # Event summary (title)
        event.add("summary", rotation)
        
        # Calculate start and end times
        start_dt = datetime.combine(current_date, times.start)
        
        if times.overnight:
            # Night shift ends the next day
            end_dt = datetime.combine(current_date + timedelta(days=1), times.end)
        else:
            end_dt = datetime.combine(current_date, times.end)
        
        event.add("dtstart", start_dt)
        event.add("dtend", end_dt)
        
        # Add description
        event.add("description", f"Rotation: {rotation}")
        
        # Add creation timestamp
        event.add("dtstamp", datetime.now())
        
        yield event
        
        current_date += timedelta(days=1)


def calendar_to_ics(cal: Calendar) -> bytes:
    """Convert a Calendar object to ICS bytes."""
    return cal.to_ical()


def generate_resident_ics(resident_name: str) -> bytes:
    """
    Generate ICS file content for a resident.
    
    Args:
        resident_name: The name of the resident
        
    Returns:
        ICS file content as bytes
    """
    cal = generate_calendar(resident_name)
    return calendar_to_ics(cal)
