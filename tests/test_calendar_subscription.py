import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func


_db_initialized = False


async def _ensure_db_initialized():
    global _db_initialized
    if _db_initialized:
        return
    from app.database import init_db
    await init_db()
    _db_initialized = True


@pytest.mark.asyncio(loop_scope="session")
async def test_calendar_subscription_endpoints_return_ics():
    from app.database import async_session_maker
    from app.main import app
    from app.models import Resident, PGYLevel

    await _ensure_db_initialized()

    email = "mbooreni@ttuhsc.edu"
    async with async_session_maker() as session:
        result = await session.execute(
            select(Resident).where(func.lower(Resident.email) == email)
        )
        resident = result.scalar_one_or_none()
        if not resident:
            resident = Resident(name="M. Boorenie", email=email, pgy_level=PGYLevel.PGY1)
            session.add(resident)
            await session.commit()
        resident_token = resident.calendar_token

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        by_email = await client.get(f"/api/calendar/by-email.ics?email={email}")
        assert by_email.status_code == 200
        assert "text/calendar" in by_email.headers.get("content-type", "")
        assert by_email.text.startswith("BEGIN:VCALENDAR")

        by_token = await client.get(f"/api/calendar/{resident_token}.ics")
        assert by_token.status_code == 200
        assert "text/calendar" in by_token.headers.get("content-type", "")
        assert by_token.text.startswith("BEGIN:VCALENDAR")

        head = await client.head(f"/api/calendar/{resident_token}.ics")
        assert head.status_code == 200
        assert "text/calendar" in head.headers.get("content-type", "")

        missing = await client.get("/api/calendar/Unknown%20Resident.ics")
        assert missing.status_code == 404
