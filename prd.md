# Product Requirements Document (PRD)
## Residency Rotation Calendar App - Enhanced Features

**Version:** 2.0
**Date:** January 2026
**Status:** Draft

---

## 1. Executive Summary

This document outlines the requirements for enhancing the existing Residency Rotation Calendar Subscription App. The current app parses an Excel schedule and generates iCal subscription URLs for residents. The enhanced version will add Amion integration, days-off management, a swap request system, and a comprehensive admin portal.

### Key New Features
1. **Amion Integration** - Scrape attending assignments and call schedules from Amion
2. **Days Off Management** - CSV upload and LLM-powered natural language parsing
3. **Swap Request System** - Resident-initiated swaps with peer confirmation and admin approval
4. **Admin Portal** - Full spreadsheet-like editing, file uploads, and schedule management
5. **Multi-Year Archive** - Support for historical schedule viewing

---

## 2. User Personas

### 2.1 Resident (End User)
- **Goals:** View personal rotation schedule, request swaps, see who's on call, know their attending
- **Access:** No authentication required; uses private calendar subscription URL
- **Device:** Primarily mobile (iPhone/Android), some desktop

### 2.2 Chief Resident / Program Coordinator (Admin)
- **Goals:** Manage schedules, approve swaps, upload days off, maintain source of truth
- **Access:** Magic link email authentication
- **Device:** Desktop primarily, mobile for approvals

### 2.3 Program (Context)
- **Size:** Large program (50-100 residents)
- **Structure:** TY, PGY1, PGY2, PGY3 levels
- **Academic Year:** July 1 - June 30

---

## 3. Technical Architecture

### 3.1 Technology Stack

| Component | Technology | Rationale |
|-----------|------------|-----------|
| Backend | FastAPI (Python) | Existing, async support, excellent performance |
| Database | PostgreSQL | Robust, scalable, excellent for relational data |
| Frontend | HTML/CSS/JS (vanilla) → Consider React/Vue for admin | Mobile-first responsive |
| Scraping | Playwright or BeautifulSoup | Browser automation for Amion |
| LLM | OpenAI GPT API | Natural language parsing for days off |
| Deployment | Railway/Render/Heroku | Simple PaaS, easy scaling |
| Calendar | icalendar (Python) | Existing, ICS generation |

### 3.2 System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                              │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Resident Web    │  Admin Portal    │  Calendar Clients         │
│  (Mobile-first)  │  (Desktop-first) │  (iOS/Google/Outlook)     │
└────────┬─────────┴────────┬─────────┴─────────────┬─────────────┘
         │                  │                       │
         ▼                  ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      FastAPI Backend                             │
├──────────────────┬──────────────────┬───────────────────────────┤
│  Public API      │  Admin API       │  Calendar API             │
│  - View schedules│  - Auth (magic)  │  - ICS generation         │
│  - Request swaps │  - CRUD ops      │  - Subscription feeds     │
│  - View call     │  - File uploads  │                           │
└────────┬─────────┴────────┬─────────┴─────────────┬─────────────┘
         │                  │                       │
         ▼                  ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Service Layer                               │
├────────────┬────────────┬────────────┬────────────┬─────────────┤
│ Schedule   │ Swap       │ Days Off   │ Amion      │ LLM         │
│ Service    │ Service    │ Service    │ Scraper    │ Parser      │
└────────────┴────────────┴────────────┴────────────┴─────────────┘
         │                  │                       │
         ▼                  ▼                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                      PostgreSQL Database                         │
├──────────────────────────────────────────────────────────────────┤
│ residents | rotations | swaps | days_off | admins | audit_log   │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Database Schema

```sql
-- Core Tables
CREATE TABLE academic_years (
    id SERIAL PRIMARY KEY,
    name VARCHAR(20) NOT NULL,          -- e.g., "2025-2026"
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_current BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE residents (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255),
    pgy_level VARCHAR(10) NOT NULL,     -- 'TY', 'PGY1', 'PGY2', 'PGY3'
    calendar_token UUID DEFAULT gen_random_uuid(),  -- Private URL token
    academic_year_id INTEGER REFERENCES academic_years(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE rotations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,          -- e.g., 'ICU', 'NIGHT', 'ED'
    display_name VARCHAR(100),
    color VARCHAR(7),                   -- Hex color for calendar
    location VARCHAR(100),
    start_time TIME,
    end_time TIME,
    is_overnight BOOLEAN DEFAULT FALSE,
    weekdays_only BOOLEAN DEFAULT FALSE,
    generates_events BOOLEAN DEFAULT TRUE,  -- FALSE for VAC, Research
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE schedule_assignments (
    id SERIAL PRIMARY KEY,
    resident_id INTEGER REFERENCES residents(id),
    rotation_id INTEGER REFERENCES rotations(id),
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    academic_year_id INTEGER REFERENCES academic_years(id),
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(resident_id, week_start)
);

-- Amion Integration
CREATE TABLE attendings (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    service VARCHAR(50),                -- Service/team they cover
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE attending_assignments (
    id SERIAL PRIMARY KEY,
    attending_id INTEGER REFERENCES attendings(id),
    service VARCHAR(50) NOT NULL,
    date DATE NOT NULL,
    academic_year_id INTEGER REFERENCES academic_years(id),
    source VARCHAR(20) DEFAULT 'amion', -- 'amion' or 'manual'
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(service, date)
);

CREATE TABLE call_assignments (
    id SERIAL PRIMARY KEY,
    resident_id INTEGER REFERENCES residents(id),
    call_type VARCHAR(30) NOT NULL,     -- 'pre-call', 'on-call', 'post-call'
    date DATE NOT NULL,
    service VARCHAR(50),
    location VARCHAR(100),
    academic_year_id INTEGER REFERENCES academic_years(id),
    source VARCHAR(20) DEFAULT 'amion',
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(resident_id, date, call_type)
);

-- Days Off
CREATE TABLE day_off_types (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) NOT NULL,          -- 'Vacation', 'Sick', 'Conference', etc.
    color VARCHAR(7),                   -- Hex color
    is_system BOOLEAN DEFAULT FALSE,    -- TRUE for built-in types
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE days_off (
    id SERIAL PRIMARY KEY,
    resident_id INTEGER REFERENCES residents(id),
    type_id INTEGER REFERENCES day_off_types(id),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    notes TEXT,
    approved_by INTEGER REFERENCES admins(id),
    approved_at TIMESTAMP,
    source VARCHAR(20) DEFAULT 'manual', -- 'manual', 'csv', 'llm'
    created_at TIMESTAMP DEFAULT NOW()
);

-- Swap System
CREATE TABLE swap_requests (
    id SERIAL PRIMARY KEY,
    requester_id INTEGER REFERENCES residents(id),
    target_id INTEGER REFERENCES residents(id),
    requester_assignment_id INTEGER REFERENCES schedule_assignments(id),
    target_assignment_id INTEGER REFERENCES schedule_assignments(id),
    status VARCHAR(20) DEFAULT 'pending', -- 'pending', 'peer_confirmed', 'approved', 'rejected', 'cancelled'
    requester_note TEXT,
    peer_confirmed_at TIMESTAMP,
    admin_reviewed_by INTEGER REFERENCES admins(id),
    admin_reviewed_at TIMESTAMP,
    admin_note TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Admin
CREATE TABLE admins (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(100),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    last_login TIMESTAMP
);

CREATE TABLE magic_links (
    id SERIAL PRIMARY KEY,
    admin_id INTEGER REFERENCES admins(id),
    token UUID DEFAULT gen_random_uuid(),
    expires_at TIMESTAMP NOT NULL,
    used_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Audit Log
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    admin_id INTEGER REFERENCES admins(id),
    action VARCHAR(50) NOT NULL,        -- 'schedule_edit', 'swap_approve', 'days_off_add', etc.
    entity_type VARCHAR(50),            -- 'schedule_assignment', 'swap_request', etc.
    entity_id INTEGER,
    old_value JSONB,
    new_value JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Amion Sync Tracking
CREATE TABLE amion_sync_log (
    id SERIAL PRIMARY KEY,
    sync_type VARCHAR(30) NOT NULL,     -- 'attendings', 'call_assignments'
    status VARCHAR(20) NOT NULL,        -- 'success', 'failed', 'partial'
    records_processed INTEGER,
    errors JSONB,
    started_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);
```

---

## 4. Feature Specifications

### 4.1 Amion Integration

#### 4.1.1 Overview
Scrape the public Amion calendar to extract:
- Attending assignments per service/team
- Call assignments (who is on-call, pre-call, post-call)
- Color-coded service/rotation information

#### 4.1.2 Data Source
- **URL:** `https://www.amion.com/organizations/f87f9307-c777-45cd-93da-e50d85933289/schedules/6b06ea0f-d6cc-48d5-ba23-fbebaed28db2/calendar`
- **Access:** Public (no authentication required per user)
- **Data visible:** Calendar grid with names, attendings by service, locations, color-coded assignments

#### 4.1.3 Sync Strategy
- **Frequency:** Daily automated sync (configurable)
- **Timing:** Run during off-peak hours (e.g., 3 AM)
- **Method:** Playwright browser automation to handle JavaScript-rendered content
- **Fallback:** Manual sync trigger in admin portal

#### 4.1.4 Data Matching
- **Residents:** Match by name from the Excel spreadsheet (source of truth for resident list)
- **Attendings:** Extract from Amion, store in database
- **Fuzzy Matching:** If exact match fails, present candidates for admin confirmation
- **Unmatched Handling:** Log and alert admin for review

#### 4.1.5 Technical Implementation
```python
# Amion Scraper Service (pseudocode)
class AmionScraper:
    async def scrape_call_schedule(self, month: date) -> List[CallAssignment]:
        """Scrape call assignments for a given month."""
        pass

    async def scrape_attending_assignments(self, month: date) -> List[AttendingAssignment]:
        """Scrape attending assignments by service."""
        pass

    async def match_resident(self, amion_name: str) -> Optional[Resident]:
        """Match Amion name to resident in database."""
        pass
```

---

### 4.2 Days Off Management

#### 4.2.1 Day Off Types (Pre-configured)
| Type | Color | System |
|------|-------|--------|
| Vacation | `#10B981` (green) | Yes |
| Sick | `#EF4444` (red) | Yes |
| Conference | `#6366F1` (indigo) | Yes |
| Educational Leave | `#8B5CF6` (purple) | Yes |
| Personal | `#F59E0B` (amber) | Yes |
| Custom... | Configurable | No |

#### 4.2.2 CSV Upload

**Template Format (strict):**
```csv
resident_name,start_date,end_date,type,notes
John Smith,2026-01-15,2026-01-17,Vacation,Family trip
Jane Doe,2026-02-01,2026-02-01,Conference,ACEP Conference
```

**Validation Rules:**
- All columns required except `notes`
- Dates must be in ISO format (YYYY-MM-DD)
- `end_date` >= `start_date`
- `resident_name` must match a resident in the system
- `type` must match existing day off type (case-insensitive)

**Upload Flow:**
1. Admin downloads template from admin portal
2. Admin fills in data
3. Admin uploads CSV
4. System validates and shows preview with any errors
5. Admin confirms import
6. Days off added to database

#### 4.2.3 LLM-Powered Text Input

**Interface:** Simple text area on admin portal

**Example Inputs:**
- "John Smith is off December 25-27 for vacation"
- "Jane Doe sick day January 3rd"
- "The following residents have conference Jan 15-17: John Smith, Jane Doe, Bob Wilson"

**Processing Flow:**
1. Admin enters natural language text
2. System sends to OpenAI GPT-4 with structured extraction prompt
3. GPT returns structured JSON with extracted days off
4. System validates against resident names
5. Admin reviews and confirms
6. Days off added to database

**LLM Prompt Template:**
```
Extract days off requests from the following text. Return JSON array with:
- resident_name: string
- start_date: YYYY-MM-DD
- end_date: YYYY-MM-DD
- type: one of [Vacation, Sick, Conference, Educational Leave, Personal]
- notes: optional string

Today's date is {current_date}. Assume current academic year if year not specified.

Text: {user_input}
```

---

### 4.3 Swap Request System

#### 4.3.1 Swap Rules (Business Logic)
| Requester Level | Can Swap With |
|-----------------|---------------|
| TY | TY, PGY1 |
| PGY1 | TY, PGY1 |
| PGY2 | PGY2, PGY3 |
| PGY3 | PGY2, PGY3 |

#### 4.3.2 Swap Workflow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   PENDING    │────▶│PEER_CONFIRMED│────▶│   APPROVED   │────▶│  COMPLETED   │
│              │     │              │     │              │     │              │
│ Resident A   │     │ Resident B   │     │ Admin        │     │ Schedules    │
│ creates req  │     │ confirms     │     │ approves     │     │ updated      │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
       │                    │                    │
       ▼                    ▼                    ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  CANCELLED   │     │  CANCELLED   │     │  REJECTED    │
│              │     │              │     │              │
│ Either party │     │ Resident B   │     │ Admin        │
│ cancels      │     │ declines     │     │ rejects      │
└──────────────┘     └──────────────┘     └──────────────┘
```

#### 4.3.3 Swap Request UI (Resident)

**Create Swap Request:**
1. Resident views their schedule
2. Clicks on a rotation week they want to swap
3. System shows eligible residents (same rotation, valid PGY swap)
4. Resident selects target person and their week
5. Adds optional note
6. Submits request

**View Swap Requests:**
- "My Requests" - swaps I initiated
- "Requests for Me" - swaps others want with me
- Status indicators for each

#### 4.3.4 Swap Request UI (Admin)

**Admin Dashboard:**
- List of pending swaps (peer-confirmed, awaiting approval)
- Ability to view both residents' full schedules
- Approve/Reject with optional note
- Filter by status, date, resident

---

### 4.4 Admin Portal

#### 4.4.1 Authentication

**Magic Link Flow:**
1. Admin enters email on login page
2. System verifies email is in approved admin list
3. System sends email with magic link (valid 15 minutes)
4. Admin clicks link, receives session token (valid 7 days)
5. Session stored in secure HTTP-only cookie

**Admin Management:**
- Existing admins can invite new admins by email
- Admins can deactivate other admins (not themselves)

#### 4.4.2 Schedule Grid Editor

**Features:**
- Full spreadsheet-like interface (similar to Google Sheets)
- Rows = Residents (grouped by PGY level)
- Columns = Weeks
- Cells = Rotation assignment
- Click cell to edit (dropdown of available rotations)
- Color-coded by rotation type
- Changes save immediately (with undo capability)
- Audit log records all changes

**Technical Implementation:**
- Consider using AG Grid, Handsontable, or similar library
- WebSocket for real-time updates if multiple admins editing
- Optimistic updates with conflict resolution

#### 4.4.3 Excel Upload (Mass Update)

**Supported Format:** Must match existing `schedule.xlsx` structure exactly

**Upload Flow:**
1. Admin uploads Excel file
2. System parses and validates against expected format
3. System shows diff view (what will change)
4. Admin confirms changes
5. System updates database
6. Audit log records bulk update

**Validation:**
- Column headers must match expected format
- All resident names must exist in system
- All rotation names must exist in system
- Date ranges must be valid

#### 4.4.4 Days Off Management (Admin View)

- View all days off in calendar or list view
- Filter by resident, type, date range
- Add/edit/delete days off manually
- CSV upload interface
- LLM text input interface
- Download days off report

#### 4.4.5 Amion Sync Management

- View last sync status and timestamp
- Trigger manual sync
- View sync history and errors
- Configure sync schedule
- Review and resolve name mismatches

#### 4.4.6 Settings

- Manage rotation types (add/edit colors, times, etc.)
- Manage day off types
- Manage admin users
- Configure academic year
- Export all data

---

### 4.5 Calendar Generation (Enhanced)

#### 4.5.1 Event Structure

Each calendar event will include:

```
SUMMARY: ICU
DTSTART: 20260115T070000
DTEND: 20260115T180000
DESCRIPTION:
  Rotation: ICU
  Attending: Dr. Smith
  Call Status: Pre-Call
  Location: Main Hospital 4th Floor
CATEGORIES: rotation,pre-call
X-APPLE-CALENDAR-COLOR: #FF6B6B
```

#### 4.5.2 Call Status Colors

| Status | Color | Usage |
|--------|-------|-------|
| Regular Day | Rotation color | Normal rotation day |
| Pre-Call | `#FCD34D` (yellow) | Day before on-call |
| On-Call | `#EF4444` (red) | On-call day |
| Post-Call | `#10B981` (green) | Day after on-call |
| Day Off | Day off type color | Vacation, sick, etc. |

#### 4.5.3 Calendar URL Structure

```
# Resident calendar (private token)
/api/calendar/{calendar_token}.ics

# Example:
/api/calendar/a1b2c3d4-e5f6-7890-abcd-ef1234567890.ics
```

---

### 4.6 Resident-Facing Features

#### 4.6.1 Schedule Viewer

- View personal schedule (calendar or list view)
- View any resident's schedule (full transparency)
- See who's on call today/this week
- See attending assignments per service
- Mobile-first responsive design

#### 4.6.2 Swap Request Interface

- Request a swap with another resident
- View incoming swap requests
- Confirm or decline swap requests
- View swap history

---

## 5. API Endpoints

### 5.1 Public API (No Auth)

```
GET  /api/residents                    # List all residents
GET  /api/residents/{id}/schedule      # Get resident schedule
GET  /api/calendar/{token}.ics         # ICS calendar feed
GET  /api/call/today                   # Today's call schedule
GET  /api/call/{date}                  # Call schedule for date
GET  /api/attendings/{date}            # Attending assignments for date
```

### 5.2 Resident API (Token-based)

```
POST /api/swaps                        # Create swap request
GET  /api/swaps/mine                   # My swap requests
GET  /api/swaps/incoming               # Swap requests for me
POST /api/swaps/{id}/confirm           # Confirm swap (peer)
POST /api/swaps/{id}/decline           # Decline swap (peer)
POST /api/swaps/{id}/cancel            # Cancel my swap request
```

### 5.3 Admin API (Magic Link Auth)

```
# Auth
POST /api/admin/login                  # Request magic link
GET  /api/admin/verify/{token}         # Verify magic link
POST /api/admin/logout                 # Logout

# Schedule Management
GET  /api/admin/schedule               # Get full schedule grid
PUT  /api/admin/schedule/assignment    # Update single assignment
POST /api/admin/schedule/upload        # Upload Excel file
GET  /api/admin/schedule/export        # Export as Excel

# Swaps
GET  /api/admin/swaps                  # List pending swaps
POST /api/admin/swaps/{id}/approve     # Approve swap
POST /api/admin/swaps/{id}/reject      # Reject swap

# Days Off
GET  /api/admin/days-off               # List all days off
POST /api/admin/days-off               # Add days off
PUT  /api/admin/days-off/{id}          # Update days off
DELETE /api/admin/days-off/{id}        # Delete days off
POST /api/admin/days-off/upload-csv    # Upload CSV
POST /api/admin/days-off/parse-text    # LLM parse text
GET  /api/admin/days-off/template      # Download CSV template

# Amion
POST /api/admin/amion/sync             # Trigger manual sync
GET  /api/admin/amion/status           # Get sync status
GET  /api/admin/amion/mismatches       # Get unmatched names
POST /api/admin/amion/resolve          # Resolve name mismatch

# Settings
GET  /api/admin/rotations              # List rotations
POST /api/admin/rotations              # Add rotation
PUT  /api/admin/rotations/{id}         # Update rotation
GET  /api/admin/admins                 # List admins
POST /api/admin/admins/invite          # Invite new admin

# Audit
GET  /api/admin/audit-log              # View audit log
```

---

## 6. UI/UX Specifications

### 6.1 Design Principles

- **Mobile-first:** Primary resident use is on phones
- **Clean and minimal:** Focus on information, reduce clutter
- **Fast:** Instant feedback, optimistic updates
- **Accessible:** WCAG 2.1 AA compliance

### 6.2 Resident Interface

#### Pages:
1. **Home/Dashboard**
   - Today's schedule at a glance
   - Upcoming week preview
   - Pending swap requests badge
   - Quick link to full calendar

2. **My Schedule**
   - Calendar view (month/week toggle)
   - List view option
   - Tap event for details (attending, call status, etc.)

3. **All Schedules**
   - Search/filter by resident name
   - View any resident's schedule

4. **Who's On Call**
   - Today's call schedule
   - Date picker to view other days
   - Grouped by service/location

5. **Swap Requests**
   - Tab: My Requests
   - Tab: Requests for Me
   - Create new swap button

### 6.3 Admin Interface

#### Pages:
1. **Dashboard**
   - Quick stats (pending swaps, recent changes)
   - Amion sync status
   - Recent audit log entries

2. **Schedule Grid**
   - Full spreadsheet editor
   - Toolbar: Upload, Export, Add Resident
   - Filters: PGY level, rotation, date range

3. **Swap Management**
   - List of pending swaps
   - Approve/reject workflow

4. **Days Off**
   - Calendar view of all days off
   - CSV upload
   - LLM text input
   - Manual add/edit

5. **Amion Settings**
   - Sync status and history
   - Manual sync trigger
   - Name matching resolution

6. **Settings**
   - Manage rotations
   - Manage day off types
   - Manage admins
   - Academic year config

---

## 7. Non-Functional Requirements

### 7.1 Performance
- Calendar ICS generation: < 500ms
- Page load: < 2s on 3G
- API response: < 200ms p95

### 7.2 Scalability
- Support 100 concurrent users
- Handle 1000 calendar subscription refreshes/hour

### 7.3 Security
- HTTPS only
- Magic links expire in 15 minutes
- Session tokens expire in 7 days
- Admin actions require valid session
- Calendar tokens are UUIDs (unguessable)
- Input sanitization on all endpoints
- SQL injection prevention (parameterized queries)

### 7.4 Reliability
- 99.9% uptime target
- Daily database backups
- Graceful degradation if Amion unavailable

### 7.5 Audit & Compliance
- Log all admin schedule modifications
- Log all swap approvals/rejections
- Retain audit logs for academic year + 1 year

---

## 8. Implementation Phases

### Phase 1: Foundation (Database & Auth)
- Set up PostgreSQL database with schema
- Implement admin magic link authentication
- Migrate existing Excel parsing to database
- Basic admin portal shell

### Phase 2: Admin Portal Core
- Schedule grid editor (view + edit)
- Excel upload with validation
- Basic audit logging
- Rotation management

### Phase 3: Amion Integration
- Playwright scraper for Amion
- Daily sync job
- Attending assignments storage
- Call status extraction
- Name matching and resolution UI

### Phase 4: Enhanced Calendar
- Update ICS generation with attending info
- Add call status (pre-call, on-call, post-call)
- Color coding for call status
- Include days off in calendar

### Phase 5: Days Off Management
- CSV upload with template
- LLM text parsing integration
- Days off CRUD in admin portal
- Calendar integration

### Phase 6: Swap System
- Swap request data model
- Resident swap request UI
- Peer confirmation flow
- Admin approval workflow
- Schedule update on approval

### Phase 7: Resident Features
- Enhanced resident schedule viewer
- Who's on call page
- All schedules browser
- Mobile optimization

### Phase 8: Polish & Launch
- Performance optimization
- Error handling improvements
- Documentation
- User testing
- Production deployment

---

## 9. Environment Variables

```bash
# Database
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# Application
BASE_URL=https://your-app.railway.app
SECRET_KEY=your-secret-key-for-sessions

# OpenAI (for LLM parsing)
OPENAI_API_KEY=sk-...

# Amion
AMION_BASE_URL=https://www.amion.com/organizations/f87f9307-c777-45cd-93da-e50d85933289/schedules/6b06ea0f-d6cc-48d5-ba23-fbebaed28db2

# Email (for magic links)
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASS=your-sendgrid-api-key
FROM_EMAIL=calendar@yourprogram.edu
```

---

## 10. Open Questions / Future Considerations

1. **Push notifications:** Currently relying on calendar sync; may want email/push for urgent changes
2. **Conflict detection:** Automatically detect scheduling conflicts (double-booked, etc.)
3. **Mobile app:** Native iOS/Android app vs PWA
4. **Integration with other systems:** Epic, EMR, duty hour tracking
5. **Backup call assignments:** Handle jeopardy/backup call scheduling
6. **Conference room booking:** Integration with room scheduling

---

## 11. Acceptance Criteria Summary

### Must Have (MVP)
- [ ] Admin can log in via magic link
- [ ] Admin can view and edit schedule grid
- [ ] Admin can upload Excel to update schedule
- [ ] Amion scraper runs daily and populates attending/call data
- [ ] Calendar shows rotation + attending + call status
- [ ] Residents can request swaps
- [ ] Target resident can confirm/decline swap
- [ ] Admin can approve/reject swaps
- [ ] Admin can upload days off via CSV
- [ ] Admin can add days off via LLM text input
- [ ] Days off appear on resident calendars
- [ ] Mobile-responsive resident interface

### Should Have
- [ ] Audit log for major admin actions
- [ ] Multiple academic year support
- [ ] Name fuzzy matching for Amion data
- [ ] Swap rule enforcement (PGY level restrictions)

### Nice to Have
- [ ] Real-time schedule grid updates (WebSocket)
- [ ] Advanced reporting/analytics
- [ ] Bulk swap operations
- [ ] API rate limiting

---

## 12. Appendix

### A. Current Excel Format Reference

The existing `schedule.xlsx` format:
- Row 1: Week date ranges (e.g., "July 1-4", "July 5-11")
- Column A: "Resident Names"
- Column B onwards: "WEEK 1", "WEEK 2", etc.
- Subsequent rows: Resident names with rotation codes in week columns
- Rotation codes: ICU, NIGHT, ED, VAC, etc.

### B. Amion URL Parameters

```
Base: https://www.amion.com/organizations/{org_id}/schedules/{schedule_id}/calendar
Parameters:
  - assignment_kind: call
  - month: YYYY-MM-01
  - y_axis: names
```

### C. Color Palette

```css
--rotation-icu: #EF4444;      /* Red */
--rotation-night: #6366F1;    /* Indigo */
--rotation-ed: #F59E0B;       /* Amber */
--rotation-clinic: #10B981;   /* Green */
--call-precall: #FCD34D;      /* Yellow */
--call-oncall: #EF4444;       /* Red */
--call-postcall: #10B981;     /* Green */
--dayoff-vacation: #10B981;   /* Green */
--dayoff-sick: #EF4444;       /* Red */
--dayoff-conference: #6366F1; /* Indigo */
```
