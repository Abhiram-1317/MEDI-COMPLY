from __future__ import annotations

from datetime import date, timedelta

import pytest

from medi_comply.compliance.regulatory_calendar import (
    CalendarQueryResult,
    CodeSetVersionTracker,
    DOSValidationResult,
    RegulatoryBody,
    RegulatoryCalendar,
    RegulatoryEvent,
    RegulationStatus,
    RegulationType,
    StalenessDetector,
    UrgencyLevel,
    seed_regulatory_events,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tracker() -> CodeSetVersionTracker:
    return CodeSetVersionTracker()


@pytest.fixture()
def staleness(tracker: CodeSetVersionTracker) -> StalenessDetector:
    return StalenessDetector(tracker)


@pytest.fixture()
def seeded_calendar() -> RegulatoryCalendar:
    cal = RegulatoryCalendar()
    seed_regulatory_events(cal)
    return cal


@pytest.fixture()
def empty_calendar() -> RegulatoryCalendar:
    return RegulatoryCalendar()


# ---------------------------------------------------------------------------
# CodeSetVersionTracker tests
# ---------------------------------------------------------------------------


def test_icd10_fy2025_version(tracker: CodeSetVersionTracker) -> None:
    as_of = date(2025, 3, 15)
    version = tracker.get_effective_version("ICD-10-CM", as_of)
    assert version["version"] == "FY2025"
    assert version["effective_date"] == date(2024, 10, 1)


def test_icd10_fy2024_version(tracker: CodeSetVersionTracker) -> None:
    as_of = date(2024, 3, 15)
    version = tracker.get_effective_version("ICD-10-CM", as_of)
    assert version["version"] == "FY2024"
    assert version["effective_date"] == date(2023, 10, 1)


def test_icd10_version_boundary(tracker: CodeSetVersionTracker) -> None:
    on_boundary = tracker.get_effective_version("ICD-10-CM", date(2024, 10, 1))
    before_boundary = tracker.get_effective_version("ICD-10-CM", date(2024, 9, 30))
    assert on_boundary["version"] == "FY2025"
    assert before_boundary["version"] == "FY2024"


def test_cpt_2025_version(tracker: CodeSetVersionTracker) -> None:
    version = tracker.get_effective_version("CPT", date(2025, 6, 15))
    assert version["version"] == "2025"
    assert version["effective_date"] == date(2025, 1, 1)


def test_cpt_version_boundary(tracker: CodeSetVersionTracker) -> None:
    start = tracker.get_effective_version("CPT", date(2025, 1, 1))
    end = tracker.get_effective_version("CPT", date(2024, 12, 31))
    assert start["version"] == "2025"
    assert end["version"] == "2024"


def test_all_versions_listed(tracker: CodeSetVersionTracker) -> None:
    icd_versions = tracker.get_all_versions("ICD-10-CM")
    cpt_versions = tracker.get_all_versions("CPT")
    assert len(icd_versions) >= 2
    assert len(cpt_versions) >= 2


def test_code_valid_on_date(tracker: CodeSetVersionTracker) -> None:
    result = tracker.is_code_valid_on_date("A00", "ICD-10-CM", date(2024, 12, 1))
    assert result["valid"]
    assert result["version"] == "FY2025"


def test_code_changes_between_dates(tracker: CodeSetVersionTracker) -> None:
    changes = tracker.get_code_changes_between("ICD-10-CM", date(2023, 10, 1), date(2025, 10, 1))
    assert any(c["version"] == "FY2025" for c in changes)


# ---------------------------------------------------------------------------
# StalenessDetector tests
# ---------------------------------------------------------------------------


def test_no_staleness_same_day(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos
    result = staleness.check_staleness(dos, kb_date)
    assert result.is_valid
    assert staleness.get_staleness_risk(result.staleness_days) == "LOW"


def test_low_staleness_within_week(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos - timedelta(days=5)
    risk = staleness.get_staleness_risk((dos - kb_date).days)
    assert risk == "LOW"


def test_medium_staleness_within_month(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos - timedelta(days=20)
    risk = staleness.get_staleness_risk((dos - kb_date).days)
    assert risk == "MEDIUM"


def test_high_staleness_over_month(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos - timedelta(days=60)
    risk = staleness.get_staleness_risk((dos - kb_date).days)
    assert risk == "HIGH"


def test_critical_staleness_over_90_days(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos - timedelta(days=120)
    risk = staleness.get_staleness_risk((dos - kb_date).days)
    assert risk == "CRITICAL"


def test_cross_fiscal_year_boundary_warning(staleness: StalenessDetector) -> None:
    dos = date(2025, 10, 2)
    kb_date = date(2025, 7, 1)
    result = staleness.check_staleness(dos, kb_date)
    assert any("ICD-10" in w for w in result.warnings)


def test_cross_calendar_year_boundary_warning(staleness: StalenessDetector) -> None:
    dos = date(2025, 1, 2)
    kb_date = date(2024, 12, 1)
    result = staleness.check_staleness(dos, kb_date)
    assert any("CPT" in w or "HCPCS" in w for w in result.warnings)


def test_cross_quarterly_ncci_warning(staleness: StalenessDetector) -> None:
    dos = date(2025, 4, 2)
    kb_date = date(2025, 3, 1)
    result = staleness.check_staleness(dos, kb_date)
    assert any("NCCI" in w for w in result.warnings)


def test_dos_validation_result_complete(staleness: StalenessDetector) -> None:
    dos = date.today()
    kb_date = dos
    result = staleness.check_staleness(dos, kb_date)
    assert isinstance(result, DOSValidationResult)
    assert result.date_of_service == dos
    assert result.knowledge_base_version_date == kb_date
    assert result.applicable_code_set_version


def test_gap_risks_identified(staleness: StalenessDetector) -> None:
    dos = date(2025, 10, 5)
    kb_date = date(2025, 7, 1)
    risks = staleness.identify_gap_risks(dos, kb_date)
    assert any("ICD-10" in r for r in risks)


# ---------------------------------------------------------------------------
# RegulatoryCalendar event management tests
# ---------------------------------------------------------------------------


def _make_event(title: str, days_from_today: int, **kwargs) -> RegulatoryEvent:
    return RegulatoryEvent(
        title=title,
        description=kwargs.get("description", title),
        regulatory_body=kwargs.get("regulatory_body", RegulatoryBody.CMS),
        regulation_type=kwargs.get("regulation_type", RegulationType.CODE_SET_UPDATE),
        status=kwargs.get("status", RegulationStatus.PROPOSED),
        effective_date=date.today() + timedelta(days=days_from_today),
        affected_code_types=kwargs.get("affected_code_types", []),
        affected_payers=kwargs.get("affected_payers", []),
        comment_deadline=kwargs.get("comment_deadline"),
    )


def test_add_event(empty_calendar: RegulatoryCalendar) -> None:
    event = _make_event("Test Event", 10)
    empty_calendar.add_event(event)
    assert event in empty_calendar.events


def test_get_upcoming_events(empty_calendar: RegulatoryCalendar) -> None:
    soon_event = _make_event("Upcoming", 10)
    empty_calendar.add_event(soon_event)
    result = empty_calendar.get_upcoming_events(days_ahead=30)
    assert isinstance(result, CalendarQueryResult)
    assert soon_event in result.events_found


def test_get_events_by_date_range(empty_calendar: RegulatoryCalendar) -> None:
    target_event = _make_event("Range Event", 15)
    empty_calendar.add_event(target_event)
    window = empty_calendar.get_events_by_date_range(date.today(), date.today() + timedelta(days=20))
    assert target_event in window.events_found


def test_filter_by_regulation_type(empty_calendar: RegulatoryCalendar) -> None:
    code_event = _make_event("Code Update", 5, regulation_type=RegulationType.CODE_SET_UPDATE)
    billing_event = _make_event("Billing", 5, regulation_type=RegulationType.BILLING_RULE_CHANGE)
    empty_calendar.add_event(code_event)
    empty_calendar.add_event(billing_event)
    filtered = empty_calendar.get_upcoming_events(days_ahead=10, regulation_type=RegulationType.BILLING_RULE_CHANGE)
    assert billing_event in filtered.events_found
    assert code_event not in filtered.events_found


def test_filter_by_regulatory_body(empty_calendar: RegulatoryCalendar) -> None:
    cms_event = _make_event("CMS Event", 5, regulatory_body=RegulatoryBody.CMS)
    ama_event = _make_event("AMA Event", 5, regulatory_body=RegulatoryBody.AMA)
    empty_calendar.add_event(cms_event)
    empty_calendar.add_event(ama_event)
    filtered = empty_calendar.get_upcoming_events(days_ahead=10, regulatory_body=RegulatoryBody.AMA)
    assert ama_event in filtered.events_found
    assert cms_event not in filtered.events_found


def test_get_events_affecting_icd10(empty_calendar: RegulatoryCalendar) -> None:
    icd_event = _make_event("ICD Event", 5, affected_code_types=["ICD-10-CM"])
    empty_calendar.add_event(icd_event)
    results = empty_calendar.get_events_affecting_code_type("ICD-10-CM", days_ahead=30)
    assert icd_event in results


def test_get_events_affecting_cpt(empty_calendar: RegulatoryCalendar) -> None:
    cpt_event = _make_event("CPT Event", 5, affected_code_types=["CPT"])
    empty_calendar.add_event(cpt_event)
    results = empty_calendar.get_events_affecting_code_type("CPT", days_ahead=30)
    assert cpt_event in results


def test_get_events_affecting_payer(empty_calendar: RegulatoryCalendar) -> None:
    payer_event = _make_event("Payer Event", 5, affected_payers=["PAYER_X"])
    empty_calendar.add_event(payer_event)
    results = empty_calendar.get_events_affecting_payer("PAYER_X", days_ahead=30)
    assert payer_event in results


# ---------------------------------------------------------------------------
# Seed data tests
# ---------------------------------------------------------------------------


def test_seed_loads_minimum_20_events(seeded_calendar: RegulatoryCalendar) -> None:
    assert len(seeded_calendar.events) >= 20


def test_seed_has_icd10_updates(seeded_calendar: RegulatoryCalendar) -> None:
    assert any("ICD-10-CM" in ev.affected_code_types for ev in seeded_calendar.events)


def test_seed_has_cpt_updates(seeded_calendar: RegulatoryCalendar) -> None:
    assert any("CPT" in ev.affected_code_types for ev in seeded_calendar.events)


def test_seed_has_ncci_updates(seeded_calendar: RegulatoryCalendar) -> None:
    assert any("NCCI" in ev.affected_code_types for ev in seeded_calendar.events)


def test_seed_has_fee_schedule_updates(seeded_calendar: RegulatoryCalendar) -> None:
    assert any("fee schedule" in ev.title.lower() for ev in seeded_calendar.events)


def test_seed_has_past_events(seeded_calendar: RegulatoryCalendar) -> None:
    assert any(ev.effective_date < date.today() for ev in seeded_calendar.events)


def test_seed_has_future_events(seeded_calendar: RegulatoryCalendar) -> None:
    assert any(ev.effective_date > date.today() for ev in seeded_calendar.events)


# ---------------------------------------------------------------------------
# Compliance deadlines and urgency
# ---------------------------------------------------------------------------


def test_get_compliance_deadlines() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Deadline", 10, comment_deadline=date.today() + timedelta(days=10))
    cal.add_event(event)
    deadlines = cal.get_compliance_deadlines(days_ahead=30)
    assert any(d["event_id"] == event.event_id for d in deadlines)


def test_deadline_urgency_calculation() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Urgency", 15, comment_deadline=date.today() + timedelta(days=15))
    cal.add_event(event)
    deadlines = cal.get_compliance_deadlines(days_ahead=20)
    assert deadlines[0]["urgency"] in {u.value for u in UrgencyLevel}


def test_deadline_immediate_7_days() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Immediate", 5, comment_deadline=date.today() + timedelta(days=5))
    cal.add_event(event)
    deadline = cal.get_compliance_deadlines(days_ahead=10)[0]
    assert deadline["urgency"] == UrgencyLevel.IMMEDIATE.value


def test_deadline_imminent_30_days() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Imminent", 20, comment_deadline=date.today() + timedelta(days=20))
    cal.add_event(event)
    deadline = cal.get_compliance_deadlines(days_ahead=30)[0]
    assert deadline["urgency"] == UrgencyLevel.IMMINENT.value


def test_deadline_upcoming_90_days() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Upcoming", 60, comment_deadline=date.today() + timedelta(days=60))
    cal.add_event(event)
    deadline = cal.get_compliance_deadlines(days_ahead=90)[0]
    assert deadline["urgency"] == UrgencyLevel.UPCOMING.value


def test_deadline_planning_over_90() -> None:
    cal = RegulatoryCalendar()
    event = _make_event("Planning", 120, comment_deadline=date.today() + timedelta(days=120))
    cal.add_event(event)
    deadline = cal.get_compliance_deadlines(days_ahead=200)[0]
    assert deadline["urgency"] == UrgencyLevel.PLANNING.value


def test_deadlines_sorted_by_date() -> None:
    cal = RegulatoryCalendar()
    e1 = _make_event("First", 30, comment_deadline=date.today() + timedelta(days=30))
    e2 = _make_event("Second", 10, comment_deadline=date.today() + timedelta(days=10))
    e3 = _make_event("Third", 20, comment_deadline=date.today() + timedelta(days=20))
    for ev in (e1, e2, e3):
        cal.add_event(ev)
    deadlines = cal.get_compliance_deadlines(days_ahead=40)
    dates = [d["deadline_date"] for d in deadlines]
    assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Fiscal year tests
# ---------------------------------------------------------------------------


def test_fiscal_year_october(seeded_calendar: RegulatoryCalendar) -> None:
    assert seeded_calendar.get_fiscal_year(date(2024, 10, 15)) == "FY2025"


def test_fiscal_year_september(seeded_calendar: RegulatoryCalendar) -> None:
    assert seeded_calendar.get_fiscal_year(date(2024, 9, 15)) == "FY2024"


def test_fiscal_year_boundary(seeded_calendar: RegulatoryCalendar) -> None:
    assert seeded_calendar.get_fiscal_year(date(2024, 10, 1)) == "FY2025"
    assert seeded_calendar.get_fiscal_year(date(2024, 9, 30)) == "FY2024"


def test_fiscal_year_january(seeded_calendar: RegulatoryCalendar) -> None:
    assert seeded_calendar.get_fiscal_year(date(2025, 1, 15)) == "FY2025"


# ---------------------------------------------------------------------------
# DOS validation tests
# ---------------------------------------------------------------------------


def test_validate_dos_current_kb(seeded_calendar: RegulatoryCalendar) -> None:
    today = date.today()
    result = seeded_calendar.validate_date_of_service(today, today)
    assert result.is_valid
    assert not result.warnings


def test_validate_dos_stale_kb(seeded_calendar: RegulatoryCalendar) -> None:
    today = date.today()
    old_kb = today - timedelta(days=120)
    result = seeded_calendar.validate_date_of_service(today, old_kb)
    assert not result.is_valid
    assert result.warnings


def test_validate_dos_across_fy_boundary(seeded_calendar: RegulatoryCalendar) -> None:
    dos = date(2025, 10, 2)
    kb_date = date(2025, 7, 1)
    result = seeded_calendar.validate_date_of_service(dos, kb_date)
    assert any("ICD-10" in w for w in result.warnings)


def test_validate_dos_result_has_version(seeded_calendar: RegulatoryCalendar) -> None:
    today = date.today()
    result = seeded_calendar.validate_date_of_service(today, today)
    assert result.applicable_code_set_version


def test_validate_dos_result_has_changes(seeded_calendar: RegulatoryCalendar) -> None:
    dos = date(2025, 11, 1)
    kb_date = date(2025, 6, 1)
    result = seeded_calendar.validate_date_of_service(dos, kb_date)
    assert result.regulatory_changes_between


# ---------------------------------------------------------------------------
# Current version tests
# ---------------------------------------------------------------------------


def test_get_current_code_set_versions(seeded_calendar: RegulatoryCalendar) -> None:
    versions = seeded_calendar.get_current_code_set_versions()
    assert set(["ICD-10-CM", "CPT", "HCPCS", "NCCI"]).issubset(versions.keys())


def test_versions_include_icd10(seeded_calendar: RegulatoryCalendar) -> None:
    versions = seeded_calendar.get_current_code_set_versions()
    assert versions.get("ICD-10-CM")


def test_versions_include_cpt(seeded_calendar: RegulatoryCalendar) -> None:
    versions = seeded_calendar.get_current_code_set_versions()
    assert versions.get("CPT")


# ---------------------------------------------------------------------------
# Regulatory compliance checks
# ---------------------------------------------------------------------------


def test_check_regulatory_compliance_current() -> None:
    cal = RegulatoryCalendar()
    current = _make_event("Current", -1, status=RegulationStatus.EFFECTIVE)
    cal.add_event(current)
    result = cal.check_regulatory_compliance(date.today())
    assert result["compliant"]
    assert not result["overdue_items"]


def test_check_regulatory_compliance_overdue() -> None:
    cal = RegulatoryCalendar()
    overdue = _make_event("Overdue", -10, status=RegulationStatus.PROPOSED)
    cal.add_event(overdue)
    result = cal.check_regulatory_compliance(date.today())
    assert not result["compliant"]
    assert overdue in result["overdue_items"]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_generate_regulatory_brief() -> None:
    cal = RegulatoryCalendar()
    upcoming = _make_event("Brief Event", 5)
    cal.add_event(upcoming)
    brief = cal.generate_regulatory_brief(days_ahead=10)
    assert "Regulatory Brief" in brief
    assert "Brief Event" in brief


def test_brief_includes_upcoming_changes() -> None:
    cal = RegulatoryCalendar()
    upcoming = _make_event("Upcoming Change", 7)
    cal.add_event(upcoming)
    brief = cal.generate_regulatory_brief(days_ahead=10)
    assert "Upcoming Change" in brief


def test_get_calendar_year_events(seeded_calendar: RegulatoryCalendar) -> None:
    events = seeded_calendar.get_calendar_year_events(2025)
    assert any(ev.effective_date.year == 2025 for ev in events)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_calendar_no_error(empty_calendar: RegulatoryCalendar) -> None:
    upcoming = empty_calendar.get_upcoming_events(days_ahead=30)
    assert upcoming.total_found == 0


def test_far_future_date(tracker: CodeSetVersionTracker) -> None:
    version = tracker.get_effective_version("ICD-10-CM", date(2100, 1, 1))
    assert version


def test_far_past_date(tracker: CodeSetVersionTracker) -> None:
    version = tracker.get_effective_version("ICD-10-CM", date(2000, 1, 1))
    assert version


def test_result_serialization(seeded_calendar: RegulatoryCalendar) -> None:
    today = date.today()
    result = seeded_calendar.validate_date_of_service(today, today)
    payload = result.model_dump()
    assert payload["date_of_service"] == today
    assert "warnings" in payload
