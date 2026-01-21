"""Tests for health check endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport

# Note: These tests require a running database
# For CI/CD, use docker-compose to spin up test environment


@pytest.mark.asyncio
async def test_liveness_check():
    """Test that liveness endpoint returns alive status."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "alive"


@pytest.mark.asyncio
async def test_root_page():
    """Test that root page returns HTML."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_resident_portal():
    """Test that resident portal returns HTML."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/resident")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_admin_login_page():
    """Test that admin login page returns HTML."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/admin/login")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
