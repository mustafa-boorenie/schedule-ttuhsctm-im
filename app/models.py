"""
SQLAlchemy database models.
"""
from datetime import datetime, date, time
from typing import Optional, List
from uuid import uuid4

from sqlalchemy import (
    String, Integer, Boolean, Date, Time, DateTime, Text, ForeignKey,
    UniqueConstraint, Index, Enum as SQLEnum
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from .database import Base


# Enums
class PGYLevel(str, enum.Enum):
    TY = "TY"
    PGY1 = "PGY1"
    PGY2 = "PGY2"
    PGY3 = "PGY3"


class SwapStatus(str, enum.Enum):
    PENDING = "pending"
    PEER_CONFIRMED = "peer_confirmed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class DataSource(str, enum.Enum):
    MANUAL = "manual"
    EXCEL = "excel"
    AMION = "amion"
    CSV = "csv"
    LLM = "llm"


class SyncStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


# Models
class AcademicYear(Base):
    """Academic year (e.g., 2025-2026)."""
    __tablename__ = "academic_years"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(20), nullable=False)  # e.g., "2025-2026"
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    residents: Mapped[List["Resident"]] = relationship(back_populates="academic_year")
    schedule_assignments: Mapped[List["ScheduleAssignment"]] = relationship(back_populates="academic_year")


class Resident(Base):
    """Resident in the program."""
    __tablename__ = "residents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    pgy_level: Mapped[PGYLevel] = mapped_column(SQLEnum(PGYLevel), nullable=False)
    calendar_token: Mapped[str] = mapped_column(
        UUID(as_uuid=False), default=lambda: str(uuid4()), unique=True
    )
    academic_year_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("academic_years.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    academic_year: Mapped[Optional["AcademicYear"]] = relationship(back_populates="residents")
    schedule_assignments: Mapped[List["ScheduleAssignment"]] = relationship(back_populates="resident")
    call_assignments: Mapped[List["CallAssignment"]] = relationship(back_populates="resident")
    days_off: Mapped[List["DayOff"]] = relationship(back_populates="resident")
    swap_requests_made: Mapped[List["SwapRequest"]] = relationship(
        back_populates="requester", foreign_keys="SwapRequest.requester_id"
    )
    swap_requests_received: Mapped[List["SwapRequest"]] = relationship(
        back_populates="target", foreign_keys="SwapRequest.target_id"
    )

    __table_args__ = (
        Index("ix_residents_name", "name"),
        Index("ix_residents_calendar_token", "calendar_token"),
    )


class Rotation(Base):
    """Rotation type (e.g., ICU, NIGHT, ED)."""
    __tablename__ = "rotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex color
    location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    start_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    end_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    is_overnight: Mapped[bool] = mapped_column(Boolean, default=False)
    weekdays_only: Mapped[bool] = mapped_column(Boolean, default=False)
    generates_events: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    schedule_assignments: Mapped[List["ScheduleAssignment"]] = relationship(back_populates="rotation")


class ScheduleAssignment(Base):
    """A resident's rotation assignment for a specific week."""
    __tablename__ = "schedule_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resident_id: Mapped[int] = mapped_column(Integer, ForeignKey("residents.id"), nullable=False)
    rotation_id: Mapped[int] = mapped_column(Integer, ForeignKey("rotations.id"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    academic_year_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("academic_years.id"), nullable=True
    )
    source: Mapped[DataSource] = mapped_column(SQLEnum(DataSource), default=DataSource.MANUAL)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    resident: Mapped["Resident"] = relationship(back_populates="schedule_assignments")
    rotation: Mapped["Rotation"] = relationship(back_populates="schedule_assignments")
    academic_year: Mapped[Optional["AcademicYear"]] = relationship(back_populates="schedule_assignments")

    __table_args__ = (
        UniqueConstraint("resident_id", "week_start", name="uq_resident_week"),
        Index("ix_schedule_assignments_week", "week_start", "week_end"),
    )


class Attending(Base):
    """Attending physician."""
    __tablename__ = "attendings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    service: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    assignments: Mapped[List["AttendingAssignment"]] = relationship(back_populates="attending")


class AttendingAssignment(Base):
    """Attending assignment to a service for a specific date."""
    __tablename__ = "attending_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    attending_id: Mapped[int] = mapped_column(Integer, ForeignKey("attendings.id"), nullable=False)
    service: Mapped[str] = mapped_column(String(50), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    academic_year_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("academic_years.id"), nullable=True
    )
    source: Mapped[DataSource] = mapped_column(SQLEnum(DataSource), default=DataSource.AMION)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    attending: Mapped["Attending"] = relationship(back_populates="assignments")

    __table_args__ = (
        UniqueConstraint("service", "date", name="uq_attending_service_date"),
        Index("ix_attending_assignments_date", "date"),
    )


class CallAssignment(Base):
    """Call assignment for a resident on a specific date."""
    __tablename__ = "call_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resident_id: Mapped[int] = mapped_column(Integer, ForeignKey("residents.id"), nullable=False)
    call_type: Mapped[str] = mapped_column(String(30), nullable=False)  # pre-call, on-call, post-call
    date: Mapped[date] = mapped_column(Date, nullable=False)
    service: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    location: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    academic_year_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("academic_years.id"), nullable=True
    )
    source: Mapped[DataSource] = mapped_column(SQLEnum(DataSource), default=DataSource.AMION)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    resident: Mapped["Resident"] = relationship(back_populates="call_assignments")

    __table_args__ = (
        UniqueConstraint("resident_id", "date", "call_type", name="uq_resident_date_call"),
        Index("ix_call_assignments_date", "date"),
    )


class DayOffType(Base):
    """Type of day off (Vacation, Sick, Conference, etc.)."""
    __tablename__ = "day_off_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    color: Mapped[Optional[str]] = mapped_column(String(7), nullable=True)  # Hex color
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    days_off: Mapped[List["DayOff"]] = relationship(back_populates="day_off_type")


class DayOff(Base):
    """Day(s) off for a resident."""
    __tablename__ = "days_off"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    resident_id: Mapped[int] = mapped_column(Integer, ForeignKey("residents.id"), nullable=False)
    type_id: Mapped[int] = mapped_column(Integer, ForeignKey("day_off_types.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("admins.id"), nullable=True)
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    source: Mapped[DataSource] = mapped_column(SQLEnum(DataSource), default=DataSource.MANUAL)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    resident: Mapped["Resident"] = relationship(back_populates="days_off")
    day_off_type: Mapped["DayOffType"] = relationship(back_populates="days_off")
    approved_by_admin: Mapped[Optional["Admin"]] = relationship(foreign_keys=[approved_by])

    __table_args__ = (
        Index("ix_days_off_dates", "start_date", "end_date"),
    )


class SwapRequest(Base):
    """Swap request between two residents."""
    __tablename__ = "swap_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requester_id: Mapped[int] = mapped_column(Integer, ForeignKey("residents.id"), nullable=False)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("residents.id"), nullable=False)
    requester_assignment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedule_assignments.id"), nullable=False
    )
    target_assignment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("schedule_assignments.id"), nullable=False
    )
    status: Mapped[SwapStatus] = mapped_column(SQLEnum(SwapStatus), default=SwapStatus.PENDING)
    requester_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    peer_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    admin_reviewed_by: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("admins.id"), nullable=True)
    admin_reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    admin_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    requester: Mapped["Resident"] = relationship(
        back_populates="swap_requests_made", foreign_keys=[requester_id]
    )
    target: Mapped["Resident"] = relationship(
        back_populates="swap_requests_received", foreign_keys=[target_id]
    )
    requester_assignment: Mapped["ScheduleAssignment"] = relationship(foreign_keys=[requester_assignment_id])
    target_assignment: Mapped["ScheduleAssignment"] = relationship(foreign_keys=[target_assignment_id])
    reviewed_by_admin: Mapped[Optional["Admin"]] = relationship(foreign_keys=[admin_reviewed_by])

    __table_args__ = (
        Index("ix_swap_requests_status", "status"),
    )


class Admin(Base):
    """Admin user (chief resident, program coordinator)."""
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    magic_links: Mapped[List["MagicLink"]] = relationship(back_populates="admin")
    audit_logs: Mapped[List["AuditLog"]] = relationship(back_populates="admin")


class MagicLink(Base):
    """Magic link for admin authentication."""
    __tablename__ = "magic_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[int] = mapped_column(Integer, ForeignKey("admins.id"), nullable=False)
    token: Mapped[str] = mapped_column(UUID(as_uuid=False), default=lambda: str(uuid4()), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    admin: Mapped["Admin"] = relationship(back_populates="magic_links")

    __table_args__ = (
        Index("ix_magic_links_token", "token"),
    )


class AuditLog(Base):
    """Audit log for admin actions."""
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("admins.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Relationships
    admin: Mapped[Optional["Admin"]] = relationship(back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_log_created_at", "created_at"),
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
    )


class AmionSyncLog(Base):
    """Log of Amion sync operations."""
    __tablename__ = "amion_sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sync_type: Mapped[str] = mapped_column(String(30), nullable=False)  # attendings, call_assignments
    status: Mapped[SyncStatus] = mapped_column(SQLEnum(SyncStatus), nullable=False)
    records_processed: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    errors: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
