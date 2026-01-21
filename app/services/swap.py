"""
Swap Request Service.

Handles the complete swap workflow:
1. Resident A requests swap
2. Resident B (target) confirms
3. Admin approves
4. Schedule gets updated

PGY Level Rules:
- TY/PGY1 can swap with TY/PGY1
- PGY2/PGY3 can swap with PGY2/PGY3
"""
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Resident, SwapRequest, SwapStatus, ScheduleAssignment,
    Rotation, AuditLog, PGYLevel
)


# PGY Level swap compatibility rules
PGY_SWAP_GROUPS = {
    PGYLevel.TY: {PGYLevel.TY, PGYLevel.PGY1},
    PGYLevel.PGY1: {PGYLevel.TY, PGYLevel.PGY1},
    PGYLevel.PGY2: {PGYLevel.PGY2, PGYLevel.PGY3},
    PGYLevel.PGY3: {PGYLevel.PGY2, PGYLevel.PGY3},
}


class SwapService:
    """Service for managing swap requests."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ============== Validation ==============

    def can_swap_pgy_levels(self, level1: PGYLevel, level2: PGYLevel) -> bool:
        """Check if two PGY levels can swap with each other."""
        allowed = PGY_SWAP_GROUPS.get(level1, set())
        return level2 in allowed

    async def validate_swap_request(
        self,
        requester_id: int,
        target_id: int,
        requester_assignment_id: int,
        target_assignment_id: int,
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate a swap request.

        Returns (is_valid, error_message).
        """
        # Can't swap with yourself
        if requester_id == target_id:
            return False, "Cannot swap with yourself"

        # Get residents
        result = await self.db.execute(
            select(Resident).where(Resident.id.in_([requester_id, target_id]))
        )
        residents = {r.id: r for r in result.scalars().all()}

        if requester_id not in residents:
            return False, "Requester not found"
        if target_id not in residents:
            return False, "Target resident not found"

        requester = residents[requester_id]
        target = residents[target_id]

        # Check PGY level compatibility
        if not self.can_swap_pgy_levels(requester.pgy_level, target.pgy_level):
            return False, f"PGY level mismatch: {requester.pgy_level.value} cannot swap with {target.pgy_level.value}"

        # Get assignments
        result = await self.db.execute(
            select(ScheduleAssignment).where(
                ScheduleAssignment.id.in_([requester_assignment_id, target_assignment_id])
            )
        )
        assignments = {a.id: a for a in result.scalars().all()}

        if requester_assignment_id not in assignments:
            return False, "Requester's assignment not found"
        if target_assignment_id not in assignments:
            return False, "Target's assignment not found"

        req_assignment = assignments[requester_assignment_id]
        tgt_assignment = assignments[target_assignment_id]

        # Verify ownership
        if req_assignment.resident_id != requester_id:
            return False, "Requester assignment does not belong to requester"
        if tgt_assignment.resident_id != target_id:
            return False, "Target assignment does not belong to target"

        # Check for existing pending swap
        result = await self.db.execute(
            select(SwapRequest).where(
                SwapRequest.requester_id == requester_id,
                SwapRequest.requester_assignment_id == requester_assignment_id,
                SwapRequest.status.in_([SwapStatus.PENDING, SwapStatus.PEER_CONFIRMED])
            )
        )
        if result.scalar_one_or_none():
            return False, "A pending swap request already exists for this assignment"

        return True, None

    # ============== Swap Creation ==============

    async def create_swap_request(
        self,
        requester_id: int,
        target_id: int,
        requester_assignment_id: int,
        target_assignment_id: int,
        requester_note: Optional[str] = None,
    ) -> SwapRequest:
        """
        Create a new swap request.

        The request starts in PENDING status, waiting for target confirmation.
        """
        # Validate first
        is_valid, error = await self.validate_swap_request(
            requester_id, target_id, requester_assignment_id, target_assignment_id
        )
        if not is_valid:
            raise ValueError(error)

        swap = SwapRequest(
            requester_id=requester_id,
            target_id=target_id,
            requester_assignment_id=requester_assignment_id,
            target_assignment_id=target_assignment_id,
            requester_note=requester_note,
            status=SwapStatus.PENDING,
        )
        self.db.add(swap)
        await self.db.flush()

        return swap

    # ============== Peer Actions ==============

    async def confirm_swap(self, swap_id: int, target_id: int) -> SwapRequest:
        """
        Target resident confirms the swap request.

        Moves status from PENDING to PEER_CONFIRMED.
        """
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            raise ValueError("Swap request not found")

        if swap.target_id != target_id:
            raise ValueError("You are not the target of this swap request")

        if swap.status != SwapStatus.PENDING:
            raise ValueError(f"Cannot confirm swap in {swap.status.value} status")

        swap.status = SwapStatus.PEER_CONFIRMED
        swap.peer_confirmed_at = datetime.utcnow()

        return swap

    async def decline_swap(self, swap_id: int, target_id: int) -> SwapRequest:
        """
        Target resident declines the swap request.

        Moves status to REJECTED.
        """
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            raise ValueError("Swap request not found")

        if swap.target_id != target_id:
            raise ValueError("You are not the target of this swap request")

        if swap.status != SwapStatus.PENDING:
            raise ValueError(f"Cannot decline swap in {swap.status.value} status")

        swap.status = SwapStatus.REJECTED

        return swap

    async def cancel_swap(self, swap_id: int, requester_id: int) -> SwapRequest:
        """
        Requester cancels their own swap request.

        Only works for PENDING or PEER_CONFIRMED status.
        """
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            raise ValueError("Swap request not found")

        if swap.requester_id != requester_id:
            raise ValueError("You are not the requester of this swap")

        if swap.status not in [SwapStatus.PENDING, SwapStatus.PEER_CONFIRMED]:
            raise ValueError(f"Cannot cancel swap in {swap.status.value} status")

        swap.status = SwapStatus.CANCELLED

        return swap

    # ============== Admin Actions ==============

    async def approve_swap(
        self,
        swap_id: int,
        admin_id: int,
        admin_note: Optional[str] = None,
    ) -> SwapRequest:
        """
        Admin approves a peer-confirmed swap.

        This also performs the actual schedule swap.
        """
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            raise ValueError("Swap request not found")

        if swap.status != SwapStatus.PEER_CONFIRMED:
            raise ValueError(f"Cannot approve swap in {swap.status.value} status. Must be peer_confirmed.")

        # Perform the actual schedule swap
        await self._execute_swap(swap)

        # Update swap status
        swap.status = SwapStatus.APPROVED
        swap.admin_reviewed_by = admin_id
        swap.admin_reviewed_at = datetime.utcnow()
        swap.admin_note = admin_note

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="swap_approve",
            entity_type="swap_request",
            entity_id=swap_id,
            old_value={"status": "peer_confirmed"},
            new_value={
                "status": "approved",
                "requester_id": swap.requester_id,
                "target_id": swap.target_id,
            },
        )
        self.db.add(audit)

        return swap

    async def reject_swap(
        self,
        swap_id: int,
        admin_id: int,
        admin_note: Optional[str] = None,
    ) -> SwapRequest:
        """
        Admin rejects a swap request.

        Can reject PENDING or PEER_CONFIRMED swaps.
        """
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            raise ValueError("Swap request not found")

        if swap.status not in [SwapStatus.PENDING, SwapStatus.PEER_CONFIRMED]:
            raise ValueError(f"Cannot reject swap in {swap.status.value} status")

        old_status = swap.status.value
        swap.status = SwapStatus.REJECTED
        swap.admin_reviewed_by = admin_id
        swap.admin_reviewed_at = datetime.utcnow()
        swap.admin_note = admin_note

        # Audit log
        audit = AuditLog(
            admin_id=admin_id,
            action="swap_reject",
            entity_type="swap_request",
            entity_id=swap_id,
            old_value={"status": old_status},
            new_value={"status": "rejected", "admin_note": admin_note},
        )
        self.db.add(audit)

        return swap

    async def _execute_swap(self, swap: SwapRequest):
        """
        Execute the actual schedule swap.

        Swaps the rotation_id between the two assignments.
        """
        # Get both assignments
        result = await self.db.execute(
            select(ScheduleAssignment).where(
                ScheduleAssignment.id.in_([
                    swap.requester_assignment_id,
                    swap.target_assignment_id
                ])
            )
        )
        assignments = {a.id: a for a in result.scalars().all()}

        req_assignment = assignments[swap.requester_assignment_id]
        tgt_assignment = assignments[swap.target_assignment_id]

        # Swap the rotation IDs
        req_rotation = req_assignment.rotation_id
        tgt_rotation = tgt_assignment.rotation_id

        req_assignment.rotation_id = tgt_rotation
        tgt_assignment.rotation_id = req_rotation

    # ============== Queries ==============

    async def get_swap_requests(
        self,
        resident_id: Optional[int] = None,
        status: Optional[SwapStatus] = None,
        as_requester: bool = True,
        as_target: bool = True,
        limit: int = 50,
    ) -> List[SwapRequest]:
        """Get swap requests with optional filters."""
        query = select(SwapRequest)

        conditions = []

        if resident_id:
            if as_requester and as_target:
                conditions.append(
                    or_(
                        SwapRequest.requester_id == resident_id,
                        SwapRequest.target_id == resident_id
                    )
                )
            elif as_requester:
                conditions.append(SwapRequest.requester_id == resident_id)
            elif as_target:
                conditions.append(SwapRequest.target_id == resident_id)

        if status:
            conditions.append(SwapRequest.status == status)

        if conditions:
            query = query.where(and_(*conditions))

        query = query.order_by(SwapRequest.created_at.desc()).limit(limit)

        result = await self.db.execute(query)
        return result.scalars().all()

    async def get_swap_with_details(self, swap_id: int) -> Optional[dict]:
        """Get a swap request with full resident and assignment details."""
        result = await self.db.execute(
            select(SwapRequest).where(SwapRequest.id == swap_id)
        )
        swap = result.scalar_one_or_none()

        if not swap:
            return None

        # Get residents
        result = await self.db.execute(
            select(Resident).where(
                Resident.id.in_([swap.requester_id, swap.target_id])
            )
        )
        residents = {r.id: r for r in result.scalars().all()}

        # Get assignments with rotations
        result = await self.db.execute(
            select(ScheduleAssignment, Rotation)
            .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
            .where(
                ScheduleAssignment.id.in_([
                    swap.requester_assignment_id,
                    swap.target_assignment_id
                ])
            )
        )
        assignments = {a.id: (a, r) for a, r in result.all()}

        req_assignment, req_rotation = assignments.get(swap.requester_assignment_id, (None, None))
        tgt_assignment, tgt_rotation = assignments.get(swap.target_assignment_id, (None, None))

        return {
            "id": swap.id,
            "status": swap.status.value,
            "requester": {
                "id": swap.requester_id,
                "name": residents[swap.requester_id].name if swap.requester_id in residents else None,
                "pgy_level": residents[swap.requester_id].pgy_level.value if swap.requester_id in residents else None,
            },
            "target": {
                "id": swap.target_id,
                "name": residents[swap.target_id].name if swap.target_id in residents else None,
                "pgy_level": residents[swap.target_id].pgy_level.value if swap.target_id in residents else None,
            },
            "requester_assignment": {
                "id": swap.requester_assignment_id,
                "rotation": req_rotation.name if req_rotation else None,
                "week_start": req_assignment.week_start.isoformat() if req_assignment else None,
                "week_end": req_assignment.week_end.isoformat() if req_assignment else None,
            } if req_assignment else None,
            "target_assignment": {
                "id": swap.target_assignment_id,
                "rotation": tgt_rotation.name if tgt_rotation else None,
                "week_start": tgt_assignment.week_start.isoformat() if tgt_assignment else None,
                "week_end": tgt_assignment.week_end.isoformat() if tgt_assignment else None,
            } if tgt_assignment else None,
            "requester_note": swap.requester_note,
            "admin_note": swap.admin_note,
            "peer_confirmed_at": swap.peer_confirmed_at.isoformat() if swap.peer_confirmed_at else None,
            "admin_reviewed_at": swap.admin_reviewed_at.isoformat() if swap.admin_reviewed_at else None,
            "created_at": swap.created_at.isoformat() if swap.created_at else None,
        }

    async def get_eligible_swap_targets(
        self,
        requester_id: int,
        assignment_id: int,
    ) -> List[dict]:
        """
        Get list of residents eligible to swap with for a given assignment.

        Returns residents with compatible PGY levels and their assignments
        for the same week.
        """
        # Get requester and their assignment
        result = await self.db.execute(
            select(Resident).where(Resident.id == requester_id)
        )
        requester = result.scalar_one_or_none()

        if not requester:
            return []

        result = await self.db.execute(
            select(ScheduleAssignment).where(ScheduleAssignment.id == assignment_id)
        )
        req_assignment = result.scalar_one_or_none()

        if not req_assignment:
            return []

        # Get compatible PGY levels
        compatible_levels = PGY_SWAP_GROUPS.get(requester.pgy_level, set())

        # Find other residents with compatible levels and same academic year
        result = await self.db.execute(
            select(Resident).where(
                Resident.id != requester_id,
                Resident.is_active == True,
                Resident.pgy_level.in_(compatible_levels),
                Resident.academic_year_id == requester.academic_year_id,
            )
        )
        eligible_residents = result.scalars().all()

        if not eligible_residents:
            return []

        # Get their assignments for the same week
        resident_ids = [r.id for r in eligible_residents]
        result = await self.db.execute(
            select(ScheduleAssignment, Rotation)
            .join(Rotation, ScheduleAssignment.rotation_id == Rotation.id)
            .where(
                ScheduleAssignment.resident_id.in_(resident_ids),
                ScheduleAssignment.week_start == req_assignment.week_start,
            )
        )
        assignments = {a.resident_id: (a, r) for a, r in result.all()}

        return [
            {
                "resident_id": r.id,
                "resident_name": r.name,
                "pgy_level": r.pgy_level.value,
                "assignment_id": assignments[r.id][0].id if r.id in assignments else None,
                "rotation": assignments[r.id][1].name if r.id in assignments else None,
                "week_start": assignments[r.id][0].week_start.isoformat() if r.id in assignments else None,
            }
            for r in eligible_residents
            if r.id in assignments
        ]
