"""
Enhanced ICS calendar generator with database support.

Features:
- Rotation schedules from database
- Call status events (pre-call, on-call, post-call)
- Attending information
- Days off integration
- Color coding for different event types
"""
from datetime import date, datetime, timedelta, time
from typing import Optional, List, Tuple
from icalendar import Calendar, Event, vDuration
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Resident, Rotation, ScheduleAssignment,
    CallAssignment, AttendingAssignment, Attending,
    DayOff, DayOffType
)


# Color codes for different event types (Apple/Google Calendar compatible)
# These use the X-APPLE-CALENDAR-COLOR property
COLORS = {
    "rotation": "#06b6d4",      # Cyan for regular rotations
    "on-call": "#ef4444",       # Red for on-call
    "pre-call": "#f59e0b",      # Amber for pre-call
    "post-call": "#22c55e",     # Green for post-call
    "day-off": "#8b5cf6",       # Purple for days off
    "night": "#1e3a8a",         # Dark blue for night shifts
    "icu": "#dc2626",           # Red for ICU
}

# Call type display names and times
CALL_CONFIG = {
    "on-call": {
        "display": "ON CALL",
        "emoji": "🔴",
        "start": time(18, 0),   # 6 PM start
        "end": time(7, 0),      # 7 AM next day
        "overnight": True,
    },
    "pre-call": {
        "display": "PRE-CALL",
        "emoji": "🟡",
        "start": time(6, 0),
        "end": time(18, 0),
        "overnight": False,
    },
    "post-call": {
        "display": "POST-CALL",
        "emoji": "🟢",
        "start": time(7, 0),
        "end": time(12, 0),     # Usually leave by noon
        "overnight": False,
    },
}


class CalendarService:
    """Enhanced calendar generation service."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def generate_calendar(
        self,
        resident_id: int,
        include_rotations: bool = True,
        include_call: bool = True,
        include_attending: bool = True,
        include_days_off: bool = True,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Calendar:
        """
        Generate a complete iCal calendar for a resident.

        Args:
            resident_id: The resident's database ID
            include_rotations: Include rotation schedule events
            include_call: Include call status events
            include_attending: Include attending information in descriptions
            include_days_off: Include days off events
            start_date: Optional start date filter
            end_date: Optional end date filter

        Returns:
            An icalendar.Calendar object
        """
        # Get resident info
        result = await self.db.execute(
            select(Resident).where(Resident.id == resident_id)
        )
        resident = result.scalar_one_or_none()

        if not resident:
            raise ValueError(f"Resident with id {resident_id} not found")

        # Create calendar
        cal = Calendar()
        cal.add("prodid", "-//Residency Rotation Calendar//EN")
        cal.add("version", "2.0")
        cal.add("calscale", "GREGORIAN")
        cal.add("method", "PUBLISH")
        cal.add("x-wr-calname", f"{resident.name} - Schedule")
        cal.add("x-wr-timezone", "America/New_York")
        cal.add('x-published-ttl', vDuration(timedelta(hours=6)))  # Refresh every 6 hours (iOS compatible)

        # Add rotation events
        if include_rotations:
            rotation_events = await self._get_rotation_events(
                resident_id, start_date, end_date, include_attending
            )
            for event in rotation_events:
                cal.add_component(event)

        # Add call events
        if include_call:
            call_events = await self._get_call_events(
                resident_id, start_date, end_date
            )
            for event in call_events:
                cal.add_component(event)

        # Add days off events
        if include_days_off:
            days_off_events = await self._get_days_off_events(
                resident_id, start_date, end_date
            )
            for event in days_off_events:
                cal.add_component(event)

        return cal

    async def _get_rotation_events(
        self,
        resident_id: int,
        start_date: Optional[date],
        end_date: Optional[date],
        include_attending: bool,
    ) -> List[Event]:
        """Generate events for rotation assignments."""
        events = []

        # Build query
        query = (
            select(ScheduleAssignment, Rotation)
            .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
            .where(ScheduleAssignment.resident_id == resident_id)
        )

        if start_date:
            query = query.where(ScheduleAssignment.week_end >= start_date)
        if end_date:
            query = query.where(ScheduleAssignment.week_start <= end_date)

        query = query.order_by(ScheduleAssignment.week_start)

        result = await self.db.execute(query)
        assignments = result.all()

        for assignment, rotation in assignments:
            # Get attending info if requested
            attending_info = ""
            if include_attending:
                attending_info = await self._get_attending_for_period(
                    assignment.week_start,
                    assignment.week_end,
                    rotation.name,
                )

            # Generate events for each day of the rotation
            week_events = self._create_rotation_week_events(
                resident_id=resident_id,
                rotation=rotation,
                week_start=assignment.week_start,
                week_end=assignment.week_end,
                attending_info=attending_info,
            )
            events.extend(week_events)

        return events

    def _create_rotation_week_events(
        self,
        resident_id: int,
        rotation: Rotation,
        week_start: date,
        week_end: date,
        attending_info: str = "",
    ) -> List[Event]:
        """Create individual day events for a rotation week."""
        events = []
        current_date = week_start

        # Parse rotation times
        start_time = self._parse_time(rotation.start_time) or time(6, 0)
        end_time = self._parse_time(rotation.end_time) or time(19, 0)

        # Determine color based on rotation type
        color = rotation.color or COLORS.get("rotation")
        if "NIGHT" in rotation.name.upper():
            color = COLORS.get("night", color)
        elif "ICU" in rotation.name.upper():
            color = COLORS.get("icu", color)

        while current_date <= week_end:
            # Skip weekends for weekday-only rotations
            if rotation.weekdays_only and current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue

            event = Event()
            # Stable UID is important for subscribed calendars (prevents duplicates on refresh).
            event.add("uid", f"rotation-{resident_id}-{current_date.isoformat()}@rotation-calendar")
            event.add("summary", rotation.name)

            # Calculate times
            start_dt = datetime.combine(current_date, start_time)
            if rotation.is_overnight:
                end_dt = datetime.combine(current_date + timedelta(days=1), end_time)
            else:
                end_dt = datetime.combine(current_date, end_time)

            event.add("dtstart", start_dt)
            event.add("dtend", end_dt)

            # Build description
            description_parts = [f"Rotation: {rotation.name}"]
            if rotation.location:
                description_parts.append(f"Location: {rotation.location}")
            if attending_info:
                description_parts.append(f"\nAttending: {attending_info}")

            event.add("description", "\n".join(description_parts))
            event.add("dtstamp", datetime.now())

            # Add color (for Apple Calendar)
            if color:
                event.add("x-apple-calendar-color", color)

            # Add categories for filtering
            event.add("categories", [rotation.name])

            events.append(event)
            current_date += timedelta(days=1)

        return events

    async def _get_call_events(
        self,
        resident_id: int,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> List[Event]:
        """Generate events for call assignments."""
        events = []

        query = select(CallAssignment).where(
            CallAssignment.resident_id == resident_id
        )

        if start_date:
            query = query.where(CallAssignment.date >= start_date)
        if end_date:
            query = query.where(CallAssignment.date <= end_date)

        query = query.order_by(CallAssignment.date)

        try:
            result = await self.db.execute(query)
            assignments = result.scalars().all()
        except Exception:
            # If schema is missing call columns or table, skip call events (non-fatal for MVP)
            await self.db.rollback()
            return events

        for assignment in assignments:
            config = CALL_CONFIG.get(assignment.call_type, CALL_CONFIG["on-call"])

            event = Event()
            # Stable UID is important for subscribed calendars (prevents duplicates on refresh).
            event.add("uid", f"call-{assignment.id}@rotation-calendar")

            # Summary with emoji for visibility
            summary = f"{config['emoji']} {config['display']}"
            if assignment.attending_name:
                summary += f" - {assignment.attending_name}"
            elif assignment.service:
                summary += f" - {assignment.service}"
            event.add("summary", summary)

            # Calculate times
            start_dt = datetime.combine(assignment.date, config["start"])
            if config["overnight"]:
                end_dt = datetime.combine(
                    assignment.date + timedelta(days=1),
                    config["end"]
                )
            else:
                end_dt = datetime.combine(assignment.date, config["end"])

            event.add("dtstart", start_dt)
            event.add("dtend", end_dt)

            # Description
            description_parts = [
                f"Call Status: {config['display']}",
                f"Date: {assignment.date.strftime('%A, %B %d, %Y')}",
            ]
            if assignment.attending_name:
                description_parts.append(f"Attending: {assignment.attending_name}")
            if assignment.service:
                description_parts.append(f"Service: {assignment.service}")
            if assignment.location:
                description_parts.append(f"Location: {assignment.location}")

            event.add("description", "\n".join(description_parts))
            event.add("dtstamp", datetime.now())

            # Color based on call type
            color = COLORS.get(assignment.call_type, COLORS["on-call"])
            event.add("x-apple-calendar-color", color)

            # Categories
            event.add("categories", ["Call", assignment.call_type])

            # High priority for on-call
            if assignment.call_type == "on-call":
                event.add("priority", 1)

            events.append(event)

        return events

    async def _get_days_off_events(
        self,
        resident_id: int,
        start_date: Optional[date],
        end_date: Optional[date],
    ) -> List[Event]:
        """Generate events for days off."""
        events = []

        query = (
            select(DayOff, DayOffType)
            .join(DayOffType, DayOff.type_id == DayOffType.id)
            .where(DayOff.resident_id == resident_id)
        )

        if start_date:
            query = query.where(DayOff.end_date >= start_date)
        if end_date:
            query = query.where(DayOff.start_date <= end_date)

        query = query.order_by(DayOff.start_date)

        try:
            result = await self.db.execute(query)
            days_off = result.all()
        except Exception:
            await self.db.rollback()
            return events

        for day_off, day_off_type in days_off:
            event = Event()
            # Stable UID is important for subscribed calendars (prevents duplicates on refresh).
            event.add("uid", f"dayoff-{day_off.id}@rotation-calendar")

            # Summary with type
            summary = f"🏖️ {day_off_type.name}"
            event.add("summary", summary)

            # All-day event for days off
            event.add("dtstart", day_off.start_date)
            # For all-day events, end date is exclusive
            event.add("dtend", day_off.end_date + timedelta(days=1))

            # Description
            description_parts = [
                f"Day Off Type: {day_off_type.name}",
                f"Dates: {day_off.start_date.strftime('%b %d')} - {day_off.end_date.strftime('%b %d, %Y')}",
            ]
            if day_off.notes:
                description_parts.append(f"\nNotes: {day_off.notes}")

            event.add("description", "\n".join(description_parts))
            event.add("dtstamp", datetime.now())

            # Color
            color = day_off_type.color or COLORS["day-off"]
            event.add("x-apple-calendar-color", color)

            # Categories
            event.add("categories", ["Day Off", day_off_type.name])

            # Transparency - show as free
            event.add("transp", "TRANSPARENT")

            events.append(event)

        return events

    async def _get_attending_for_period(
        self,
        start_date: date,
        end_date: date,
        service: str,
    ) -> str:
        """Get attending physician info for a date range and service."""
        query = (
            select(AttendingAssignment, Attending)
            .join(Attending, AttendingAssignment.attending_id == Attending.id)
            .where(
                and_(
                    AttendingAssignment.date >= start_date,
                    AttendingAssignment.date <= end_date,
                    AttendingAssignment.service.ilike(f"%{service}%"),
                )
            )
            .order_by(AttendingAssignment.date)
            .limit(5)
        )

        result = await self.db.execute(query)
        assignments = result.all()

        if not assignments:
            return ""

        # Get unique attending names
        attending_names = list(set(a.name for _, a in assignments))
        return ", ".join(attending_names[:3])  # Limit to 3 names

    def _parse_time(self, time_str: Optional[str]) -> Optional[time]:
        """Parse a time string to a time object."""
        if not time_str:
            return None
        try:
            if isinstance(time_str, time):
                return time_str
            parts = time_str.split(":")
            return time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        except (ValueError, IndexError):
            return None


async def generate_resident_calendar(
    db: AsyncSession,
    resident_id: int,
    **kwargs,
) -> bytes:
    """
    Generate ICS file content for a resident.

    Args:
        db: Database session
        resident_id: The resident's database ID
        **kwargs: Additional options passed to CalendarService.generate_calendar

    Returns:
        ICS file content as bytes
    """
    service = CalendarService(db)
    calendar = await service.generate_calendar(resident_id, **kwargs)
    return calendar.to_ical()


async def generate_resident_calendar_by_token(
    db: AsyncSession,
    calendar_token: str,
    **kwargs,
) -> Tuple[bytes, str]:
    """
    Generate ICS file content for a resident by their calendar token.

    Args:
        db: Database session
        calendar_token: The resident's unique calendar token
        **kwargs: Additional options

    Returns:
        Tuple of (ICS content bytes, resident name)
    """
    result = await db.execute(
        select(Resident).where(Resident.calendar_token == calendar_token)
    )
    resident = result.scalar_one_or_none()

    if not resident:
        raise ValueError(f"No resident found with token: {calendar_token}")
    resident_name = resident.name
    try:
        content = await generate_resident_calendar(db, resident.id, **kwargs)
        return content, resident_name
    except Exception:
        await db.rollback()
        # Fallback: return empty calendar to avoid hard failure (legacy schemas)
        cal = Calendar()
        cal.add("prodid", "-//Residency Rotation Calendar//EN")
        cal.add("version", "2.0")
        cal.add("x-wr-calname", f"{resident_name} - Schedule")
        return cal.to_ical(), resident_name
