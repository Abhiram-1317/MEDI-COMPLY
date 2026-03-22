"""Shared utility helpers for MEDI-COMPLY."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def safe_get_section(entity: Any) -> str:
    """Safely extract a section identifier from *entity*."""
    section = _read_field(entity, "section")
    if _is_non_empty_string(section):
        return str(section)

    nested = _extract_section_from_evidence(_read_field(entity, "source_evidence"))
    if nested:
        return nested

    for attr in ("evidence", "clinical_evidence", "evidence_links"):
        nested = _extract_section_from_evidence(_read_field(entity, attr))
        if nested:
            return nested
    return "UNKNOWN"


def safe_get_text(entity: Any) -> str:
    """Return the most descriptive text available for *entity*."""
    for attr in (
        "entity_text",
        "text",
        "normalized_text",
        "source_text",
        "description",
        "drug_name",
        "condition_text",
        "procedure_text",
        "entity",
        "title",
        "name",
        "display",
    ):
        value = _read_field(entity, attr)
        if _is_non_empty_string(value):
            return str(value)
    nested = safe_get_evidence_text(entity)
    return nested or ""


def safe_get_evidence(entity: Any) -> list[Any]:
    """Return a normalized list of evidence objects for *entity*."""
    if entity is None:
        return []
    for attr in ("clinical_evidence", "evidence", "evidence_links", "source_evidence"):
        value = _read_field(entity, attr)
        normalized = _normalize_iterable(value)
        if normalized:
            return normalized
    return []


def safe_get_evidence_text(entity: Any) -> str:
    """Safely grab source or surrounding text for *entity*."""
    for attr in ("source_text", "text", "normalized_text", "surrounding_text"):
        val = _read_field(entity, attr)
        if _is_non_empty_string(val):
            return str(val)

    nested = _extract_text_from_evidence(_read_field(entity, "source_evidence"))
    if nested:
        return nested

    for attr in ("evidence", "clinical_evidence"):
        nested = _extract_text_from_evidence(_read_field(entity, attr))
        if nested:
            return nested

    return ""


def safe_get_confidence(entity: Any, default: float = 0.0) -> float:
    """Return a confidence score regardless of naming differences."""
    for attr in ("confidence", "confidence_score", "overall_confidence", "confidence_level"):
        value = _read_field(entity, attr)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return float(default)


def safe_get_code(entity: Any) -> str:
    """Return a code value regardless of attribute naming."""
    for attr in ("code", "code_value", "code_id", "procedure_code", "diagnosis_code"):
        value = _read_field(entity, attr)
        if _is_non_empty_string(value):
            return str(value)
    return ""


def _extract_section_from_evidence(evidence: Any) -> str:
    if evidence is None:
        return ""
    if isinstance(evidence, dict):
        section = evidence.get("section")
        if _is_non_empty_string(section):
            return str(section)
    elif isinstance(evidence, Sequence) and evidence:
        return _extract_section_from_evidence(evidence[0])
    else:
        section = getattr(evidence, "section", None)
        if _is_non_empty_string(section):
            return str(section)
    return ""


def _extract_text_from_evidence(evidence: Any) -> str:
    if evidence is None:
        return ""
    if isinstance(evidence, dict):
        for key in ("source_text", "surrounding_text", "text", "exact_text"):
            val = evidence.get(key)
            if _is_non_empty_string(val):
                return str(val)
    elif isinstance(evidence, Sequence) and evidence:
        return _extract_text_from_evidence(evidence[0])
    else:
        for key in ("source_text", "surrounding_text", "text", "exact_text"):
            val = getattr(evidence, key, None)
            if _is_non_empty_string(val):
                return str(val)
    return ""


def _normalize_iterable(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item is not None]
    if isinstance(value, tuple) or isinstance(value, set):
        return [item for item in value if item is not None]
    return [value]


def _read_field(entity: Any, attr: str) -> Any:
    if entity is None:
        return None
    if isinstance(entity, dict):
        return entity.get(attr)
    return getattr(entity, attr, None)


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
