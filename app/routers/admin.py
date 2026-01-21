"""
Admin API routes for managing schedules, residents, rotations, etc.
"""
from typing import List, Optional
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..database import get_db
from ..models import (
    Admin, Resident, Rotation, ScheduleAssignment, AcademicYear,
    DayOffType, DayOff, SwapRequest, AuditLog, PGYLevel, SwapStatus, DataSource
)
from ..schemas import (
    ResidentCreate, ResidentUpdate, ResidentResponse, ResidentListResponse,
    RotationCreate, RotationUpdate, RotationResponse,
    ScheduleAssignmentCreate, ScheduleAssignmentUpdate, ScheduleAssignmentResponse,
    AcademicYearCreate, AcademicYearResponse,
    DayOffTypeCreate, DayOffTypeResponse,
    DayOffCreate, DayOffResponse,
    AdminCreate, AdminResponse,
    AuditLogResponse,
    SwapRequestResponse, SwapApproval,
)
from .admin_auth import require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ============== Academic Years ==============

@router.get("/academic-years", response_model=List[AcademicYearResponse])
async def list_academic_years(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all academic years."""
    result = await db.execute(select(AcademicYear).order_by(AcademicYear.start_date.desc()))
    return result.scalars().all()


@router.post("/academic-years", response_model=AcademicYearResponse)
async def create_academic_year(
    data: AcademicYearCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new academic year."""
    # If setting as current, unset any existing current
    if data.is_current:
        result = await db.execute(select(AcademicYear).where(AcademicYear.is_current == True))
        for ay in result.scalars():
            ay.is_current = False

    academic_year = AcademicYear(**data.model_dump())
    db.add(academic_year)
    await db.flush()
    return academic_year


# ============== Residents ==============

@router.get("/residents", response_model=List[ResidentListResponse])
async def list_residents(
    pgy_level: Optional[PGYLevel] = None,
    active_only: bool = True,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all residents."""
    query = select(Resident)
    if pgy_level:
        query = query.where(Resident.pgy_level == pgy_level)
    if active_only:
        query = query.where(Resident.is_active == True)
    query = query.order_by(Resident.pgy_level, Resident.name)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/residents", response_model=ResidentResponse)
async def create_resident(
    data: ResidentCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new resident."""
    resident = Resident(**data.model_dump())
    db.add(resident)
    await db.flush()

    # Audit log
    await _create_audit_log(db, admin.id, "resident_create", "resident", resident.id, None, data.model_dump())

    return resident


@router.get("/residents/{resident_id}", response_model=ResidentResponse)
async def get_resident(
    resident_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a specific resident."""
    result = await db.execute(select(Resident).where(Resident.id == resident_id))
    resident = result.scalar_one_or_none()
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")
    return resident


@router.put("/residents/{resident_id}", response_model=ResidentResponse)
async def update_resident(
    resident_id: int,
    data: ResidentUpdate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a resident."""
    result = await db.execute(select(Resident).where(Resident.id == resident_id))
    resident = result.scalar_one_or_none()
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")

    old_values = {"name": resident.name, "email": resident.email, "pgy_level": str(resident.pgy_level)}

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(resident, key, value)

    # Audit log
    await _create_audit_log(db, admin.id, "resident_update", "resident", resident_id, old_values, update_data)

    return resident


# ============== Rotations ==============

@router.get("/rotations", response_model=List[RotationResponse])
async def list_rotations(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all rotation types."""
    result = await db.execute(select(Rotation).order_by(Rotation.name))
    return result.scalars().all()


@router.post("/rotations", response_model=RotationResponse)
async def create_rotation(
    data: RotationCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new rotation type."""
    rotation = Rotation(**data.model_dump())
    db.add(rotation)
    await db.flush()
    return rotation


@router.put("/rotations/{rotation_id}", response_model=RotationResponse)
async def update_rotation(
    rotation_id: int,
    data: RotationUpdate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a rotation type."""
    result = await db.execute(select(Rotation).where(Rotation.id == rotation_id))
    rotation = result.scalar_one_or_none()
    if not rotation:
        raise HTTPException(status_code=404, detail="Rotation not found")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(rotation, key, value)

    return rotation


# ============== Schedule Assignments ==============

@router.get("/schedule", response_model=List[ScheduleAssignmentResponse])
async def get_schedule(
    resident_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get schedule assignments with optional filters."""
    query = select(ScheduleAssignment)

    if resident_id:
        query = query.where(ScheduleAssignment.resident_id == resident_id)
    if start_date:
        query = query.where(ScheduleAssignment.week_end >= start_date)
    if end_date:
        query = query.where(ScheduleAssignment.week_start <= end_date)

    query = query.order_by(ScheduleAssignment.week_start, ScheduleAssignment.resident_id)
    result = await db.execute(query)
    return result.scalars().all()


@router.put("/schedule/assignment", response_model=ScheduleAssignmentResponse)
async def update_schedule_assignment(
    resident_id: int,
    week_start: date,
    data: ScheduleAssignmentUpdate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update a single schedule assignment (for grid editing)."""
    result = await db.execute(
        select(ScheduleAssignment).where(
            ScheduleAssignment.resident_id == resident_id,
            ScheduleAssignment.week_start == week_start
        )
    )
    assignment = result.scalar_one_or_none()

    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    old_rotation_id = assignment.rotation_id
    assignment.rotation_id = data.rotation_id

    # Audit log
    await _create_audit_log(
        db, admin.id, "schedule_edit", "schedule_assignment", assignment.id,
        {"rotation_id": old_rotation_id},
        {"rotation_id": data.rotation_id}
    )

    return assignment


# ============== Day Off Types ==============

@router.get("/day-off-types", response_model=List[DayOffTypeResponse])
async def list_day_off_types(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all day off types."""
    result = await db.execute(select(DayOffType).order_by(DayOffType.name))
    return result.scalars().all()


@router.post("/day-off-types", response_model=DayOffTypeResponse)
async def create_day_off_type(
    data: DayOffTypeCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new day off type."""
    day_off_type = DayOffType(**data.model_dump())
    db.add(day_off_type)
    await db.flush()
    return day_off_type


# ============== Days Off ==============

@router.get("/days-off", response_model=List[DayOffResponse])
async def list_days_off(
    resident_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List days off with optional filters."""
    query = select(DayOff)

    if resident_id:
        query = query.where(DayOff.resident_id == resident_id)
    if start_date:
        query = query.where(DayOff.end_date >= start_date)
    if end_date:
        query = query.where(DayOff.start_date <= end_date)

    query = query.order_by(DayOff.start_date)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/days-off", response_model=DayOffResponse)
async def create_day_off(
    data: DayOffCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new day off entry."""
    day_off = DayOff(
        **data.model_dump(),
        source=DataSource.MANUAL,
        approved_by=admin.id,
    )
    db.add(day_off)
    await db.flush()

    await _create_audit_log(db, admin.id, "day_off_create", "day_off", day_off.id, None, data.model_dump())

    return day_off


@router.delete("/days-off/{day_off_id}")
async def delete_day_off(
    day_off_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a day off entry."""
    result = await db.execute(select(DayOff).where(DayOff.id == day_off_id))
    day_off = result.scalar_one_or_none()
    if not day_off:
        raise HTTPException(status_code=404, detail="Day off not found")

    await _create_audit_log(
        db, admin.id, "day_off_delete", "day_off", day_off_id,
        {"resident_id": day_off.resident_id, "start_date": str(day_off.start_date)},
        None
    )

    await db.delete(day_off)
    return {"status": "deleted"}


# ============== Swap Requests ==============

@router.get("/swaps", response_model=List[SwapRequestResponse])
async def list_swap_requests(
    status: Optional[SwapStatus] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List swap requests (default: pending approval)."""
    query = select(SwapRequest)

    if status:
        query = query.where(SwapRequest.status == status)
    else:
        # Default to showing peer-confirmed swaps awaiting admin approval
        query = query.where(SwapRequest.status == SwapStatus.PEER_CONFIRMED)

    query = query.order_by(SwapRequest.created_at.desc())
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/swaps/{swap_id}/approve")
async def approve_swap(
    swap_id: int,
    data: SwapApproval,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Approve a swap request."""
    result = await db.execute(
        select(SwapRequest)
        .where(SwapRequest.id == swap_id)
        .options(
            selectinload(SwapRequest.requester_assignment),
            selectinload(SwapRequest.target_assignment)
        )
    )
    swap = result.scalar_one_or_none()

    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    if swap.status != SwapStatus.PEER_CONFIRMED:
        raise HTTPException(status_code=400, detail="Swap must be peer-confirmed before admin approval")

    # Perform the swap
    requester_rotation_id = swap.requester_assignment.rotation_id
    target_rotation_id = swap.target_assignment.rotation_id

    swap.requester_assignment.rotation_id = target_rotation_id
    swap.target_assignment.rotation_id = requester_rotation_id

    # Update swap status
    swap.status = SwapStatus.APPROVED
    swap.admin_reviewed_by = admin.id
    swap.admin_reviewed_at = func.now()
    swap.admin_note = data.admin_note

    await _create_audit_log(
        db, admin.id, "swap_approve", "swap_request", swap_id,
        {"status": "peer_confirmed"},
        {"status": "approved", "admin_note": data.admin_note}
    )

    return {"status": "approved", "swap_id": swap_id}


@router.post("/swaps/{swap_id}/reject")
async def reject_swap(
    swap_id: int,
    data: SwapApproval,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reject a swap request."""
    result = await db.execute(select(SwapRequest).where(SwapRequest.id == swap_id))
    swap = result.scalar_one_or_none()

    if not swap:
        raise HTTPException(status_code=404, detail="Swap request not found")

    swap.status = SwapStatus.REJECTED
    swap.admin_reviewed_by = admin.id
    swap.admin_reviewed_at = func.now()
    swap.admin_note = data.admin_note

    await _create_audit_log(
        db, admin.id, "swap_reject", "swap_request", swap_id,
        {"status": str(swap.status)},
        {"status": "rejected", "admin_note": data.admin_note}
    )

    return {"status": "rejected", "swap_id": swap_id}


# ============== Admin Management ==============

@router.get("/admins", response_model=List[AdminResponse])
async def list_admins(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all admin users."""
    result = await db.execute(select(Admin).order_by(Admin.email))
    return result.scalars().all()


@router.post("/admins/invite", response_model=AdminResponse)
async def invite_admin(
    data: AdminCreate,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Invite a new admin user."""
    # Check if already exists
    result = await db.execute(select(Admin).where(Admin.email == data.email.lower()))
    existing = result.scalar_one_or_none()

    if existing:
        if existing.is_active:
            raise HTTPException(status_code=400, detail="Admin with this email already exists")
        else:
            # Reactivate
            existing.is_active = True
            existing.name = data.name
            return existing

    new_admin = Admin(
        email=data.email.lower(),
        name=data.name,
        is_active=True,
    )
    db.add(new_admin)
    await db.flush()

    await _create_audit_log(db, admin.id, "admin_invite", "admin", new_admin.id, None, {"email": data.email})

    return new_admin


# ============== Audit Log ==============

@router.get("/audit-log", response_model=List[AuditLogResponse])
async def get_audit_log(
    limit: int = 100,
    offset: int = 0,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get audit log entries."""
    result = await db.execute(
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


# ============== Helper Functions ==============

async def _create_audit_log(
    db: AsyncSession,
    admin_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    old_value: dict | None,
    new_value: dict | None,
):
    """Create an audit log entry."""
    # Convert date objects to strings for JSON serialization
    if old_value:
        old_value = _serialize_dict(old_value)
    if new_value:
        new_value = _serialize_dict(new_value)

    log = AuditLog(
        admin_id=admin_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(log)


def _serialize_dict(d: dict) -> dict:
    """Serialize dict values for JSON storage."""
    result = {}
    for k, v in d.items():
        if isinstance(v, date):
            result[k] = v.isoformat()
        elif hasattr(v, 'value'):  # Enum
            result[k] = v.value
        else:
            result[k] = v
    return result
