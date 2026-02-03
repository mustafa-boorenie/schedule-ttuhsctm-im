"""
Program rules service for seeding and retrieval.
"""
from __future__ import annotations

from datetime import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AcademicYear, ProgramRules, NightToDayGapType


# Baseline ACGME-like defaults (minimal)
_ACGME_DEFAULTS = {
    "duty_hours_max_7d": 80,
    "duty_hours_avg_week": 80,
    "min_days_off_per_week": 1.0,
    "night_to_day_gap_value": 1,
    "night_to_day_gap_type": NightToDayGapType.CALENDAR_DAY,
    "block_length_days": 7,
    "rotation_change_day": 5,  # Saturday
    "clinic_interval_weeks": 5,
    "clinic_start_time": time(8, 0),
    "clinic_end_time": time(17, 0),
    "clinic_weekdays_only": True,
    "jeopardy_primary_slots_per_pgy": 1,
    "jeopardy_backup_slots_per_pgy": 1,
    "jeopardy_requires_elective": True,
    "floor_min_hours_per_year": None,
    "floor_min_weeks_per_year": None,
}


# Local overrides agreed with program leadership
_LOCAL_OVERRIDES = {
    "duty_hours_max_7d": 100,
    "duty_hours_avg_week": 80,
    "min_days_off_per_week": 1.0,
    "night_to_day_gap_value": 1,
    "night_to_day_gap_type": NightToDayGapType.CALENDAR_DAY,
    "block_length_days": 7,
    "rotation_change_day": 5,
    "clinic_interval_weeks": 5,
    "clinic_start_time": time(8, 0),
    "clinic_end_time": time(17, 0),
    "clinic_weekdays_only": True,
    "jeopardy_primary_slots_per_pgy": 1,
    "jeopardy_backup_slots_per_pgy": 1,
    "jeopardy_requires_elective": True,
}


def _build_default_rules() -> dict:
    defaults = dict(_ACGME_DEFAULTS)
    defaults.update(_LOCAL_OVERRIDES)
    return defaults


async def get_or_create_rules(
    db: AsyncSession,
    academic_year_id: int,
) -> ProgramRules:
    result = await db.execute(
        select(ProgramRules).where(ProgramRules.academic_year_id == academic_year_id)
    )
    rules = result.scalar_one_or_none()
    if rules:
        return rules

    defaults = _build_default_rules()
    rules = ProgramRules(academic_year_id=academic_year_id, **defaults)
    db.add(rules)
    await db.flush()
    return rules


async def ensure_rules_for_current_year(db: AsyncSession) -> Optional[ProgramRules]:
    result = await db.execute(
        select(AcademicYear).where(AcademicYear.is_current == True)
    )
    academic_year = result.scalar_one_or_none()
    if not academic_year:
        return None

    return await get_or_create_rules(db, academic_year.id)
