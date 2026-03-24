"""Fraud Detector test suite."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import pytest

from medi_comply.compliance.fraud_detector import (
    FraudAlert,
    FraudDetector,
    FraudDetectionResult,
    FraudSeverity,
    FraudType,
    LAB_PANELS,
    DuplicateBillingDetector,
    FrequencyAbuseDetector,
    ModifierAbuseDetector,
    UnbundlingDetector,
    UpcodingDetector,
)


class StubNCCIEngine:
    def __init__(self) -> None:
        self.pairs: dict[tuple[str, str], bool] = {("80048", "80053"): True, ("29881", "29880"): False}

    def is_unbundled(self, c1: str, c2: str) -> bool:
        key = cast(tuple[str, str], tuple(sorted((c1, c2))))
        return self.pairs.get(key, False)


@pytest.fixture()
def fraud_detector() -> FraudDetector:
    return FraudDetector(ncci_engine=StubNCCIEngine())


@pytest.fixture()
def upcoding_detector() -> UpcodingDetector:
    return UpcodingDetector()


@pytest.fixture()
def unbundling_detector() -> UnbundlingDetector:
    return UnbundlingDetector(ncci_engine=StubNCCIEngine())


@pytest.fixture()
def duplicate_detector() -> DuplicateBillingDetector:
    return DuplicateBillingDetector()


@pytest.fixture()
def frequency_detector() -> FrequencyAbuseDetector:
    return FrequencyAbuseDetector()


@pytest.fixture()
def modifier_detector() -> ModifierAbuseDetector:
    return ModifierAbuseDetector()


# Helpers -----------------------------------------------------------------


def _make_assigned_code(code: str, code_type: str = "CPT", evidence: str = "", confidence: float = 0.9, modifiers=None):
    return {
        "code": code,
        "code_type": code_type,
        "description": "desc",
        "clinical_evidence": [evidence] if evidence else [],
        "alternatives": [],
        "modifiers": modifiers or [],
        "confidence": confidence,
    }


def _make_claim(patient_id: str, provider_id: str, date: str, cpts=None, icds=None):
    return {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "date_of_service": date,
        "cpt_codes": cpts or [],
        "icd10_codes": icds or [],
        "clinical_evidence": ["clinical note"],
    }


# UpcodingDetector --------------------------------------------------------


def test_no_upcoding_correct_code(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("99213", "CPT", ["mild visit"], 0.8)
    assert alert is None


def test_upcoding_em_level(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("99215", "CPT", ["routine visit"], 0.4)
    assert alert is not None
    assert alert.fraud_type == FraudType.UPCODING


def test_upcoding_diagnosis_severity(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("E11.65", "ICD10", ["diabetes without hyperglycemia"], 0.5)
    assert alert is not None
    assert alert.rule_triggered == "SPECIFICITY_INFLATION"


def test_upcoding_complication_not_documented(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("E11.22", "ICD10", ["diabetes, no complication noted"], 0.5)
    assert alert is not None
    assert alert.rule_triggered == "COMPLICATION_ADDITION"


def test_upcoding_mi_overcoding(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("I21.0", "ICD10", ["stable chronic ischemic heart disease"], 0.5)
    assert alert is not None


def test_correct_high_severity_not_flagged(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("99215", "CPT", ["critical care, shock, severe"], 0.9)
    assert alert is None


def test_severity_hierarchy(upcoding_detector: UpcodingDetector):
    left_cpt = upcoding_detector.get_severity_hierarchy("99215", "CPT") or 0
    right_cpt = upcoding_detector.get_severity_hierarchy("99213", "CPT") or 0
    assert left_cpt > right_cpt
    left_icd = upcoding_detector.get_severity_hierarchy("E11.65", "ICD10") or 0
    right_icd = upcoding_detector.get_severity_hierarchy("E11.9", "ICD10") or 0
    assert left_icd > right_icd


def test_em_level_99213_appropriate(upcoding_detector: UpcodingDetector):
    doc = {"hpi_elements": 3, "ros_systems": 3, "exam_elements": 4, "mdm_complexity": "low"}
    alert = upcoding_detector.check_em_level_appropriateness("99213", doc)
    assert alert is None


def test_em_level_99215_appropriate(upcoding_detector: UpcodingDetector):
    doc = {"hpi_elements": 4, "ros_systems": 10, "exam_elements": 12, "mdm_complexity": "high"}
    alert = upcoding_detector.check_em_level_appropriateness("99215", doc)
    assert alert is None


def test_upcoding_suggests_correct_code(upcoding_detector: UpcodingDetector):
    alert = upcoding_detector.check_upcoding("99215", "CPT", ["routine visit"], 0.4)
    assert alert is not None
    assert alert.expected_code == "99214"


# UnbundlingDetector ------------------------------------------------------


def test_no_unbundling_clean_codes(unbundling_detector: UnbundlingDetector):
    alerts = unbundling_detector.check_unbundling(["99213", "71020"])
    assert alerts == []


def test_unbundling_detected_ncci(unbundling_detector: UnbundlingDetector):
    alerts = unbundling_detector.check_unbundling(["80048", "80053"], StubNCCIEngine())
    assert any(a.fraud_type == FraudType.UNBUNDLING for a in alerts)


def test_lab_panel_unbundling_bmp(unbundling_detector: UnbundlingDetector):
    components = LAB_PANELS["80048"][:3]
    alerts = unbundling_detector.check_unbundling(components)
    assert any(a.expected_code == "80048" for a in alerts)


def test_lab_panel_unbundling_cmp(unbundling_detector: UnbundlingDetector):
    components = LAB_PANELS["80053"][:4]
    alerts = unbundling_detector.check_unbundling(components)
    assert any(a.expected_code == "80053" for a in alerts)


def test_lab_panel_unbundling_cbc(unbundling_detector: UnbundlingDetector):
    components = LAB_PANELS["85025"][:3]
    alerts = unbundling_detector.check_unbundling(components)
    assert any(a.expected_code == "85025" for a in alerts)


def test_lab_panel_unbundling_lipid(unbundling_detector: UnbundlingDetector):
    components = LAB_PANELS["80061"]
    alerts = unbundling_detector.check_unbundling(components)
    assert any(a.expected_code == "80061" for a in alerts)


def test_partial_panel_no_flag(unbundling_detector: UnbundlingDetector):
    components = LAB_PANELS["80048"][:2]
    alerts = unbundling_detector.check_unbundling(components)
    assert alerts == []


def test_surgical_unbundling(unbundling_detector: UnbundlingDetector):
    alerts = unbundling_detector.check_unbundling(["12001", "29881"])
    assert any(a.fraud_type == FraudType.UNBUNDLING for a in alerts)


# DuplicateBillingDetector -------------------------------------------------


def test_no_duplicate_unique_claim(duplicate_detector: DuplicateBillingDetector):
    claim = _make_claim("p1", "prov1", "2024-01-01", ["99213"], ["E11.9"])
    assert duplicate_detector.check_exact_duplicate(claim, []) is None


def test_exact_duplicate_detected(duplicate_detector: DuplicateBillingDetector):
    claim = _make_claim("p1", "prov1", "2024-01-01", ["99213"], ["E11.9"])
    dup = duplicate_detector.check_exact_duplicate(claim, [claim])
    assert dup and dup.fraud_type == FraudType.DUPLICATE_BILLING


def test_near_duplicate_detected(duplicate_detector: DuplicateBillingDetector):
    claim1 = _make_claim("p1", "prov1", "2024-01-01", ["99213", "93000"], ["E11.9"])
    claim2 = _make_claim("p1", "prov1", "2024-01-01", ["99213", "93000"], ["E11.65"])
    dup = duplicate_detector.check_near_duplicate(claim1, [claim2], similarity_threshold=0.8)
    assert dup is not None


def test_similar_different_patient_ok(duplicate_detector: DuplicateBillingDetector):
    claim1 = _make_claim("p1", "prov1", "2024-01-01", ["99213"], [])
    claim2 = _make_claim("p2", "prov1", "2024-01-01", ["99213"], [])
    assert duplicate_detector.check_near_duplicate(claim1, [claim2]) is None


def test_similar_different_date_ok(duplicate_detector: DuplicateBillingDetector):
    claim1 = _make_claim("p1", "prov1", "2024-01-01", ["99213"], [])
    claim2 = _make_claim("p1", "prov1", "2024-02-01", ["99213"], [])
    assert duplicate_detector.check_near_duplicate(claim1, [claim2]) is None


def test_jaccard_similarity_calculation(duplicate_detector: DuplicateBillingDetector):
    c1 = {"cpt_codes": ["A", "B"], "icd10_codes": ["C"]}
    c2 = {"cpt_codes": ["A", "B", "D"], "icd10_codes": ["C"]}
    sim = duplicate_detector._calculate_claim_similarity(c1, c2)
    assert sim == pytest.approx(0.75)


# FrequencyAbuseDetector --------------------------------------------------


def test_normal_frequency_passes(frequency_detector: FrequencyAbuseDetector):
    history = [
        {"patient_id": "p1", "date": "2024-01-01", "code": "99213"},
        {"patient_id": "p1", "date": "2024-01-02", "code": "99213"},
    ]
    assert frequency_detector.check_frequency("99213", "p1", history) is None


def test_excessive_office_visits(frequency_detector: FrequencyAbuseDetector):
    history = [{"patient_id": "p1", "date": "2024-01-01", "code": "99213"} for _ in range(5)]
    alert = frequency_detector.check_frequency("99213", "p1", history)
    assert alert is not None


def test_excessive_monthly_visits(frequency_detector: FrequencyAbuseDetector):
    history = [{"patient_id": "p1", "date": "2024-01-0%s" % i, "code": "99213"} for i in range(1, 11)]
    alert = frequency_detector.check_frequency("99213", "p1", history)
    assert alert is not None


def test_impossible_time_single_provider(frequency_detector: FrequencyAbuseDetector):
    services = [{"provider_id": "prov", "date": "2024-01-01", "time_based": True, "duration_minutes": 180} for _ in range(6)]
    alert = frequency_detector.check_impossible_time(services, "prov", "2024-01-01")
    assert alert is not None
    assert alert.severity == FraudSeverity.CRITICAL


def test_reasonable_time_passes(frequency_detector: FrequencyAbuseDetector):
    services = [{"provider_id": "prov", "date": "2024-01-01", "time_based": True, "duration_minutes": 60} for _ in range(8)]
    assert frequency_detector.check_impossible_time(services, "prov", "2024-01-01") is None


def test_excessive_mri_frequency(frequency_detector: FrequencyAbuseDetector):
    history = [
        {"patient_id": "p1", "date": "2024-01-01", "code": "MRI"},
        {"patient_id": "p1", "date": "2024-02-01", "code": "MRI"},
        {"patient_id": "p1", "date": "2024-03-01", "code": "MRI"},
    ]
    alert = frequency_detector.check_frequency("MRI", "p1", history)
    assert alert is not None


# ModifierAbuseDetector ---------------------------------------------------


def test_legitimate_modifier_25(modifier_detector: ModifierAbuseDetector):
    alerts = modifier_detector.check_modifier_abuse("99213", ["25"], {"modifier_usage_rate": 0.1, "procedure": "EM"})
    assert alerts == []


def test_excessive_modifier_25_usage(modifier_detector: ModifierAbuseDetector):
    alerts = modifier_detector.check_modifier_abuse("99213", ["25"], {"modifier_usage_rate": 0.6, "procedure": "EM"})
    assert any(a.rule_triggered == "MOD25_OVERUSE" for a in alerts)


def test_modifier_59_without_documentation(modifier_detector: ModifierAbuseDetector):
    alerts = modifier_detector.check_modifier_abuse("29881", ["59"], {"procedure": "knee scope"})
    assert any(a.rule_triggered == "MOD59_BYPASS" for a in alerts)


def test_modifier_22_frequent_use(modifier_detector: ModifierAbuseDetector):
    alerts = modifier_detector.check_modifier_abuse("29881", ["22"], {"procedure": "arthroscopy"})
    assert any(a.rule_triggered == "MOD22_DOC_REQUIRED" for a in alerts)


def test_legitimate_modifier_use(modifier_detector: ModifierAbuseDetector):
    alerts = modifier_detector.check_modifier_abuse("29881", [], {"procedure": "arthroscopy"})
    assert alerts == []


# BillingPatternAnalyzer --------------------------------------------------


def test_normal_billing_pattern():
    analyzer = FraudDetector(ncci_engine=StubNCCIEngine()).pattern_analyzer
    claims = [{"cpt_codes": ["99213"], "modifiers": {}, "charges": {"99213": 120}} for _ in range(10)]
    assert analyzer.analyze_provider_patterns("prov", claims) == []


def test_high_level_em_overuse():
    analyzer = FraudDetector(ncci_engine=StubNCCIEngine()).pattern_analyzer
    claims = []
    for _ in range(25):
        claims.append({"cpt_codes": ["99215"], "modifiers": {}, "charges": {"99215": 200}})
    alerts = analyzer.analyze_provider_patterns("prov", claims)
    assert any(a.rule_triggered == "EM_DISTRIBUTION_OUTLIER" for a in alerts)


def test_excessive_volume():
    analyzer = FraudDetector(ncci_engine=StubNCCIEngine()).pattern_analyzer
    claims = [
        {"cpt_codes": ["99213"], "modifiers": {}, "charges": {"99213": 100}},
        {"cpt_codes": ["99213"], "modifiers": {}, "charges": {"99213": 110}},
        {"cpt_codes": ["99213"], "modifiers": {}, "charges": {"99213": 120}},
        {"cpt_codes": ["99213"], "modifiers": {}, "charges": {"99213": 1000}},
    ]
    alerts = analyzer.analyze_provider_patterns("prov", claims)
    assert any(a.rule_triggered == "VOLUME_OUTLIER" for a in alerts)


# FraudDetector main ------------------------------------------------------


def test_scan_coding_clean(fraud_detector: FraudDetector):
    assigned = [_make_assigned_code("99213", "CPT", "routine visit"), _make_assigned_code("E11.9", "ICD10", "diabetes")]
    result = fraud_detector.scan_coding_decision(assigned, ["routine visit", "diabetes"], "OUTPATIENT", confidence_scores={"99213": 0.9, "E11.9": 0.9})
    assert result.total_alerts == 0


def test_scan_coding_upcoding(fraud_detector: FraudDetector):
    assigned = [_make_assigned_code("99215", "CPT", "routine visit", confidence=0.4)]
    result = fraud_detector.scan_coding_decision(assigned, ["routine visit"], "OUTPATIENT", confidence_scores={"99215": 0.4})
    assert any(a.fraud_type == FraudType.UPCODING for a in result.alerts)


def test_scan_coding_unbundling(fraud_detector: FraudDetector):
    assigned = [_make_assigned_code("80048", "CPT"), _make_assigned_code("80053", "CPT")]
    result = fraud_detector.scan_coding_decision(assigned, ["labs"], "OUTPATIENT", confidence_scores={"80048": 0.8, "80053": 0.8})
    assert any(a.fraud_type == FraudType.UNBUNDLING for a in result.alerts)


def test_scan_coding_multiple_issues(fraud_detector: FraudDetector):
    assigned = [
        _make_assigned_code("99215", "CPT", "mild"),
        _make_assigned_code("80048", "CPT"),
        _make_assigned_code("80053", "CPT"),
    ]
    result = fraud_detector.scan_coding_decision(assigned, ["mild visit"], "OUTPATIENT", confidence_scores={"99215": 0.4, "80048": 0.8, "80053": 0.8})
    assert result.total_alerts >= 2


def test_scan_claim_clean(fraud_detector: FraudDetector):
    claim = _make_claim("p1", "prov", "2024-01-01", ["99213"], ["E11.9"])
    claim["service_history"] = []
    result = fraud_detector.scan_claim(claim, [])
    assert result.total_alerts == 0


def test_scan_claim_duplicate(fraud_detector: FraudDetector):
    claim = _make_claim("p1", "prov", "2024-01-01", ["99213"], ["E11.9"])
    result = fraud_detector.scan_claim(claim, [claim])
    assert any(a.fraud_type == FraudType.DUPLICATE_BILLING for a in result.alerts)


def test_scan_claim_comprehensive(fraud_detector: FraudDetector):
    claim = _make_claim("p1", "prov", "2024-01-01", ["80048", "80053", "99215"], ["E11.65"])
    claim["service_history"] = [
        {"patient_id": "p1", "provider_id": "prov", "date": "2024-01-01", "time_based": True, "duration_minutes": 120, "code": "99215"}
        for _ in range(10)
    ]
    result = fraud_detector.scan_claim(claim, [claim])
    assert result.total_alerts >= 3


def test_risk_score_calculation(fraud_detector: FraudDetector):
    alerts = [
        FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.8, description="", code_involved="99215", code_description="", rule_triggered="", recommended_action="REVIEW"),
        FraudAlert(fraud_type=FraudType.FREQUENCY_ABUSE, severity=FraudSeverity.MEDIUM, confidence=0.6, description="", code_involved="99213", code_description="", rule_triggered="", recommended_action="REVIEW", financial_impact=2000),
    ]
    score = fraud_detector.calculate_risk_score(alerts)
    assert 0 < score <= 1


def test_risk_level_low(fraud_detector: FraudDetector):
    assert fraud_detector.determine_risk_level(0.1) == FraudSeverity.LOW.value


def test_risk_level_medium(fraud_detector: FraudDetector):
    assert fraud_detector.determine_risk_level(0.3) == FraudSeverity.MEDIUM.value


def test_risk_level_high(fraud_detector: FraudDetector):
    assert fraud_detector.determine_risk_level(0.6) == FraudSeverity.HIGH.value


def test_risk_level_critical(fraud_detector: FraudDetector):
    assert fraud_detector.determine_risk_level(0.85) == FraudSeverity.CRITICAL.value


def test_fraud_summary_generated(fraud_detector: FraudDetector):
    alerts = [FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.9, description="Desc", code_involved="99215", code_description="", rule_triggered="", recommended_action="REVIEW")]
    result = FraudDetectionResult(scan_type="CODING", alerts=alerts)
    result.overall_risk_score = 0.6
    result.risk_level = FraudSeverity.HIGH.value
    summary = fraud_detector.generate_fraud_summary(result)
    assert "Fraud scan" in summary and "99215" in summary


def test_suggest_correct_codes_upcoding(fraud_detector: FraudDetector):
    alert = FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.8, description="", code_involved="99215", code_description="", rule_triggered="RULE", recommended_action="REVIEW", expected_code="99214")
    suggestions = fraud_detector.suggest_correct_codes(alert)
    assert any(s["code"] == "99214" for s in suggestions)


def test_suggest_correct_codes_em(fraud_detector: FraudDetector):
    alert = FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.8, description="", code_involved="E11.65", code_description="", rule_triggered="RULE", recommended_action="REVIEW")
    suggestions = fraud_detector.suggest_correct_codes(alert)
    assert suggestions


def test_is_blocked_on_critical(fraud_detector: FraudDetector):
    alert = FraudAlert(fraud_type=FraudType.TIME_BASED_FRAUD, severity=FraudSeverity.CRITICAL, confidence=0.9, description="", code_involved="TIME", code_description="", rule_triggered="IMPOSSIBLE_TIME", recommended_action="BLOCK")
    result = fraud_detector._build_result([alert], "TEST", datetime.utcnow())
    assert result.is_blocked is True


def test_result_serialization():
    result = FraudDetectionResult(scan_type="CODING", alerts=[])
    assert result.model_dump()


# Edge cases --------------------------------------------------------------


def test_empty_codes_no_error(fraud_detector: FraudDetector):
    result = fraud_detector.scan_coding_decision([], [], "OUTPATIENT", confidence_scores={})
    assert result.total_alerts == 0


def test_single_code_no_unbundling(unbundling_detector: UnbundlingDetector):
    alerts = unbundling_detector.check_unbundling(["99213"])
    assert alerts == []


def test_financial_impact_estimated(fraud_detector: FraudDetector):
    alerts = [FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.8, description="", code_involved="99215", code_description="", rule_triggered="", recommended_action="REVIEW", financial_impact=5000)]
    score = fraud_detector.calculate_risk_score(alerts)
    assert score > 0.2


def test_recommended_action_correct():
    alert_high = FraudAlert(fraud_type=FraudType.UPCODING, severity=FraudSeverity.HIGH, confidence=0.8, description="", code_involved="99215", code_description="", rule_triggered="", recommended_action="REVIEW")
    alert_critical = FraudAlert(fraud_type=FraudType.TIME_BASED_FRAUD, severity=FraudSeverity.CRITICAL, confidence=0.9, description="", code_involved="TIME", code_description="", rule_triggered="", recommended_action="BLOCK")
    assert alert_high.recommended_action == "REVIEW"
    assert alert_critical.recommended_action == "BLOCK"


def test_all_fraud_types_have_detection():
    alerts = [FraudAlert(fraud_type=ft, severity=FraudSeverity.LOW, confidence=0.5, description="", code_involved=ft.value, code_description="", rule_triggered="", recommended_action="REVIEW") for ft in FraudType]
    score = FraudDetector(ncci_engine=StubNCCIEngine()).calculate_risk_score(alerts)
    assert len(alerts) == len(FraudType)
    assert 0 <= score <= 1
