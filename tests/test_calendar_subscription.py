import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func


@pytest.mark.asyncio(loop_scope="session")
async def test_calendar_by_email_returns_ics():
    from app.database import async_session_maker, init_db
    from app.main import app
    from app.models import Resident, PGYLevel

    await init_db()

    email = "mbooreni@ttuhsc.edu"
    async with async_session_maker() as session:
        result = await session.execute(
            select(Resident).where(func.lower(Resident.email) == email)
        )
        resident = result.scalar_one_or_none()
        if not resident:
            resident = Resident(name="Boorenie, Mustafa", email=email, pgy_level=PGYLevel.PGY1)
            session.add(resident)
            await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"/api/calendar/by-email.ics?email={email}")
        assert resp.status_code == 200
        assert "text/calendar" in resp.headers.get("content-type", "")
        assert resp.text.startswith("BEGIN:VCALENDAR")


@pytest.mark.asyncio(loop_scope="session")
async def test_calendar_by_name_legacy_fallback_returns_ics():
    from app.database import init_db
    from app.main import app

    await init_db()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/calendar/M.%20Boorenie.ics")
        assert resp.status_code == 200
        assert "text/calendar" in resp.headers.get("content-type", "")
        assert resp.text.startswith("BEGIN:VCALENDAR")

        head = await client.head("/api/calendar/M.%20Boorenie.ics")
        assert head.status_code == 200
        assert "text/calendar" in head.headers.get("content-type", "")
