"""FastAPI coding route helpers and response conversion utilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable, Optional
from medi_comply.schemas.coding_result import (
    CodingResult,
    SingleCodeDecision,
)

if TYPE_CHECKING:  # pragma: no cover
    from medi_comply.guardrails.compliance_report import ComplianceReport
    from medi_comply.result_models import MediComplyResult
else:  # pragma: no cover
    ComplianceReport = Any
    MediComplyResult = Any


def _safe_model_dump(value: Any, default: Any = None) -> Any:
    """Return a serializable form regardless of the underlying object."""
    if value is None:
        return default
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return list(value)
    return value


def _safe_iter(value: Optional[Iterable[Any]]) -> list[Any]:
    if not value:
        return []
    return list(value)


def _convert_reasoning_chain(decision: SingleCodeDecision) -> list[dict[str, Any]]:
    chain = _safe_iter(getattr(decision, "reasoning_chain", None))
    converted: list[dict[str, Any]] = []
    for step in chain:
        converted.append(
            {
                "step_number": getattr(step, "step_number", 0),
                "action": getattr(step, "action", ""),
                "detail": getattr(step, "detail", ""),
                "evidence_ref": getattr(step, "evidence_ref", None),
                "guideline_ref": getattr(step, "guideline_ref", None),
                "sub_decision": getattr(step, "sub_decision", None),
                "confidence_impact": getattr(step, "confidence_impact", None),
            }
        )
    return converted


def _convert_evidence(decision: SingleCodeDecision) -> list[dict[str, Any]]:
    evidence = _safe_iter(getattr(decision, "clinical_evidence", None))
    converted: list[dict[str, Any]] = []
    for item in evidence:
        converted.append(
            {
                "evidence_id": getattr(item, "evidence_id", ""),
                "entity_id": getattr(item, "entity_id", ""),
                "source_text": getattr(item, "source_text", ""),
                "section": getattr(item, "section", ""),
                "page": getattr(item, "page", 0),
                "line": getattr(item, "line", 0),
                "char_offset": getattr(item, "char_offset", (0, 0)),
                "relevance": getattr(item, "relevance", ""),
            }
        )
    return converted


def _convert_alternatives(decision: SingleCodeDecision) -> list[dict[str, Any]]:
    alternatives = _safe_iter(getattr(decision, "alternatives_considered", None))
    converted: list[dict[str, Any]] = []
    for alt in alternatives:
        converted.append(
            {
                "code": getattr(alt, "code", ""),
                "description": getattr(alt, "description", ""),
                "reason_rejected": getattr(alt, "reason_rejected", ""),
                "would_be_correct_if": getattr(alt, "would_be_correct_if", None),
            }
        )
    return converted


def _convert_confidence_factors(decision: SingleCodeDecision) -> list[dict[str, Any]]:
    factors = _safe_iter(getattr(decision, "confidence_factors", None))
    converted: list[dict[str, Any]] = []
    for factor in factors:
        converted.append(
            {
                "factor": getattr(factor, "factor", ""),
                "impact": getattr(factor, "impact", ""),
                "weight": getattr(factor, "weight", 0.0),
                "detail": getattr(factor, "detail", ""),
            }
        )
    return converted


def _convert_code_decision(decision: Optional[SingleCodeDecision]) -> Optional[dict[str, Any]]:
    if decision is None:
        return None
    return {
        "decision_id": getattr(decision, "decision_id", None),
        "code": getattr(decision, "code", ""),
        "code_type": getattr(decision, "code_type", ""),
        "description": getattr(decision, "description", ""),
        "sequence_position": getattr(decision, "sequence_position", ""),
        "sequence_number": getattr(decision, "sequence_number", 0),
        "reasoning_chain": _convert_reasoning_chain(decision),
        "clinical_evidence": _convert_evidence(decision),
        "alternatives_considered": _convert_alternatives(decision),
        "confidence_score": getattr(decision, "confidence_score", 0.0),
        "confidence_factors": _convert_confidence_factors(decision),
        "use_additional_applied": _safe_iter(getattr(decision, "use_additional_applied", None)),
        "code_first_applied": _safe_iter(getattr(decision, "code_first_applied", None)),
        "combination_code_note": getattr(decision, "combination_code_note", None),
        "requires_human_review": getattr(decision, "requires_human_review", False),
        "review_reason": getattr(decision, "review_reason", None),
        "is_billable": getattr(decision, "is_billable", True),
        "guidelines_cited": _safe_iter(getattr(decision, "guidelines_cited", None)),
    }


def _convert_coding_result(result: Optional[CodingResult]) -> Optional[dict[str, Any]]:
    if result is None:
        return None

    return {
        "coding_result_id": getattr(result, "coding_result_id", None),
        "scr_id": getattr(result, "scr_id", ""),
        "context_id": getattr(result, "context_id", ""),
        "created_at": getattr(result, "created_at", None),
        "processing_time_ms": getattr(result, "processing_time_ms", 0.0),
        "encounter_type": getattr(result, "encounter_type", ""),
        "patient_age": getattr(result, "patient_age", 0),
        "patient_gender": getattr(result, "patient_gender", ""),
        "diagnosis_codes": [
            _convert_code_decision(decision)
            for decision in _safe_iter(getattr(result, "diagnosis_codes", None))
        ],
        "principal_diagnosis": _convert_code_decision(getattr(result, "principal_diagnosis", None)),
        "procedure_codes": [
            _convert_code_decision(decision)
            for decision in _safe_iter(getattr(result, "procedure_codes", None))
        ],
        "overall_confidence": getattr(result, "overall_confidence", 0.0),
        "total_codes_assigned": getattr(result, "total_codes_assigned", 0),
        "total_icd10_codes": getattr(result, "total_icd10_codes", 0),
        "total_cpt_codes": getattr(result, "total_cpt_codes", 0),
        "has_combination_codes": getattr(result, "has_combination_codes", False),
        "has_use_additional_codes": getattr(result, "has_use_additional_codes", False),
        "has_code_first_codes": getattr(result, "has_code_first_codes", False),
        "requires_human_review": getattr(result, "requires_human_review", False),
        "review_reasons": _safe_iter(getattr(result, "review_reasons", None)),
        "attempt_number": getattr(result, "attempt_number", 1),
        "previous_feedback": getattr(result, "previous_feedback", None),
        "coding_summary": getattr(result, "coding_summary", ""),
        "all_guidelines_cited": _safe_iter(getattr(result, "all_guidelines_cited", None)),
    }


def _convert_compliance_report(report: Optional[ComplianceReport]) -> Optional[dict[str, Any]]:
    return _safe_model_dump(report, default=None)


def _convert_to_response(result: Optional[Any]) -> dict[str, Any]:
    """Convert a MediComplyResult into a FastAPI-friendly payload."""
    if result is None:
        return {}

    coding_result = _convert_coding_result(getattr(result, "coding_result", None))
    compliance_report = _convert_compliance_report(getattr(result, "compliance_report", None))

    errors = [
        _safe_model_dump(err, default={})
        for err in _safe_iter(getattr(result, "errors", None))
    ]

    return {
        "result_id": getattr(result, "result_id", None),
        "trace_id": getattr(result, "trace_id", None),
        "status": getattr(result, "status", "UNKNOWN"),
        "started_at": getattr(result, "started_at", None),
        "completed_at": getattr(result, "completed_at", None),
        "total_processing_time_ms": getattr(result, "total_processing_time_ms", 0.0),
        "encounter_type": getattr(result, "encounter_type", ""),
        "document_type": getattr(result, "document_type", ""),
        "coding_result": coding_result,
        "compliance_report": compliance_report,
        "audit_report_summary": getattr(result, "audit_report_summary", ""),
        "audit_report_full": _safe_model_dump(getattr(result, "audit_report_full", None), default=None),
        "evidence_map": _safe_model_dump(getattr(result, "evidence_map", None), default=None),
        "risk_assessment": _safe_model_dump(getattr(result, "risk_assessment", None), default=None),
        "warnings": _safe_iter(getattr(result, "warnings", None)),
        "errors": errors,
        "metrics": _safe_model_dump(getattr(result, "metrics", None), default={}),
        "retry_count": getattr(result, "retry_count", 0),
        "escalation": _safe_model_dump(getattr(result, "escalation", None), default=None),
    }
