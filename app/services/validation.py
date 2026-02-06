"""
Schedule validation utilities for hard constraints.

Current hard rules (MVP):
- Rotation week must start on Saturday.
- Duty hours <=100 in any rolling 7-day window.
- Duty hours average <=80 per week (Sat–Fri).

Hours are derived from rotation start/end times; overnight rotations roll to next day.
Call assignments are intentionally ignored for MVP.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rotation, ScheduleAssignment, Resident


@dataclass
class Violation:
    code: str
    message: str
    severity: str  # "hard" | "soft"
    span_start: date
    span_end: date
    resident_id: Optional[int] = None

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "span": {
                "start": self.span_start.isoformat(),
                "end": self.span_end.isoformat(),
            },
            "resident_id": self.resident_id,
        }


class ValidationError(Exception):
    """Raised when validation finds at least one violation."""

    def __init__(self, violations: List[Violation], context: str):
        super().__init__("validation_failed")
        self.violations = violations
        self.context = context


def _rotation_hours_for_date(rotation: Rotation, day: date) -> float:
    """Compute hours worked on a given day for a rotation."""
    if rotation.weekdays_only and day.weekday() >= 5:
        return 0.0

    start: time = rotation.start_time or time(6, 0)
    end: time = rotation.end_time or time(19, 0)
    start_dt = datetime.combine(day, start)
    if rotation.is_overnight:
        end_dt = datetime.combine(day + timedelta(days=1), end)
    else:
        end_dt = datetime.combine(day, end)

    delta = end_dt - start_dt
    hours = delta.total_seconds() / 3600.0
    return max(hours, 0.0)


def validate_schedule(
    assignments: Iterable[ScheduleAssignment],
    rotations: Dict[int, Rotation],
) -> List[Violation]:
    """Pure validation against a set of assignments and rotation metadata."""
    violations: List[Violation] = []

    # Build per-resident daily hours
    resident_daily_hours: Dict[int, Dict[date, float]] = defaultdict(lambda: defaultdict(float))

    for assignment in assignments:
        rotation = rotations.get(assignment.rotation_id)
        if not rotation:
            continue

        # Block change day rule
        if assignment.week_start.weekday() != 5:  # 5 = Saturday
            violations.append(
                Violation(
                    code="block_change_day",
                    message="Rotation week must start on Saturday",
                    severity="hard",
                    span_start=assignment.week_start,
                    span_end=assignment.week_end,
                    resident_id=assignment.resident_id,
                )
            )

        current = assignment.week_start
        while current <= assignment.week_end:
            hours = _rotation_hours_for_date(rotation, current)
            if hours > 0:
                resident_daily_hours[assignment.resident_id][current] += hours
            current += timedelta(days=1)

    # Duty hour rules per resident
    for resident_id, daily in resident_daily_hours.items():
        days_sorted = sorted(daily.items(), key=lambda x: x[0])

        # Rolling 7-day window <= 100h
        window = deque()
        rolling_total = 0.0
        for day, hours in days_sorted:
            window.append((day, hours))
            rolling_total += hours

            while window and (day - window[0][0]).days > 6:
                oldest_day, oldest_hours = window.popleft()
                rolling_total -= oldest_hours

            if rolling_total > 100.0:
                violations.append(
                    Violation(
                        code="duty_hours_7d",
                        message=f"Duty hours exceed 100h in 7-day window ({rolling_total:.1f}h)",
                        severity="hard",
                        span_start=window[0][0],
                        span_end=day,
                        resident_id=resident_id,
                    )
                )

        # Weekly total <= 80h (Sat–Fri weeks)
        week_buckets: Dict[date, float] = defaultdict(float)
        for day, hours in days_sorted:
            # find Saturday of the week for bucket key
            saturday = day - timedelta(days=(day.weekday() - 5) % 7)
            week_buckets[saturday] += hours

        for week_start, total in week_buckets.items():
            if total > 80.0:
                week_end = week_start + timedelta(days=6)
                violations.append(
                    Violation(
                        code="duty_hours_avg_week",
                        message=f"Weekly duty hours exceed 80h ({total:.1f}h)",
                        severity="hard",
                        span_start=week_start,
                        span_end=week_end,
                        resident_id=resident_id,
                    )
                )

    return violations


async def validate_residents_schedule(
    db: AsyncSession,
    resident_ids: Iterable[int],
    context: str,
) -> None:
    """
    Load schedule/rotation data for the given residents and raise ValidationError on violations.
    """
    resident_ids = list(resident_ids)
    if not resident_ids:
        return

    result = await db.execute(
        select(ScheduleAssignment, Rotation)
        .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
        .where(ScheduleAssignment.resident_id.in_(resident_ids))
    )
    rows = result.all()
    assignments = [row[0] for row in rows]
    rotations = {row[1].id: row[1] for row in rows}

    violations = validate_schedule(assignments, rotations)
    if violations:
        raise ValidationError(violations, context)


def as_validation_response(err: ValidationError) -> dict:
    """Convert a ValidationError into a consistent response payload."""
    return {
        "status": "validation_failed",
        "context": err.context,
        "violations": [v.as_dict() for v in err.violations],
    }
