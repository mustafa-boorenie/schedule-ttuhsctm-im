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


@pytest.mark.asyncio(loop_scope="session")
async def test_resident_schedule_includes_rotation_fields():
    from app.database import async_session_maker
    from app.main import app
    from app.models import PGYLevel, Resident, Rotation, ScheduleAssignment

    await _ensure_db_initialized()

    resident_email = "schedule_api_test@ttuhsc.edu"
    rotation_name = "TEST_ROTATION_API"
    week_start = date(2030, 1, 1)
    week_end = week_start + timedelta(days=6)

    async with async_session_maker() as session:
        rot_result = await session.execute(
            select(Rotation).where(func.lower(Rotation.name) == rotation_name.lower())
        )
        rotation = rot_result.scalar_one_or_none()
        if not rotation:
            rotation = Rotation(name=rotation_name, display_name="Test Rotation", color="#06b6d4")
            session.add(rotation)
            await session.flush()

        resident_result = await session.execute(
            select(Resident).where(func.lower(Resident.email) == resident_email)
        )
        resident = resident_result.scalar_one_or_none()
        if not resident:
            resident = Resident(name="Schedule API Test", email=resident_email, pgy_level=PGYLevel.PGY1)
            session.add(resident)
            await session.flush()

        assignment_result = await session.execute(
            select(ScheduleAssignment).where(
                ScheduleAssignment.resident_id == resident.id,
                ScheduleAssignment.week_start == week_start,
            )
        )
        assignment = assignment_result.scalar_one_or_none()
        if not assignment:
            session.add(
                ScheduleAssignment(
                    resident_id=resident.id,
                    rotation_id=rotation.id,
                    week_start=week_start,
                    week_end=week_end,
                )
            )
        await session.commit()
        resident_id = resident.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/residents/{resident_id}/schedule")
        assert response.status_code == 200
        payload = response.json()
        assert "assignments" in payload
        assert len(payload["assignments"]) > 0
        first = payload["assignments"][0]
        assert "rotation_name" in first
        assert "rotation_display_name" in first
        assert "rotation_color" in first
