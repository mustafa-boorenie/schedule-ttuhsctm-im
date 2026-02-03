"""Resident lookup utilities for email-based access."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Resident


def normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.strip().lower())


def extract_email_local(email: str) -> str:
    return normalize_identifier(email.split("@", 1)[0])


def find_best_match(
    target: str,
    candidates: Iterable[str],
    *,
    min_ratio: float = 0.75,
    min_delta: float = 0.05,
) -> Optional[str]:
    target_norm = normalize_identifier(target)
    best = None
    best_ratio = 0.0
    second_best = 0.0

    for candidate in candidates:
        candidate_norm = normalize_identifier(candidate)
        if not candidate_norm:
            continue
        ratio = SequenceMatcher(None, target_norm, candidate_norm).ratio()
        if ratio > best_ratio:
            second_best = best_ratio
            best_ratio = ratio
            best = candidate
        elif ratio > second_best:
            second_best = ratio

    if best is None or best_ratio < min_ratio:
        return None
    if second_best > 0 and (best_ratio - second_best) < min_delta:
        return None
    return best


async def get_resident_by_email(
    db: AsyncSession,
    email: str,
    *,
    min_ratio: float = 0.75,
    min_delta: float = 0.05,
) -> Resident:
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        raise ValueError("Invalid email")

    result = await db.execute(
        select(Resident)
        .where(
            Resident.is_active == True,
            Resident.email.is_not(None),
            func.lower(Resident.email) == email_clean,
        )
    )
    resident = result.scalar_one_or_none()
    if resident:
        return resident

    result = await db.execute(
        select(Resident).where(Resident.is_active == True)
    )
    residents = result.scalars().all()
    if not residents:
        raise ValueError("No active residents found")

    target = extract_email_local(email_clean)
    matched_name = find_best_match(
        target,
        [r.name for r in residents],
        min_ratio=min_ratio,
        min_delta=min_delta,
    )
    if not matched_name:
        raise ValueError("No resident matched this email")

    for candidate in residents:
        if candidate.name == matched_name:
            return candidate

    raise ValueError("Matched resident not found")
