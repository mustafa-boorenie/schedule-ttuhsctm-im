"""
Swap Request API routes.

Includes both resident-facing and admin endpoints.
"""
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from ..database import get_db
from ..models import Admin, Resident, SwapRequest, SwapStatus, ScheduleAssignment, Rotation
from ..services.swap import SwapService
from ..services.validation import ValidationError, as_validation_response
from ..services.resident_lookup import get_resident_by_email
from .admin_auth import require_admin

router = APIRouter(tags=["swaps"])


# ============== Schemas ==============

class SwapRequestCreate(BaseModel):
    target_id: int
    requester_assignment_id: int
    target_assignment_id: int
    note: Optional[str] = None


class SwapRequestResponse(BaseModel):
    id: int
    requester_id: int
    requester_name: Optional[str] = None
    requester_pgy: Optional[str] = None
    target_id: int
    target_name: Optional[str] = None
    target_pgy: Optional[str] = None
    requester_assignment_id: int
    requester_rotation: Optional[str] = None
    requester_week: Optional[str] = None
    target_assignment_id: int
    target_rotation: Optional[str] = None
    target_week: Optional[str] = None
    status: str
    requester_note: Optional[str] = None
    admin_note: Optional[str] = None
    peer_confirmed_at: Optional[str] = None
    admin_reviewed_at: Optional[str] = None
    created_at: Optional[str] = None


class EligibleTargetResponse(BaseModel):
    resident_id: int
    resident_name: str
    pgy_level: str
    assignment_id: Optional[int]
    rotation: Optional[str]
    week_start: Optional[str]


# ============== Helper: Get Resident from Token ==============

async def get_resident_from_email(
    email: str,
    db: AsyncSession,
) -> Resident:
    """Get a resident by their email (exact or fuzzy match)."""
    try:
        return await get_resident_by_email(db, email)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ============== Resident API ==============

@router.post("/api/swaps", response_model=SwapRequestResponse)
async def create_swap_request(
    data: SwapRequestCreate,
    email: str = Query(..., description="Resident email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new swap request.

    The request will be in PENDING status until the target confirms.
    """
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    try:
        swap = await service.create_swap_request(
            requester_id=resident.id,
            target_id=data.target_id,
            requester_assignment_id=data.requester_assignment_id,
            target_assignment_id=data.target_assignment_id,
            requester_note=data.note,
        )
        details = await service.get_swap_with_details(swap.id)
        return _format_swap_response(details)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/swaps/mine", response_model=List[SwapRequestResponse])
async def get_my_swap_requests(
    email: str = Query(..., description="Resident email"),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get swap requests I initiated."""
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    status_filter = SwapStatus(status) if status else None

    swaps = await service.get_swap_requests(
        resident_id=resident.id,
        status=status_filter,
        as_requester=True,
        as_target=False,
    )

    return await _format_swap_list(swaps, db)


@router.get("/api/swaps/incoming", response_model=List[SwapRequestResponse])
async def get_incoming_swap_requests(
    email: str = Query(..., description="Resident email"),
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get swap requests where I am the target."""
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    status_filter = SwapStatus(status) if status else None

    swaps = await service.get_swap_requests(
        resident_id=resident.id,
        status=status_filter,
        as_requester=False,
        as_target=True,
    )

    return await _format_swap_list(swaps, db)


@router.post("/api/swaps/{swap_id}/confirm")
async def confirm_swap_request(
    swap_id: int,
    email: str = Query(..., description="Resident email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Confirm a swap request (as the target resident).

    Moves the swap to PEER_CONFIRMED status.
    """
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    try:
        swap = await service.confirm_swap(swap_id, resident.id)
        return {"status": "confirmed", "swap_id": swap.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/swaps/{swap_id}/decline")
async def decline_swap_request(
    swap_id: int,
    email: str = Query(..., description="Resident email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Decline a swap request (as the target resident).

    Moves the swap to REJECTED status.
    """
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    try:
        swap = await service.decline_swap(swap_id, resident.id)
        return {"status": "declined", "swap_id": swap.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/swaps/{swap_id}/cancel")
async def cancel_swap_request(
    swap_id: int,
    email: str = Query(..., description="Resident email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Cancel my own swap request.

    Only works for PENDING or PEER_CONFIRMED swaps.
    """
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    try:
        swap = await service.cancel_swap(swap_id, resident.id)
        return {"status": "cancelled", "swap_id": swap.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/swaps/eligible-targets", response_model=List[EligibleTargetResponse])
async def get_eligible_swap_targets(
    assignment_id: int,
    email: str = Query(..., description="Resident email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Get list of residents eligible to swap with for a given assignment.

    Returns residents with compatible PGY levels who have assignments
    for the same week.
    """
    resident = await get_resident_from_email(email, db)

    service = SwapService(db)
    targets = await service.get_eligible_swap_targets(resident.id, assignment_id)

    return targets


# ============== Admin API ==============

@router.get("/api/admin/swaps", response_model=List[SwapRequestResponse])
async def admin_list_swaps(
    status: Optional[str] = None,
    limit: int = Query(50, le=200),
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    List all swap requests (admin).

    Filter by status to see pending, peer_confirmed, etc.
    """
    service = SwapService(db)
    status_filter = SwapStatus(status) if status else None

    swaps = await service.get_swap_requests(
        status=status_filter,
        limit=limit,
    )

    return await _format_swap_list(swaps, db)


@router.get("/api/admin/swaps/pending", response_model=List[SwapRequestResponse])
async def admin_list_pending_swaps(
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    List swaps awaiting admin approval (peer_confirmed status).
    """
    service = SwapService(db)

    swaps = await service.get_swap_requests(
        status=SwapStatus.PEER_CONFIRMED,
    )

    return await _format_swap_list(swaps, db)


@router.get("/api/admin/swaps/{swap_id}")
async def admin_get_swap_details(
    swap_id: int,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get detailed information about a swap request."""
    service = SwapService(db)
    details = await service.get_swap_with_details(swap_id)

    if not details:
        raise HTTPException(status_code=404, detail="Swap request not found")

    return details


@router.post("/api/admin/swaps/{swap_id}/approve")
async def admin_approve_swap(
    swap_id: int,
    note: Optional[str] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Approve a peer-confirmed swap request.

    This will execute the schedule swap.
    """
    service = SwapService(db)
    try:
        swap = await service.approve_swap(swap_id, admin.id, note)
        return {
            "status": "approved",
            "swap_id": swap.id,
            "message": "Swap approved and schedule updated",
        }
    except ValidationError as ve:
        return JSONResponse(status_code=400, content=as_validation_response(ve))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/admin/swaps/{swap_id}/reject")
async def admin_reject_swap(
    swap_id: int,
    note: Optional[str] = None,
    admin: Admin = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reject a swap request."""
    service = SwapService(db)
    try:
        swap = await service.reject_swap(swap_id, admin.id, note)
        return {"status": "rejected", "swap_id": swap.id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============== Helper Functions ==============

def _format_swap_response(details: dict) -> SwapRequestResponse:
    """Format swap details into response model."""
    return SwapRequestResponse(
        id=details["id"],
        requester_id=details["requester"]["id"],
        requester_name=details["requester"]["name"],
        requester_pgy=details["requester"]["pgy_level"],
        target_id=details["target"]["id"],
        target_name=details["target"]["name"],
        target_pgy=details["target"]["pgy_level"],
        requester_assignment_id=details["requester_assignment"]["id"] if details["requester_assignment"] else 0,
        requester_rotation=details["requester_assignment"]["rotation"] if details["requester_assignment"] else None,
        requester_week=details["requester_assignment"]["week_start"] if details["requester_assignment"] else None,
        target_assignment_id=details["target_assignment"]["id"] if details["target_assignment"] else 0,
        target_rotation=details["target_assignment"]["rotation"] if details["target_assignment"] else None,
        target_week=details["target_assignment"]["week_start"] if details["target_assignment"] else None,
        status=details["status"],
        requester_note=details["requester_note"],
        admin_note=details["admin_note"],
        peer_confirmed_at=details["peer_confirmed_at"],
        admin_reviewed_at=details["admin_reviewed_at"],
        created_at=details["created_at"],
    )


async def _format_swap_list(swaps: List[SwapRequest], db: AsyncSession) -> List[SwapRequestResponse]:
    """Format a list of swaps with full details."""
    if not swaps:
        return []

    # Get all related data
    resident_ids = set()
    assignment_ids = set()
    for swap in swaps:
        resident_ids.add(swap.requester_id)
        resident_ids.add(swap.target_id)
        assignment_ids.add(swap.requester_assignment_id)
        assignment_ids.add(swap.target_assignment_id)

    # Get residents
    result = await db.execute(
        select(Resident).where(Resident.id.in_(resident_ids))
    )
    residents = {r.id: r for r in result.scalars().all()}

    # Get assignments with rotations
    result = await db.execute(
        select(ScheduleAssignment, Rotation)
        .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
        .where(ScheduleAssignment.id.in_(assignment_ids))
    )
    assignments = {a.id: (a, r) for a, r in result.all()}

    responses = []
    for swap in swaps:
        req_res = residents.get(swap.requester_id)
        tgt_res = residents.get(swap.target_id)
        req_asgn, req_rot = assignments.get(swap.requester_assignment_id, (None, None))
        tgt_asgn, tgt_rot = assignments.get(swap.target_assignment_id, (None, None))

        responses.append(SwapRequestResponse(
            id=swap.id,
            requester_id=swap.requester_id,
            requester_name=req_res.name if req_res else None,
            requester_pgy=req_res.pgy_level.value if req_res else None,
            target_id=swap.target_id,
            target_name=tgt_res.name if tgt_res else None,
            target_pgy=tgt_res.pgy_level.value if tgt_res else None,
            requester_assignment_id=swap.requester_assignment_id,
            requester_rotation=req_rot.name if req_rot else None,
            requester_week=req_asgn.week_start.isoformat() if req_asgn else None,
            target_assignment_id=swap.target_assignment_id,
            target_rotation=tgt_rot.name if tgt_rot else None,
            target_week=tgt_asgn.week_start.isoformat() if tgt_asgn else None,
            status=swap.status.value,
            requester_note=swap.requester_note,
            admin_note=swap.admin_note,
            peer_confirmed_at=swap.peer_confirmed_at.isoformat() if swap.peer_confirmed_at else None,
            admin_reviewed_at=swap.admin_reviewed_at.isoformat() if swap.admin_reviewed_at else None,
            created_at=swap.created_at.isoformat() if swap.created_at else None,
        ))

    return responses
