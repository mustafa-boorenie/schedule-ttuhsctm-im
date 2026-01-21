# Test Coverage Analysis

## Executive Summary

The codebase currently has **extremely minimal test coverage** (~0.6%). Only 4 basic endpoint tests exist for health checks and page rendering. All business logic, services, and API endpoints are completely untested.

| Metric | Value |
|--------|-------|
| Total Python Lines | ~8,715 |
| Lines With Tests | ~55 |
| Test Coverage | **0.63%** |
| Test Files | 1 |
| Test Functions | 4 |
| Services Tested | 0/8 |
| Routers Tested | 0/6 |

---

## Current Test Coverage

### What's Tested (4 tests)
- `/api/health/live` - Liveness endpoint
- `/` - Root page renders HTML
- `/resident` - Resident portal renders HTML
- `/admin/login` - Admin login page renders HTML

### What's NOT Tested
- **All 8 services** (3,232 lines)
- **All 6 API routers** (2,607 lines)
- **All 13 database models** (353 lines)
- **All Pydantic schemas** (464 lines)
- **Middleware** (164 lines)
- **Configuration/Settings** (199 lines)

---

## Critical Areas Requiring Tests

### Priority 1: Core Business Logic Services

#### 1. `services/calendar.py` (481 lines) - **HIGH PRIORITY**
The calendar generation is the core value proposition of this application.

**Recommended Tests:**
```python
# tests/test_calendar_service.py

@pytest.mark.asyncio
async def test_generate_calendar_for_resident():
    """Test calendar generation includes proper ICS structure."""

@pytest.mark.asyncio
async def test_calendar_includes_rotation_events():
    """Test that rotation assignments appear as calendar events."""

@pytest.mark.asyncio
async def test_calendar_includes_call_events():
    """Test on-call, pre-call, post-call events are generated correctly."""

@pytest.mark.asyncio
async def test_calendar_includes_days_off():
    """Test days off appear as all-day events."""

def test_parse_time_valid_formats():
    """Test time parsing handles various formats."""

def test_parse_time_invalid_returns_none():
    """Test invalid time strings return None."""

@pytest.mark.asyncio
async def test_calendar_date_filtering():
    """Test start_date/end_date parameters filter events."""

@pytest.mark.asyncio
async def test_calendar_resident_not_found_raises():
    """Test ValueError raised for invalid resident_id."""
```

#### 2. `services/auth.py` (131 lines) - **HIGH PRIORITY (Security Critical)**
Authentication is security-critical and must have comprehensive tests.

**Recommended Tests:**
```python
# tests/test_auth_service.py

@pytest.mark.asyncio
async def test_get_admin_by_email_found():
    """Test finding admin by email."""

@pytest.mark.asyncio
async def test_get_admin_by_email_inactive_not_found():
    """Test inactive admins are not returned."""

@pytest.mark.asyncio
async def test_create_magic_link_invalidates_previous():
    """Test creating new magic link invalidates old ones."""

@pytest.mark.asyncio
async def test_magic_link_expires():
    """Test expired magic links are rejected."""

@pytest.mark.asyncio
async def test_magic_link_single_use():
    """Test magic links can only be used once."""

def test_create_access_token_contains_claims():
    """Test JWT contains required claims (sub, email, exp)."""

def test_verify_access_token_valid():
    """Test valid JWT is accepted."""

def test_verify_access_token_expired_rejected():
    """Test expired JWT is rejected."""

def test_verify_access_token_invalid_signature_rejected():
    """Test tampered JWT is rejected."""
```

#### 3. `services/swap.py` (517 lines) - **HIGH PRIORITY**
Complex workflow logic that requires thorough testing.

**Recommended Tests:**
```python
# tests/test_swap_service.py

# PGY Level Validation
def test_pgy_level_swap_ty_with_pgy1_allowed():
    """TY can swap with PGY1."""

def test_pgy_level_swap_pgy2_with_pgy3_allowed():
    """PGY2 can swap with PGY3."""

def test_pgy_level_swap_ty_with_pgy2_not_allowed():
    """TY cannot swap with PGY2."""

# Swap Request Validation
@pytest.mark.asyncio
async def test_cannot_swap_with_self():
    """Test swapping with yourself is rejected."""

@pytest.mark.asyncio
async def test_duplicate_pending_swap_rejected():
    """Test cannot create duplicate pending swap."""

# Swap Workflow
@pytest.mark.asyncio
async def test_swap_workflow_pending_to_confirmed():
    """Test target can confirm pending swap."""

@pytest.mark.asyncio
async def test_swap_workflow_confirmed_to_approved():
    """Test admin can approve confirmed swap."""

@pytest.mark.asyncio
async def test_swap_execute_swaps_rotation_ids():
    """Test approval actually swaps the rotation assignments."""

@pytest.mark.asyncio
async def test_only_target_can_confirm():
    """Test only the target resident can confirm a swap."""

@pytest.mark.asyncio
async def test_only_requester_can_cancel():
    """Test only the requester can cancel their swap."""
```

#### 4. `services/days_off.py` (707 lines) - **MEDIUM-HIGH PRIORITY**

**Recommended Tests:**
```python
# tests/test_days_off_service.py

# CSV Parsing
def test_generate_csv_template_has_headers():
    """Test template has required columns."""

@pytest.mark.asyncio
async def test_parse_csv_valid_data():
    """Test valid CSV is parsed correctly."""

@pytest.mark.asyncio
async def test_parse_csv_missing_columns_error():
    """Test missing required columns return error."""

@pytest.mark.asyncio
async def test_parse_csv_invalid_dates():
    """Test invalid date formats are flagged."""

@pytest.mark.asyncio
async def test_parse_csv_unknown_resident():
    """Test unknown resident names are flagged."""

# CRUD Operations
@pytest.mark.asyncio
async def test_create_day_off():
    """Test creating a day off entry."""

@pytest.mark.asyncio
async def test_update_day_off():
    """Test updating a day off entry."""

@pytest.mark.asyncio
async def test_delete_day_off():
    """Test deleting a day off entry."""

@pytest.mark.asyncio
async def test_get_days_off_with_filters():
    """Test filtering days off by resident, type, dates."""

# Fuzzy Matching
def test_fuzzy_match_name_exact():
    """Test exact name matches return correct result."""

def test_fuzzy_match_name_close():
    """Test close matches above threshold are found."""

def test_fuzzy_match_name_too_different():
    """Test names below threshold return None."""
```

### Priority 2: API Routers

#### 5. `routers/admin_auth.py` (141 lines) - **HIGH PRIORITY**

**Recommended Tests:**
```python
# tests/test_admin_auth_router.py

@pytest.mark.asyncio
async def test_request_magic_link_valid_email():
    """Test magic link request for valid admin email."""

@pytest.mark.asyncio
async def test_request_magic_link_unknown_email():
    """Test unknown email doesn't reveal user existence."""

@pytest.mark.asyncio
async def test_verify_magic_link_valid():
    """Test valid magic link logs user in."""

@pytest.mark.asyncio
async def test_verify_magic_link_expired():
    """Test expired magic link is rejected."""

@pytest.mark.asyncio
async def test_logout_clears_session():
    """Test logout invalidates session."""
```

#### 6. `routers/schedule.py` (530 lines) - **MEDIUM PRIORITY**

**Recommended Tests:**
```python
# tests/test_schedule_router.py

@pytest.mark.asyncio
async def test_get_residents_list():
    """Test listing all residents."""

@pytest.mark.asyncio
async def test_get_resident_schedule():
    """Test getting schedule for specific resident."""

@pytest.mark.asyncio
async def test_get_calendar_by_token():
    """Test ICS generation by calendar token."""

@pytest.mark.asyncio
async def test_get_calendar_invalid_token():
    """Test 404 for invalid calendar token."""
```

#### 7. `routers/swap.py` (417 lines) - **MEDIUM PRIORITY**

**Recommended Tests:**
```python
# tests/test_swap_router.py

@pytest.mark.asyncio
async def test_create_swap_request():
    """Test creating a swap request via API."""

@pytest.mark.asyncio
async def test_confirm_swap_request():
    """Test confirming swap via API."""

@pytest.mark.asyncio
async def test_admin_approve_swap():
    """Test admin approval endpoint."""

@pytest.mark.asyncio
async def test_get_eligible_targets():
    """Test getting eligible swap partners."""
```

### Priority 3: Models and Schemas

#### 8. `models.py` (353 lines)

**Recommended Tests:**
```python
# tests/test_models.py

def test_resident_calendar_token_auto_generated():
    """Test calendar token is auto-generated."""

def test_pgy_level_enum_values():
    """Test PGY level enum contains expected values."""

def test_swap_status_enum_values():
    """Test swap status enum has all workflow states."""

@pytest.mark.asyncio
async def test_schedule_assignment_unique_constraint():
    """Test resident can only have one assignment per week."""
```

#### 9. `schemas.py` (464 lines)

**Recommended Tests:**
```python
# tests/test_schemas.py

def test_resident_schema_validation():
    """Test resident schema validates correctly."""

def test_schedule_assignment_schema():
    """Test schedule assignment schema."""

def test_swap_request_schema_status_values():
    """Test swap request status validation."""
```

### Priority 4: Supporting Infrastructure

#### 10. `middleware.py` (164 lines)

**Recommended Tests:**
```python
# tests/test_middleware.py

@pytest.mark.asyncio
async def test_security_headers_added():
    """Test security headers are present in responses."""

@pytest.mark.asyncio
async def test_rate_limiting():
    """Test rate limiting middleware."""
```

#### 11. `services/excel_import.py` (425 lines)

**Recommended Tests:**
```python
# tests/test_excel_import.py

@pytest.mark.asyncio
async def test_parse_excel_valid_file():
    """Test parsing valid Excel file."""

@pytest.mark.asyncio
async def test_parse_excel_invalid_format():
    """Test handling of invalid Excel format."""
```

---

## Test Infrastructure Recommendations

### 1. Create `conftest.py` with Shared Fixtures

```python
# tests/conftest.py
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from httpx import AsyncClient, ASGITransport

from app.database import Base
from app.main import app

# Test database
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

@pytest_asyncio.fixture
async def db_engine():
    """Create test database engine."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create test database session."""
    async_session = sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with async_session() as session:
        yield session
        await session.rollback()

@pytest_asyncio.fixture
async def client():
    """Create test HTTP client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

@pytest_asyncio.fixture
async def test_resident(db_session):
    """Create a test resident."""
    from app.models import Resident, PGYLevel
    resident = Resident(
        name="Test Resident",
        email="test@example.com",
        pgy_level=PGYLevel.PGY1,
    )
    db_session.add(resident)
    await db_session.commit()
    return resident

@pytest_asyncio.fixture
async def test_admin(db_session):
    """Create a test admin."""
    from app.models import Admin
    admin = Admin(
        email="admin@example.com",
        name="Test Admin",
        is_active=True,
    )
    db_session.add(admin)
    await db_session.commit()
    return admin
```

### 2. Add Test Factories

```python
# tests/factories.py
from datetime import date, timedelta
from app.models import (
    Resident, Rotation, ScheduleAssignment,
    PGYLevel, SwapRequest, SwapStatus
)

class ResidentFactory:
    @staticmethod
    def create(db_session, **kwargs):
        defaults = {
            "name": "Test Resident",
            "pgy_level": PGYLevel.PGY1,
        }
        defaults.update(kwargs)
        resident = Resident(**defaults)
        db_session.add(resident)
        return resident

class RotationFactory:
    @staticmethod
    def create(db_session, **kwargs):
        defaults = {
            "name": "Test Rotation",
        }
        defaults.update(kwargs)
        rotation = Rotation(**defaults)
        db_session.add(rotation)
        return rotation

class ScheduleAssignmentFactory:
    @staticmethod
    def create(db_session, resident, rotation, **kwargs):
        defaults = {
            "week_start": date.today(),
            "week_end": date.today() + timedelta(days=6),
        }
        defaults.update(kwargs)
        assignment = ScheduleAssignment(
            resident_id=resident.id,
            rotation_id=rotation.id,
            **defaults
        )
        db_session.add(assignment)
        return assignment
```

### 3. Mock External Services

```python
# tests/mocks.py
from unittest.mock import AsyncMock

def mock_email_service():
    """Mock email service for testing."""
    mock = AsyncMock()
    mock.send_magic_link.return_value = True
    return mock

def mock_openai_client():
    """Mock OpenAI client for LLM tests."""
    mock = AsyncMock()
    mock.chat.completions.create.return_value.choices[0].message.content = '[]'
    return mock
```

---

## Recommended Test File Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── factories.py             # Test data factories
├── mocks.py                 # Mock objects
│
├── unit/                    # Unit tests (no database)
│   ├── test_calendar_utils.py
│   ├── test_pgy_swap_rules.py
│   └── test_csv_parsing.py
│
├── integration/             # Integration tests (with database)
│   ├── test_calendar_service.py
│   ├── test_auth_service.py
│   ├── test_swap_service.py
│   └── test_days_off_service.py
│
├── api/                     # API endpoint tests
│   ├── test_health.py       # (existing)
│   ├── test_admin_auth.py
│   ├── test_schedule.py
│   └── test_swap.py
│
└── e2e/                     # End-to-end tests
    ├── test_swap_workflow.py
    └── test_calendar_subscription.py
```

---

## Implementation Roadmap

### Phase 1: Foundation (Critical)
1. Set up `conftest.py` with database fixtures
2. Add tests for `services/auth.py` (security critical)
3. Add tests for `services/calendar.py` (core feature)

### Phase 2: Core Business Logic
4. Add tests for `services/swap.py`
5. Add tests for `services/days_off.py`
6. Add tests for swap workflow (e2e)

### Phase 3: API Layer
7. Add tests for `routers/admin_auth.py`
8. Add tests for `routers/schedule.py`
9. Add tests for `routers/swap.py`
10. Add tests for `routers/days_off.py`

### Phase 4: Complete Coverage
11. Add tests for `services/excel_import.py`
12. Add tests for `middleware.py`
13. Add tests for models/schemas validation

---

## Suggested Tools to Add

Add to `requirements.txt`:
```
pytest-cov==4.1.0          # Coverage reporting
factory-boy==3.3.0         # Test factories
faker==22.0.0              # Fake data generation
pytest-mock==3.12.0        # Mocking utilities
aiosqlite==0.19.0          # SQLite for async tests
```

Run coverage with:
```bash
pytest --cov=app --cov-report=html --cov-report=term-missing
```

---

## Summary

The most critical gaps are:

| Component | Risk Level | Reason |
|-----------|-----------|--------|
| `auth.py` | **Critical** | Security - authentication bugs could allow unauthorized access |
| `calendar.py` | **High** | Core feature - users depend on accurate calendar data |
| `swap.py` | **High** | Complex workflow - state machine logic is error-prone |
| `days_off.py` | **Medium** | Data integrity - incorrect parsing could corrupt schedules |
| API routers | **Medium** | Input validation - protect against malformed requests |

Start with Phase 1 to establish the foundation, then systematically work through Phases 2-4 to achieve comprehensive coverage. A target of **80% code coverage** is recommended.
