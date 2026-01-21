"""
Pydantic schemas for API request/response validation.
"""
from datetime import datetime, date, time
from typing import Optional, List
from pydantic import BaseModel, EmailStr, Field
from enum import Enum


# Enums
class PGYLevel(str, Enum):
    TY = "TY"
    PGY1 = "PGY1"
    PGY2 = "PGY2"
    PGY3 = "PGY3"


class SwapStatus(str, Enum):
    PENDING = "pending"
    PEER_CONFIRMED = "peer_confirmed"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


# Base schemas
class AcademicYearBase(BaseModel):
    name: str
    start_date: date
    end_date: date


class AcademicYearCreate(AcademicYearBase):
    is_current: bool = False


class AcademicYearResponse(AcademicYearBase):
    id: int
    is_current: bool
    created_at: datetime

    class Config:
        from_attributes = True


# Resident schemas
class ResidentBase(BaseModel):
    name: str
    email: Optional[EmailStr] = None
    pgy_level: PGYLevel


class ResidentCreate(ResidentBase):
    academic_year_id: Optional[int] = None


class ResidentUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    pgy_level: Optional[PGYLevel] = None
    is_active: Optional[bool] = None


class ResidentResponse(ResidentBase):
    id: int
    calendar_token: str
    academic_year_id: Optional[int]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class ResidentListResponse(BaseModel):
    id: int
    name: str
    pgy_level: PGYLevel
    is_active: bool

    class Config:
        from_attributes = True


# Rotation schemas
class RotationBase(BaseModel):
    name: str
    display_name: Optional[str] = None
    color: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    is_overnight: bool = False
    weekdays_only: bool = False
    generates_events: bool = True


class RotationCreate(RotationBase):
    pass


class RotationUpdate(BaseModel):
    display_name: Optional[str] = None
    color: Optional[str] = None
    location: Optional[str] = None
    start_time: Optional[time] = None
    end_time: Optional[time] = None
    is_overnight: Optional[bool] = None
    weekdays_only: Optional[bool] = None
    generates_events: Optional[bool] = None


class RotationResponse(RotationBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# Schedule assignment schemas
class ScheduleAssignmentBase(BaseModel):
    resident_id: int
    rotation_id: int
    week_start: date
    week_end: date


class ScheduleAssignmentCreate(ScheduleAssignmentBase):
    academic_year_id: Optional[int] = None


class ScheduleAssignmentUpdate(BaseModel):
    rotation_id: int


class ScheduleAssignmentResponse(ScheduleAssignmentBase):
    id: int
    academic_year_id: Optional[int]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ScheduleAssignmentWithDetails(BaseModel):
    id: int
    week_start: date
    week_end: date
    resident: ResidentListResponse
    rotation: RotationResponse

    class Config:
        from_attributes = True


# Attending schemas
class AttendingBase(BaseModel):
    name: str
    service: Optional[str] = None


class AttendingCreate(AttendingBase):
    pass


class AttendingResponse(AttendingBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AttendingAssignmentBase(BaseModel):
    attending_id: int
    service: str
    date: date


class AttendingAssignmentCreate(AttendingAssignmentBase):
    academic_year_id: Optional[int] = None


class AttendingAssignmentResponse(AttendingAssignmentBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class AttendingAssignmentWithDetails(BaseModel):
    id: int
    service: str
    date: date
    attending: AttendingResponse

    class Config:
        from_attributes = True


# Call assignment schemas
class CallAssignmentBase(BaseModel):
    resident_id: int
    call_type: str  # pre-call, on-call, post-call
    date: date
    service: Optional[str] = None
    location: Optional[str] = None


class CallAssignmentCreate(CallAssignmentBase):
    academic_year_id: Optional[int] = None


class CallAssignmentResponse(CallAssignmentBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# Day off schemas
class DayOffTypeBase(BaseModel):
    name: str
    color: Optional[str] = None


class DayOffTypeCreate(DayOffTypeBase):
    is_system: bool = False


class DayOffTypeResponse(DayOffTypeBase):
    id: int
    is_system: bool
    created_at: datetime

    class Config:
        from_attributes = True


class DayOffBase(BaseModel):
    resident_id: int
    type_id: int
    start_date: date
    end_date: date
    notes: Optional[str] = None


class DayOffCreate(DayOffBase):
    pass


class DayOffResponse(DayOffBase):
    id: int
    approved_by: Optional[int]
    approved_at: Optional[datetime]
    source: str
    created_at: datetime

    class Config:
        from_attributes = True


class DayOffWithDetails(BaseModel):
    id: int
    start_date: date
    end_date: date
    notes: Optional[str]
    resident: ResidentListResponse
    day_off_type: DayOffTypeResponse

    class Config:
        from_attributes = True


# Swap request schemas
class SwapRequestCreate(BaseModel):
    target_id: int
    requester_assignment_id: int
    target_assignment_id: int
    requester_note: Optional[str] = None


class SwapRequestResponse(BaseModel):
    id: int
    requester_id: int
    target_id: int
    requester_assignment_id: int
    target_assignment_id: int
    status: SwapStatus
    requester_note: Optional[str]
    peer_confirmed_at: Optional[datetime]
    admin_reviewed_by: Optional[int]
    admin_reviewed_at: Optional[datetime]
    admin_note: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SwapRequestWithDetails(BaseModel):
    id: int
    status: SwapStatus
    requester_note: Optional[str]
    admin_note: Optional[str]
    created_at: datetime
    requester: ResidentListResponse
    target: ResidentListResponse
    requester_assignment: ScheduleAssignmentWithDetails
    target_assignment: ScheduleAssignmentWithDetails

    class Config:
        from_attributes = True


class SwapApproval(BaseModel):
    admin_note: Optional[str] = None


# Admin schemas
class AdminBase(BaseModel):
    email: EmailStr
    name: Optional[str] = None


class AdminCreate(AdminBase):
    pass


class AdminResponse(AdminBase):
    id: int
    is_active: bool
    created_at: datetime
    last_login: Optional[datetime]

    class Config:
        from_attributes = True


class AdminLoginRequest(BaseModel):
    email: EmailStr


class MagicLinkVerifyResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    admin: AdminResponse


# Audit log schemas
class AuditLogResponse(BaseModel):
    id: int
    admin_id: Optional[int]
    action: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    old_value: Optional[dict]
    new_value: Optional[dict]
    created_at: datetime

    class Config:
        from_attributes = True


# Excel upload schemas
class ExcelUploadResponse(BaseModel):
    status: str
    residents_processed: int
    weeks_processed: int
    assignments_created: int
    errors: List[str] = []


# Days off CSV/LLM schemas
class DaysOffCSVRow(BaseModel):
    resident_name: str
    start_date: date
    end_date: date
    type: str
    notes: Optional[str] = None


class DaysOffParseRequest(BaseModel):
    text: str


class DaysOffParseResponse(BaseModel):
    parsed_entries: List[DaysOffCSVRow]
    confidence: float
    raw_response: Optional[str] = None


# Schedule grid schemas
class ScheduleGridCell(BaseModel):
    resident_id: int
    week_start: date
    rotation_id: Optional[int]
    rotation_name: Optional[str]


class ScheduleGridRow(BaseModel):
    resident: ResidentListResponse
    assignments: List[ScheduleGridCell]


class ScheduleGridResponse(BaseModel):
    weeks: List[dict]  # [{start: date, end: date, label: str}]
    rows: List[ScheduleGridRow]


# Calendar event schema (for API response, not ICS)
class CalendarEvent(BaseModel):
    title: str
    start: datetime
    end: datetime
    rotation: Optional[str] = None
    attending: Optional[str] = None
    call_status: Optional[str] = None
    location: Optional[str] = None
    color: Optional[str] = None
    is_day_off: bool = False
    day_off_type: Optional[str] = None


# Call schedule response
class CallScheduleEntry(BaseModel):
    resident: ResidentListResponse
    call_type: str
    service: Optional[str]
    location: Optional[str]


class CallScheduleResponse(BaseModel):
    date: date
    entries: List[CallScheduleEntry]
    attendings: List[AttendingAssignmentWithDetails]


# Health check response
class HealthCheckResponse(BaseModel):
    status: str
    version: str
    database: str
    scheduler: str
    timestamp: datetime


# Error response
class ErrorResponse(BaseModel):
    error: str
    message: str
    path: Optional[str] = None
    request_id: Optional[str] = None


# Success response
class SuccessResponse(BaseModel):
    status: str = "success"
    message: str
