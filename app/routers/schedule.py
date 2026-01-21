"""
Schedule management API routes for the grid editor.
"""
from typing import List, Optional
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
import pandas as pd
import io

from ..database import get_db
from ..models import (
    Admin, Resident, Rotation, ScheduleAssignment, AcademicYear,
    PGYLevel, DataSource
)
from ..services.excel_import import ExcelImportService
from .admin_auth import require_admin

router = APIRouter(prefix="/api/admin/schedule", tags=["schedule"])


# ============== Schedule Grid API ==============

@router.get("/grid")
async def get_schedule_grid(
    academic_year_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    pgy_levels: Optional[str] = Query(None, description="Comma-separated PGY levels"),
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get schedule data formatted for grid display.

    Returns a structure with:
    - weeks: list of week periods with start/end dates
    - residents: list of residents grouped by PGY level
    - assignments: dict mapping "resident_id:week_start" to rotation data
    - rotations: list of all available rotations for dropdown
    """
    # Get academic year
    if academic_year_id:
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.id == academic_year_id)
        )
        academic_year = result.scalar_one_or_none()
    else:
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.is_current == True)
        )
        academic_year = result.scalar_one_or_none()

    if not academic_year:
        # Return empty grid if no academic year
        return {
            "weeks": [],
            "residents": [],
            "assignments": {},
            "rotations": [],
            "academic_year": None,
        }

    # Set date range from academic year if not provided
    if not start_date:
        start_date = academic_year.start_date
    if not end_date:
        end_date = academic_year.end_date

    # Get all weeks within range
    weeks = await _get_weeks_in_range(db, start_date, end_date, academic_year.id)

    # Get residents filtered by PGY level
    resident_query = select(Resident).where(
        Resident.academic_year_id == academic_year.id,
        Resident.is_active == True
    )

    if pgy_levels:
        levels = [PGYLevel(l.strip()) for l in pgy_levels.split(",") if l.strip()]
        if levels:
            resident_query = resident_query.where(Resident.pgy_level.in_(levels))

    resident_query = resident_query.order_by(Resident.pgy_level, Resident.name)
    result = await db.execute(resident_query)
    residents = result.scalars().all()

    # Get all assignments for these residents in date range
    if residents:
        resident_ids = [r.id for r in residents]
        result = await db.execute(
            select(ScheduleAssignment)
            .options(selectinload(ScheduleAssignment.rotation))
            .where(
                ScheduleAssignment.resident_id.in_(resident_ids),
                ScheduleAssignment.week_start >= start_date,
                ScheduleAssignment.week_end <= end_date
            )
        )
        assignments_list = result.scalars().all()
    else:
        assignments_list = []

    # Build assignments dict
    assignments = {}
    for a in assignments_list:
        key = f"{a.resident_id}:{a.week_start.isoformat()}"
        assignments[key] = {
            "id": a.id,
            "rotation_id": a.rotation_id,
            "rotation_name": a.rotation.name if a.rotation else None,
            "rotation_color": a.rotation.color if a.rotation else None,
        }

    # Get all rotations for dropdown
    result = await db.execute(select(Rotation).order_by(Rotation.name))
    rotations = result.scalars().all()

    return {
        "academic_year": {
            "id": academic_year.id,
            "name": academic_year.name,
            "start_date": academic_year.start_date.isoformat(),
            "end_date": academic_year.end_date.isoformat(),
        },
        "weeks": weeks,
        "residents": [
            {
                "id": r.id,
                "name": r.name,
                "pgy_level": r.pgy_level.value,
                "email": r.email,
            }
            for r in residents
        ],
        "assignments": assignments,
        "rotations": [
            {
                "id": r.id,
                "name": r.name,
                "color": r.color,
                "display_name": r.display_name or r.name,
            }
            for r in rotations
        ],
    }


@router.put("/cell")
async def update_schedule_cell(
    resident_id: int,
    week_start: date,
    rotation_id: Optional[int] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Update a single cell in the schedule grid.

    If rotation_id is None, deletes the assignment.
    """
    from ..models import AuditLog

    # Find existing assignment
    result = await db.execute(
        select(ScheduleAssignment).where(
            ScheduleAssignment.resident_id == resident_id,
            ScheduleAssignment.week_start == week_start
        )
    )
    assignment = result.scalar_one_or_none()

    old_rotation_id = assignment.rotation_id if assignment else None

    if rotation_id is None:
        # Delete assignment
        if assignment:
            await db.delete(assignment)

            # Audit log
            audit = AuditLog(
                admin_id=admin.id,
                action="schedule_delete",
                entity_type="schedule_assignment",
                entity_id=assignment.id,
                old_value={"rotation_id": old_rotation_id, "resident_id": resident_id},
                new_value=None,
            )
            db.add(audit)

        return {"status": "deleted"}

    # Get rotation to verify it exists
    result = await db.execute(select(Rotation).where(Rotation.id == rotation_id))
    rotation = result.scalar_one_or_none()
    if not rotation:
        raise HTTPException(status_code=404, detail="Rotation not found")

    if assignment:
        # Update existing
        assignment.rotation_id = rotation_id
        assignment.source = DataSource.MANUAL

        # Audit log
        audit = AuditLog(
            admin_id=admin.id,
            action="schedule_edit",
            entity_type="schedule_assignment",
            entity_id=assignment.id,
            old_value={"rotation_id": old_rotation_id},
            new_value={"rotation_id": rotation_id},
        )
        db.add(audit)
    else:
        # Get resident to find academic year and calculate week_end
        result = await db.execute(select(Resident).where(Resident.id == resident_id))
        resident = result.scalar_one_or_none()
        if not resident:
            raise HTTPException(status_code=404, detail="Resident not found")

        # Calculate week_end (assuming 7-day weeks)
        week_end = week_start + timedelta(days=6)

        assignment = ScheduleAssignment(
            resident_id=resident_id,
            rotation_id=rotation_id,
            week_start=week_start,
            week_end=week_end,
            academic_year_id=resident.academic_year_id,
            source=DataSource.MANUAL,
        )
        db.add(assignment)
        await db.flush()

        # Audit log
        audit = AuditLog(
            admin_id=admin.id,
            action="schedule_create",
            entity_type="schedule_assignment",
            entity_id=assignment.id,
            old_value=None,
            new_value={"rotation_id": rotation_id, "resident_id": resident_id},
        )
        db.add(audit)

    return {
        "status": "updated",
        "assignment": {
            "id": assignment.id,
            "rotation_id": rotation_id,
            "rotation_name": rotation.name,
            "rotation_color": rotation.color,
        }
    }


@router.post("/bulk-update")
async def bulk_update_schedule(
    updates: List[dict],
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk update multiple schedule cells.

    Each update should have: resident_id, week_start, rotation_id (or null to delete)
    """
    results = []
    for update in updates:
        try:
            result = await update_schedule_cell(
                resident_id=update["resident_id"],
                week_start=date.fromisoformat(update["week_start"]),
                rotation_id=update.get("rotation_id"),
                admin=admin,
                db=db,
            )
            results.append({"success": True, **update, "result": result})
        except Exception as e:
            results.append({"success": False, **update, "error": str(e)})

    return {"results": results}


# ============== Excel Import/Export ==============

@router.post("/upload")
async def upload_schedule_excel(
    file: UploadFile = File(...),
    preview_only: bool = Query(False, description="If true, only preview changes without applying"),
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload and import an Excel schedule file.

    If preview_only=true, returns what would change without applying.
    """
    from pathlib import Path
    import tempfile

    # Validate file type
    if not file.filename.endswith('.xlsx'):
        raise HTTPException(status_code=400, detail="File must be an Excel file (.xlsx)")

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        import_service = ExcelImportService(db)

        if preview_only:
            # Parse and return preview without committing
            result = await import_service.import_excel(tmp_path)
            await db.rollback()  # Don't save changes
            return {
                "preview": True,
                "changes": result,
            }
        else:
            result = await import_service.import_excel(tmp_path)

            # Audit log
            from ..models import AuditLog
            audit = AuditLog(
                admin_id=admin.id,
                action="schedule_bulk_import",
                entity_type="schedule",
                entity_id=None,
                old_value=None,
                new_value={
                    "filename": file.filename,
                    "residents_processed": result["residents_processed"],
                    "assignments_created": result["assignments_created"],
                },
            )
            db.add(audit)

            return {
                "preview": False,
                "imported": True,
                **result,
            }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error processing file: {str(e)}")
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/export")
async def export_schedule_excel(
    academic_year_id: Optional[int] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Export current schedule as Excel file.
    """
    # Get academic year
    if academic_year_id:
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.id == academic_year_id)
        )
        academic_year = result.scalar_one_or_none()
    else:
        result = await db.execute(
            select(AcademicYear).where(AcademicYear.is_current == True)
        )
        academic_year = result.scalar_one_or_none()

    if not academic_year:
        raise HTTPException(status_code=404, detail="No academic year found")

    # Get all data
    grid_data = await get_schedule_grid(
        academic_year_id=academic_year.id,
        admin=admin,
        db=db,
    )

    # Build DataFrame
    weeks = grid_data["weeks"]
    residents = grid_data["residents"]
    assignments = grid_data["assignments"]

    # Create column headers
    columns = ["Resident Names"] + [f"WEEK {i+1}" for i in range(len(weeks))]

    # Create rows
    rows = []

    # First row: date ranges
    date_row = [""] + [f"{w['label']}" for w in weeks]
    rows.append(date_row)

    # Group residents by PGY level
    current_pgy = None
    for resident in residents:
        if resident["pgy_level"] != current_pgy:
            # Add PGY header row
            current_pgy = resident["pgy_level"]
            rows.append([current_pgy] + [""] * len(weeks))

        # Add resident row
        row = [resident["name"]]
        for week in weeks:
            key = f"{resident['id']}:{week['start']}"
            assignment = assignments.get(key, {})
            row.append(assignment.get("rotation_name", ""))
        rows.append(row)

    # Create DataFrame
    df = pd.DataFrame(rows, columns=columns)

    # Write to bytes buffer
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Schedule')
    output.seek(0)

    filename = f"schedule_{academic_year.name.replace('-', '_')}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@router.get("/template")
async def download_schedule_template(
    admin: Admin = Depends(require_admin),
):
    """
    Download a blank schedule template Excel file.
    """
    # Create template with headers
    columns = ["Resident Names"] + [f"WEEK {i+1}" for i in range(52)]

    # Sample structure
    rows = [
        [""] + [f"Month {(i//4)+1} Week" for i in range(52)],  # Date row placeholder
        ["PGY1"] + [""] * 52,
        ["Sample Resident 1"] + [""] * 52,
        ["Sample Resident 2"] + [""] * 52,
        ["PGY2"] + [""] * 52,
        ["Sample Resident 3"] + [""] * 52,
    ]

    df = pd.DataFrame(rows, columns=columns)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Schedule')
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=schedule_template.xlsx"}
    )


# ============== Helper Functions ==============

async def _get_weeks_in_range(
    db: AsyncSession,
    start_date: date,
    end_date: date,
    academic_year_id: int,
) -> List[dict]:
    """Get all unique weeks from assignments in the date range."""
    result = await db.execute(
        select(ScheduleAssignment.week_start, ScheduleAssignment.week_end)
        .where(
            ScheduleAssignment.academic_year_id == academic_year_id,
            ScheduleAssignment.week_start >= start_date,
            ScheduleAssignment.week_end <= end_date
        )
        .distinct()
        .order_by(ScheduleAssignment.week_start)
    )
    weeks_data = result.all()

    if weeks_data:
        return [
            {
                "start": w.week_start.isoformat(),
                "end": w.week_end.isoformat(),
                "label": _format_week_label(w.week_start, w.week_end),
            }
            for w in weeks_data
        ]

    # If no assignments yet, generate weeks from date range
    weeks = []
    current = start_date
    week_num = 1
    while current <= end_date:
        week_end = current + timedelta(days=6)
        if week_end > end_date:
            week_end = end_date
        weeks.append({
            "start": current.isoformat(),
            "end": week_end.isoformat(),
            "label": _format_week_label(current, week_end),
        })
        current = week_end + timedelta(days=1)
        week_num += 1

    return weeks


def _format_week_label(start: date, end: date) -> str:
    """Format a week date range as a label."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    if start.month == end.month:
        return f"{months[start.month-1]} {start.day}-{end.day}"
    else:
        return f"{months[start.month-1]} {start.day}-{months[end.month-1]} {end.day}"
