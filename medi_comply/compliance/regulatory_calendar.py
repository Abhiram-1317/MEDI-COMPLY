"""Regulatory calendar and version tracking for MEDI-COMPLY.

Tracks regulatory events, code set versions, and validates date-of-service
(DOS) compliance against knowledge base currency. Designed for conservative
(stale-biased) validation and easy seed/update of regulatory timelines.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RegulatoryBody(str, Enum):
    CMS = "CMS"
    WHO = "WHO"
    AMA = "AMA"
    OIG = "OIG"
    HHS = "HHS"
    STATE = "STATE"
    PAYER = "PAYER"
    NCQA = "NCQA"
    JOINT_COMMISSION = "JOINT_COMMISSION"


class RegulationType(str, Enum):
    CODE_SET_UPDATE = "CODE_SET_UPDATE"
    BILLING_RULE_CHANGE = "BILLING_RULE_CHANGE"
    COVERAGE_POLICY_CHANGE = "COVERAGE_POLICY_CHANGE"
    FEE_SCHEDULE_UPDATE = "FEE_SCHEDULE_UPDATE"
    COMPLIANCE_REQUIREMENT = "COMPLIANCE_REQUIREMENT"
    REPORTING_REQUIREMENT = "REPORTING_REQUIREMENT"
    PRIVACY_SECURITY = "PRIVACY_SECURITY"
    AUTHORIZATION_CHANGE = "AUTHORIZATION_CHANGE"
    GUIDELINE_UPDATE = "GUIDELINE_UPDATE"
    EMERGENCY_REGULATION = "EMERGENCY_REGULATION"
    PARITY_REQUIREMENT = "PARITY_REQUIREMENT"
    INTEROPERABILITY = "INTEROPERABILITY"
    PRICE_TRANSPARENCY = "PRICE_TRANSPARENCY"


class RegulationStatus(str, Enum):
    PROPOSED = "PROPOSED"
    FINAL = "FINAL"
    EFFECTIVE = "EFFECTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"
    STAYED = "STAYED"


class UrgencyLevel(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    PLANNING = "PLANNING"
    UPCOMING = "UPCOMING"
    IMMINENT = "IMMINENT"
    IMMEDIATE = "IMMEDIATE"
    EMERGENCY = "EMERGENCY"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RegulatoryEvent(BaseModel):
    """Regulatory event with impact on coding, coverage, or compliance."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    description: str
    regulatory_body: RegulatoryBody
    regulation_type: RegulationType
    status: RegulationStatus
    published_date: Optional[date] = None
    comment_deadline: Optional[date] = None
    effective_date: date
    end_date: Optional[date] = None
    superseded_by: Optional[str] = None
    affected_code_types: List[str] = Field(default_factory=list)
    affected_code_ranges: List[str] = Field(default_factory=list)
    affected_payers: List[str] = Field(default_factory=list)
    affected_states: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None
    federal_register_citation: Optional[str] = None
    impact_summary: str = ""
    action_required: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    priority: int = 5


class CalendarQueryResult(BaseModel):
    """Result container for calendar queries."""

    query_description: str
    events_found: List[RegulatoryEvent]
    total_found: int
    upcoming_deadlines: List[Dict[str, Any]]
    urgency_summary: Dict[str, int]


class DOSValidationResult(BaseModel):
    """Validation outcome for matching DOS against knowledge base currency."""

    date_of_service: date
    knowledge_base_version_date: date
    is_valid: bool
    staleness_days: int
    warnings: List[str]
    applicable_code_set_version: str
    code_set_effective_date: date
    code_set_end_date: Optional[date]
    regulatory_changes_between: List[RegulatoryEvent]
    recommendation: str


class ComplianceDeadline(BaseModel):
    """Tracks compliance actions with due dates and urgency."""

    event_id: str
    title: str
    deadline_date: date
    days_remaining: int
    urgency: UrgencyLevel
    action_required: str
    responsible_party: str
    status: str
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Annual timeline (static reference)
# ---------------------------------------------------------------------------

ANNUAL_REGULATORY_TIMELINE: Dict[str, List[str]] = {
    "JANUARY 1": [
        "CPT code updates effective (AMA)",
        "Medicare Physician Fee Schedule (MPFS) updates effective",
        "OPPS (Outpatient Prospective Payment System) updates",
        "New HCPCS codes effective",
        "Quality reporting period begins",
    ],
    "APRIL 1": [
        "NCCI edit quarterly update (Q2)",
        "Medicare Advantage plan changes",
        "Spring LCD/NCD updates",
    ],
    "JULY 1": [
        "NCCI edit quarterly update (Q3)",
        "Mid-year HCPCS updates",
        "State Medicaid fee schedule updates (varies)",
    ],
    "OCTOBER 1": [
        "ICD-10-CM/PCS code updates effective (fiscal year start)",
        "IPPS (Inpatient Prospective Payment System) updates",
        "New ICD-10 codes, revised codes, deleted codes",
        "MS-DRG weight updates",
        "NCCI edit quarterly update (Q4)",
    ],
    "ONGOING/VARIABLE": [
        "LCD/NCD changes (monthly/as needed)",
        "Payer policy updates (variable)",
        "State regulatory changes (variable)",
        "Emergency PHE waivers (as needed)",
        "Coding guideline clarifications (as needed)",
    ],
}


# ---------------------------------------------------------------------------
# Code set version tracker
# ---------------------------------------------------------------------------


class CodeSetVersionTracker:
    """Tracks code set versions by effective period and resolves by date."""

    def __init__(self) -> None:
        # Example version tables; can be extended by knowledge updater
        self._versions: Dict[str, List[Dict[str, Any]]] = {
            "ICD-10-CM": [
                {"version": "FY2024", "effective_date": date(2023, 10, 1), "end_date": date(2024, 9, 30), "fiscal_year": "FY2024"},
                {"version": "FY2025", "effective_date": date(2024, 10, 1), "end_date": date(2025, 9, 30), "fiscal_year": "FY2025"},
                {"version": "FY2026", "effective_date": date(2025, 10, 1), "end_date": date(2026, 9, 30), "fiscal_year": "FY2026"},
            ],
            "CPT": [
                {"version": "2024", "effective_date": date(2024, 1, 1), "end_date": date(2024, 12, 31), "fiscal_year": "2024"},
                {"version": "2025", "effective_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "fiscal_year": "2025"},
                {"version": "2026", "effective_date": date(2026, 1, 1), "end_date": date(2026, 12, 31), "fiscal_year": "2026"},
            ],
            "HCPCS": [
                {"version": "2025", "effective_date": date(2025, 1, 1), "end_date": date(2025, 12, 31), "fiscal_year": "2025"},
                {"version": "2026", "effective_date": date(2026, 1, 1), "end_date": date(2026, 12, 31), "fiscal_year": "2026"},
            ],
            "NCCI": [
                {"version": "Q1-2025", "effective_date": date(2025, 1, 1), "end_date": date(2025, 3, 31), "fiscal_year": "FY2025"},
                {"version": "Q2-2025", "effective_date": date(2025, 4, 1), "end_date": date(2025, 6, 30), "fiscal_year": "FY2025"},
                {"version": "Q3-2025", "effective_date": date(2025, 7, 1), "end_date": date(2025, 9, 30), "fiscal_year": "FY2025"},
                {"version": "Q4-2025", "effective_date": date(2025, 10, 1), "end_date": date(2025, 12, 31), "fiscal_year": "FY2026"},
            ],
        }

    def get_effective_version(self, code_type: str, as_of_date: date) -> Dict[str, Any]:
        versions = self._versions.get(code_type, [])
        for entry in versions:
            start = entry["effective_date"]
            end = entry.get("end_date")
            if start <= as_of_date <= (end or as_of_date):
                return entry
        return versions[-1] if versions else {}

    def get_all_versions(self, code_type: str) -> List[Dict[str, Any]]:
        return list(self._versions.get(code_type, []))

    def is_code_valid_on_date(self, code: str, code_type: str, service_date: date) -> Dict[str, Any]:
        version = self.get_effective_version(code_type, service_date)
        note = "Code validity unknown; detailed code tables not loaded"
        valid = bool(version)
        if not valid:
            note = "No version found for code type"
        return {"valid": valid, "version": version.get("version"), "note": note}

    def get_code_changes_between(self, code_type: str, start_date: date, end_date: date) -> List[Dict[str, Any]]:
        versions = self._versions.get(code_type, [])
        return [v for v in versions if v["effective_date"] > start_date and v["effective_date"] <= end_date]


# ---------------------------------------------------------------------------
# Staleness detector
# ---------------------------------------------------------------------------


class StalenessDetector:
    """Detects staleness between KB version date and date of service."""

    def __init__(self, tracker: CodeSetVersionTracker) -> None:
        self.tracker = tracker

    def get_staleness_risk(self, staleness_days: int) -> str:
        if staleness_days <= 7:
            return "LOW"
        if staleness_days <= 30:
            return "MEDIUM"
        if staleness_days <= 90:
            return "HIGH"
        return "CRITICAL"

    def identify_gap_risks(self, date_of_service: date, kb_effective_date: date) -> List[str]:
        risks: List[str] = []
        if date_of_service >= date(date_of_service.year, 10, 1) and kb_effective_date < date(date_of_service.year, 10, 1):
            risks.append("FY ICD-10 code set may have changed on Oct 1")
        if date_of_service >= date(date_of_service.year, 1, 1) and kb_effective_date < date(date_of_service.year, 1, 1):
            risks.append("CPT/HCPCS code sets may have changed on Jan 1")
        quarter_starts = [date(date_of_service.year, 1, 1), date(date_of_service.year, 4, 1), date(date_of_service.year, 7, 1), date(date_of_service.year, 10, 1)]
        for q in quarter_starts:
            if date_of_service >= q and kb_effective_date < q:
                risks.append("NCCI edits may have changed this quarter")
        return risks

    def check_staleness(self, date_of_service: date, kb_version_date: date) -> DOSValidationResult:
        staleness_days = (date_of_service - kb_version_date).days
        risks = self.identify_gap_risks(date_of_service, kb_version_date)

        icd_version = self.tracker.get_effective_version("ICD-10-CM", date_of_service)
        applicable_version = icd_version.get("version", "UNKNOWN")
        code_set_effective = icd_version.get("effective_date", kb_version_date)
        code_set_end = icd_version.get("end_date")

        recommendation = "Knowledge base aligns with DOS" if not risks else "Update knowledge base to latest code sets before coding"
        is_valid = staleness_days <= 30 and not risks

        return DOSValidationResult(
            date_of_service=date_of_service,
            knowledge_base_version_date=kb_version_date,
            is_valid=is_valid,
            staleness_days=staleness_days,
            warnings=risks,
            applicable_code_set_version=str(applicable_version),
            code_set_effective_date=code_set_effective,
            code_set_end_date=code_set_end,
            regulatory_changes_between=[],
            recommendation=recommendation,
        )


# ---------------------------------------------------------------------------
# Regulatory calendar engine
# ---------------------------------------------------------------------------


class RegulatoryCalendar:
    """Main calendar engine for regulatory event tracking and DOS validation."""

    def __init__(self) -> None:
        self.events: List[RegulatoryEvent] = []
        self.tracker = CodeSetVersionTracker()
        self.staleness = StalenessDetector(self.tracker)

    def add_event(self, event: RegulatoryEvent) -> None:
        self.events.append(event)

    def _urgency_for_date(self, target_date: date) -> UrgencyLevel:
        today = date.today()
        delta = (target_date - today).days
        if delta < 0:
            return UrgencyLevel.IMMEDIATE
        if delta <= 7:
            return UrgencyLevel.IMMEDIATE
        if delta <= 30:
            return UrgencyLevel.IMMINENT
        if delta <= 90:
            return UrgencyLevel.UPCOMING
        return UrgencyLevel.PLANNING

    def _query(self, predicate) -> List[RegulatoryEvent]:
        return sorted([ev for ev in self.events if predicate(ev)], key=lambda e: e.effective_date)

    def get_upcoming_events(
        self,
        days_ahead: int = 90,
        regulation_type: Optional[RegulationType] = None,
        regulatory_body: Optional[RegulatoryBody] = None,
    ) -> CalendarQueryResult:
        today = date.today()
        window_end = today + timedelta(days=days_ahead)
        events = self._query(
            lambda e: today <= e.effective_date <= window_end
            and (regulation_type is None or e.regulation_type == regulation_type)
            and (regulatory_body is None or e.regulatory_body == regulatory_body)
        )
        upcoming_deadlines = self._build_deadlines(events)
        urgency_summary = self._summarize_urgency(upcoming_deadlines)
        return CalendarQueryResult(
            query_description=f"Events in next {days_ahead} days",
            events_found=events,
            total_found=len(events),
            upcoming_deadlines=upcoming_deadlines,
            urgency_summary=urgency_summary,
        )

    def get_events_by_date_range(self, start_date: date, end_date: date) -> CalendarQueryResult:
        events = self._query(lambda e: start_date <= e.effective_date <= end_date)
        deadlines = self._build_deadlines(events)
        return CalendarQueryResult(
            query_description=f"Events between {start_date} and {end_date}",
            events_found=events,
            total_found=len(events),
            upcoming_deadlines=deadlines,
            urgency_summary=self._summarize_urgency(deadlines),
        )

    def get_events_affecting_code_type(self, code_type: str, days_ahead: int = 90) -> List[RegulatoryEvent]:
        today = date.today()
        window_end = today + timedelta(days=days_ahead)
        return self._query(
            lambda e: today <= e.effective_date <= window_end and code_type in e.affected_code_types
        )

    def get_events_affecting_payer(self, payer_id: str, days_ahead: int = 90) -> List[RegulatoryEvent]:
        today = date.today()
        window_end = today + timedelta(days=days_ahead)
        return self._query(
            lambda e: today <= e.effective_date <= window_end and ("ALL" in e.affected_payers or payer_id in e.affected_payers)
        )

    def validate_date_of_service(self, date_of_service: date, kb_version_date: date) -> DOSValidationResult:
        validation = self.staleness.check_staleness(date_of_service, kb_version_date)
        changes = [
            ev
            for ev in self.events
            if kb_version_date < ev.effective_date <= date_of_service and ev.regulation_type == RegulationType.CODE_SET_UPDATE
        ]
        validation.regulatory_changes_between = changes
        if changes:
            validation.is_valid = False
            validation.warnings.append("Code set updates detected between KB version and DOS")
            validation.recommendation = "Update KB to latest code sets and rerun coding"
        return validation

    def get_compliance_deadlines(self, days_ahead: int = 90) -> List[Dict[str, Any]]:
        today = date.today()
        window_end = today + timedelta(days=days_ahead)
        deadlines: List[ComplianceDeadline] = []
        for ev in self.events:
            if ev.comment_deadline and today <= ev.comment_deadline <= window_end:
                urgency = self._urgency_for_date(ev.comment_deadline)
                deadlines.append(
                    ComplianceDeadline(
                        event_id=ev.event_id,
                        title=ev.title,
                        deadline_date=ev.comment_deadline,
                        days_remaining=(ev.comment_deadline - today).days,
                        urgency=urgency,
                        action_required="Submit comments / prepare compliance plan",
                        responsible_party="Compliance Officer",
                        status="PENDING",
                    )
                )
        sorted_deadlines = sorted(deadlines, key=lambda d: d.deadline_date)
        # Return dictionaries for easier consumption in UI/tests
        return [
            {
                "event_id": d.event_id,
                "title": d.title,
                "deadline_date": d.deadline_date,
                "days_remaining": d.days_remaining,
                "urgency": d.urgency.value if hasattr(d.urgency, "value") else str(d.urgency),
                "action_required": d.action_required,
                "responsible_party": d.responsible_party,
                "status": d.status,
            }
            for d in sorted_deadlines
        ]

    def get_current_code_set_versions(self) -> Dict[str, Any]:
        today = date.today()
        return {
            "ICD-10-CM": self.tracker.get_effective_version("ICD-10-CM", today).get("version"),
            "CPT": self.tracker.get_effective_version("CPT", today).get("version"),
            "HCPCS": self.tracker.get_effective_version("HCPCS", today).get("version"),
            "NCCI": self.tracker.get_effective_version("NCCI", today).get("version"),
        }

    def check_regulatory_compliance(self, current_date: date) -> Dict[str, Any]:
        overdue = [ev for ev in self.events if ev.effective_date < current_date and ev.status != RegulationStatus.EFFECTIVE]
        upcoming = [ev for ev in self.events if current_date <= ev.effective_date <= current_date + timedelta(days=30)]
        return {
            "compliant": len(overdue) == 0,
            "overdue_items": overdue,
            "upcoming_items": upcoming,
        }

    def get_fiscal_year(self, as_of_date: date) -> str:
        start_year = as_of_date.year if as_of_date.month >= 10 else as_of_date.year - 1
        return f"FY{start_year + 1}"

    def get_calendar_year_events(self, year: int) -> List[RegulatoryEvent]:
        return self._query(lambda e: e.effective_date.year == year)

    def generate_regulatory_brief(self, days_ahead: int = 30) -> str:
        query = self.get_upcoming_events(days_ahead=days_ahead)
        lines = [f"Regulatory Brief: Next {days_ahead} days"]
        for ev in query.events_found:
            urgency = self._urgency_for_date(ev.effective_date).value
            lines.append(f"- [{urgency}] {ev.effective_date}: {ev.title} ({ev.regulation_type.value})")
        if not query.events_found:
            lines.append("- No upcoming regulatory changes in window")
        return "\n".join(lines)

    def _build_deadlines(self, events: Iterable[RegulatoryEvent]) -> List[Dict[str, Any]]:
        today = date.today()
        results: List[Dict[str, Any]] = []
        for ev in events:
            delta = (ev.effective_date - today).days
            results.append(
                {
                    "event_id": ev.event_id,
                    "title": ev.title,
                    "deadline_date": ev.effective_date,
                    "days_remaining": delta,
                    "urgency": self._urgency_for_date(ev.effective_date).value,
                }
            )
        return results

    def _summarize_urgency(self, deadlines: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for d in deadlines:
            summary[d["urgency"]] = summary.get(d["urgency"], 0) + 1
        return summary


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


def seed_regulatory_events(calendar: RegulatoryCalendar) -> None:
    """Populate calendar with sample past/present/future events."""
    today = date.today()

    samples: List[RegulatoryEvent] = [
        RegulatoryEvent(
            title="FY2024 ICD-10-CM Updates",
            description="Annual ICD-10-CM code set update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.EFFECTIVE,
            published_date=date(2023, 8, 15),
            effective_date=date(2023, 10, 1),
            affected_code_types=["ICD-10-CM"],
            source_url="https://example.com/icd10-fy2024",
            impact_summary="FY2024 ICD-10 changes now in effect",
            action_required=["Ensure ICD-10 FY2024 codes loaded"],
            tags=["ICD-10", "FY2024"],
            priority=8,
        ),
        RegulatoryEvent(
            title="FY2025 ICD-10-CM Updates",
            description="Annual ICD-10-CM code set update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.EFFECTIVE,
            published_date=date(2024, 8, 15),
            effective_date=date(2024, 10, 1),
            affected_code_types=["ICD-10-CM"],
            source_url="https://example.com/icd10-fy2025",
            impact_summary="FY2025 ICD-10 changes now in effect",
            action_required=["Validate ICD-10 FY2025 deployment"],
            tags=["ICD-10", "FY2025"],
            priority=8,
        ),
        RegulatoryEvent(
            title="CPT 2024 Updates",
            description="Annual CPT code set update",
            regulatory_body=RegulatoryBody.AMA,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.EFFECTIVE,
            published_date=date(2023, 11, 1),
            effective_date=date(2024, 1, 1),
            affected_code_types=["CPT"],
            source_url="https://example.com/cpt-2024",
            impact_summary="CPT 2024 codes active",
            action_required=["Load CPT 2024", "Communicate coding changes"],
            tags=["CPT", "2024"],
            priority=7,
        ),
        RegulatoryEvent(
            title="CPT 2025 Updates",
            description="Annual CPT code set update",
            regulatory_body=RegulatoryBody.AMA,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.EFFECTIVE,
            published_date=date(2024, 11, 1),
            effective_date=date(2025, 1, 1),
            affected_code_types=["CPT"],
            source_url="https://example.com/cpt-2025",
            impact_summary="CPT 2025 codes active",
            action_required=["Load CPT 2025"],
            tags=["CPT", "2025"],
            priority=7,
        ),
        RegulatoryEvent(
            title="Q1 2025 NCCI Edits",
            description="Quarterly NCCI update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.BILLING_RULE_CHANGE,
            status=RegulationStatus.EFFECTIVE,
            effective_date=date(2025, 1, 1),
            affected_code_types=["NCCI"],
            source_url="https://example.com/ncci-q1-2025",
            impact_summary="Q1 NCCI edits in effect",
            action_required=["Update NCCI tables"],
            tags=["NCCI", "Q1"],
            priority=6,
        ),
        RegulatoryEvent(
            title="Q2 2025 NCCI Edits",
            description="Quarterly NCCI update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.BILLING_RULE_CHANGE,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 4, 1),
            affected_code_types=["NCCI"],
            source_url="https://example.com/ncci-q2-2025",
            impact_summary="Q2 NCCI edits scheduled",
            action_required=["Prepare for Q2 NCCI"],
            tags=["NCCI", "Q2"],
            priority=6,
        ),
        RegulatoryEvent(
            title="2025 Medicare Physician Fee Schedule",
            description="Annual MPFS rate update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.FEE_SCHEDULE_UPDATE,
            status=RegulationStatus.EFFECTIVE,
            effective_date=date(2025, 1, 1),
            affected_code_types=["CPT", "HCPCS"],
            source_url="https://example.com/mpfs-2025",
            impact_summary="2025 MPFS payment rates active",
            action_required=["Update fee schedules"],
            tags=["MPFS", "2025"],
            priority=8,
        ),
        RegulatoryEvent(
            title="CMS-0057-F Interoperability and Prior Auth",
            description="Interoperability and prior authorization final rule",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.INTEROPERABILITY,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 1, 1),
            source_url="https://example.com/cms-0057-f",
            impact_summary="Requires payers to improve prior auth APIs",
            action_required=["Implement API changes", "Update prior auth workflows"],
            tags=["interoperability", "prior auth"],
            priority=9,
        ),
        RegulatoryEvent(
            title="No Surprises Act Enforcement Update",
            description="Updated enforcement timelines",
            regulatory_body=RegulatoryBody.HHS,
            regulation_type=RegulationType.COMPLIANCE_REQUIREMENT,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 3, 1),
            source_url="https://example.com/nsa-2025",
            impact_summary="Adjusts enforcement priorities for NSA",
            action_required=["Review NSA workflows"],
            tags=["NSA", "enforcement"],
            priority=7,
        ),
        RegulatoryEvent(
            title="MHPAEA Final Rule 2024",
            description="Mental health parity enforcement changes",
            regulatory_body=RegulatoryBody.HHS,
            regulation_type=RegulationType.PARITY_REQUIREMENT,
            status=RegulationStatus.EFFECTIVE,
            effective_date=date(2024, 12, 1),
            source_url="https://example.com/mhpaea-2024",
            impact_summary="Tightens parity analyses",
            action_required=["Update parity checks", "Collect comparative analyses"],
            tags=["parity", "MHPAEA"],
            priority=8,
        ),
        RegulatoryEvent(
            title="FY2026 ICD-10-CM Updates",
            description="Future ICD-10-CM code set update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 10, 1),
            affected_code_types=["ICD-10-CM"],
            source_url="https://example.com/icd10-fy2026",
            impact_summary="Plan for FY2026 ICD-10 changes",
            action_required=["Plan ICD-10 FY2026 deployment"],
            tags=["ICD-10", "FY2026"],
            priority=8,
        ),
        RegulatoryEvent(
            title="CPT 2026 Updates",
            description="Future CPT code set update",
            regulatory_body=RegulatoryBody.AMA,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2026, 1, 1),
            affected_code_types=["CPT"],
            source_url="https://example.com/cpt-2026",
            impact_summary="Prepare for CPT 2026",
            action_required=["Review draft CPT 2026"],
            tags=["CPT", "2026"],
            priority=7,
        ),
        RegulatoryEvent(
            title="Q3 2025 NCCI Edits",
            description="Quarterly NCCI update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.BILLING_RULE_CHANGE,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 7, 1),
            affected_code_types=["NCCI"],
            source_url="https://example.com/ncci-q3-2025",
            impact_summary="Q3 NCCI edits scheduled",
            action_required=["Prepare for Q3 NCCI"],
            tags=["NCCI", "Q3"],
            priority=6,
        ),
        RegulatoryEvent(
            title="Q4 2025 NCCI Edits",
            description="Quarterly NCCI update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.BILLING_RULE_CHANGE,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2025, 10, 1),
            affected_code_types=["NCCI"],
            source_url="https://example.com/ncci-q4-2025",
            impact_summary="Q4 NCCI edits scheduled",
            action_required=["Prepare for Q4 NCCI"],
            tags=["NCCI", "Q4"],
            priority=6,
        ),
        RegulatoryEvent(
            title="2026 Medicare Physician Fee Schedule",
            description="Future MPFS rate update",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.FEE_SCHEDULE_UPDATE,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2026, 1, 1),
            affected_code_types=["CPT", "HCPCS"],
            source_url="https://example.com/mpfs-2026",
            impact_summary="Plan 2026 fee schedule updates",
            action_required=["Model 2026 payment impacts"],
            tags=["MPFS", "2026"],
            priority=8,
        ),
        RegulatoryEvent(
            title="Spring LCD/NCD Updates",
            description="Seasonal LCD/NCD changes",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.COVERAGE_POLICY_CHANGE,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2025, 4, 1),
            affected_code_types=["LCD", "NCD"],
            source_url="https://example.com/lcd-spring-2025",
            impact_summary="Coverage policy updates spring 2025",
            action_required=["Review LCD/NCD updates"],
            tags=["LCD", "NCD"],
            priority=6,
        ),
        RegulatoryEvent(
            title="State Medicaid Fee Schedule Updates (CA)",
            description="California Medicaid mid-year update",
            regulatory_body=RegulatoryBody.STATE,
            regulation_type=RegulationType.FEE_SCHEDULE_UPDATE,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2025, 7, 1),
            affected_code_types=["CPT", "HCPCS"],
            affected_states=["CA"],
            source_url="https://example.com/ca-medicaid-2025",
            impact_summary="State-specific fee changes",
            action_required=["Update CA Medicaid rates"],
            tags=["state", "CA"],
            priority=6,
        ),
        RegulatoryEvent(
            title="State Prior Authorization Reform (NY)",
            description="NY prior auth law change",
            regulatory_body=RegulatoryBody.STATE,
            regulation_type=RegulationType.AUTHORIZATION_CHANGE,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 9, 1),
            affected_states=["NY"],
            source_url="https://example.com/ny-prior-auth",
            impact_summary="NY prior auth requirements eased",
            action_required=["Update NY prior auth rules"],
            tags=["state", "NY"],
            priority=7,
        ),
        RegulatoryEvent(
            title="Emergency PHE Waiver Extension",
            description="Emergency waiver extension",
            regulatory_body=RegulatoryBody.HHS,
            regulation_type=RegulationType.EMERGENCY_REGULATION,
            status=RegulationStatus.EFFECTIVE,
            effective_date=date(2025, 2, 15),
            end_date=date(2025, 5, 15),
            source_url="https://example.com/phe-waiver",
            impact_summary="Temporary flexibilities extended",
            action_required=["Honor waiver flexibilities"],
            tags=["emergency", "waiver"],
            priority=9,
        ),
        RegulatoryEvent(
            title="Quality Reporting Update 2025",
            description="Quality measure set refresh",
            regulatory_body=RegulatoryBody.NCQA,
            regulation_type=RegulationType.REPORTING_REQUIREMENT,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 1, 1),
            source_url="https://example.com/quality-2025",
            impact_summary="New measures for 2025 reporting",
            action_required=["Update quality measure mapping"],
            tags=["quality", "NCQA"],
            priority=7,
        ),
        RegulatoryEvent(
            title="Price Transparency Update",
            description="Enhanced price transparency requirements",
            regulatory_body=RegulatoryBody.HHS,
            regulation_type=RegulationType.PRICE_TRANSPARENCY,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2025, 6, 1),
            source_url="https://example.com/price-transparency",
            impact_summary="Hospitals/payers must expand data sharing",
            action_required=["Plan price transparency changes"],
            tags=["price transparency"],
            priority=7,
        ),
        RegulatoryEvent(
            title="Prior Auth API Deadline",
            description="Payer API compliance deadline",
            regulatory_body=RegulatoryBody.PAYER,
            regulation_type=RegulationType.AUTHORIZATION_CHANGE,
            status=RegulationStatus.FINAL,
            effective_date=date(2025, 11, 1),
            comment_deadline=date(2025, 9, 1),
            source_url="https://example.com/payer-api",
            impact_summary="Payers must expose prior auth APIs",
            action_required=["Implement payer API endpoints"],
            tags=["API", "prior auth"],
            priority=8,
        ),
        RegulatoryEvent(
            title="Joint Commission Accreditation Update",
            description="Accreditation standards update",
            regulatory_body=RegulatoryBody.JOINT_COMMISSION,
            regulation_type=RegulationType.COMPLIANCE_REQUIREMENT,
            status=RegulationStatus.PROPOSED,
            effective_date=date(2025, 8, 1),
            source_url="https://example.com/jc-2025",
            impact_summary="Updated accreditation standards",
            action_required=["Review accreditation checklist"],
            tags=["accreditation"],
            priority=6,
        ),
        RegulatoryEvent(
            title="Future Placeholder ICD-10 Update",
            description="Guarantee at least one future event for testing",
            regulatory_body=RegulatoryBody.CMS,
            regulation_type=RegulationType.CODE_SET_UPDATE,
            status=RegulationStatus.PROPOSED,
            effective_date=today + timedelta(days=365),
            affected_code_types=["ICD-10-CM"],
            source_url="https://example.com/icd10-future-placeholder",
            impact_summary="Upcoming ICD-10 change window",
            action_required=["Plan future ICD-10 deployment"],
            tags=["ICD-10", "future"],
            priority=5,
        ),
    ]

    for ev in samples:
        calendar.add_event(ev)


__all__ = [
    "RegulatoryBody",
    "RegulationType",
    "RegulationStatus",
    "UrgencyLevel",
    "RegulatoryEvent",
    "CalendarQueryResult",
    "DOSValidationResult",
    "ComplianceDeadline",
    "ANNUAL_REGULATORY_TIMELINE",
    "CodeSetVersionTracker",
    "StalenessDetector",
    "RegulatoryCalendar",
    "seed_regulatory_events",
]
