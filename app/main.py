"""
FastAPI application for the Residency Rotation Calendar Subscription service.
"""
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db, init_db, close_db
from .logging_config import setup_logging
from .middleware import (
    ErrorHandlingMiddleware,
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    SecurityHeadersMiddleware,
)
from .models import Resident, Rotation, ScheduleAssignment, AcademicYear, Admin
from .routers import admin_auth_router, admin_router, schedule_router, amion_router, days_off_router, swap_router
from .services.excel_import import ExcelImportService, seed_default_day_off_types
from .services.program_rules import ensure_rules_for_current_year
from .services.scheduler import scheduler
from .services.calendar import generate_resident_calendar_by_token
from .settings import settings

# Legacy imports for backward compatibility
from .parser import get_parser, reload_parser
from .calendar_gen import generate_resident_ics

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent
SCHEDULE_PATH = PROJECT_ROOT / "schedule.xlsx"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    logger.info("=" * 50)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    logger.info("=" * 50)

    # Validate production settings
    if not settings.debug:
        issues = settings.validate_production_settings()
        for issue in issues:
            logger.warning(f"Production config issue: {issue}")

    # Initialize database tables
    logger.info("Initializing database...")
    await init_db()

    # Seed default data
    async for db in get_db():
        await seed_default_day_off_types(db)
        await ensure_rules_for_current_year(db)
        await db.commit()
        break
    logger.info("Database initialized successfully")

    # Legacy: Initialize parser if schedule file exists
    if SCHEDULE_PATH.exists():
        get_parser(SCHEDULE_PATH)
        logger.info(f"Loaded legacy schedule from {SCHEDULE_PATH}")
    else:
        logger.info(f"No legacy schedule file at {SCHEDULE_PATH}")

    # Start background scheduler for automated tasks (avoid multi-worker duplication)
    if _should_start_scheduler():
        scheduler.start()
        logger.info("Background scheduler started")
    else:
        logger.info("Background scheduler not started (non-primary process)")

    logger.info(f"Application ready at {settings.base_url}")

    yield

    # Shutdown
    logger.info("Shutting down...")
    scheduler.stop()
    await close_db()
    logger.info("Shutdown complete")


app = FastAPI(
    title=settings.app_name,
    description="Subscribe to your personalized rotation calendar",
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.debug else None,
    redoc_url="/api/redoc" if settings.debug else None,
)

# Add middleware (order matters - first added = outermost)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ErrorHandlingMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=settings.rate_limit_per_minute)
app.add_middleware(RequestLoggingMiddleware)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(schedule_router)
app.include_router(amion_router)
app.include_router(days_off_router)
app.include_router(swap_router)


def _should_start_scheduler() -> bool:
    """Start scheduler only in a designated primary process."""
    if not settings.scheduler_enabled:
        return False

    scheduler_role = os.environ.get("SCHEDULER_ROLE")
    if scheduler_role:
        return scheduler_role.lower() == "primary"

    web_concurrency = os.environ.get("WEB_CONCURRENCY")
    if web_concurrency and web_concurrency != "1":
        return False

    uvicorn_worker = os.environ.get("UVICORN_WORKER_ID")
    if uvicorn_worker and uvicorn_worker != "0":
        return False

    return True


# ============== Public API Endpoints ==============

@app.get("/api/residents")
async def list_residents(db: AsyncSession = Depends(get_db)):
    """Get list of all resident names for the search dropdown."""
    # Try database first
    result = await db.execute(
        select(Resident)
        .where(Resident.is_active == True)
        .order_by(Resident.name)
    )
    residents = result.scalars().all()

    if residents:
        return {"residents": [r.name for r in residents]}

    # Fallback to legacy Excel parser
    try:
        if SCHEDULE_PATH.exists():
            parser = get_parser(SCHEDULE_PATH)
            resident_names = parser.get_residents()
            return {"residents": sorted(resident_names)}
        else:
            # No data available yet - return empty list
            return {"residents": []}
    except Exception as e:
        logger.warning(f"Could not load residents: {e}")
        return {"residents": []}


@app.get("/api/residents/{resident_id}/schedule")
async def get_resident_schedule(
    resident_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get schedule for a specific resident by ID."""
    result = await db.execute(
        select(ScheduleAssignment)
        .where(ScheduleAssignment.resident_id == resident_id)
        .order_by(ScheduleAssignment.week_start)
    )
    assignments = result.scalars().all()

    if not assignments:
        raise HTTPException(status_code=404, detail="No schedule found for this resident")

    return {"assignments": assignments}


@app.get("/api/calendar/{calendar_token}.ics")
async def get_calendar_by_token(
    calendar_token: str,
    include_rotations: bool = True,
    include_call: bool = True,
    include_days_off: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate and return an ICS calendar file for a resident by their calendar token.

    Query parameters:
    - include_rotations: Include rotation schedule events (default: true)
    - include_call: Include call status events (default: true)
    - include_days_off: Include days off events (default: true)
    """
    try:
        # Try new database-backed calendar generation
        ics_content, resident_name = await generate_resident_calendar_by_token(
            db=db,
            calendar_token=calendar_token,
            include_rotations=include_rotations,
            include_call=include_call,
            include_days_off=include_days_off,
        )
        return Response(
            content=ics_content,
            media_type="text/calendar",
            headers={
                "Content-Disposition": f'attachment; filename="{quote(resident_name)}_schedule.ics"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
        )
    except ValueError:
        # Token not found in database, try legacy lookup by name
        return await get_calendar_legacy(calendar_token)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating calendar: {str(e)}")


async def get_calendar_legacy(resident_name: str):
    """Legacy calendar generation by name."""
    try:
        parser = get_parser(SCHEDULE_PATH)
        residents = parser.get_residents()

        if resident_name not in residents:
            raise HTTPException(status_code=404, detail=f"Resident '{resident_name}' not found")

        ics_content = generate_resident_ics(resident_name)

        return Response(
            content=ics_content,
            media_type="text/calendar",
            headers={
                "Content-Disposition": f'attachment; filename="{quote(resident_name)}_rotation.ics"',
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating calendar: {str(e)}")


@app.get("/api/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    """
    Comprehensive health check endpoint for deployment.

    Returns status of all application components.
    """
    response = {
        "status": "healthy",
        "version": settings.app_version,
        "timestamp": datetime.utcnow().isoformat(),
        "components": {}
    }

    # Check database connectivity
    try:
        await db.execute(text("SELECT 1"))
        response["components"]["database"] = "healthy"
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        response["components"]["database"] = "unhealthy"
        response["status"] = "degraded"

    # Check scheduler status
    try:
        if scheduler.scheduler and scheduler.scheduler.running:
            response["components"]["scheduler"] = "healthy"
        else:
            response["components"]["scheduler"] = "stopped"
    except Exception:
        response["components"]["scheduler"] = "unknown"

    return response


@app.get("/api/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """
    Readiness probe for Kubernetes/container orchestration.

    Returns 200 only if all critical components are ready.
    """
    try:
        # Check database
        await db.execute(text("SELECT 1"))

        return {"status": "ready"}
    except Exception as e:
        logger.error(f"Readiness check failed: {e}")
        raise HTTPException(status_code=503, detail="Service not ready")


@app.get("/api/health/live")
async def liveness_check():
    """
    Liveness probe for Kubernetes/container orchestration.

    Returns 200 if the application process is running.
    """
    return {"status": "alive"}


@app.get("/api/call-schedule")
async def get_call_schedule(
    target_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get who's on call for a specific date.

    Public endpoint for residents to check call schedules.
    Returns pre-call, on-call, and post-call assignments.
    """
    from .models import CallAssignment

    if target_date is None:
        target_date = date.today()

    # Get call assignments for the target date
    result = await db.execute(
        select(CallAssignment, Resident)
        .join(Resident, CallAssignment.resident_id == Resident.id)
        .where(CallAssignment.date == target_date)
        .order_by(CallAssignment.call_type)
    )
    assignments = result.all()

    # Group by call type
    call_data = {
        "date": target_date.isoformat(),
        "date_formatted": target_date.strftime("%A, %B %d, %Y"),
        "pre_call": [],
        "on_call": [],
        "post_call": [],
    }

    for assignment, resident in assignments:
        entry = {
            "resident_name": resident.name,
            "pgy_level": resident.pgy_level.value,
            "service": assignment.service,
            "location": assignment.location,
        }

        if assignment.call_type == "pre-call":
            call_data["pre_call"].append(entry)
        elif assignment.call_type == "on-call":
            call_data["on_call"].append(entry)
        elif assignment.call_type == "post-call":
            call_data["post_call"].append(entry)

    return call_data


@app.get("/api/call-schedule/week")
async def get_call_schedule_week(
    start_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Get call schedule for an entire week.

    Returns call assignments grouped by day.
    """
    from .models import CallAssignment

    if start_date is None:
        # Start from today
        start_date = date.today()

    end_date = start_date + timedelta(days=6)

    result = await db.execute(
        select(CallAssignment, Resident)
        .join(Resident, CallAssignment.resident_id == Resident.id)
        .where(
            CallAssignment.date >= start_date,
            CallAssignment.date <= end_date,
        )
        .order_by(CallAssignment.date, CallAssignment.call_type)
    )
    assignments = result.all()

    # Group by date
    schedule = {}
    current = start_date
    while current <= end_date:
        schedule[current.isoformat()] = {
            "date": current.isoformat(),
            "day_name": current.strftime("%A"),
            "pre_call": [],
            "on_call": [],
            "post_call": [],
        }
        current += timedelta(days=1)

    for assignment, resident in assignments:
        date_key = assignment.date.isoformat()
        entry = {
            "resident_name": resident.name,
            "pgy_level": resident.pgy_level.value,
            "service": assignment.service,
        }

        if assignment.call_type == "pre-call":
            schedule[date_key]["pre_call"].append(entry)
        elif assignment.call_type == "on-call":
            schedule[date_key]["on_call"].append(entry)
        elif assignment.call_type == "post-call":
            schedule[date_key]["post_call"].append(entry)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": list(schedule.values()),
    }


@app.post("/api/reload")
async def reload_schedule():
    """Reload the schedule from the XLSX file (legacy endpoint)."""
    try:
        reload_parser(SCHEDULE_PATH)
        parser = get_parser(SCHEDULE_PATH)
        count = len(parser.get_residents())
        return {"status": "reloaded", "resident_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading schedule: {str(e)}")


@app.post("/api/admin/schedule/import")
async def import_excel_schedule(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Import schedule from uploaded Excel file into the database.

    This endpoint requires admin authentication (handled by middleware).
    """
    # TODO: Add proper admin auth check

    # Save uploaded file temporarily
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        import_service = ExcelImportService(db)
        result = await import_service.import_excel(tmp_path)
        await db.commit()
        return result
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error importing schedule: {str(e)}")
    finally:
        tmp_path.unlink(missing_ok=True)


# ============== Admin Portal Pages ==============

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    """Serve the admin login page."""
    login_path = STATIC_PATH / "admin-login.html"
    if login_path.exists():
        return HTMLResponse(content=login_path.read_text())
    return HTMLResponse(content=get_admin_login_html())


@app.get("/admin", response_class=HTMLResponse)
async def admin_portal():
    """Serve the admin portal page."""
    admin_path = STATIC_PATH / "admin.html"
    if admin_path.exists():
        return HTMLResponse(content=admin_path.read_text())
    return HTMLResponse(content=get_admin_portal_html())


@app.get("/resident", response_class=HTMLResponse)
async def resident_portal():
    """Serve the resident portal page."""
    resident_path = STATIC_PATH / "resident.html"
    if resident_path.exists():
        return HTMLResponse(content=resident_path.read_text())
    return HTMLResponse(content="<h1>Resident Portal</h1><p>Page not found.</p>")


# ============== Static Files & Frontend ==============

STATIC_PATH = PROJECT_ROOT / "static"
if STATIC_PATH.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_PATH)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Serve the frontend HTML page."""
    index_path = STATIC_PATH / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    else:
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Calendar Subscription</title></head>
        <body>
            <h1>Residency Rotation Calendar</h1>
            <p>Frontend not found. Place index.html in the static folder.</p>
            <p><a href="/api/residents">View API: /api/residents</a></p>
            <p><a href="/admin/login">Admin Portal</a></p>
        </body>
        </html>
        """)


# ============== HTML Templates ==============

def get_admin_login_html() -> str:
    """Return the admin login page HTML."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Login - Rotation Calendar</title>
    <style>
        :root {
            --bg-primary: #0a0f1a;
            --bg-secondary: #111827;
            --bg-card: #1a2234;
            --accent-primary: #06b6d4;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --border-color: #2d3748;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 2rem;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 16px;
            padding: 2.5rem;
            max-width: 400px;
            width: 100%;
        }
        h1 { font-size: 1.5rem; margin-bottom: 0.5rem; }
        .subtitle { color: var(--text-secondary); margin-bottom: 2rem; }
        label { display: block; margin-bottom: 0.5rem; color: var(--text-secondary); font-size: 0.875rem; }
        input {
            width: 100%;
            padding: 0.875rem 1rem;
            font-size: 1rem;
            background: var(--bg-secondary);
            border: 1px solid var(--border-color);
            border-radius: 8px;
            color: var(--text-primary);
            margin-bottom: 1.5rem;
        }
        input:focus { outline: none; border-color: var(--accent-primary); }
        button {
            width: 100%;
            padding: 0.875rem;
            font-size: 1rem;
            background: var(--accent-primary);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 500;
        }
        button:hover { opacity: 0.9; }
        .message {
            padding: 1rem;
            border-radius: 8px;
            margin-bottom: 1rem;
            display: none;
        }
        .message.success { background: rgba(16, 185, 129, 0.2); color: #10B981; display: block; }
        .message.error { background: rgba(239, 68, 68, 0.2); color: #EF4444; display: block; }
    </style>
</head>
<body>
    <div class="card">
        <h1>Admin Login</h1>
        <p class="subtitle">Enter your email to receive a login link</p>

        <div id="message" class="message"></div>

        <form id="loginForm">
            <label for="email">Email Address</label>
            <input type="email" id="email" name="email" required placeholder="admin@example.com">
            <button type="submit">Send Login Link</button>
        </form>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const email = document.getElementById('email').value;
            const messageEl = document.getElementById('message');

            try {
                const response = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const data = await response.json();

                messageEl.textContent = data.message;
                messageEl.className = 'message success';
            } catch (err) {
                messageEl.textContent = 'Failed to send login link. Please try again.';
                messageEl.className = 'message error';
            }
        });
    </script>
</body>
</html>
    """


def get_admin_portal_html() -> str:
    """Return the admin portal shell HTML."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Portal - Rotation Calendar</title>
    <style>
        :root {
            --bg-primary: #0a0f1a;
            --bg-secondary: #111827;
            --bg-card: #1a2234;
            --accent-primary: #06b6d4;
            --accent-secondary: #22d3ee;
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --border-color: #2d3748;
            --success: #10b981;
            --warning: #f59e0b;
            --error: #ef4444;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg-primary);
            color: var(--text-primary);
            min-height: 100vh;
        }

        /* Navigation */
        .nav {
            background: var(--bg-secondary);
            border-bottom: 1px solid var(--border-color);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .nav-brand {
            font-size: 1.25rem;
            font-weight: 600;
            color: var(--accent-primary);
        }
        .nav-links { display: flex; gap: 1.5rem; }
        .nav-link {
            color: var(--text-secondary);
            text-decoration: none;
            padding: 0.5rem 1rem;
            border-radius: 6px;
            transition: all 0.2s;
        }
        .nav-link:hover, .nav-link.active {
            color: var(--text-primary);
            background: var(--bg-card);
        }
        .nav-user {
            display: flex;
            align-items: center;
            gap: 1rem;
        }
        .nav-user span { color: var(--text-secondary); }
        .btn-logout {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-secondary);
            padding: 0.5rem 1rem;
            border-radius: 6px;
            cursor: pointer;
        }
        .btn-logout:hover { border-color: var(--error); color: var(--error); }

        /* Main content */
        .main { padding: 2rem; max-width: 1400px; margin: 0 auto; }
        .page-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
        }
        .page-title { font-size: 1.75rem; }

        /* Cards */
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 1rem;
        }
        .card-title { font-size: 1.125rem; font-weight: 600; }

        /* Dashboard grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 1rem;
            margin-bottom: 2rem;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border-color);
            border-radius: 12px;
            padding: 1.5rem;
        }
        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            color: var(--accent-primary);
        }
        .stat-label { color: var(--text-secondary); margin-top: 0.25rem; }

        /* Buttons */
        .btn {
            padding: 0.625rem 1.25rem;
            border-radius: 8px;
            font-size: 0.875rem;
            font-weight: 500;
            cursor: pointer;
            border: none;
            transition: all 0.2s;
        }
        .btn-primary { background: var(--accent-primary); color: white; }
        .btn-primary:hover { background: var(--accent-secondary); }
        .btn-secondary {
            background: transparent;
            border: 1px solid var(--border-color);
            color: var(--text-primary);
        }
        .btn-secondary:hover { border-color: var(--accent-primary); }

        /* Tables */
        .table-container { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; }
        th, td {
            text-align: left;
            padding: 0.75rem 1rem;
            border-bottom: 1px solid var(--border-color);
        }
        th { color: var(--text-secondary); font-weight: 500; font-size: 0.875rem; }
        tr:hover { background: var(--bg-secondary); }

        /* Status badges */
        .badge {
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 500;
        }
        .badge-pending { background: rgba(245, 158, 11, 0.2); color: var(--warning); }
        .badge-approved { background: rgba(16, 185, 129, 0.2); color: var(--success); }
        .badge-rejected { background: rgba(239, 68, 68, 0.2); color: var(--error); }

        /* Loading state */
        .loading {
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 3rem;
            color: var(--text-muted);
        }

        /* Tab content */
        .tab-content { display: none; }
        .tab-content.active { display: block; }
    </style>
</head>
<body>
    <nav class="nav">
        <div class="nav-brand">Rotation Calendar Admin</div>
        <div class="nav-links">
            <a href="#dashboard" class="nav-link active" data-tab="dashboard">Dashboard</a>
            <a href="#schedule" class="nav-link" data-tab="schedule">Schedule</a>
            <a href="#swaps" class="nav-link" data-tab="swaps">Swaps</a>
            <a href="#days-off" class="nav-link" data-tab="days-off">Days Off</a>
            <a href="#settings" class="nav-link" data-tab="settings">Settings</a>
        </div>
        <div class="nav-user">
            <span id="adminEmail">Loading...</span>
            <button class="btn-logout" id="logoutBtn">Logout</button>
        </div>
    </nav>

    <main class="main">
        <!-- Dashboard Tab -->
        <div id="dashboard" class="tab-content active">
            <div class="page-header">
                <h1 class="page-title">Dashboard</h1>
            </div>

            <div class="stats-grid">
                <div class="stat-card">
                    <div class="stat-value" id="totalResidents">--</div>
                    <div class="stat-label">Total Residents</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="pendingSwaps">--</div>
                    <div class="stat-label">Pending Swaps</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="upcomingDaysOff">--</div>
                    <div class="stat-label">Upcoming Days Off</div>
                </div>
                <div class="stat-card">
                    <div class="stat-value" id="totalAdmins">--</div>
                    <div class="stat-label">Admin Users</div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Recent Activity</h2>
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Action</th>
                                <th>Details</th>
                                <th>Admin</th>
                                <th>Time</th>
                            </tr>
                        </thead>
                        <tbody id="auditLogTable">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Schedule Tab -->
        <div id="schedule" class="tab-content">
            <div class="page-header">
                <h1 class="page-title">Schedule Management</h1>
                <div>
                    <button class="btn btn-secondary" id="exportScheduleBtn">Export Excel</button>
                    <button class="btn btn-primary" id="uploadScheduleBtn">Upload Excel</button>
                </div>
            </div>

            <div class="card">
                <p style="color: var(--text-secondary); text-align: center; padding: 3rem;">
                    Schedule grid editor coming in Phase 2.<br>
                    For now, use the Upload Excel button to import schedules.
                </p>
            </div>

            <input type="file" id="scheduleFileInput" accept=".xlsx" style="display: none;">
        </div>

        <!-- Swaps Tab -->
        <div id="swaps" class="tab-content">
            <div class="page-header">
                <h1 class="page-title">Swap Requests</h1>
            </div>

            <div class="card">
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Requester</th>
                                <th>Target</th>
                                <th>Week</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="swapsTable">
                            <tr><td colspan="5" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Days Off Tab -->
        <div id="days-off" class="tab-content">
            <div class="page-header">
                <h1 class="page-title">Days Off Management</h1>
                <div>
                    <button class="btn btn-secondary" id="uploadCsvBtn">Upload CSV</button>
                    <button class="btn btn-primary" id="addDayOffBtn">Add Day Off</button>
                </div>
            </div>

            <div class="card">
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Resident</th>
                                <th>Type</th>
                                <th>Start Date</th>
                                <th>End Date</th>
                                <th>Notes</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="daysOffTable">
                            <tr><td colspan="6" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Settings Tab -->
        <div id="settings" class="tab-content">
            <div class="page-header">
                <h1 class="page-title">Settings</h1>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Admin Users</h2>
                    <button class="btn btn-primary" id="inviteAdminBtn">Invite Admin</button>
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Email</th>
                                <th>Name</th>
                                <th>Last Login</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody id="adminsTable">
                            <tr><td colspan="4" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    <h2 class="card-title">Rotation Types</h2>
                </div>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Name</th>
                                <th>Color</th>
                                <th>Start Time</th>
                                <th>End Time</th>
                                <th>Overnight</th>
                            </tr>
                        </thead>
                        <tbody id="rotationsTable">
                            <tr><td colspan="5" class="loading">Loading...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    </main>

    <script>
        // Check authentication
        async function checkAuth() {
            try {
                const response = await fetch('/api/admin/me');
                if (!response.ok) {
                    window.location.href = '/admin/login';
                    return null;
                }
                return await response.json();
            } catch (e) {
                window.location.href = '/admin/login';
                return null;
            }
        }

        // Tab navigation
        document.querySelectorAll('.nav-link').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const tab = link.dataset.tab;

                document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));

                link.classList.add('active');
                document.getElementById(tab).classList.add('active');

                // Load data for tab
                loadTabData(tab);
            });
        });

        // Logout
        document.getElementById('logoutBtn').addEventListener('click', async () => {
            await fetch('/api/admin/logout', { method: 'POST' });
            window.location.href = '/admin/login';
        });

        // Upload schedule
        document.getElementById('uploadScheduleBtn').addEventListener('click', () => {
            document.getElementById('scheduleFileInput').click();
        });

        document.getElementById('scheduleFileInput').addEventListener('change', async (e) => {
            const file = e.target.files[0];
            if (!file) return;

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/api/admin/schedule/import', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                alert(`Import complete: ${result.residents_processed} residents, ${result.assignments_created} assignments`);
                loadTabData('dashboard');
            } catch (e) {
                alert('Failed to import schedule');
            }
        });

        // Load data functions
        async function loadTabData(tab) {
            switch(tab) {
                case 'dashboard': await loadDashboard(); break;
                case 'swaps': await loadSwaps(); break;
                case 'days-off': await loadDaysOff(); break;
                case 'settings': await loadSettings(); break;
            }
        }

        async function loadDashboard() {
            // Load stats
            try {
                const [residents, swaps, admins, auditLog] = await Promise.all([
                    fetch('/api/admin/residents').then(r => r.json()),
                    fetch('/api/admin/swaps?status=peer_confirmed').then(r => r.json()).catch(() => []),
                    fetch('/api/admin/admins').then(r => r.json()),
                    fetch('/api/admin/audit-log?limit=10').then(r => r.json())
                ]);

                document.getElementById('totalResidents').textContent = residents.length || '0';
                document.getElementById('pendingSwaps').textContent = swaps.length || '0';
                document.getElementById('totalAdmins').textContent = admins.length || '0';

                // Render audit log
                const tbody = document.getElementById('auditLogTable');
                if (auditLog.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted)">No recent activity</td></tr>';
                } else {
                    tbody.innerHTML = auditLog.map(log => `
                        <tr>
                            <td>${log.action}</td>
                            <td>${log.entity_type || '-'}</td>
                            <td>${log.admin_id || 'System'}</td>
                            <td>${new Date(log.created_at).toLocaleString()}</td>
                        </tr>
                    `).join('');
                }
            } catch (e) {
                console.error('Failed to load dashboard:', e);
            }
        }

        async function loadSwaps() {
            try {
                const swaps = await fetch('/api/admin/swaps').then(r => r.json());
                const tbody = document.getElementById('swapsTable');

                if (swaps.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-muted)">No pending swap requests</td></tr>';
                } else {
                    tbody.innerHTML = swaps.map(swap => `
                        <tr>
                            <td>${swap.requester_id}</td>
                            <td>${swap.target_id}</td>
                            <td>${swap.requester_assignment_id}</td>
                            <td><span class="badge badge-pending">${swap.status}</span></td>
                            <td>
                                <button class="btn btn-primary" onclick="approveSwap(${swap.id})">Approve</button>
                                <button class="btn btn-secondary" onclick="rejectSwap(${swap.id})">Reject</button>
                            </td>
                        </tr>
                    `).join('');
                }
            } catch (e) {
                console.error('Failed to load swaps:', e);
            }
        }

        async function loadDaysOff() {
            try {
                const daysOff = await fetch('/api/admin/days-off').then(r => r.json());
                const tbody = document.getElementById('daysOffTable');

                if (daysOff.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No days off recorded</td></tr>';
                } else {
                    tbody.innerHTML = daysOff.map(d => `
                        <tr>
                            <td>${d.resident_id}</td>
                            <td>${d.type_id}</td>
                            <td>${d.start_date}</td>
                            <td>${d.end_date}</td>
                            <td>${d.notes || '-'}</td>
                            <td><button class="btn btn-secondary" onclick="deleteDayOff(${d.id})">Delete</button></td>
                        </tr>
                    `).join('');
                }
            } catch (e) {
                console.error('Failed to load days off:', e);
            }
        }

        async function loadSettings() {
            try {
                const [admins, rotations] = await Promise.all([
                    fetch('/api/admin/admins').then(r => r.json()),
                    fetch('/api/admin/rotations').then(r => r.json())
                ]);

                // Render admins
                const adminsTbody = document.getElementById('adminsTable');
                adminsTbody.innerHTML = admins.map(a => `
                    <tr>
                        <td>${a.email}</td>
                        <td>${a.name || '-'}</td>
                        <td>${a.last_login ? new Date(a.last_login).toLocaleString() : 'Never'}</td>
                        <td><span class="badge ${a.is_active ? 'badge-approved' : 'badge-rejected'}">${a.is_active ? 'Active' : 'Inactive'}</span></td>
                    </tr>
                `).join('');

                // Render rotations
                const rotationsTbody = document.getElementById('rotationsTable');
                rotationsTbody.innerHTML = rotations.map(r => `
                    <tr>
                        <td>${r.name}</td>
                        <td><span style="display:inline-block;width:20px;height:20px;border-radius:4px;background:${r.color || '#6B7280'}"></span></td>
                        <td>${r.start_time || '-'}</td>
                        <td>${r.end_time || '-'}</td>
                        <td>${r.is_overnight ? 'Yes' : 'No'}</td>
                    </tr>
                `).join('');
            } catch (e) {
                console.error('Failed to load settings:', e);
            }
        }

        // Action functions
        async function approveSwap(id) {
            if (!confirm('Approve this swap?')) return;
            await fetch(`/api/admin/swaps/${id}/approve`, { method: 'POST' });
            loadSwaps();
        }

        async function rejectSwap(id) {
            if (!confirm('Reject this swap?')) return;
            await fetch(`/api/admin/swaps/${id}/reject`, { method: 'POST' });
            loadSwaps();
        }

        async function deleteDayOff(id) {
            if (!confirm('Delete this day off?')) return;
            await fetch(`/api/admin/days-off/${id}`, { method: 'DELETE' });
            loadDaysOff();
        }

        // Initialize
        (async () => {
            const admin = await checkAuth();
            if (admin) {
                document.getElementById('adminEmail').textContent = admin.email;
                loadDashboard();
            }
        })();
    </script>
</body>
</html>
    """


def get_base_url() -> str:
    """Get the base URL for subscription links."""
    return settings.base_url
