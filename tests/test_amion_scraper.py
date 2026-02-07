from datetime import date

from app.services.amion_scraper import (
    AmionScraper,
    OnCallEntry,
    TeamAttendingAssignment,
)


def test_resolve_header_dates_mid_month_week_stays_in_target_month():
    found_dates = [
        (0, 15, "15 Sun"),
        (1, 16, "16 Mon"),
        (2, 17, "17 Tue"),
        (3, 18, "18 Wed"),
        (4, 19, "19 Thu"),
        (5, 20, "20 Fri"),
        (6, 21, "21 Sat"),
    ]

    resolved, seen = AmionScraper._resolve_header_dates(
        found_dates=found_dates,
        year=2026,
        month=2,
        seen_month_start=True,
    )

    assert seen is True
    assert [d for _, d, _ in resolved] == [
        date(2026, 2, 15),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 21),
    ]


def test_resolve_header_dates_first_row_handles_previous_month_spillover():
    found_dates = [
        (0, 28, "28 Sun"),
        (1, 29, "29 Mon"),
        (2, 30, "30 Tue"),
        (3, 31, "31 Wed"),
        (4, 1, "1 Thu"),
        (5, 2, "2 Fri"),
        (6, 3, "3 Sat"),
    ]

    resolved, seen = AmionScraper._resolve_header_dates(
        found_dates=found_dates,
        year=2026,
        month=1,
        seen_month_start=False,
    )

    assert seen is True
    assert [d for _, d, _ in resolved] == [
        date(2025, 12, 28),
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 3),
    ]


def test_generate_call_assignments_uses_team_and_date_specific_resident_map():
    scraper = AmionScraper(db=None)  # type: ignore[arg-type]

    oncall_entries = [
        OnCallEntry(
            attending_name="ESPARZA",
            date=date(2026, 2, 6),
            service="Hospitalist On-Call",
        )
    ]
    team_attending = [
        TeamAttendingAssignment(
            team_name="Blue Team",
            attending_name="ESPARZA",
            start_date=date(2026, 2, 2),
            end_date=date(2026, 2, 7),
        )
    ]
    residents_by_team_date = {
        ("BLUE", date(2026, 2, 6)): {101},
        ("BLUE", date(2026, 2, 7)): {202},
    }

    assignments = scraper.generate_call_assignments_for_residents(
        oncall_entries=oncall_entries,
        team_attending_map=team_attending,
        residents_by_team_date=residents_by_team_date,
    )

    assert len(assignments) == 3
    assert {a["resident_id"] for a in assignments} == {101}
    assert {a["call_type"] for a in assignments} == {"pre-call", "on-call", "post-call"}
    assert {a["date"] for a in assignments} == {
        date(2026, 2, 5),
        date(2026, 2, 6),
        date(2026, 2, 7),
    }
