"""
Days Off Management API routes.
"""
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from pydantic import BaseModel

from ..database import get_db
from ..models import Admin, Resident, DayOff, DayOffType
from ..services.days_off import DaysOffService
from .admin_auth import require_admin

router = APIRouter(prefix="/api/admin/days-off", tags=["days-off"])


# ============== Schemas ==============

class DayOffCreate(BaseModel):
    resident_id: int
    type_id: int
    start_date: date
    end_date: date
    notes: Optional[str] = None


class DayOffUpdate(BaseModel):
    type_id: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    notes: Optional[str] = None


class DayOffResponse(BaseModel):
    id: int
    resident_id: int
    resident_name: Optional[str] = None
    type_id: int
    type_name: Optional[str] = None
    type_color: Optional[str] = None
    start_date: date
    end_date: date
    notes: Optional[str]
    source: Optional[str] = None
    approved_by: Optional[int] = None

    class Config:
        from_attributes = True


class CSVUploadRequest(BaseModel):
    content: str  # CSV content as string


class LLMParseRequest(BaseModel):
    text: str  # Natural language text to parse


class DayOffTypeResponse(BaseModel):
    id: int
    name: str
    color: Optional[str]
    is_system: bool

    class Config:
        from_attributes = True


class DayOffTypeCreate(BaseModel):
    name: str
    color: Optional[str] = None


# ============== Day Off Types ==============

@router.get("/types", response_model=List[DayOffTypeResponse])
async def list_day_off_types(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all available day off types."""
    result = await db.execute(
        select(DayOffType).order_by(DayOffType.name)
    )
    return result.scalars().all()


@router.post("/types", response_model=DayOffTypeResponse)
async def create_day_off_type(
    data: DayOffTypeCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a custom day off type."""
    day_off_type = DayOffType(
        name=data.name,
        color=data.color or "#6B7280",
        is_system=False,
    )
    db.add(day_off_type)
    await db.flush()
    return day_off_type


@router.delete("/types/{type_id}")
async def delete_day_off_type(
    type_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a custom day off type (system types cannot be deleted)."""
    result = await db.execute(
        select(DayOffType).where(DayOffType.id == type_id)
    )
    day_off_type = result.scalar_one_or_none()

    if not day_off_type:
        raise HTTPException(status_code=404, detail="Day off type not found")

    if day_off_type.is_system:
        raise HTTPException(status_code=400, detail="Cannot delete system day off types")

    await db.delete(day_off_type)
    return {"status": "deleted"}


# ============== Days Off CRUD ==============

@router.get("", response_model=List[DayOffResponse])
async def list_days_off(
    resident_id: Optional[int] = None,
    type_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    limit: int = Query(100, le=500),
    offset: int = 0,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all days off with optional filters."""
    service = DaysOffService(db)
    days_off, total = await service.get_days_off(
        resident_id=resident_id,
        type_id=type_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )

    # Get resident and type info
    resident_ids = list(set(d.resident_id for d in days_off))
    type_ids = list(set(d.type_id for d in days_off))

    residents = {}
    if resident_ids:
        result = await db.execute(
            select(Resident).where(Resident.id.in_(resident_ids))
        )
        residents = {r.id: r.name for r in result.scalars()}

    types = {}
    if type_ids:
        result = await db.execute(
            select(DayOffType).where(DayOffType.id.in_(type_ids))
        )
        types = {t.id: t for t in result.scalars()}

    return [
        DayOffResponse(
            id=d.id,
            resident_id=d.resident_id,
            resident_name=residents.get(d.resident_id),
            type_id=d.type_id,
            type_name=types.get(d.type_id).name if d.type_id in types else None,
            type_color=types.get(d.type_id).color if d.type_id in types else None,
            start_date=d.start_date,
            end_date=d.end_date,
            notes=d.notes,
            source=d.source.value if d.source else None,
            approved_by=d.approved_by,
        )
        for d in days_off
    ]


@router.post("", response_model=DayOffResponse)
async def create_day_off(
    data: DayOffCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new day off entry."""
    # Validate resident exists
    result = await db.execute(
        select(Resident).where(Resident.id == data.resident_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Resident not found")

    # Validate type exists
    result = await db.execute(
        select(DayOffType).where(DayOffType.id == data.type_id)
    )
    day_off_type = result.scalar_one_or_none()
    if not day_off_type:
        raise HTTPException(status_code=404, detail="Day off type not found")

    # Validate dates
    if data.start_date > data.end_date:
        raise HTTPException(status_code=400, detail="Start date must be before or equal to end date")

    service = DaysOffService(db)
    day_off = await service.create_day_off(
        resident_id=data.resident_id,
        type_id=data.type_id,
        start_date=data.start_date,
        end_date=data.end_date,
        notes=data.notes,
        admin_id=admin.id,
    )

    # Get resident name
    result = await db.execute(
        select(Resident).where(Resident.id == data.resident_id)
    )
    resident = result.scalar_one()

    return DayOffResponse(
        id=day_off.id,
        resident_id=day_off.resident_id,
        resident_name=resident.name,
        type_id=day_off.type_id,
        type_name=day_off_type.name,
        type_color=day_off_type.color,
        start_date=day_off.start_date,
        end_date=day_off.end_date,
        notes=day_off.notes,
        source=day_off.source.value if day_off.source else None,
        approved_by=day_off.approved_by,
    )


@router.put("/{day_off_id}", response_model=DayOffResponse)
async def update_day_off(
    day_off_id: int,
    data: DayOffUpdate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing day off entry."""
    service = DaysOffService(db)
    day_off = await service.update_day_off(
        day_off_id=day_off_id,
        admin_id=admin.id,
        type_id=data.type_id,
        start_date=data.start_date,
        end_date=data.end_date,
        notes=data.notes,
    )

    if not day_off:
        raise HTTPException(status_code=404, detail="Day off not found")

    # Get related info
    result = await db.execute(
        select(Resident).where(Resident.id == day_off.resident_id)
    )
    resident = result.scalar_one()

    result = await db.execute(
        select(DayOffType).where(DayOffType.id == day_off.type_id)
    )
    day_off_type = result.scalar_one()

    return DayOffResponse(
        id=day_off.id,
        resident_id=day_off.resident_id,
        resident_name=resident.name,
        type_id=day_off.type_id,
        type_name=day_off_type.name,
        type_color=day_off_type.color,
        start_date=day_off.start_date,
        end_date=day_off.end_date,
        notes=day_off.notes,
        source=day_off.source.value if day_off.source else None,
        approved_by=day_off.approved_by,
    )


@router.delete("/{day_off_id}")
async def delete_day_off(
    day_off_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a day off entry."""
    service = DaysOffService(db)
    deleted = await service.delete_day_off(day_off_id, admin.id)

    if not deleted:
        raise HTTPException(status_code=404, detail="Day off not found")

    return {"status": "deleted"}


# ============== CSV Operations ==============

@router.get("/template", response_class=PlainTextResponse)
async def download_csv_template(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Download the CSV template for days off upload."""
    service = DaysOffService(db)
    template = service.generate_csv_template()
    return PlainTextResponse(
        content=template,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=days_off_template.csv"
        }
    )


@router.post("/preview-csv")
async def preview_csv_upload(
    data: CSVUploadRequest,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Preview a CSV upload without importing.

    Returns parsed entries with validation results.
    """
    service = DaysOffService(db)
    result = await service.parse_csv(data.content)

    return {
        "preview": True,
        "total_rows": len(result.entries),
        "valid_rows": len([e for e in result.entries if not e.error]),
        "entries": [
            {
                "resident_name": e.resident_name,
                "start_date": e.start_date.isoformat(),
                "end_date": e.end_date.isoformat(),
                "type": e.day_off_type,
                "notes": e.notes,
                "error": e.error,
                "valid": not e.error,
            }
            for e in result.entries
        ],
        "errors": result.errors,
        "warnings": result.warnings,
    }


@router.post("/upload-csv")
async def upload_csv(
    data: CSVUploadRequest,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Import days off from CSV content.

    The CSV should have columns: resident_name, start_date, end_date, type, notes
    """
    service = DaysOffService(db)
    result = await service.import_csv(data.content, admin.id)
    return result


# ============== LLM Parsing ==============

@router.post("/parse-text")
async def parse_text(
    data: LLMParseRequest,
    preview_only: bool = Query(True, description="If true, only preview without importing"),
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Parse natural language text to extract days off using LLM.

    Examples:
    - "John Smith is off December 25-27 for vacation"
    - "Dr. Doe has a conference Feb 15"
    - "The following residents have vacation Jan 15-17: John Smith, Jane Doe"

    Set preview_only=false to import the parsed entries.
    """
    service = DaysOffService(db)

    if preview_only:
        result = await service.parse_text_with_llm(data.text)
        return {
            "preview": True,
            "total_parsed": len(result.entries),
            "entries": [
                {
                    "resident_name": e.resident_name,
                    "start_date": e.start_date.isoformat(),
                    "end_date": e.end_date.isoformat(),
                    "type": e.day_off_type,
                    "notes": e.notes,
                    "error": e.error,
                    "valid": not e.error,
                }
                for e in result.entries
            ],
            "errors": result.errors,
            "warnings": result.warnings,
        }
    else:
        result = await service.import_from_llm(data.text, admin.id)
        return result


# ============== Calendar View ==============

@router.get("/calendar")
async def get_days_off_calendar(
    start_date: date,
    end_date: date,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Get days off in a calendar-friendly format.

    Returns days off grouped by date for calendar display.
    """
    service = DaysOffService(db)
    days_off, _ = await service.get_days_off(
        start_date=start_date,
        end_date=end_date,
        limit=500,
    )

    # Get related info
    resident_ids = list(set(d.resident_id for d in days_off))
    type_ids = list(set(d.type_id for d in days_off))

    residents = {}
    if resident_ids:
        result = await db.execute(
            select(Resident).where(Resident.id.in_(resident_ids))
        )
        residents = {r.id: r for r in result.scalars()}

    types = {}
    if type_ids:
        result = await db.execute(
            select(DayOffType).where(DayOffType.id.in_(type_ids))
        )
        types = {t.id: t for t in result.scalars()}

    # Build calendar data
    from datetime import timedelta
    calendar = {}
    current = start_date
    while current <= end_date:
        calendar[current.isoformat()] = []
        current += timedelta(days=1)

    for d in days_off:
        resident = residents.get(d.resident_id)
        day_off_type = types.get(d.type_id)

        current = d.start_date
        while current <= d.end_date:
            date_key = current.isoformat()
            if date_key in calendar:
                calendar[date_key].append({
                    "id": d.id,
                    "resident_name": resident.name if resident else None,
                    "pgy_level": resident.pgy_level.value if resident else None,
                    "type": day_off_type.name if day_off_type else None,
                    "color": day_off_type.color if day_off_type else None,
                })
            current += timedelta(days=1)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": calendar,
    }
