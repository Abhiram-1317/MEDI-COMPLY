from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from medi_comply.api.routes.coding import _convert_to_response
from medi_comply.schemas.coding_result import (
    AlternativeCode,
    ClinicalEvidenceLink,
    CodingResult,
    ConfidenceFactor,
    ReasoningStep,
    SingleCodeDecision,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _DummyMetrics:
    def __init__(self) -> None:
        self._payload = {"total_time_ms": 10.0}

    def model_dump(self) -> dict[str, float]:
        return self._payload


def _metrics() -> _DummyMetrics:
    return _DummyMetrics()


def _decision(code: str = "I10", code_type: str = "ICD10") -> SingleCodeDecision:
    return SingleCodeDecision(
        code=code,
        code_type=code_type,
        description="Test description",
        sequence_position="PRINCIPAL",
        sequence_number=1,
        reasoning_chain=[
            ReasoningStep(
                step_number=1,
                action="Assess",
                detail="Reviewed documentation",
            )
        ],
        clinical_evidence=[
            ClinicalEvidenceLink(
                evidence_id="EV-1",
                entity_id="EN-1",
                source_text="evidence",
                section="ASSESSMENT",
                page=1,
                line=1,
                char_offset=(0, 10),
                relevance="HIGH",
            )
        ],
        alternatives_considered=[
            AlternativeCode(
                code="ALT",
                description="Alt desc",
                reason_rejected="Low relevance",
            )
        ],
        confidence_score=0.95,
        confidence_factors=[
            ConfidenceFactor(
                factor="Doc",
                impact="POSITIVE",
                weight=0.5,
                detail="Solid",
            )
        ],
        requires_human_review=False,
        review_reason=None,
        is_billable=True,
        guidelines_cited=["GL-1"],
        use_additional_applied=[],
        code_first_applied=[],
    )


def _coding_result(decisions: list[SingleCodeDecision]) -> CodingResult:
    return CodingResult(
        scr_id="SCR-1",
        context_id="CTX-1",
        created_at=_now(),
        processing_time_ms=100.0,
        encounter_type="INPATIENT",
        patient_age=65,
        patient_gender="FEMALE",
        diagnosis_codes=decisions,
        principal_diagnosis=decisions[0] if decisions else None,
        procedure_codes=[],
        overall_confidence=0.9,
        total_codes_assigned=len(decisions),
        total_icd10_codes=len(decisions),
        total_cpt_codes=0,
        has_combination_codes=False,
        has_use_additional_codes=False,
        has_code_first_codes=False,
        requires_human_review=False,
        review_reasons=[],
        attempt_number=1,
        previous_feedback=None,
        coding_summary="Summary",
        all_guidelines_cited=["GL-1"],
    )


class _DummyComplianceReport:
    def __init__(self, decision: str = "PASS") -> None:
        self.overall_decision = decision

    def model_dump(self) -> dict[str, str]:
        return {"overall_decision": self.overall_decision}


def _pipeline_result(**overrides) -> SimpleNamespace:
    base = {
        "result_id": "RES-1",
        "trace_id": "TRACE-1",
        "status": "SUCCESS",
        "started_at": _now(),
        "completed_at": _now(),
        "total_processing_time_ms": 123.0,
        "encounter_type": "INPATIENT",
        "document_type": "DISCHARGE_SUMMARY",
        "coding_result": None,
        "compliance_report": None,
        "audit_report_summary": "",
        "audit_report_full": None,
        "evidence_map": None,
        "risk_assessment": None,
        "pipeline_stages": [],
        "retry_count": 0,
        "escalation": None,
        "errors": [],
        "warnings": [],
        "metrics": _metrics(),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_convert_null_coding_result() -> None:
    result = _pipeline_result()
    payload = _convert_to_response(result)
    assert payload["coding_result"] is None
    assert payload["compliance_report"] is None


def test_convert_partial_result() -> None:
    decision = _decision()
    decision.reasoning_chain = None  # type: ignore[attr-defined]
    decision.clinical_evidence = None  # type: ignore[attr-defined]
    decision.alternatives_considered = None  # type: ignore[attr-defined]
    decision.confidence_factors = None  # type: ignore[attr-defined]
    coding_result = _coding_result([decision])
    payload = _convert_to_response(_pipeline_result(coding_result=coding_result))

    diag = payload["coding_result"]["diagnosis_codes"][0]
    assert diag["reasoning_chain"] == []
    assert diag["clinical_evidence"] == []
    assert diag["alternatives_considered"] == []
    assert diag["confidence_factors"] == []


def test_convert_full_result() -> None:
    diagnosis = _decision()
    procedure = _decision(code="93306", code_type="CPT")
    coding_result = _coding_result([diagnosis])
    coding_result.procedure_codes.append(procedure)
    comp_report = _DummyComplianceReport()

    payload = _convert_to_response(
        _pipeline_result(coding_result=coding_result, compliance_report=comp_report)
    )

    assert payload["coding_result"]["procedure_codes"]
    assert payload["compliance_report"]["overall_decision"] == "PASS"
    assert payload["coding_result"]["diagnosis_codes"][0]["code"] == "I10"
