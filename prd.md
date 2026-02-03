# Product Requirements Document (PRD)
## AI-Driven Residency Scheduling App

**Version:** 0.1 (Rewrite Baseline)
**Date:** February 3, 2026
**Status:** Draft for discussion

---

## 1) Problem Statement
The current app primarily publishes a schedule as calendars. It does not solve the core residency scheduling problem: building, validating, and maintaining schedules under complex constraints (coverage, duty hours, fairness, education, days off, swaps, and service needs). We need a fit-for-purpose scheduling system that can generate, validate, and evolve schedules while minimizing administrative overhead and preserving safety/compliance.

## 2) Goals (Outcomes)
1. **Generate a valid schedule** that satisfies coverage, duty hours, and program-specific rules.
2. **Make schedule changes fast and safe** (days off, swaps, sick coverage) with auditability.
3. **Give residents a clear, trusted view** via web + calendar subscriptions.
4. **Reduce coordinator workload** via AI-assisted intake, reasoning, and change recommendations.

## 3) Non‑Goals (For MVP)
- Full cross-program staffing optimization across departments.
- Payroll/HRIS integration.
- Complex billing/cost optimization.
- Multi-institution federation.

## 4) Personas
- **Resident**: sees schedule, requests swaps/days off, receives notifications.
- **Chief Resident / Coordinator (Scheduler Admin)**: builds schedule, resolves conflicts, approves swaps.
- **Program Director**: oversight, policy changes, compliance reporting.

## 5) Core User Journeys
1. **Build a schedule** from inputs (rotations, staffing needs, constraints, resident preferences).
2. **Validate and publish** (conflict checks + compliance report) then publish to calendar.
3. **Change request** (swap/days off) → AI suggests valid options → admin approval → publish.
4. **Incident handling** (last-minute sick call) → AI suggests coverage → admin approves.

## 6) Functional Requirements (MVP)
### 6.1 Scheduling Inputs
- Residents (PGY, track, restrictions, vacation allotment, night float eligibility).
- Rotations/services (coverage requirements, hours, location, call rules).
- Academic year and week structure (start/end, block size).
- Constraints:
  - Duty hours (hard).
  - Coverage minimums (hard).
  - Required rotations and sequencing (hard/soft).
  - Fairness targets (soft): nights, weekends, holidays.
  - Resident preferences and requests (soft).

### 6.2 Schedule Engine (AI-Assisted)
- Constraint model with **hard/soft** rule distinction.
- Generate schedule candidate(s) and rank by violations/fairness.
- Explainability: list of unmet soft constraints and why.
- Ability to lock segments and re-run partial optimization.
 - Enforce duty hour rules and block structure (see §12 Constraints).

### 6.3 Change Management
- Swap requests with eligibility checks and audit trail.
- Days off requests (structured + AI parsing from free text).
- Last-minute coverage workflow with escalation.
- Every change updates published calendars.

### 6.4 Publishing
- Resident-facing web view.
- Calendar subscriptions (ICS) with tokenized access.
- Change notifications (email + optional SMS later).

### 6.5 Admin Portal
- Grid view for schedule edits.
- Conflict indicators and rule violations.
- Upload/import from Excel for bootstrap.
- Audit log of all changes.

### 6.6 Security & Roles
- Role-based access: resident vs admin vs director.
- Admin authentication with secure login.
- Tokenized read-only calendar links.

## 7) AI Features (Targeted, Not Magical)
- **Intake parsing**: days off and swap requests from natural language.
- **Suggestion engine**: propose valid swaps or coverage options.
- **Constraint explanation**: why a swap or request is invalid.
- **Conflict detection**: highlight risky changes before approval.

## 8) Data Model (High-Level)
- Residents, Rotations, Assignments (weekly blocks).
- CoverageRequirements (per service/week/day).
- Constraints (rule definitions + severity).
- Requests (swap, days off, coverage).
- AuditLog + Notifications.

## 9) Metrics (Success Criteria)
- % schedules generated with zero hard violations.
- Admin time to build schedule (hours).
- Median time to resolve a swap request.
- Resident satisfaction (qualitative survey).
- Calendar data freshness (<10 minutes from change to publish).

## 10) Risks & Constraints
- **Data quality**: dirty imports or inconsistent rotation names.
- **Rule ambiguity**: program policies must be formalized.
- **AI mis-suggestions**: must be constrained by hard rules.
- **Trust**: explainability is required for adoption.

## 11) Open Questions (Need Answers Before Re-architecture)
1. What are the **hard constraints** that must never be violated?
2. What is the **block structure** (weekly? 2-week? 4-week)?
3. How are **coverage requirements** defined (per day, per service)?
4. Who has final approval authority for swaps and schedule edits?
5. What integrations matter (Amion, EHR, payroll, paging)?

## 12) Constraints (Confirmed)
- **Duty hours (hard):** max 100 hours in any 7-day period; average 80 hours/week.
- **Days off (hard):** minimum average 1 day off per week.
- **Night → day transition (hard):** at least 1 day off before switching from night to day; configurable as a rule parameter (full 24h vs calendar day).
- **Block structure (hard):** 1‑week blocks; rotations switch on **Saturday**.
- **Clinic cadence (hard):** per resident, every 5th week is clinic; hours **08:00–17:00 Mon–Fri**.
- **Rotation timing:** residents stay on the same rotation Sat–Fri; changes happen on Saturday.
- **Coverage roles (hard):** two backups per PGY year (Jeopardy + Backup Jeopardy).
- **Schedule roles:** team colors represent floor coverage; minimum hours on floors required (details TBD).
- **Approval:** any chief can finalize approvals.
- **Jeopardy eligibility:** only residents on electives are eligible for Jeopardy/Backup Jeopardy.
- **Jeopardy timing:** Jeopardy switches on Saturday with schedule change.
- **Integration:** local calendar is source of truth; Amion is a periodic sync source with auto‑approve of updates and full audit log.

---

# Proposed Next Step
Use this PRD as the baseline and confirm the open questions. Once confirmed, we will propose an architecture and a migration plan that preserves a stable core while we replace or refactor components.
