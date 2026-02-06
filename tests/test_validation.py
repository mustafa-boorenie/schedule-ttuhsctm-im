import datetime

import pytest

from app.models import Rotation, ScheduleAssignment
from app.services.validation import validate_schedule


def make_rotation(**kwargs):
    defaults = {
        "name": "ICU",
        "start_time": datetime.time(6, 0),
        "end_time": datetime.time(18, 0),
        "is_overnight": False,
        "weekdays_only": False,
    }
    defaults.update(kwargs)
    return Rotation(**defaults)


def make_assignment(resident_id: int, rotation_id: int, start: datetime.date):
    return ScheduleAssignment(
        resident_id=resident_id,
        rotation_id=rotation_id,
        week_start=start,
        week_end=start + datetime.timedelta(days=6),
    )


def test_block_change_day_violation():
    rotation = make_rotation()
    monday = datetime.date(2026, 2, 2)  # Monday
    assignment = make_assignment(1, 1, monday)

    violations = validate_schedule([assignment], {1: rotation})
    assert any(v.code == "block_change_day" for v in violations)


def test_duty_hours_7d_violation():
    # 18h per day for 7 days = 126h
    rotation = make_rotation(start_time=datetime.time(6, 0), end_time=datetime.time(0, 0), is_overnight=True)
    saturday = datetime.date(2026, 1, 3)  # Saturday
    assignment = make_assignment(1, 1, saturday)

    violations = validate_schedule([assignment], {1: rotation})
    assert any(v.code == "duty_hours_7d" for v in violations)


def test_week_total_violation():
    rotation = make_rotation(start_time=datetime.time(6, 0), end_time=datetime.time(22, 0))  # 16h/day
    saturday = datetime.date(2026, 1, 3)
    assignment = make_assignment(1, 1, saturday)

    violations = validate_schedule([assignment], {1: rotation})
    assert any(v.code == "duty_hours_avg_week" for v in violations)
