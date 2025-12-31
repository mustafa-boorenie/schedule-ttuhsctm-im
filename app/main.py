"""
FastAPI application for the Residency Rotation Calendar Subscription service.
"""
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from .parser import get_parser, reload_parser
from .calendar_gen import generate_resident_ics

# Get the project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Initialize the parser with the schedule file
SCHEDULE_PATH = PROJECT_ROOT / "schedule.xlsx"

app = FastAPI(
    title="Residency Rotation Calendar",
    description="Subscribe to your personalized rotation calendar",
    version="1.0.0",
)

# Initialize parser on startup
@app.on_event("startup")
async def startup_event():
    """Initialize the schedule parser on startup."""
    if SCHEDULE_PATH.exists():
        get_parser(SCHEDULE_PATH)
    else:
        print(f"Warning: Schedule file not found at {SCHEDULE_PATH}")


@app.get("/api/residents")
async def list_residents():
    """Get list of all resident names for the search dropdown."""
    try:
        parser = get_parser(SCHEDULE_PATH)
        residents = parser.get_residents()
        return {"residents": sorted(residents)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error loading residents: {str(e)}")


@app.get("/api/calendar/{resident_name}.ics")
async def get_calendar(resident_name: str, request: Request):
    """
    Generate and return an ICS calendar file for a specific resident.
    
    This endpoint is used for calendar subscription - the URL can be added
    to iOS Calendar, Google Calendar, or any other calendar app that
    supports iCal subscriptions.
    """
    try:
        parser = get_parser(SCHEDULE_PATH)
        residents = parser.get_residents()
        
        # Check if resident exists
        if resident_name not in residents:
            raise HTTPException(status_code=404, detail=f"Resident '{resident_name}' not found")
        
        # Generate the ICS file
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
async def health_check():
    """Health check endpoint for deployment."""
    return {"status": "healthy"}


@app.post("/api/reload")
async def reload_schedule():
    """Reload the schedule from the XLSX file (for updates)."""
    try:
        reload_parser(SCHEDULE_PATH)
        parser = get_parser(SCHEDULE_PATH)
        count = len(parser.get_residents())
        return {"status": "reloaded", "resident_count": count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading schedule: {str(e)}")


# Serve static files (frontend)
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
        </body>
        </html>
        """)


def get_base_url() -> str:
    """Get the base URL for subscription links."""
    return os.environ.get("BASE_URL", "http://localhost:8000")

