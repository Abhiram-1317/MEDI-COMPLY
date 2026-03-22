"""Golden test suite covering 100 canonical scenarios."""

from __future__ import annotations

import time
import uuid

import pytest

from medi_comply.tests.conftest import SAMPLE_NOTES
from medi_comply.tests.golden_test_cases import GOLDEN_CASES, GoldenTestCase


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[case.case_id for case in GOLDEN_CASES])
@pytest.mark.asyncio
async def test_golden_case(case: GoldenTestCase, initialized_system):
    system = initialized_system
    note = case.clinical_note_override or SAMPLE_NOTES.get(case.clinical_note_key, "")
    assert note, f"{case.case_id}: Missing clinical note"

    start = time.time()
    result = await system.process(
        clinical_note=note,
        patient_context={
            "age": case.patient_age,
            "gender": case.patient_gender,
            "encounter_type": case.encounter_type,
        },
    )
    elapsed_ms = (time.time() - start) * 1000

    assert result is not None, f"{case.case_id}: Result is None"
    assert result.status in case.expected_status, (
        f"{case.case_id}: Status {result.status} not in {case.expected_status}"
    )
    assert getattr(result, "trace_id", None), f"{case.case_id}: Missing trace_id"
    assert result.total_processing_time_ms > 0, f"{case.case_id}: No processing time recorded"

    coding_result = getattr(result, "coding_result", None)
    if result.status == "SUCCESS" and coding_result:
        codes = list(coding_result.diagnosis_codes or [])
        code_set = {cd.code for cd in codes}

        assert len(codes) >= case.min_codes, (
            f"{case.case_id}: Expected ≥{case.min_codes} codes, got {len(codes)}"
        )
        assert len(codes) <= case.max_codes, (
            f"{case.case_id}: Expected ≤{case.max_codes} codes, got {len(codes)}"
        )

        for required_code in case.must_include_codes:
            assert required_code in code_set, (
                f"{case.case_id}: Required code {required_code} missing. Got {code_set}"
            )

        for forbidden_code in case.must_not_include_codes:
            assert forbidden_code not in code_set, (
                f"{case.case_id}: Forbidden code {forbidden_code} present"
            )

        if case.expected_primary_dx_prefix and coding_result.principal_diagnosis:
            pdx = coding_result.principal_diagnosis.code
            assert pdx.startswith(case.expected_primary_dx_prefix), (
                f"{case.case_id}: Primary dx {pdx} missing prefix {case.expected_primary_dx_prefix}"
            )

        if case.min_overall_confidence > 0:
            assert coding_result.overall_confidence >= case.min_overall_confidence, (
                f"{case.case_id}: Confidence {coding_result.overall_confidence:.2f} < "
                f"{case.min_overall_confidence}"
            )

        for cd in codes:
            assert 0.0 <= cd.confidence_score <= 1.0, (
                f"{case.case_id}: Code {cd.code} confidence out of range"
            )

        if case.expect_reasoning_chains:
            for cd in codes:
                assert cd.reasoning_chain, f"{case.case_id}: Code {cd.code} missing reasoning chain"

        if case.expect_evidence_for_all_codes:
            for cd in codes:
                assert cd.clinical_evidence, f"{case.case_id}: Code {cd.code} missing evidence"

        if case.expect_no_excludes1_violation:
            _check_no_excludes1(case.case_id, code_set, system.knowledge_manager)

    if case.expect_audit_complete:
        assert getattr(result, "audit_report_summary", ""), (
            f"{case.case_id}: Audit report summary empty"
        )
        warnings = getattr(result, "warnings", None) or []
        bad_warns = [w for w in warnings if "section" in w.lower() or "attribute" in w.lower()]
        assert not bad_warns, f"{case.case_id}: Audit warnings include section issues: {bad_warns}"

    if case.expect_compliance_pass and getattr(result, "compliance_report", None):
        assert result.compliance_report.overall_decision == "PASS", (
            f"{case.case_id}: Compliance decision {result.compliance_report.overall_decision}"
        )

    if case.case_id == "EDGE-017":
        assert elapsed_ms < 30000, f"{case.case_id}: Took {elapsed_ms:.0f}ms > 30s"

    if case.case_id == "EDGE-018":
        try:
            uuid.UUID(str(result.trace_id))
        except ValueError:
            pytest.fail(f"{case.case_id}: Invalid trace_id {result.trace_id}")

    if case.case_id == "EDGE-019":
        metrics = getattr(result, "metrics", None)
        assert metrics is not None, f"{case.case_id}: Metrics missing"
        assert getattr(metrics, "total_time_ms", 0) > 0, f"{case.case_id}: Metrics not populated"


def _check_no_excludes1(case_id: str, codes: set[str], km):
    code_list = list(codes)
    for idx in range(len(code_list)):
        for jdx in range(idx + 1, len(code_list)):
            code_a = code_list[idx]
            code_b = code_list[jdx]
            checker = getattr(km, "check_excludes", None)
            if not checker:
                continue
            result = checker(code_a, code_b)
            if not result:
                continue
            if getattr(result, "is_excluded", False) and getattr(result, "excludes_type", "") == "EXCLUDES1":
                pytest.fail(f"{case_id}: Excludes1 violation between {code_a} and {code_b}")
