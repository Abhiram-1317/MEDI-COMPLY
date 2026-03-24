"""Fraud detection utilities for MEDI-COMPLY.

This module provides detectors for common healthcare billing fraud patterns
including upcoding, unbundling, duplicate billing, modifier abuse, frequency
abuse, and billing-pattern anomalies. It produces structured fraud alerts that
can be consumed by guardrails (Layer 4 semantic checks) and claims adjudication
workflows.
"""

from __future__ import annotations

import math
import statistics
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FraudType(str, Enum):
    """Supported fraud patterns."""

    UPCODING = "UPCODING"
    DOWNCODING = "DOWNCODING"
    UNBUNDLING = "UNBUNDLING"
    PHANTOM_BILLING = "PHANTOM_BILLING"
    DUPLICATE_BILLING = "DUPLICATE_BILLING"
    MISREPRESENTATION = "MISREPRESENTATION"
    UNNECESSARY_SERVICE = "UNNECESSARY_SERVICE"
    MODIFIER_ABUSE = "MODIFIER_ABUSE"
    UNBUNDLED_LAB = "UNBUNDLED_LAB"
    FREQUENCY_ABUSE = "FREQUENCY_ABUSE"
    SITE_OF_SERVICE_FRAUD = "SITE_OF_SERVICE_FRAUD"
    KICKBACK_INDICATOR = "KICKBACK_INDICATOR"
    IDENTITY_FRAUD = "IDENTITY_FRAUD"
    TIME_BASED_FRAUD = "TIME_BASED_FRAUD"


class FraudSeverity(str, Enum):
    """Severity levels for fraud alerts."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FraudAlert(BaseModel):
    """Represents a single fraud alert raised by a detector."""

    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    fraud_type: FraudType
    severity: FraudSeverity
    confidence: float = Field(ge=0.0, le=1.0)
    description: str
    code_involved: str
    code_description: str
    evidence_gap: Optional[str] = None
    expected_code: Optional[str] = None
    expected_description: Optional[str] = None
    financial_impact: Optional[float] = None
    documentation_reference: Optional[str] = None
    rule_triggered: str
    recommended_action: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved: bool = False
    resolution_notes: Optional[str] = None


class FraudDetectionResult(BaseModel):
    """Aggregate result of a fraud scan."""

    scan_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scan_type: str
    alerts: List[FraudAlert] = Field(default_factory=list)
    total_alerts: int = 0
    critical_alerts: int = 0
    high_alerts: int = 0
    medium_alerts: int = 0
    low_alerts: int = 0
    overall_risk_score: float = 0.0
    risk_level: str = FraudSeverity.LOW.value
    is_blocked: bool = False
    processing_time_ms: float = 0.0
    recommendations: List[str] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Reference Data
# ---------------------------------------------------------------------------


UPCODING_PAIRS: Dict[str, List[str]] = {
    # E/M upcoding common mappings
    "99215": ["99214", "99213"],
    "99223": ["99222", "99221"],
    "99233": ["99232", "99231"],
    "99285": ["99284", "99283"],
    # Diagnosis upcoding
    "E11.65": ["E11.9"],
    "I21.0": ["I25.10"],
    "I21.9": ["I25.9"],
    "J18.9": ["J06.9"],
    "N18.4": ["N18.3"],
    "M54.51": ["M54.5"],
    # Procedure upcoding
    "43239": ["43235"],
    "29881": ["29880"],
}

LAB_PANELS: Dict[str, List[str]] = {
    "80048": ["82310", "82374", "82435", "82565", "82947", "84132", "84295", "84520"],
    "80053": ["82310", "82374", "82435", "82565", "82947", "84132", "84295", "84520", "82040", "82247", "82248", "84075", "84460", "84450"],
    "85025": ["85004", "85007", "85008", "85014", "85018", "85048"],
    "80061": ["82465", "83718", "84478"],
    "80076": ["82040", "82247", "82248", "84075", "84460", "84450", "82271"],
    "80069": ["82040", "82247", "82565", "82947", "84132", "84520", "84075", "84155", "84295", "84550"],
}


# ---------------------------------------------------------------------------
# Upcoding Detector
# ---------------------------------------------------------------------------


class UpcodingDetector:
    """Detects potential upcoding or downcoding based on documentation evidence."""

    def __init__(self, knowledge_manager: Optional[object] = None) -> None:
        self.knowledge_manager = knowledge_manager

    def check_upcoding(
        self,
        assigned_code: str,
        code_type: str,
        clinical_evidence: List[str],
        confidence_score: float,
        alternatives_considered: Optional[List[Dict]] = None,
        all_codes: Optional[Sequence[str]] = None,
        code_description: str = "",
    ) -> Optional[FraudAlert]:
        evidence_text = " ".join(clinical_evidence).lower()
        code = assigned_code.upper()
        rule_triggered = None

        all_codes_upper = [c.upper() for c in (all_codes or []) if c]
        description_text = (code_description or "").lower()

        # Severity mismatch (documentation mild but code implies severe acuity)
        severe_markers = ["severe", "stemi", "acute", "critical", "with failure", "with complications"]
        mild_markers = ["mild", "stable", "non-cardiac", "no complication", "routine", "discomfort"]
        has_severe_descriptor = any(term in description_text for term in severe_markers)
        if not rule_triggered and has_severe_descriptor and any(m in evidence_text for m in mild_markers):
            rule_triggered = "SEVERITY_MISMATCH"

        if not rule_triggered and code in {"I21.0", "I21.9", "I21.3", "I21.4"}:
            chronic_markers = [
                "stable ischemic",
                "chronic ischemic",
                "stable angina",
                "stable chronic ischemic heart disease",
            ]
            if any(marker in evidence_text for marker in chronic_markers):
                rule_triggered = "ACUTE_MI_OVERSTATED"

        if not rule_triggered and code_type.upper() == "ICD10":
            negated_hyper = any(phrase in evidence_text for phrase in ["without hyperglycemia", "no hyperglycemia"])
            has_hyperglycemia = "hyperglycemia" in evidence_text and not negated_hyper
            if not has_hyperglycemia and code.startswith("E11.6"):
                rule_triggered = "SPECIFICITY_INFLATION"

            negated_complication = any(phrase in evidence_text for phrase in ["no complication", "without complication"])
            has_ckd_support = any(code_val.startswith("N18") for code_val in all_codes_upper)
            has_complication = (("complication" in evidence_text or "ckd" in evidence_text) and not negated_complication) or has_ckd_support
            if not has_complication and code.startswith("E11.2"):
                rule_triggered = "COMPLICATION_ADDITION"

        # Level of service upcoding for E/M
        if not rule_triggered and code_type.upper() == "CPT" and code.startswith("99"):
            sev_level = self.get_severity_hierarchy(code, code_type)
            if sev_level and sev_level >= 4 and confidence_score < 0.5:
                rule_triggered = "LEVEL_OF_SERVICE_UPCODING"

        if not rule_triggered:
            return None

        expected = UPCODING_PAIRS.get(code, [])
        expected_code = expected[0] if expected else None
        return FraudAlert(
            fraud_type=FraudType.UPCODING,
            severity=FraudSeverity.HIGH,
            confidence=max(confidence_score, 0.6),
            description=f"Potential upcoding: {code} may be too severe for documentation",
            code_involved=code,
            code_description=code_description or "Assigned code potentially exceeds supported severity",
            evidence_gap="Insufficient severity or complication documentation",
            expected_code=expected_code,
            expected_description="Lower severity code likely appropriate" if expected_code else None,
            financial_impact=None,
            documentation_reference=evidence_text[:256] if evidence_text else None,
            rule_triggered=rule_triggered,
            recommended_action="REVIEW",
        )

    def check_em_level_appropriateness(self, em_code: str, documentation_elements: Dict) -> Optional[FraudAlert]:
        em_code = em_code.upper()
        hpi = documentation_elements.get("hpi_elements", 0)
        ros = documentation_elements.get("ros_systems", 0)
        exam = documentation_elements.get("exam_elements", 0)
        mdm = documentation_elements.get("mdm_complexity", "low").lower()

        # Simple rules-of-thumb for level mapping
        level_score = 0
        level_score += min(hpi, 4)
        level_score += min(ros, 10) // 3
        level_score += min(exam, 12) // 3
        mdm_map = {"straightforward": 1, "low": 2, "moderate": 3, "high": 4}
        level_score += mdm_map.get(mdm, 1)
        expected_level = min(max(2, level_score // 3), 5)

        # CPT E/M codes use the last digit for the level; using the last two digits over-flags (e.g., 99213 -> 13)
        actual_level = int(em_code[-1]) if em_code[-1].isdigit() else expected_level
        if actual_level - expected_level >= 2:
            return FraudAlert(
                fraud_type=FraudType.UPCODING,
                severity=FraudSeverity.HIGH,
                confidence=0.7,
                description=f"E/M level {em_code} exceeds documented elements (expected ~{expected_level})",
                code_involved=em_code,
                code_description="Evaluation and Management service",
                evidence_gap="Documentation elements do not support billed level",
                expected_code=f"9921{expected_level}" if em_code.startswith("9921") else None,
                expected_description="Lower E/M level aligns with documentation",
                documentation_reference=str(documentation_elements),
                rule_triggered="EM_LEVEL_MISMATCH",
                recommended_action="REVIEW",
            )
        return None

    def get_severity_hierarchy(self, code: str, code_type: str) -> Optional[int]:
        code = code.upper()
        if code_type.upper() == "CPT" and code.startswith("99") and len(code) == 5:
            try:
                return int(code[-1])  # heuristic: last digit as level
            except ValueError:
                return None
        if code_type.upper() == "ICD10" and code.startswith("E11"):
            if "65" in code:
                return 4
            if "2" in code:
                return 3
            return 1
        return None


# ---------------------------------------------------------------------------
# Unbundling Detector
# ---------------------------------------------------------------------------


class UnbundlingDetector:
    """Detects unbundling using NCCI edits and panel/component rules."""

    def __init__(self, ncci_engine: Optional[object] = None) -> None:
        self.ncci_engine = ncci_engine

    def check_unbundling(self, cpt_codes: List[str], ncci_engine: Optional[object] = None) -> List[FraudAlert]:
        engine: Any = ncci_engine or self.ncci_engine
        alerts: List[FraudAlert] = []
        codes = [c.upper() for c in cpt_codes]
        for i, code_a in enumerate(codes):
            for code_b in codes[i + 1 :]:
                if engine and hasattr(engine, "is_unbundled"):
                    try:
                        unbundled = engine.is_unbundled(code_a, code_b)
                    except Exception:
                        unbundled = False
                else:
                    unbundled = False
                if unbundled:
                    alerts.append(
                        FraudAlert(
                            fraud_type=FraudType.UNBUNDLING,
                            severity=FraudSeverity.HIGH,
                            confidence=0.75,
                            description=f"Codes {code_a} and {code_b} appear unbundled per NCCI edits",
                            code_involved=f"{code_a},{code_b}",
                            code_description="Potential NCCI Column1/Column2 violation",
                            evidence_gap="Billing separately for bundled services",
                            expected_code=code_a,
                            rule_triggered="NCCI_UNBUNDLING",
                            recommended_action="REVIEW",
                        )
                    )
        alerts.extend(self.check_surgical_unbundling(codes))
        panel_alert = self.check_lab_panel_unbundling(codes)
        if panel_alert:
            alerts.append(panel_alert)
        return alerts

    def check_lab_panel_unbundling(self, lab_codes: List[str]) -> Optional[FraudAlert]:
        codes = set(lab_codes)
        best_alert: Optional[FraudAlert] = None
        best_hits = 0
        best_component_count = 0
        best_coverage = 0.0
        best_priority: Tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        for panel_code, components in LAB_PANELS.items():
            component_hits = codes.intersection(set(components))
            if len(component_hits) >= 3 and panel_code not in codes:
                coverage = len(component_hits) / len(components)
                hits = len(component_hits)
                # Priority: group 2 for richer signal (4+ hits) where we prefer comprehensive panels; group 1 uses coverage to pick best fit
                if hits >= 4:
                    priority = (2.0, float(hits), float(len(components)), coverage)
                else:
                    priority = (1.0, coverage, float(hits), float(-len(components)))
                if priority > best_priority:
                    best_priority = priority
                    best_hits = hits
                    best_component_count = len(components)
                    best_coverage = coverage
                    best_alert = FraudAlert(
                        fraud_type=FraudType.UNBUNDLED_LAB,
                        severity=FraudSeverity.MEDIUM,
                        confidence=0.7,
                        description=f"Lab components {sorted(component_hits)} suggest {panel_code} panel should be used",
                        code_involved=",".join(sorted(component_hits)),
                        code_description="Lab panel components billed separately",
                        evidence_gap="Panel code absent",
                        expected_code=panel_code,
                        expected_description="Bill panel instead of individual components",
                        rule_triggered="LAB_PANEL_UNBUNDLING",
                        recommended_action="EDUCATE",
                    )
        return best_alert

    def check_surgical_unbundling(self, cpt_codes: List[str]) -> List[FraudAlert]:
        alerts: List[FraudAlert] = []
        codes = [c.upper() for c in cpt_codes]
        surgical_includes = {"12001", "12002", "12004"}  # simple closures often bundled
        for code in codes:
            if code in surgical_includes and any(c.startswith("2") for c in codes):
                alerts.append(
                    FraudAlert(
                        fraud_type=FraudType.UNBUNDLING,
                        severity=FraudSeverity.MEDIUM,
                        confidence=0.6,
                        description=f"Closure code {code} likely bundled into primary procedure",
                        code_involved=code,
                        code_description="Surgical closure",
                        evidence_gap="Primary procedure includes closure",
                        rule_triggered="SURGICAL_BUNDLE",
                        recommended_action="REVIEW",
                    )
                )
        return alerts


# ---------------------------------------------------------------------------
# Duplicate Billing Detector
# ---------------------------------------------------------------------------


class DuplicateBillingDetector:
    """Detects exact and near-duplicate billing events."""

    def check_exact_duplicate(self, claim_data: Dict, previous_claims: Optional[List[Dict]] = None) -> Optional[FraudAlert]:
        if not previous_claims:
            return None
        for prev in previous_claims:
            if (
                prev.get("patient_id") == claim_data.get("patient_id")
                and prev.get("date_of_service") == claim_data.get("date_of_service")
                and prev.get("provider_id") == claim_data.get("provider_id")
                and set(prev.get("cpt_codes", [])) == set(claim_data.get("cpt_codes", []))
            ):
                return FraudAlert(
                    fraud_type=FraudType.DUPLICATE_BILLING,
                    severity=FraudSeverity.HIGH,
                    confidence=0.9,
                    description="Exact duplicate claim detected",
                    code_involved=",".join(claim_data.get("cpt_codes", [])),
                    code_description="Duplicate billing",
                    rule_triggered="EXACT_DUPLICATE",
                    recommended_action="BLOCK",
                )
        return None

    def check_near_duplicate(
        self,
        claim_data: Dict,
        previous_claims: Optional[List[Dict]] = None,
        similarity_threshold: float = 0.9,
    ) -> Optional[FraudAlert]:
        if not previous_claims:
            return None
        for prev in previous_claims:
            if prev.get("patient_id") != claim_data.get("patient_id"):
                continue
            similarity = self._calculate_claim_similarity(prev, claim_data)
            dates: Set[str] = {
                d
                for d in (
                    prev.get("date_of_service"),
                    claim_data.get("date_of_service"),
                )
                if isinstance(d, str)
            }
            close_dates = len(dates) == 1 or self._dates_close(dates)
            if close_dates and prev.get("provider_id") == claim_data.get("provider_id"):
                similarity += 0.2
            if close_dates and prev.get("provider_id") != claim_data.get("provider_id"):
                similarity += 0.1
            similarity = min(similarity, 1.0)

            if similarity >= similarity_threshold:
                return FraudAlert(
                    fraud_type=FraudType.DUPLICATE_BILLING,
                    severity=FraudSeverity.MEDIUM,
                    confidence=similarity,
                    description="Near-duplicate claim detected",
                    code_involved=",".join(claim_data.get("cpt_codes", [])),
                    code_description="Potential duplicate billing",
                    rule_triggered="NEAR_DUPLICATE",
                    recommended_action="REVIEW",
                )
        return None

    def _calculate_claim_similarity(self, claim1: Dict, claim2: Dict) -> float:
        cpt1 = set(claim1.get("cpt_codes", []))
        cpt2 = set(claim2.get("cpt_codes", []))
        icd1 = set(claim1.get("icd10_codes", []))
        icd2 = set(claim2.get("icd10_codes", []))

        cpt_union = len(cpt1 | cpt2)
        icd_union = len(icd1 | icd2)
        cpt_score = len(cpt1 & cpt2) / cpt_union if cpt_union else 0.0
        icd_score = len(icd1 & icd2) / icd_union if icd_union else 0.0

        # CPT overlap carries more weight than diagnosis overlap for duplicates
        return 0.75 * cpt_score + 0.25 * icd_score

    def _dates_close(self, dates: Iterable[str]) -> bool:
        try:
            parsed = [datetime.fromisoformat(d) for d in dates if d]
            if len(parsed) < 2:
                return False
            delta = max(parsed) - min(parsed)
            return delta <= timedelta(days=2)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Frequency Abuse Detector
# ---------------------------------------------------------------------------


class FrequencyAbuseDetector:
    """Detects frequency abuse and impossible time billing."""

    DEFAULT_LIMITS = {
        "99213": {"per_day": 1, "per_month": 4},
        "99214": {"per_day": 1, "per_month": 4},
        "99215": {"per_day": 1, "per_month": 4},
        "MRI": {"per_6mo": 1},
        "PT": {"per_week": 3},
        "LAB_PANEL": {"per_day": 1},
    }

    def check_frequency(
        self,
        cpt_code: str,
        patient_id: str,
        service_history: List[Dict],
        max_frequency: Optional[Dict] = None,
    ) -> Optional[FraudAlert]:
        limits = max_frequency or self.DEFAULT_LIMITS.get(cpt_code, {})
        if not limits:
            return None
        per_day = limits.get("per_day")
        per_week = limits.get("per_week")
        per_month = limits.get("per_month")
        per_6mo = limits.get("per_6mo")

        daily_counts: Dict[str, int] = {}
        for svc in service_history:
            if svc.get("patient_id") != patient_id:
                continue
            date = svc.get("date") or svc.get("date_of_service")
            if date:
                daily_counts[date] = daily_counts.get(date, 0) + 1
        if per_day and any(count > per_day for count in daily_counts.values()):
            return self._make_frequency_alert(cpt_code, "per-day limit exceeded", FraudSeverity.MEDIUM)

        if per_week:
            weekly_counts: Dict[str, int] = {}
            for svc in service_history:
                if svc.get("patient_id") != patient_id:
                    continue
                date = svc.get("date") or svc.get("date_of_service")
                if date:
                    week_key = date[:7]
                    weekly_counts[week_key] = weekly_counts.get(week_key, 0) + 1
            if any(count > per_week for count in weekly_counts.values()):
                return self._make_frequency_alert(cpt_code, "per-week limit exceeded", FraudSeverity.MEDIUM)

        if per_month:
            monthly_counts: Dict[str, int] = {}
            for svc in service_history:
                if svc.get("patient_id") != patient_id:
                    continue
                date = svc.get("date") or svc.get("date_of_service")
                if date:
                    month_key = date[:7]
                    monthly_counts[month_key] = monthly_counts.get(month_key, 0) + 1
            if any(count > per_month for count in monthly_counts.values()):
                return self._make_frequency_alert(cpt_code, "per-month limit exceeded", FraudSeverity.MEDIUM)

        if per_6mo:
            # crude rolling window check
            dates = [svc.get("date") or svc.get("date_of_service") for svc in service_history if svc.get("patient_id") == patient_id and svc.get("code") == cpt_code]
            if len(dates) > per_6mo:
                return self._make_frequency_alert(cpt_code, "6-month frequency exceeded", FraudSeverity.HIGH)
        return None

    def check_impossible_time(self, services: List[Dict], provider_id: str, date: str) -> Optional[FraudAlert]:
        total_minutes = 0
        for svc in services:
            if svc.get("provider_id") != provider_id:
                continue
            if (svc.get("date") or svc.get("date_of_service")) != date:
                continue
            duration = svc.get("duration_minutes") or svc.get("expected_duration_minutes") or 0
            if svc.get("time_based"):
                total_minutes += duration
            elif duration:
                total_minutes += duration
        if total_minutes > 16 * 60:
            return FraudAlert(
                fraud_type=FraudType.TIME_BASED_FRAUD,
                severity=FraudSeverity.CRITICAL,
                confidence=0.9,
                description=f"Provider billed {total_minutes/60:.1f} hours in one day",
                code_involved="TIME",
                code_description="Time-based services",
                rule_triggered="IMPOSSIBLE_TIME",
                recommended_action="BLOCK",
            )
        return None

    def _make_frequency_alert(self, code: str, message: str, severity: FraudSeverity) -> FraudAlert:
        return FraudAlert(
            fraud_type=FraudType.FREQUENCY_ABUSE,
            severity=severity,
            confidence=0.65,
            description=f"{code}: {message}",
            code_involved=code,
            code_description="Frequency threshold exceeded",
            rule_triggered="FREQUENCY_ABUSE",
            recommended_action="REVIEW",
        )


# ---------------------------------------------------------------------------
# Modifier Abuse Detector
# ---------------------------------------------------------------------------


class ModifierAbuseDetector:
    """Detects improper use of CPT modifiers."""

    def check_modifier_abuse(
        self,
        cpt_code: str,
        modifiers: List[str],
        clinical_context: Optional[Dict] = None,
    ) -> List[FraudAlert]:
        alerts: List[FraudAlert] = []
        mods = [m.upper() for m in modifiers]
        proc_context = (clinical_context or {}).get("procedure", "")
        usage_rate = (clinical_context or {}).get("modifier_usage_rate", 0.0)

        if "25" in mods and usage_rate and usage_rate > 0.5:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.MODIFIER_ABUSE,
                    severity=FraudSeverity.MEDIUM,
                    confidence=0.65,
                    description=f"Modifier 25 used frequently with {cpt_code}",
                    code_involved=cpt_code,
                    code_description=proc_context or "E/M with procedure",
                    rule_triggered="MOD25_OVERUSE",
                    recommended_action="REVIEW",
                )
            )
        if "59" in mods:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.MODIFIER_ABUSE,
                    severity=FraudSeverity.MEDIUM,
                    confidence=0.6,
                    description="Modifier 59 used to bypass edits; ensure distinct service",
                    code_involved=cpt_code,
                    code_description=proc_context or "Distinct procedural service",
                    evidence_gap="Documentation must show distinct lesion/body site/session",
                    rule_triggered="MOD59_BYPASS",
                    recommended_action="REVIEW",
                )
            )
        if "22" in mods:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.MODIFIER_ABUSE,
                    severity=FraudSeverity.HIGH,
                    confidence=0.7,
                    description="Modifier 22 (increased services) requires substantial documentation",
                    code_involved=cpt_code,
                    code_description=proc_context or "Increased procedural services",
                    evidence_gap="No supporting documentation found" if not clinical_context else None,
                    rule_triggered="MOD22_DOC_REQUIRED",
                    recommended_action="REVIEW",
                )
            )
        if "76" in mods:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.MODIFIER_ABUSE,
                    severity=FraudSeverity.MEDIUM,
                    confidence=0.6,
                    description="Repeat procedure modifier requires necessity documentation",
                    code_involved=cpt_code,
                    code_description=proc_context or "Repeat procedure",
                    rule_triggered="MOD76_REPEAT",
                    recommended_action="REVIEW",
                )
            )
        return alerts


# ---------------------------------------------------------------------------
        if "26" in mods:
            allowed_prefixes = ("7", "9")  # radiology, some cardiology diagnostics
            if not cpt_code.startswith(allowed_prefixes):
                alerts.append(
                    FraudAlert(
                        fraud_type=FraudType.MODIFIER_ABUSE,
                        severity=FraudSeverity.MEDIUM,
                        confidence=0.6,
                        description="Modifier 26 (professional component) used on code that is typically global only",
                        code_involved=cpt_code,
                        code_description=proc_context or "Professional component",
                        evidence_gap="Confirm technical component billed separately and code eligible for split billing",
                        rule_triggered="MOD26_INAPPROPRIATE",
                        recommended_action="REVIEW",
                    )
                )
# Billing Pattern Analyzer
# ---------------------------------------------------------------------------


class BillingPatternAnalyzer:
    """Analyzes provider/patient level billing patterns for anomalies."""

    def analyze_provider_patterns(self, provider_id: str, claims_history: List[Dict]) -> List[FraudAlert]:
        if not claims_history:
            return []
        alerts: List[FraudAlert] = []
        em_codes = [c for claim in claims_history for c in claim.get("cpt_codes", []) if c.startswith("99")]
        mod_usage = [m for claim in claims_history for m in claim.get("modifiers", {}).values() for m in (m if isinstance(m, list) else [m])]
        volume = len(claims_history)

        if em_codes:
            high_levels = [c for c in em_codes if c.endswith("4") or c.endswith("5")]
            ratio = len(high_levels) / len(em_codes)
            if ratio > 0.7 and volume > 20:
                alerts.append(
                    FraudAlert(
                        fraud_type=FraudType.UPCODING,
                        severity=FraudSeverity.MEDIUM,
                        confidence=0.65,
                        description="High proportion of Level 4/5 E/M codes compared to peers",
                        code_involved="E/M",
                        code_description="Provider coding pattern",
                        rule_triggered="EM_DISTRIBUTION_OUTLIER",
                        recommended_action="REVIEW",
                    )
                )

        if mod_usage:
            mod25_rate = mod_usage.count("25") / len(mod_usage)
            if mod25_rate > 0.3 and volume > 10:
                alerts.append(
                    FraudAlert(
                        fraud_type=FraudType.MODIFIER_ABUSE,
                        severity=FraudSeverity.MEDIUM,
                        confidence=0.6,
                        description="Modifier 25 usage above peer norms",
                        code_involved="MOD25",
                        code_description="Modifier usage pattern",
                        rule_triggered="MOD25_PATTERN",
                        recommended_action="REVIEW",
                    )
                )

        if volume > 0:
            # crude peer comparison: flag charge outliers even in small samples
            charges = [sum((claim.get("charges", {}) or {}).values()) for claim in claims_history]
            if charges:
                mean_charge = statistics.mean(charges)
                std_charge = statistics.pstdev(charges) if len(charges) > 1 else 0
                median_charge = statistics.median(charges)
                max_charge = max(charges)
                outlier_by_std = std_charge and max_charge > mean_charge + 1.5 * std_charge
                outlier_by_ratio = median_charge and max_charge > 3 * median_charge
                if outlier_by_std or outlier_by_ratio:
                    alerts.append(
                        FraudAlert(
                            fraud_type=FraudType.FREQUENCY_ABUSE,
                            severity=FraudSeverity.MEDIUM,
                            confidence=0.55,
                            description="Billing volume or charges significantly above peers",
                            code_involved="VOLUME",
                            code_description="Provider billing volume",
                            rule_triggered="VOLUME_OUTLIER",
                            recommended_action="REVIEW",
                        )
                    )

        return alerts

    def analyze_patient_patterns(self, patient_id: str, claims_history: List[Dict]) -> List[FraudAlert]:
        alerts: List[FraudAlert] = []
        provider_set = {claim.get("provider_id") for claim in claims_history if claim.get("patient_id") == patient_id}
        if len(provider_set) > 5:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.KICKBACK_INDICATOR,
                    severity=FraudSeverity.LOW,
                    confidence=0.5,
                    description="Patient seeing many providers for same period (possible doctor shopping)",
                    code_involved="PATTERN",
                    code_description="Patient pattern",
                    rule_triggered="DOCTOR_SHOPPING",
                    recommended_action="REVIEW",
                )
            )
        locations = {claim.get("location") for claim in claims_history if claim.get("patient_id") == patient_id and claim.get("location")}
        if len(locations) > 1:
            alerts.append(
                FraudAlert(
                    fraud_type=FraudType.SITE_OF_SERVICE_FRAUD,
                    severity=FraudSeverity.MEDIUM,
                    confidence=0.55,
                    description="Services in multiple locations same period; verify legitimacy",
                    code_involved="LOCATION",
                    code_description="Geographic pattern",
                    rule_triggered="IMPOSSIBLE_GEOGRAPHY",
                    recommended_action="REVIEW",
                )
            )
        return alerts


# ---------------------------------------------------------------------------
# Fraud Detector Engine
# ---------------------------------------------------------------------------


class FraudDetector:
    """Main orchestrator for fraud detection across coding decisions and claims."""

    def __init__(self, ncci_engine: Optional[object] = None, knowledge_manager: Optional[object] = None) -> None:
        self.upcoding_detector = UpcodingDetector(knowledge_manager=knowledge_manager)
        self.unbundling_detector = UnbundlingDetector(ncci_engine=ncci_engine)
        self.duplicate_detector = DuplicateBillingDetector()
        self.frequency_detector = FrequencyAbuseDetector()
        self.modifier_detector = ModifierAbuseDetector()
        self.pattern_analyzer = BillingPatternAnalyzer()

    def scan_coding_decision(
        self,
        assigned_codes: List[Dict],
        clinical_evidence: List[str],
        encounter_type: str,
        patient_demographics: Optional[Dict] = None,
        confidence_scores: Optional[Dict[str, float]] = None,
    ) -> FraudDetectionResult:
        start = datetime.utcnow()
        alerts: List[FraudAlert] = []
        cpt_codes: List[str] = []
        all_code_values = [entry.get("code", "") for entry in assigned_codes if entry.get("code")]
        for code_entry in assigned_codes:
            code = code_entry.get("code", "").upper()
            code_type = code_entry.get("code_type", "CPT")
            cpt_codes.append(code) if code_type.upper() == "CPT" else None
            confidence = (confidence_scores or {}).get(code, 0.5)
            alert = self.upcoding_detector.check_upcoding(
                assigned_code=code,
                code_type=code_type,
                clinical_evidence=clinical_evidence,
                confidence_score=confidence,
                alternatives_considered=code_entry.get("alternatives"),
                all_codes=all_code_values,
                code_description=code_entry.get("description", ""),
            )
            if alert:
                alerts.append(alert)
            mods = code_entry.get("modifiers", []) or []
            alerts.extend(self.modifier_detector.check_modifier_abuse(code, mods, clinical_context=code_entry))

        alerts.extend(self.unbundling_detector.check_unbundling(cpt_codes))
        return self._build_result(alerts, "CODING", start)

    def scan_claim(self, claim_data: Dict, previous_claims: Optional[List[Dict]] = None) -> FraudDetectionResult:
        start = datetime.utcnow()
        alerts: List[FraudAlert] = []
        cpt_codes = claim_data.get("cpt_codes", [])
        icd10_codes = claim_data.get("icd10_codes", [])
        evidence = claim_data.get("clinical_evidence", []) or []

        # Upcoding checks for all diagnosis and procedure codes
        for code in cpt_codes:
            alert = self.upcoding_detector.check_upcoding(code, "CPT", evidence, 0.5)
            if alert:
                alerts.append(alert)
        for code in icd10_codes:
            alert = self.upcoding_detector.check_upcoding(code, "ICD10", evidence, 0.5)
            if alert:
                alerts.append(alert)

        alerts.extend(self.unbundling_detector.check_unbundling(cpt_codes))
        dup = self.duplicate_detector.check_exact_duplicate(claim_data, previous_claims)
        if dup:
            alerts.append(dup)
        near = self.duplicate_detector.check_near_duplicate(claim_data, previous_claims)
        if near:
            alerts.append(near)

        freq_alerts = []
        service_history = claim_data.get("service_history", [])
        for code in cpt_codes:
            freq = self.frequency_detector.check_frequency(code, claim_data.get("patient_id", ""), service_history)
            if freq:
                freq_alerts.append(freq)
        alerts.extend(freq_alerts)

        time_alert = self.frequency_detector.check_impossible_time(service_history, claim_data.get("provider_id", ""), claim_data.get("date_of_service", ""))
        if time_alert:
            alerts.append(time_alert)

        # Modifier abuse per code
        modifiers_map = claim_data.get("modifiers", {}) or {}
        for code, mods in modifiers_map.items():
            alerts.extend(self.modifier_detector.check_modifier_abuse(code, mods if isinstance(mods, list) else [mods], clinical_context=claim_data))

        # Pattern analysis
        provider_history = previous_claims or []
        alerts.extend(self.pattern_analyzer.analyze_provider_patterns(claim_data.get("provider_id", ""), provider_history))
        alerts.extend(self.pattern_analyzer.analyze_patient_patterns(claim_data.get("patient_id", ""), provider_history))

        return self._build_result(alerts, "CLAIM", start)

    def scan_billing_patterns(self, provider_id: str, claims_history: List[Dict]) -> FraudDetectionResult:
        start = datetime.utcnow()
        alerts = self.pattern_analyzer.analyze_provider_patterns(provider_id, claims_history)
        return self._build_result(alerts, "BILLING_PATTERN", start)

    def calculate_risk_score(self, alerts: List[FraudAlert]) -> float:
        score = 0.0
        for alert in alerts:
            weight = {
                FraudSeverity.CRITICAL: 0.4,
                FraudSeverity.HIGH: 0.2,
                FraudSeverity.MEDIUM: 0.1,
                FraudSeverity.LOW: 0.05,
            }.get(alert.severity, 0.05)
            conf = min(max(alert.confidence, 0.1), 1.0)
            impact = alert.financial_impact or 0
            impact_factor = 0.05 if impact < 1000 else 0.1 if impact < 5000 else 0.2
            score += weight * conf + impact_factor
        return min(score, 1.0)

    def determine_risk_level(self, risk_score: float) -> str:
        if risk_score >= 0.8:
            return FraudSeverity.CRITICAL.value
        if risk_score >= 0.5:
            return FraudSeverity.HIGH.value
        if risk_score >= 0.2:
            return FraudSeverity.MEDIUM.value
        return FraudSeverity.LOW.value

    def generate_fraud_summary(self, result: FraudDetectionResult) -> str:
        if not result.alerts:
            return "No fraud indicators detected."
        lines = [f"Fraud scan {result.scan_id}: {len(result.alerts)} alerts, risk={result.risk_level} ({result.overall_risk_score:.2f})"]
        for alert in result.alerts:
            lines.append(f"- [{alert.severity}] {alert.fraud_type}: {alert.description} (code={alert.code_involved})")
        return "\n".join(lines)

    def suggest_correct_codes(self, fraud_alert: FraudAlert) -> List[Dict]:
        suggestions: List[Dict] = []
        if fraud_alert.fraud_type == FraudType.UPCODING and fraud_alert.expected_code:
            suggestions.append(
                {
                    "code": fraud_alert.expected_code,
                    "description": fraud_alert.expected_description or "Lower severity code likely appropriate",
                    "reasoning": f"Suggested based on rule {fraud_alert.rule_triggered}",
                }
            )
        lower_codes = UPCODING_PAIRS.get(fraud_alert.code_involved, [])
        for code in lower_codes:
            suggestions.append({"code": code, "description": "Potential correct code", "reasoning": "Known upcoding pair"})
        return suggestions

    def _build_result(self, alerts: List[FraudAlert], scan_type: str, start_time: datetime) -> FraudDetectionResult:
        total = len(alerts)
        crit = sum(1 for a in alerts if a.severity == FraudSeverity.CRITICAL)
        high = sum(1 for a in alerts if a.severity == FraudSeverity.HIGH)
        med = sum(1 for a in alerts if a.severity == FraudSeverity.MEDIUM)
        low = sum(1 for a in alerts if a.severity == FraudSeverity.LOW)
        score = self.calculate_risk_score(alerts)
        risk_level = self.determine_risk_level(score)
        blocked = any(a.severity in {FraudSeverity.CRITICAL, FraudSeverity.HIGH} and a.recommended_action in {"BLOCK", "REVIEW"} for a in alerts)
        elapsed_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        recommendations = sorted({a.recommended_action for a in alerts}) if alerts else []
        result = FraudDetectionResult(
            scan_type=scan_type,
            alerts=alerts,
            total_alerts=total,
            critical_alerts=crit,
            high_alerts=high,
            medium_alerts=med,
            low_alerts=low,
            overall_risk_score=score,
            risk_level=risk_level,
            is_blocked=blocked,
            processing_time_ms=elapsed_ms,
            recommendations=recommendations,
        )
        result.summary = self.generate_fraud_summary(result)
        return result


__all__ = [
    "FraudType",
    "FraudSeverity",
    "FraudAlert",
    "FraudDetectionResult",
    "UpcodingDetector",
    "UnbundlingDetector",
    "DuplicateBillingDetector",
    "FrequencyAbuseDetector",
    "ModifierAbuseDetector",
    "BillingPatternAnalyzer",
    "FraudDetector",
    "UPCODING_PAIRS",
    "LAB_PANELS",
]
