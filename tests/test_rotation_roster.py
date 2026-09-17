from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select


_db_initialized = False


async def _ensure_db_initialized():
    global _db_initialized
    if _db_initialized:
        return
    from app.database import init_db
    await init_db()
    _db_initialized = True


async def _get_or_create(session, model, where, factory):
    result = await session.execute(select(model).where(*where))
    existing = result.scalar_one_or_none()
    if existing:
        return existing
    created = factory()
    session.add(created)
    await session.flush()
    return created


WEEK_START = date(2031, 3, 1)
WEEK_END = WEEK_START + timedelta(days=6)
TARGET_DATE = WEEK_START + timedelta(days=2)
ROTATION_NAME = "TEST_ROSTER_ICU"
OFF_ROTATION_NAME = "TEST_ROSTER_VAC"


async def _seed_roster():
    from app.database import async_session_maker
    from app.models import (
        DayOff,
        DayOffType,
        PGYLevel,
        Resident,
        Rotation,
        ScheduleAssignment,
    )

    async with async_session_maker() as session:
        rotation = await _get_or_create(
            session,
            Rotation,
            [func.lower(Rotation.name) == ROTATION_NAME.lower()],
            lambda: Rotation(name=ROTATION_NAME, display_name="Roster ICU", color="#06b6d4"),
        )
        vacation_rotation = await _get_or_create(
            session,
            Rotation,
            [func.lower(Rotation.name) == OFF_ROTATION_NAME.lower()],
            lambda: Rotation(
                name=OFF_ROTATION_NAME,
                display_name="Roster Vacation",
                generates_events=False,
            ),
        )

        on_service = await _get_or_create(
            session,
            Resident,
            [func.lower(Resident.email) == "roster_on_service@ttuhsc.edu"],
            lambda: Resident(
                name="Roster OnService",
                email="roster_on_service@ttuhsc.edu",
                pgy_level=PGYLevel.PGY2,
            ),
        )
        on_vacation = await _get_or_create(
            session,
            Resident,
            [func.lower(Resident.email) == "roster_on_vacation@ttuhsc.edu"],
            lambda: Resident(
                name="Roster OnVacation",
                email="roster_on_vacation@ttuhsc.edu",
                pgy_level=PGYLevel.PGY1,
            ),
        )

        for resident, rotation_row in ((on_service, rotation), (on_vacation, vacation_rotation)):
            await _get_or_create(
                session,
                ScheduleAssignment,
                [
                    ScheduleAssignment.resident_id == resident.id,
                    ScheduleAssignment.week_start == WEEK_START,
                ],
                lambda resident=resident, rotation_row=rotation_row: ScheduleAssignment(
                    resident_id=resident.id,
                    rotation_id=rotation_row.id,
                    week_start=WEEK_START,
                    week_end=WEEK_END,
                ),
            )

        vacation_type = await _get_or_create(
            session,
            DayOffType,
            [func.lower(DayOffType.name) == "vacation"],
            lambda: DayOffType(name="Vacation", color="#f59e0b"),
        )
        await _get_or_create(
            session,
            DayOff,
            [
                DayOff.resident_id == on_vacation.id,
                DayOff.start_date == WEEK_START,
            ],
            lambda: DayOff(
                resident_id=on_vacation.id,
                type_id=vacation_type.id,
                start_date=WEEK_START,
                end_date=WEEK_END,
            ),
        )

        await session.commit()
        return {"vacation_type_name": vacation_type.name}


def _find_rotation(payload, name):
    for group in payload["rotations"]:
        if group["name"] == name:
            return group
    return None


@pytest.mark.asyncio(loop_scope="session")
async def test_roster_groups_residents_by_rotation():
    from app.main import app

    await _ensure_db_initialized()
    seeded = await _seed_roster()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/rotations/roster?target_date={TARGET_DATE.isoformat()}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["date"] == TARGET_DATE.isoformat()
    assert payload["week_start"] == WEEK_START.isoformat()
    assert payload["week_end"] == WEEK_END.isoformat()
    assert payload["total_residents"] >= 2

    on_service_group = _find_rotation(payload, ROTATION_NAME)
    assert on_service_group is not None
    assert on_service_group["display_name"] == "Roster ICU"
    assert on_service_group["resident_count"] == len(on_service_group["residents"])

    entry = next(r for r in on_service_group["residents"] if r["name"] == "Roster OnService")
    assert entry["pgy_level"] == "PGY2"
    assert entry["day_off"] is None

    vacation_group = _find_rotation(payload, OFF_ROTATION_NAME)
    assert vacation_group is not None
    away = next(r for r in vacation_group["residents"] if r["name"] == "Roster OnVacation")
    assert away["day_off"] == seeded["vacation_type_name"]

    # Non-clinical buckets sort after clinical rotations
    names = [group["name"] for group in payload["rotations"]]
    assert names.index(ROTATION_NAME) < names.index(OFF_ROTATION_NAME)


@pytest.mark.asyncio(loop_scope="session")
async def test_roster_filters_by_rotation_name():
    from app.main import app

    await _ensure_db_initialized()
    await _seed_roster()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/api/rotations/roster",
            params={"target_date": TARGET_DATE.isoformat(), "rotation": ROTATION_NAME.lower()},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rotation_filter"] == ROTATION_NAME.lower()
    assert [group["name"] for group in payload["rotations"]] == [ROTATION_NAME]


@pytest.mark.asyncio(loop_scope="session")
async def test_roster_empty_for_date_without_assignments():
    from app.main import app

    await _ensure_db_initialized()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/rotations/roster?target_date=2035-06-10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["rotations"] == []
    assert payload["total_residents"] == 0
    assert payload["week_start"] is None


@pytest.mark.asyncio(loop_scope="session")
async def test_list_rotations_endpoint():
    from app.main import app

    await _ensure_db_initialized()
    await _seed_roster()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/rotations")

    assert response.status_code == 200
    rotations = response.json()["rotations"]
    names = [rotation["name"] for rotation in rotations]
    assert ROTATION_NAME in names
    match = next(rotation for rotation in rotations if rotation["name"] == ROTATION_NAME)
    assert match["display_name"] == "Roster ICU"
    assert match["color"] == "#06b6d4"
