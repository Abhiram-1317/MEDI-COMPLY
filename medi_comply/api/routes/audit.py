"""Audit route helpers for FastAPI responses."""

from __future__ import annotations

from typing import Any, Iterable, Optional

from medi_comply.result_models import MediComplyResult


def _safe_model_dump(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return value
    return value


def _safe_iter(value: Optional[Iterable[Any]]) -> list[Any]:
    if not value:
        return []
    return list(value)


def _convert_audit_response(result: Optional[MediComplyResult]) -> dict[str, Any]:
    if result is None:
        return {}

    return {
        "trace_id": getattr(result, "trace_id", None),
        "workflow_type": getattr(result, "document_type", ""),
        "status": getattr(result, "status", "UNKNOWN"),
        "audit_report_summary": getattr(result, "audit_report_summary", ""),
        "audit_report_full": _safe_model_dump(getattr(result, "audit_report_full", None), default=None),
        "evidence_map": _safe_model_dump(getattr(result, "evidence_map", None), default=None),
        "risk_assessment": _safe_model_dump(getattr(result, "risk_assessment", None), default=None),
        "pipeline_stages": [
            _safe_model_dump(stage, default={})
            for stage in _safe_iter(getattr(result, "pipeline_stages", None))
        ],
        "warnings": _safe_iter(getattr(result, "warnings", None)),
        "errors": [
            _safe_model_dump(err, default={})
            for err in _safe_iter(getattr(result, "errors", None))
        ],
        "metrics": _safe_model_dump(getattr(result, "metrics", None), default={}),
        "escalation": _safe_model_dump(getattr(result, "escalation", None), default=None),
        "retry_count": getattr(result, "retry_count", 0),
    }
