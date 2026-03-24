"""HIPAA Guard test suite covering PHI detection, de-id, safety, and controls."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from medi_comply.compliance.hipaa_guard import (
    PHIIdentifierType,
    PHIDetector,
    Deidentifier,
    LLMPHISafetyChecker,
    HIPAAAccessLogger,
    HIPAAComplianceChecker,
    MinimumNecessaryRule,
    DataRetentionManager,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def detector() -> PHIDetector:
    return PHIDetector()


@pytest.fixture()
def deidentifier(detector: PHIDetector) -> Deidentifier:
    return Deidentifier(detector)


@pytest.fixture()
def safety_checker(detector: PHIDetector, deidentifier: Deidentifier) -> LLMPHISafetyChecker:
    return LLMPHISafetyChecker(detector=detector, deidentifier=deidentifier)


# ---------------------------------------------------------------------------
# PHIDetector — Name Detection
# ---------------------------------------------------------------------------


def test_detect_patient_name(detector: PHIDetector) -> None:
    detections = detector.detect("Patient: John Smith")
    assert any(d.phi_type == PHIIdentifierType.NAME for d in detections)


def test_detect_doctor_name(detector: PHIDetector) -> None:
    detections = detector.detect("Dr. Jane Wilson")
    assert any(d.phi_type == PHIIdentifierType.NAME for d in detections)


def test_detect_name_with_title(detector: PHIDetector) -> None:
    detections = detector.detect("Mrs. Mary Johnson")
    assert any(d.phi_type == PHIIdentifierType.NAME for d in detections)


def test_medical_term_not_detected_as_name(detector: PHIDetector) -> None:
    detections = detector.detect("Parkinson disease")
    assert all(d.phi_type != PHIIdentifierType.NAME for d in detections)


def test_alzheimer_not_name(detector: PHIDetector) -> None:
    detections = detector.detect("Alzheimer's disease")
    assert all(d.phi_type != PHIIdentifierType.NAME for d in detections)


def test_cushing_not_name(detector: PHIDetector) -> None:
    detections = detector.detect("Cushing syndrome")
    assert all(d.phi_type != PHIIdentifierType.NAME for d in detections)


# ---------------------------------------------------------------------------
# PHIDetector — Date Detection
# ---------------------------------------------------------------------------


def test_detect_date_slash(detector: PHIDetector) -> None:
    detections = detector.detect("DOB: 03/15/1962")
    assert any(d.phi_type == PHIIdentifierType.DATE for d in detections)


def test_detect_date_dash(detector: PHIDetector) -> None:
    detections = detector.detect("Admission: 2025-01-15")
    assert any(d.phi_type == PHIIdentifierType.DATE for d in detections)


def test_detect_date_written(detector: PHIDetector) -> None:
    detections = detector.detect("January 15, 2025")
    assert any(d.phi_type == PHIIdentifierType.DATE for d in detections)


def test_year_only_not_detected(detector: PHIDetector) -> None:
    detections = detector.detect("diagnosed in 2024")
    assert all(d.phi_type != PHIIdentifierType.DATE for d in detections)


def test_relative_date_not_detected(detector: PHIDetector) -> None:
    detections = detector.detect("2 days ago")
    assert all(d.phi_type != PHIIdentifierType.DATE for d in detections)


# ---------------------------------------------------------------------------
# PHIDetector — SSN Detection
# ---------------------------------------------------------------------------


def test_detect_ssn_dashes(detector: PHIDetector) -> None:
    detections = detector.detect("SSN: 123-45-6789")
    assert any(d.phi_type == PHIIdentifierType.SSN for d in detections)


def test_detect_ssn_no_dashes(detector: PHIDetector) -> None:
    detections = detector.detect("SSN: 123456789")
    assert any(d.phi_type == PHIIdentifierType.SSN for d in detections)


def test_phone_not_ssn(detector: PHIDetector) -> None:
    detections = detector.detect("Call 555-123-4567 tomorrow")
    assert all(d.phi_type != PHIIdentifierType.SSN for d in detections)


# ---------------------------------------------------------------------------
# PHIDetector — Other Types
# ---------------------------------------------------------------------------


def test_detect_mrn(detector: PHIDetector) -> None:
    detections = detector.detect("MRN: 12345678")
    assert any(d.phi_type == PHIIdentifierType.MRN for d in detections)


def test_detect_phone(detector: PHIDetector) -> None:
    detections = detector.detect("(555) 123-4567")
    assert any(d.phi_type == PHIIdentifierType.PHONE for d in detections)


def test_detect_email(detector: PHIDetector) -> None:
    detections = detector.detect("patient@email.com")
    assert any(d.phi_type == PHIIdentifierType.EMAIL for d in detections)


def test_detect_address(detector: PHIDetector) -> None:
    detections = detector.detect("123 Main Street, Springfield, IL 62701")
    assert any(d.phi_type == PHIIdentifierType.GEOGRAPHIC for d in detections)


def test_detect_ip_address(detector: PHIDetector) -> None:
    detections = detector.detect("IP: 192.168.1.100")
    assert any(d.phi_type == PHIIdentifierType.IP_ADDRESS for d in detections)


def test_detect_url(detector: PHIDetector) -> None:
    detections = detector.detect("https://patient-portal.hospital.com")
    assert any(d.phi_type == PHIIdentifierType.URL for d in detections)


def test_detect_account_number(detector: PHIDetector) -> None:
    detections = detector.detect("Account #: 987654321")
    assert any(d.phi_type == PHIIdentifierType.ACCOUNT_NUMBER for d in detections)


def test_detect_health_plan_id(detector: PHIDetector) -> None:
    detections = detector.detect("Member ID: XYZ123456")
    assert any(d.phi_type == PHIIdentifierType.HEALTH_PLAN_ID for d in detections)


# ---------------------------------------------------------------------------
# PHIDetector — Clinical Text
# ---------------------------------------------------------------------------


def test_clinical_note_multiple_phi(detector: PHIDetector) -> None:
    note = (
        "Patient: John Smith DOB: 01/02/1980 seen at 123 Main St, Springfield, IL 62701. "
        "Phone (555) 123-4567. MRN: ABC123. Email john@example.com."
    )
    detections = detector.detect(note)
    types = {d.phi_type for d in detections}
    assert {PHIIdentifierType.NAME, PHIIdentifierType.DATE, PHIIdentifierType.GEOGRAPHIC, PHIIdentifierType.PHONE, PHIIdentifierType.MRN, PHIIdentifierType.EMAIL}.issubset(types)


def test_clean_clinical_text(detector: PHIDetector) -> None:
    text = "Patient presents with chest pain and shortness of breath."
    detections = detector.detect(text)
    assert detections == []


def test_high_recall(detector: PHIDetector) -> None:
    text = (
        "Patient: John Smith visited on 02/10/2024. SSN: 123-45-6789. Phone: 555-111-2222. "
        "Email: john@example.com. Address: 456 Oak Ave, Denver, CO 80202. Account # ABC99999."
    )
    detections = detector.detect(text)
    types = {d.phi_type for d in detections}
    expected = {PHIIdentifierType.NAME, PHIIdentifierType.DATE, PHIIdentifierType.SSN, PHIIdentifierType.PHONE, PHIIdentifierType.EMAIL, PHIIdentifierType.GEOGRAPHIC, PHIIdentifierType.ACCOUNT_NUMBER}
    assert expected.issubset(types)


# ---------------------------------------------------------------------------
# Deidentifier
# ---------------------------------------------------------------------------


def test_deidentify_replaces_names(deidentifier: Deidentifier) -> None:
    text = "Patient: John Smith"
    result = deidentifier.deidentify(text)
    assert "[NAME_1]" in result.deidentified_text


def test_deidentify_replaces_dates(deidentifier: Deidentifier) -> None:
    text = "DOB: 03/15/1962"
    result = deidentifier.deidentify(text)
    assert "[DATE_1]" in result.deidentified_text


def test_deidentify_replaces_ssn(deidentifier: Deidentifier) -> None:
    text = "SSN: 123-45-6789"
    result = deidentifier.deidentify(text)
    assert "[SSN_1]" in result.deidentified_text


def test_deidentify_replaces_mrn(deidentifier: Deidentifier) -> None:
    text = "MRN: 12345678"
    result = deidentifier.deidentify(text)
    assert "[MRN_1]" in result.deidentified_text


def test_deidentify_token_map_created(deidentifier: Deidentifier) -> None:
    text = "Patient: John Smith DOB: 01/01/1990"
    result = deidentifier.deidentify(text)
    assert result.token_map
    assert result.total_phi_found == len(result.token_map)


def test_deidentify_preserves_clinical_content(deidentifier: Deidentifier) -> None:
    text = "Patient reports chest pain and was seen on 01/01/2020 by Dr. Lee"
    result = deidentifier.deidentify(text)
    assert "chest pain" in result.deidentified_text


def test_deidentify_is_safe_flag(deidentifier: Deidentifier) -> None:
    text = "Patient: John Smith"
    result = deidentifier.deidentify(text)
    assert result.is_safe_for_external is True


def test_deidentify_multiple_same_type(deidentifier: Deidentifier) -> None:
    text = "Patient: John Smith and Patient: Mary Jones"
    result = deidentifier.deidentify(text)
    assert "[NAME_1]" in result.deidentified_text and "[NAME_2]" in result.deidentified_text


# ---------------------------------------------------------------------------
# Reidentifier
# ---------------------------------------------------------------------------


def test_reidentify_restores_names(deidentifier: Deidentifier) -> None:
    original = "Patient: John Smith"
    deid = deidentifier.deidentify(original)
    reid = deidentifier.reidentify(deid.deidentified_text, deid.token_map)
    assert original == reid.reidentified_text


def test_reidentify_restores_all(deidentifier: Deidentifier) -> None:
    original = "Patient: John Smith DOB: 01/01/1990 MRN: 12345678"
    deid = deidentifier.deidentify(original)
    reid = deidentifier.reidentify(deid.deidentified_text, deid.token_map)
    assert reid.tokens_failed == 0
    assert original == reid.reidentified_text


def test_reidentify_round_trip(deidentifier: Deidentifier) -> None:
    original = "SSN: 123-45-6789"
    deid = deidentifier.deidentify(original)
    reid = deidentifier.reidentify(deid.deidentified_text, deid.token_map)
    assert original == reid.reidentified_text


def test_reidentify_missing_token(deidentifier: Deidentifier) -> None:
    text = "[NAME_1]"
    token_map = {"[NAME_1]": "John Smith"}
    reid = deidentifier.reidentify(text, token_map)
    assert reid.tokens_restored == 1
    assert reid.tokens_failed == 0


# ---------------------------------------------------------------------------
# LLMPHISafetyChecker
# ---------------------------------------------------------------------------


def test_check_before_llm_safe(safety_checker: LLMPHISafetyChecker) -> None:
    result = safety_checker.check_before_llm("This is de-identified clinical summary")
    assert result["is_safe"] is True


def test_check_before_llm_unsafe(safety_checker: LLMPHISafetyChecker) -> None:
    result = safety_checker.check_before_llm("Patient: John Smith")
    assert result["is_safe"] is False
    assert result["phi_found"]


def test_check_after_llm_safe(safety_checker: LLMPHISafetyChecker) -> None:
    result = safety_checker.check_after_llm("Summary without PHI")
    assert result["is_safe"] is True


def test_check_after_llm_unsafe(safety_checker: LLMPHISafetyChecker) -> None:
    result = safety_checker.check_after_llm("Patient: John Smith")
    assert result["is_safe"] is False


@pytest.mark.asyncio
async def test_safe_llm_pipeline_deidentifies(safety_checker: LLMPHISafetyChecker) -> None:
    async def fake_llm_call(text: str) -> str:
        return text.replace("[NAME_1]", "[NAME_1] replied")

    pipeline = await safety_checker.safe_llm_pipeline("Patient: John Smith", fake_llm_call)
    assert pipeline["phi_handled"] is True
    assert "John Smith" in pipeline["response"]


@pytest.mark.asyncio
async def test_safe_llm_pipeline_reidentifies(safety_checker: LLMPHISafetyChecker) -> None:
    async def fake_llm_call(text: str) -> str:
        return text + " safely processed"

    pipeline = await safety_checker.safe_llm_pipeline("Patient: Mary Jones", fake_llm_call)
    assert "Mary Jones" in pipeline["response"]
    assert pipeline["audit_entry"]["pre_phi_found"] >= 1


# ---------------------------------------------------------------------------
# HIPAAAccessLogger
# ---------------------------------------------------------------------------


def test_log_access_created() -> None:
    logger = HIPAAAccessLogger()
    entry = logger.log_access(
        user_id="user1",
        user_role="CODER",
        action="READ",
        resource_type="coding",
        resource_id="123",
        phi_accessed=True,
    )
    assert entry.user_id == "user1"
    assert logger.get_access_logs()


def test_log_immutable() -> None:
    logger = HIPAAAccessLogger()
    logger.log_access("user1", "CODER", "READ", "coding", "123", True)
    logs_copy = logger.get_access_logs()
    logs_copy.pop()
    assert len(logger.get_access_logs()) == 1


def test_query_logs_by_user() -> None:
    logger = HIPAAAccessLogger()
    logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    logger.log_access("user2", "CODER", "READ", "coding", "2", True)
    user1_logs = logger.get_access_logs(user_id="user1")
    assert len(user1_logs) == 1 and user1_logs[0].user_id == "user1"


def test_query_logs_by_date_range() -> None:
    logger = HIPAAAccessLogger()
    past = datetime.utcnow() - timedelta(days=1)
    logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    logger._logs[0].timestamp = past  # type: ignore[attr-defined]
    logs = logger.get_access_logs(start_date=datetime.utcnow() - timedelta(days=2), end_date=datetime.utcnow())
    assert logs


def test_query_logs_by_action() -> None:
    logger = HIPAAAccessLogger()
    logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    logger.log_access("user1", "CODER", "WRITE", "coding", "1", True)
    reads = logger.get_access_logs(action="READ")
    assert len(reads) == 1 and reads[0].action == "READ"


def test_phi_access_report() -> None:
    logger = HIPAAAccessLogger()
    now = datetime.utcnow()
    logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    logger.log_access("user1", "CODER", "READ", "coding", "2", True)
    report = logger.get_phi_access_report(now - timedelta(days=1), now + timedelta(days=1))
    assert report["user1"]["coding"]["READ"] == 2


def test_suspicious_access_excessive() -> None:
    logger = HIPAAAccessLogger()
    for i in range(51):
        logger.log_access("user1", "CODER", "READ", "coding", str(i), True)
    warnings = logger.detect_suspicious_access("user1")
    assert any("Excessive" in w for w in warnings)


def test_suspicious_access_after_hours() -> None:
    logger = HIPAAAccessLogger()
    entry = logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    entry.timestamp = entry.timestamp.replace(hour=2)
    warnings = logger.detect_suspicious_access("user1", time_window_minutes=24 * 60)
    assert any("outside" in w for w in warnings)


def test_suspicious_access_many_patients() -> None:
    logger = HIPAAAccessLogger()
    for i in range(21):
        logger.log_access("user1", "CODER", "READ", "coding", f"pt-{i}", True)
    warnings = logger.detect_suspicious_access("user1")
    assert any("many different records" in w for w in warnings)


def test_retention_status() -> None:
    logger = HIPAAAccessLogger()
    status = logger.get_retention_status()
    assert status["retention_compliant"] is True


# ---------------------------------------------------------------------------
# HIPAAComplianceChecker
# ---------------------------------------------------------------------------


def test_compliance_audit_runs() -> None:
    checker = HIPAAComplianceChecker()
    status = checker.run_compliance_audit()
    assert status.checks_performed


def test_compliance_checks_categories() -> None:
    checker = HIPAAComplianceChecker()
    status = checker.run_compliance_audit()
    categories = {chk.category for chk in status.checks_performed}
    assert {"DATA_PROTECTION", "ACCESS_CONTROL", "AUDIT", "TRANSMISSION", "RETENTION"}.issubset(categories)


def test_compliance_report_generated() -> None:
    checker = HIPAAComplianceChecker()
    report = checker.generate_compliance_report(checker.run_compliance_audit())
    assert "HIPAA Compliance Report" in report


def test_phi_in_text_check() -> None:
    checker = HIPAAComplianceChecker()
    result = checker.check_phi_in_text("Patient: John Smith")
    assert result.passed is False


# ---------------------------------------------------------------------------
# MinimumNecessaryRule
# ---------------------------------------------------------------------------


def test_coder_can_access_clinical() -> None:
    rule = MinimumNecessaryRule()
    result = rule.check_minimum_necessary("CODER", "READ", [PHIIdentifierType.MRN, PHIIdentifierType.NAME])
    assert result["allowed"] is True


def test_coder_cannot_access_ssn() -> None:
    rule = MinimumNecessaryRule()
    result = rule.check_minimum_necessary("CODER", "READ", [PHIIdentifierType.SSN])
    assert result["allowed"] is False


def test_admin_no_patient_phi() -> None:
    rule = MinimumNecessaryRule()
    result = rule.check_minimum_necessary("ADMIN", "READ", [PHIIdentifierType.NAME])
    assert result["allowed"] is False


def test_auditor_deidentified_only() -> None:
    rule = MinimumNecessaryRule()
    result = rule.check_minimum_necessary("AUDITOR", "READ", [PHIIdentifierType.DATE])
    assert result["allowed"] is True


def test_get_allowed_types_by_role() -> None:
    rule = MinimumNecessaryRule()
    assert rule.get_allowed_phi_types("CODER")


# ---------------------------------------------------------------------------
# DataRetentionManager
# ---------------------------------------------------------------------------


def test_retention_policy_7_years() -> None:
    manager = DataRetentionManager()
    policy = manager.get_retention_policy()
    assert policy["retention_days"] == 2555


def test_identify_expired_records() -> None:
    logger = HIPAAAccessLogger()
    entry = logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    entry.timestamp = datetime.utcnow() - timedelta(days=3000)
    manager = DataRetentionManager(access_logger=logger)
    expired = manager.identify_expired_records()
    assert len(expired) == 1


def test_purge_dry_run() -> None:
    logger = HIPAAAccessLogger()
    entry = logger.log_access("user1", "CODER", "READ", "coding", "1", True)
    entry.timestamp = datetime.utcnow() - timedelta(days=3000)
    manager = DataRetentionManager(access_logger=logger)
    result = manager.purge_expired_records(dry_run=True)
    assert result["would_purge"] == 1


def test_retention_compliance_check() -> None:
    manager = DataRetentionManager()
    status = manager.check_retention_compliance()
    assert status["compliant"] in {True, False}


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


def test_empty_text_no_phi(detector: PHIDetector) -> None:
    assert detector.detect("") == []


def test_very_long_text(detector: PHIDetector) -> None:
    text = "Patient John Doe born 01/01/1980 " * 5000
    detections = detector.detect(text)
    assert any(d.phi_type == PHIIdentifierType.NAME for d in detections)


def test_unicode_phi(detector: PHIDetector) -> None:
    text = "Patient: John Doe 🧑‍⚕️ at 123 Main St, Springfield, IL 62701"
    detections = detector.detect(text)
    assert any(d.phi_type == PHIIdentifierType.GEOGRAPHIC for d in detections)


def test_mixed_phi_types(detector: PHIDetector) -> None:
    text = (
        "Patient: Jane Roe, DOB: 02/02/1990, SSN: 123-45-6789, Phone: 555-222-3333, "
        "Email: jane@sample.com, Address: 789 Pine Rd, Boston, MA 02115, "
        "License: NPI12345, Device: SN-ABC1234, URL: https://portal.example.com, IP: 10.0.0.1, "
        "Photo captured. UUID: 123e4567-e89b-12d3-a456-426614174000"
    )
    detections = detector.detect(text)
    types = {d.phi_type for d in detections}
    expected = {
        PHIIdentifierType.NAME,
        PHIIdentifierType.DATE,
        PHIIdentifierType.SSN,
        PHIIdentifierType.PHONE,
        PHIIdentifierType.EMAIL,
        PHIIdentifierType.GEOGRAPHIC,
        PHIIdentifierType.LICENSE_NUMBER,
        PHIIdentifierType.DEVICE_ID,
        PHIIdentifierType.URL,
        PHIIdentifierType.IP_ADDRESS,
        PHIIdentifierType.PHOTO,
        PHIIdentifierType.OTHER_UNIQUE,
    }
    assert expected.issubset(types)
