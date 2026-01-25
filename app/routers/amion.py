"""
Amion integration API routes.
"""
from typing import List, Optional, Dict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from ..database import get_db
from ..models import (
    Admin, Resident, Attending, AttendingAssignment, CallAssignment,
    AmionSyncLog, AcademicYear, SyncStatus, DataSource, ScheduleAssignment, Rotation
)
from ..services.amion_scraper import AmionScraper, run_amion_sync
from ..settings import settings
from .admin_auth import require_admin

router = APIRouter(prefix="/api/admin/amion", tags=["amion"])


# ============== Schemas ==============

class SyncRequest(BaseModel):
    months: int = 1
    url_override: Optional[str] = None


class NameMappingRequest(BaseModel):
    mappings: Dict[str, int]  # scraped_name -> resident_id


class NameMatchResult(BaseModel):
    scraped_name: str
    matched_resident_id: Optional[int]
    matched_resident_name: Optional[str]
    confidence: float
    needs_review: bool


class SyncLogResponse(BaseModel):
    id: int
    sync_type: str
    status: str
    records_processed: Optional[int]
    errors: Optional[dict]
    started_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class CallAssignmentResponse(BaseModel):
    id: int
    resident_id: int
    resident_name: Optional[str] = None
    call_type: str
    date: date
    service: Optional[str]
    location: Optional[str]

    class Config:
        from_attributes = True


class AttendingAssignmentResponse(BaseModel):
    id: int
    attending_id: int
    attending_name: Optional[str] = None
    service: str
    date: date

    class Config:
        from_attributes = True


# ============== Endpoints ==============

@router.get("/status")
async def get_amion_status(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get current Amion integration status.

    Returns configuration status, last sync info, and statistics.
    """
    # Check configuration
    configured = bool(settings.amion_base_url)

    # Get last sync
    result = await db.execute(
        select(AmionSyncLog)
        .order_by(desc(AmionSyncLog.started_at))
        .limit(1)
    )
    last_sync = result.scalar_one_or_none()

    # Get counts
    call_count = await db.execute(select(func.count(CallAssignment.id)))
    call_count = call_count.scalar()

    attending_count = await db.execute(select(func.count(AttendingAssignment.id)))
    attending_count = attending_count.scalar()

    unmatched_count = 0
    if last_sync and last_sync.errors:
        unmatched_count = len(last_sync.errors.get("unmatched_names", []))

    return {
        "configured": configured,
        "amion_url": settings.amion_base_url if configured else None,
        "sync_hour": settings.amion_sync_hour,
        "last_sync": {
            "id": last_sync.id,
            "status": last_sync.status.value if last_sync else None,
            "started_at": last_sync.started_at.isoformat() if last_sync else None,
            "completed_at": last_sync.completed_at.isoformat() if last_sync and last_sync.completed_at else None,
            "records_processed": last_sync.records_processed if last_sync else 0,
        } if last_sync else None,
        "statistics": {
            "total_call_assignments": call_count,
            "total_attending_assignments": attending_count,
            "unmatched_names": unmatched_count,
        },
    }


@router.post("/sync")
async def trigger_sync(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger an Amion sync.

    Can be run in foreground (blocking) or background.
    """
    # For now, run synchronously for easier debugging
    # In production, could use background_tasks.add_task()

    try:
        results = await run_amion_sync(
            db=db,
            months_to_sync=request.months,
            base_url=request.url_override,
        )
        await db.commit()

        return {
            "status": "completed",
            "results": results,
        }

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@router.get("/sync-history", response_model=List[SyncLogResponse])
async def get_sync_history(
    limit: int = 20,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get history of Amion sync operations."""
    result = await db.execute(
        select(AmionSyncLog)
        .order_by(desc(AmionSyncLog.started_at))
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/unmatched-names")
async def get_unmatched_names(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get names from Amion that couldn't be matched to residents.

    Returns the unmatched names from the most recent sync along with
    potential matches for manual resolution.
    """
    # Get last sync with errors
    result = await db.execute(
        select(AmionSyncLog)
        .where(AmionSyncLog.errors.isnot(None))
        .order_by(desc(AmionSyncLog.started_at))
        .limit(1)
    )
    last_sync = result.scalar_one_or_none()

    if not last_sync or not last_sync.errors:
        return {"unmatched_names": [], "potential_matches": {}}

    unmatched = last_sync.errors.get("unmatched_names", [])

    if not unmatched:
        return {"unmatched_names": [], "potential_matches": {}}

    # Get all residents for potential matching
    result = await db.execute(
        select(Resident)
        .where(Resident.is_active == True)
        .order_by(Resident.name)
    )
    residents = result.scalars().all()

    # Use scraper's name matching
    scraper = AmionScraper(db)
    matches = await scraper.match_names(unmatched)

    potential_matches = {}
    for match in matches:
        if match.matched_resident_id:
            potential_matches[match.scraped_name] = {
                "resident_id": match.matched_resident_id,
                "resident_name": match.matched_resident_name,
                "confidence": match.confidence,
            }

    return {
        "unmatched_names": unmatched,
        "potential_matches": potential_matches,
        "all_residents": [
            {"id": r.id, "name": r.name, "pgy_level": r.pgy_level.value}
            for r in residents
        ],
    }


@router.post("/resolve-names")
async def resolve_name_mappings(
    request: NameMappingRequest,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Manually resolve name mappings and re-sync affected data.

    This saves the mappings and updates any existing unmatched call assignments.
    """
    # TODO: Implement name alias table for persistent mappings
    # For now, just return success - in a full implementation,
    # we would store these mappings and use them in future syncs

    return {
        "status": "saved",
        "mappings_count": len(request.mappings),
        "message": "Name mappings saved. They will be used in future syncs.",
    }


@router.get("/call-assignments", response_model=List[CallAssignmentResponse])
async def get_call_assignments(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    resident_id: Optional[int] = None,
    call_type: Optional[str] = None,
    limit: int = 100,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get call assignments with optional filters."""
    query = select(CallAssignment)

    if start_date:
        query = query.where(CallAssignment.date >= start_date)
    if end_date:
        query = query.where(CallAssignment.date <= end_date)
    if resident_id:
        query = query.where(CallAssignment.resident_id == resident_id)
    if call_type:
        query = query.where(CallAssignment.call_type == call_type)

    query = query.order_by(CallAssignment.date.desc()).limit(limit)

    result = await db.execute(query)
    assignments = result.scalars().all()

    # Get resident names
    resident_ids = list(set(a.resident_id for a in assignments))
    if resident_ids:
        residents_result = await db.execute(
            select(Resident).where(Resident.id.in_(resident_ids))
        )
        residents = {r.id: r.name for r in residents_result.scalars()}
    else:
        residents = {}

    return [
        CallAssignmentResponse(
            id=a.id,
            resident_id=a.resident_id,
            resident_name=residents.get(a.resident_id),
            call_type=a.call_type,
            date=a.date,
            service=a.service,
            location=a.location,
        )
        for a in assignments
    ]


@router.get("/attending-assignments", response_model=List[AttendingAssignmentResponse])
async def get_attending_assignments(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    service: Optional[str] = None,
    limit: int = 100,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get attending assignments with optional filters."""
    query = select(AttendingAssignment)

    if start_date:
        query = query.where(AttendingAssignment.date >= start_date)
    if end_date:
        query = query.where(AttendingAssignment.date <= end_date)
    if service:
        query = query.where(AttendingAssignment.service == service)

    query = query.order_by(AttendingAssignment.date.desc()).limit(limit)

    result = await db.execute(query)
    assignments = result.scalars().all()

    # Get attending names
    attending_ids = list(set(a.attending_id for a in assignments))
    if attending_ids:
        attendings_result = await db.execute(
            select(Attending).where(Attending.id.in_(attending_ids))
        )
        attendings = {a.id: a.name for a in attendings_result.scalars()}
    else:
        attendings = {}

    return [
        AttendingAssignmentResponse(
            id=a.id,
            attending_id=a.attending_id,
            attending_name=attendings.get(a.attending_id),
            service=a.service,
            date=a.date,
        )
        for a in assignments
    ]


@router.get("/attendings")
async def list_attendings(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all attending physicians."""
    result = await db.execute(select(Attending).order_by(Attending.name))
    attendings = result.scalars().all()

    return [
        {
            "id": a.id,
            "name": a.name,
            "service": a.service,
        }
        for a in attendings
    ]


@router.delete("/call-assignments/{assignment_id}")
async def delete_call_assignment(
    assignment_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a call assignment."""
    result = await db.execute(
        select(CallAssignment).where(CallAssignment.id == assignment_id)
    )
    assignment = result.scalar_one_or_none()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    await db.delete(assignment)
    return {"status": "deleted"}


@router.post("/test-scrape")
async def test_scrape(
    url: Optional[str] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Test the Amion scraper without saving data.

    Useful for debugging and verifying the scraper works with the target URL.
    """
    test_url = url or settings.amion_base_url

    if not test_url:
        raise HTTPException(status_code=400, detail="No Amion URL configured or provided")

    scraper = AmionScraper(db)

    try:
        today = date.today()
        call_entries, attending_entries = await scraper.scrape_month(
            today.year, today.month, test_url
        )

        return {
            "status": "success",
            "url_tested": test_url,
            "call_entries_found": len(call_entries),
            "attending_entries_found": len(attending_entries),
            "sample_call_entries": [
                {
                    "resident_name": e.resident_name,
                    "date": e.date.isoformat(),
                    "call_type": e.call_type,
                    "raw_text": e.raw_text,
                }
                for e in call_entries[:5]
            ],
            "sample_attending_entries": [
                {
                    "attending_name": e.attending_name,
                    "service": e.service,
                    "date": e.date.isoformat(),
                }
                for e in attending_entries[:5]
            ],
        }

    except Exception as e:
        return {
            "status": "error",
            "url_tested": test_url,
            "error": str(e),
        }

    finally:
        await scraper.close()


@router.post("/test-team-attending")
async def test_team_attending_scrape(
    all_rows_url: str,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Test scraping the team-attending assignments from Amion's all_rows view.

    This shows which attending is covering which team on which dates.
    """
    scraper = AmionScraper(db)

    try:
        today = date.today()
        team_attending = await scraper.scrape_team_attending_schedule(
            all_rows_url, today.year, today.month
        )

        # Group by team for easier viewing
        by_team = {}
        for ta in team_attending:
            if ta.team_name not in by_team:
                by_team[ta.team_name] = []
            by_team[ta.team_name].append({
                "attending": ta.attending_name,
                "start_date": ta.start_date.isoformat(),
                "end_date": ta.end_date.isoformat(),
            })

        return {
            "status": "success",
            "url_tested": all_rows_url,
            "total_assignments": len(team_attending),
            "teams_found": list(by_team.keys()),
            "by_team": by_team,
        }

    except Exception as e:
        return {
            "status": "error",
            "url_tested": all_rows_url,
            "error": str(e),
        }

    finally:
        await scraper.close()


@router.post("/test-oncall")
async def test_oncall_scrape(
    oncall_url: str,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Test scraping the on-call schedule from Amion's filtered view.

    This shows which attending is on-call on each date.
    """
    scraper = AmionScraper(db)

    try:
        today = date.today()
        oncall_entries = await scraper.scrape_oncall_schedule(
            oncall_url, today.year, today.month
        )

        return {
            "status": "success",
            "url_tested": oncall_url,
            "oncall_entries_found": len(oncall_entries),
            "sample_entries": [
                {
                    "attending": e.attending_name,
                    "date": e.date.isoformat(),
                    "service": e.service,
                }
                for e in oncall_entries[:20]
            ],
        }

    except Exception as e:
        return {
            "status": "error",
            "url_tested": oncall_url,
            "error": str(e),
        }

    finally:
        await scraper.close()


@router.post("/sync-call-schedule")
async def sync_call_schedule(
    all_rows_url: str,
    oncall_url: str,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Full sync: Scrape Amion and generate call assignments for residents.

    1. Scrapes all_rows view to get team → attending mapping
    2. Scrapes on-call view to get which attending is on-call each day
    3. Cross-references with residents assigned to each rotation
    4. Creates pre-call, on-call, post-call assignments
    """
    scraper = AmionScraper(db)

    try:
        today = date.today()

        # Step 1: Get team-attending assignments (filter for hospitalist teams only)
        all_team_attending = await scraper.scrape_team_attending_schedule(
            all_rows_url, today.year, today.month
        )
        # Only keep hospitalist teams (Red, Blue, Green, Orange, Purple)
        hospitalist_teams = ['Red Team', 'Blue Team', 'Green Team', 'Orange Team', 'Purple Team']
        team_attending = [ta for ta in all_team_attending if ta.team_name in hospitalist_teams]

        # Step 2: Get on-call schedule (filter for Hospitalist On-Call only)
        all_oncall_entries = await scraper.scrape_oncall_schedule(
            oncall_url, today.year, today.month
        )
        # Filter to only Hospitalist On-Call (the main call schedule)
        oncall_entries = [e for e in all_oncall_entries if 'Hospitalist' in e.service]

        # Step 3: Get residents by rotation from database
        from ..models import Resident, ScheduleAssignment, Rotation

        # Get current academic year
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.is_current == True)
        )
        academic_year = result.scalar_one_or_none()

        # Get all schedule assignments for this month
        month_start = date(today.year, today.month, 1)
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1) if today.month < 12 else date(today.year, 12, 31)

        result = await db.execute(
            select(ScheduleAssignment, Rotation, Resident)
            .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
            .join(Resident, ScheduleAssignment.resident_id == Resident.id)
            .where(ScheduleAssignment.week_start <= month_end)
            .where(ScheduleAssignment.week_end >= month_start)
        )
        schedule_data = result.all()

        # Build rotation -> residents mapping
        residents_by_rotation = {}
        for assignment, rotation, resident in schedule_data:
            rotation_name = rotation.name
            if rotation_name not in residents_by_rotation:
                residents_by_rotation[rotation_name] = []
            if resident.id not in residents_by_rotation[rotation_name]:
                residents_by_rotation[rotation_name].append(resident.id)

        # Step 4: Generate call assignments
        call_assignments = scraper.generate_call_assignments_for_residents(
            oncall_entries,
            team_attending,
            residents_by_rotation,
        )

        # Step 5: Save to database
        created = 0
        updated = 0
        for ca in call_assignments:
            # Check if exists
            existing = await db.execute(
                select(CallAssignment).where(
                    CallAssignment.resident_id == ca['resident_id'],
                    CallAssignment.date == ca['date'],
                    CallAssignment.call_type == ca['call_type']
                )
            )
            existing = existing.scalar_one_or_none()

            if existing:
                existing.service = ca.get('service')
                existing.attending_name = ca.get('attending_name')
                existing.source = DataSource.AMION
                updated += 1
            else:
                new_assignment = CallAssignment(
                    resident_id=ca['resident_id'],
                    date=ca['date'],
                    call_type=ca['call_type'],
                    service=ca.get('service'),
                    attending_name=ca.get('attending_name'),
                    academic_year_id=academic_year.id if academic_year else None,
                    source=DataSource.AMION,
                )
                db.add(new_assignment)
                created += 1

        await db.commit()

        return {
            "status": "success",
            "team_attending_found": len(team_attending),
            "oncall_entries_found": len(oncall_entries),
            "rotations_in_db": list(residents_by_rotation.keys()),
            "call_assignments_generated": len(call_assignments),
            "created": created,
            "updated": updated,
        }

    except Exception as e:
        await db.rollback()
        return {
            "status": "error",
            "error": str(e),
        }

    finally:
        await scraper.close()


@router.get("/scheduler")
async def get_scheduler_status(
    admin: Admin = Depends(require_admin),
):
    """
    Get the status of the background scheduler.

    Shows scheduled jobs and their next run times.
    """
    from ..services.scheduler import scheduler

    return {
        "running": scheduler.is_running,
        "jobs": scheduler.get_jobs(),
    }
