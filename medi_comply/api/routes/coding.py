"""Medical coding API routes and helpers for MEDI-COMPLY."""
from __future__ import annotations
import hashlib
import json
import logging
import re
import time
import traceback
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
logger = logging.getLogger(__name__)

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
def _safe_iter(value: Optional[Iterable[Any]]) -> List[Any]:
    if not value:
        return []
    return list(value)
def _convert_list(decision: Any, attr: str, mapping: List[Tuple[str, str, Any]]) -> List[Dict[str, Any]]:
    items = _safe_iter(getattr(decision, attr, None))
    converted: List[Dict[str, Any]] = []
    for item in items:
        converted.append({dst: getattr(item, src, default) for dst, src, default in mapping})
    return converted
def _convert_code_decision(decision: Optional[Any]) -> Optional[Dict[str, Any]]:
    if decision is None:
        return None
    reasoning_mapping = [("step_number", "step_number", 0), ("action", "action", ""), ("detail", "detail", ""), ("evidence_ref", "evidence_ref", None), ("guideline_ref", "guideline_ref", None), ("sub_decision", "sub_decision", None), ("confidence_impact", "confidence_impact", None)]
    evidence_mapping = [("evidence_id", "evidence_id", ""), ("entity_id", "entity_id", ""), ("source_text", "source_text", ""), ("section", "section", ""), ("page", "page", 0), ("line", "line", 0), ("char_offset", "char_offset", (0, 0)), ("relevance", "relevance", "")]
    alternatives_mapping = [("code", "code", ""), ("description", "description", ""), ("reason_rejected", "reason_rejected", ""), ("would_be_correct_if", "would_be_correct_if", None)]
    confidence_mapping = [("factor", "factor", ""), ("impact", "impact", ""), ("weight", "weight", 0.0), ("detail", "detail", "")]
    return {
        "decision_id": getattr(decision, "decision_id", None),
        "code": getattr(decision, "code", ""),
        "code_type": getattr(decision, "code_type", ""),
        "description": getattr(decision, "description", ""),
        "sequence_position": getattr(decision, "sequence_position", ""),
        "sequence_number": getattr(decision, "sequence_number", 0),
        "reasoning_chain": _convert_list(decision, "reasoning_chain", reasoning_mapping),
        "clinical_evidence": _convert_list(decision, "clinical_evidence", evidence_mapping),
        "alternatives_considered": _convert_list(decision, "alternatives_considered", alternatives_mapping),
        "confidence_score": getattr(decision, "confidence_score", 0.0),
        "confidence_factors": _convert_list(decision, "confidence_factors", confidence_mapping),
        "use_additional_applied": _safe_iter(getattr(decision, "use_additional_applied", None)),
        "code_first_applied": _safe_iter(getattr(decision, "code_first_applied", None)),
        "combination_code_note": getattr(decision, "combination_code_note", None),
        "requires_human_review": getattr(decision, "requires_human_review", False),
        "review_reason": getattr(decision, "review_reason", None),
        "is_billable": getattr(decision, "is_billable", True),
        "guidelines_cited": _safe_iter(getattr(decision, "guidelines_cited", None)),
    }
def _convert_coding_result(result: Optional[Any]) -> Optional[Dict[str, Any]]:
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
def _convert_compliance_report(report: Optional[Any]) -> Optional[Dict[str, Any]]:
    return _safe_model_dump(report, default=None)
def _convert_to_response(result: Optional[Any]) -> Dict[str, Any]:
    """Convert a MediComplyResult into a FastAPI-friendly payload."""
    if result is None:
        return {}

    coding_result = _convert_coding_result(getattr(result, "coding_result", None))
    compliance_report = _convert_compliance_report(getattr(result, "compliance_report", None))

    errors = [_safe_model_dump(err, default={}) for err in _safe_iter(getattr(result, "errors", None))]

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


# ---------------------------------------------------------------------------
# Router models and processing logic
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1/coding", tags=["Medical Coding"])
class EncounterType(str, Enum):
    INPATIENT = "inpatient"
    OUTPATIENT = "outpatient"
    EMERGENCY = "emergency"
    OBSERVATION = "observation"
    AMBULATORY_SURGERY = "ambulatory_surgery"


class CodingProcessRequest(BaseModel):
    clinical_document: str = Field(..., description="Clinical note text", min_length=20)
    encounter_type: EncounterType = Field(default=EncounterType.OUTPATIENT)
    payer_id: Optional[str] = Field(None, description="Payer identifier for payer-specific rules")
    patient_age: Optional[int] = Field(None, ge=0, le=150)
    patient_sex: Optional[str] = Field(None, pattern="^(M|F|U)$")
    provider_id: Optional[str] = None
    encounter_id: Optional[str] = Field(default_factory=lambda: f"ENC-{uuid.uuid4().hex[:8].upper()}")
    include_drg: bool = Field(default=False, description="Calculate DRG for inpatient encounters")
    include_reasoning: bool = Field(default=True, description="Include detailed reasoning chains")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "clinical_document": "CHIEF COMPLAINT: Chest pain, shortness of breath\n\nHPI: 62-year-old male presents with substernal chest pain radiating to left arm, onset 2 hours ago. Patient has history of type 2 diabetes mellitus with diabetic nephropathy, currently on metformin 1000mg BID and lisinopril 20mg daily. Recent labs show GFR 38 mL/min.\n\nASSESSMENT:\n1. Acute NSTEMI - troponin elevated at 0.8 ng/mL\n2. Type 2 diabetes with diabetic chronic kidney disease\n3. CKD stage 3b\n4. Hypertension, uncontrolled",
                "encounter_type": "inpatient",
                "patient_age": 62,
                "patient_sex": "M",
            }
        }
    )
class SingleCodeResult(BaseModel):
    code: str
    code_type: str = Field(description="ICD-10-CM or CPT")
    description: str
    sequence: str = Field(description="PRIMARY, SECONDARY, or integer position")
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning_chain: Optional[List[Dict[str, str]]] = None
    source_evidence: Optional[Dict[str, Any]] = None
    alternatives_considered: Optional[List[Dict[str, str]]] = None
    guidelines_cited: Optional[List[str]] = None


class ComplianceCheckResult(BaseModel):
    check_name: str
    result: str = Field(description="PASS, SOFT_FAIL, or HARD_FAIL")
    detail: Optional[str] = None
    reference: Optional[str] = None


class CodingProcessResponse(BaseModel):
    request_id: str = Field(default_factory=lambda: f"CR-{uuid.uuid4().hex[:8].upper()}")
    encounter_id: str = ""
    status: str = Field(description="COMPLETED, ESCALATED, or ERROR")
    diagnosis_codes: List[SingleCodeResult] = Field(default_factory=list)
    procedure_codes: List[SingleCodeResult] = Field(default_factory=list)
    drg: Optional[Dict[str, Any]] = None
    compliance_report: Dict[str, Any] = Field(default_factory=dict)
    overall_confidence: float = 0.0
    audit_id: str = ""
    processing_time_ms: int = 0
    human_review_required: bool = False
    escalation_reason: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class CodingValidateRequest(BaseModel):
    proposed_codes: List[Dict[str, str]] = Field(..., description="List of {code, code_type} to validate", min_items=1)
    clinical_document: str = Field(..., min_length=20)
    encounter_type: EncounterType = Field(default=EncounterType.OUTPATIENT)
    patient_age: Optional[int] = None
    patient_sex: Optional[str] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "proposed_codes": [
                    {"code": "E11.22", "code_type": "ICD-10-CM"},
                    {"code": "N18.31", "code_type": "ICD-10-CM"},
                    {"code": "99214", "code_type": "CPT"},
                ],
                "clinical_document": "Patient with type 2 diabetes and diabetic CKD stage 3b. Office visit for management.",
                "encounter_type": "outpatient",
            }
        }
    )

class CodeValidationResult(BaseModel):
    code: str
    code_type: str
    is_valid: bool
    exists_in_database: bool
    is_billable: bool
    specificity_adequate: bool
    evidence_supports: bool
    compliance_checks: List[ComplianceCheckResult] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    recommended_alternative: Optional[str] = None


class CodingValidateResponse(BaseModel):
    request_id: str = Field(default_factory=lambda: f"CV-{uuid.uuid4().hex[:8].upper()}")
    validation_results: List[CodeValidationResult] = Field(default_factory=list)
    overall_valid: bool = True
    suggestions: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class AuditRetrieveResponse(BaseModel):
    audit_id: str
    workflow_type: str = "MEDICAL_CODING"
    timestamp: str = ""
    processing_time_ms: int = 0
    knowledge_base_version: str = "KB-2025-Q1"
    model_versions: Dict[str, str] = Field(default_factory=dict)
    input_reference: Dict[str, Any] = Field(default_factory=dict)
    extraction_results: Dict[str, Any] = Field(default_factory=dict)
    coding_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    compliance_checks: List[ComplianceCheckResult] = Field(default_factory=list)
    overall_risk_score: float = 0.0
    escalation_triggered: bool = False
    human_review_required: bool = False
    digital_signature: str = ""


class CodingProcessor:
    """Self-contained coding processor implementing simplified rule-based logic."""

    def __init__(self) -> None:
        self.CONDITION_CODE_MAP: Dict[str, Dict[str, str]] = {
            "type 2 diabetes": {"code": "E11.9", "desc": "Type 2 DM without complications"},
            "type 2 diabetes with diabetic nephropathy": {"code": "E11.22", "desc": "Type 2 DM with diabetic CKD"},
            "type 2 diabetes with diabetic ckd": {"code": "E11.22", "desc": "Type 2 DM with diabetic CKD"},
            "nstemi": {"code": "I21.4", "desc": "Non-ST elevation MI"},
            "acute nstemi": {"code": "I21.4", "desc": "Non-ST elevation MI"},
            "stemi": {"code": "I21.3", "desc": "ST elevation MI"},
            "hypertension": {"code": "I10", "desc": "Essential hypertension"},
            "ckd stage 3": {"code": "N18.3", "desc": "CKD stage 3"},
            "ckd stage 3a": {"code": "N18.31", "desc": "CKD stage 3a"},
            "ckd stage 3b": {"code": "N18.32", "desc": "CKD stage 3b"},
            "chest pain": {"code": "R07.9", "desc": "Chest pain, unspecified"},
            "shortness of breath": {"code": "R06.02", "desc": "Shortness of breath"},
            "pneumonia": {"code": "J18.9", "desc": "Pneumonia, unspecified"},
            "copd": {"code": "J44.1", "desc": "COPD with acute exacerbation"},
            "heart failure": {"code": "I50.9", "desc": "Heart failure, unspecified"},
            "congestive heart failure": {"code": "I50.9", "desc": "Heart failure, unspecified"},
            "atrial fibrillation": {"code": "I48.91", "desc": "Unspecified atrial fibrillation"},
            "urinary tract infection": {"code": "N39.0", "desc": "UTI, site not specified"},
            "sepsis": {"code": "A41.9", "desc": "Sepsis, unspecified organism"},
            "low back pain": {"code": "M54.5", "desc": "Low back pain"},
            "gerd": {"code": "K21.0", "desc": "GERD with esophagitis"},
            "asthma": {"code": "J45.20", "desc": "Mild intermittent asthma"},
            "obesity": {"code": "E66.01", "desc": "Morbid obesity due to excess calories"},
            "depression": {"code": "F32.9", "desc": "Major depressive disorder, single episode"},
            "anxiety": {"code": "F41.1", "desc": "Generalized anxiety disorder"},
            "dvt": {"code": "I82.40", "desc": "Acute DVT of lower extremity"},
            "pulmonary embolism": {"code": "I26.99", "desc": "Other pulmonary embolism"},
            "anemia": {"code": "D64.9", "desc": "Anemia, unspecified"},
            "hypothyroidism": {"code": "E03.9", "desc": "Hypothyroidism, unspecified"},
            "acute kidney injury": {"code": "N17.9", "desc": "Acute kidney failure, unspecified"},
        }

        self.PROCEDURE_CODE_MAP: Dict[str, Dict[str, str]] = {
            "office visit": {"code": "99213", "desc": "Office visit, established, low complexity"},
            "office visit moderate": {"code": "99214", "desc": "Office visit, established, moderate"},
            "office visit high": {"code": "99215", "desc": "Office visit, established, high"},
            "new patient visit": {"code": "99203", "desc": "Office visit, new patient, low"},
            "hospital admission": {"code": "99223", "desc": "Initial hospital care, high complexity"},
            "subsequent hospital care": {"code": "99232", "desc": "Subsequent hospital care, moderate"},
            "discharge management": {"code": "99238", "desc": "Hospital discharge, 30 min or less"},
            "chest xray": {"code": "71046", "desc": "Chest X-ray, 2 views"},
            "ekg": {"code": "93000", "desc": "Electrocardiogram, routine with interpretation"},
            "echocardiogram": {"code": "93306", "desc": "Echocardiography, transthoracic"},
            "ct head": {"code": "70450", "desc": "CT head without contrast"},
            "mri brain": {"code": "70551", "desc": "MRI brain without contrast"},
            "mri knee": {"code": "73721", "desc": "MRI lower extremity joint without contrast"},
            "cbc": {"code": "85025", "desc": "Complete blood count with differential"},
            "metabolic panel": {"code": "80053", "desc": "Comprehensive metabolic panel"},
            "urinalysis": {"code": "81003", "desc": "Urinalysis, automated"},
            "troponin": {"code": "84484", "desc": "Troponin, quantitative"},
            "cardiac catheterization": {"code": "93452", "desc": "Left heart catheterization"},
            "intubation": {"code": "31500", "desc": "Intubation, endotracheal"},
            "ventilator management": {"code": "94002", "desc": "Ventilation assist, initial day"},
        }

        self.VALID_CODES = {entry["code"] for entry in self.CONDITION_CODE_MAP.values()} | {
            entry["code"] for entry in self.PROCEDURE_CODE_MAP.values()
        }

        self.NEGATION_PATTERNS = [
            "denies",
            "no evidence of",
            "ruled out",
            "negative for",
            "without",
            "no history of",
            "not found",
            "absent",
            "no sign of",
        ]

        self.SECTION_KEYWORDS: Dict[str, str] = {
            "CHIEF COMPLAINT": "cc",
            "HPI": "hpi",
            "HISTORY OF PRESENT ILLNESS": "hpi",
            "ASSESSMENT": "assessment",
            "PLAN": "plan",
            "EXAM": "exam",
            "PHYSICAL EXAM": "exam",
            "MEDICATIONS": "meds",
            "LAB": "labs",
            "RESULTS": "labs",
            "REVIEW OF SYSTEMS": "ros",
        }

        self._audit_store: Dict[str, AuditRetrieveResponse] = {}

    def _hash_text(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _detect_sections(self, text: str) -> Dict[str, str]:
        sections: Dict[str, str] = {}
        current_section = "body"
        sections[current_section] = ""
        lines = text.splitlines()
        for line in lines:
            stripped = line.strip()
            upper = stripped.upper()
            matched_key = None
            for key in self.SECTION_KEYWORDS:
                if upper.startswith(key):
                    matched_key = self.SECTION_KEYWORDS[key]
                    break
            if matched_key:
                current_section = matched_key
                sections[current_section] = ""
                continue
            sections[current_section] += (stripped + " ") if stripped else ""
        for key in list(sections.keys()):
            sections[key] = sections[key].strip()
        return sections

    def _find_section_for_span(self, sections: Dict[str, str], snippet: str) -> str:
        snippet_lower = snippet.lower()
        for name, content in sections.items():
            if snippet_lower in content.lower():
                return name
        return "body"

    def _extract_snippet(self, text: str, start_idx: int, end_idx: int, window: int = 50) -> str:
        pre = max(0, start_idx - window)
        post = min(len(text), end_idx + window)
        return text[pre:post].strip()

    def _is_negated(self, text: str, start_idx: int, end_idx: int) -> bool:
        window = text[max(0, start_idx - 40) : min(len(text), end_idx + 40)].lower()
        for pattern in self.NEGATION_PATTERNS:
            if pattern in window:
                return True
        return False

    def _match_conditions(
        self, sections: Dict[str, str], document_lower: str, original_text: str
    ) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for keyword, meta in self.CONDITION_CODE_MAP.items():
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            for match in pattern.finditer(original_text):
                start_idx, end_idx = match.start(), match.end()
                if self._is_negated(original_text, start_idx, end_idx):
                    continue
                section_name = self._find_section_for_span(sections, match.group())
                confidence = 0.88
                if section_name == "assessment":
                    confidence += 0.05
                if match.group().lower() == keyword:
                    confidence += 0.03
                confidence = min(confidence, 0.99)
                snippet = self._extract_snippet(original_text, start_idx, end_idx)
                found.append(
                    {
                        "keyword": keyword,
                        "code": meta["code"],
                        "description": meta["desc"],
                        "section": section_name,
                        "snippet": snippet,
                        "confidence": confidence,
                        "start": start_idx,
                    }
                )
        found.sort(key=lambda x: (0 if x["section"] == "assessment" else 1, x["start"]))
        return found

    def _match_procedures(self, document_lower: str, original_text: str, encounter: EncounterType) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        for keyword, meta in self.PROCEDURE_CODE_MAP.items():
            if keyword in document_lower:
                idx = original_text.lower().find(keyword)
                snippet = self._extract_snippet(original_text, idx, idx + len(keyword))
                found.append(
                    {
                        "keyword": keyword,
                        "code": meta["code"],
                        "description": meta["desc"],
                        "section": self._find_section_for_span({"body": original_text}, keyword),
                        "snippet": snippet,
                    }
                )
        if encounter == EncounterType.INPATIENT and not any(f["code"] == "99223" for f in found):
            meta = self.PROCEDURE_CODE_MAP["hospital admission"]
            found.append(
                {
                    "keyword": "hospital admission",
                    "code": meta["code"],
                    "description": meta["desc"],
                    "section": "admission",
                    "snippet": "Auto-added for inpatient encounter",
                }
            )
        return found

    def _deduplicate_conditions(self, found: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ordered: List[Dict[str, Any]] = []
        seen_codes: set[str] = set()
        specific_first = sorted(found, key=lambda x: (-len(x["keyword"]), x["start"]))
        for item in specific_first:
            code = item["code"]
            keyword = item["keyword"]
            if code in seen_codes:
                continue
            if any(keyword in existing["keyword"] for existing in ordered):
                continue
            seen_codes.add(code)
            ordered.append(item)
        ordered.sort(key=lambda x: (0 if x["section"] == "assessment" else 1, x["start"]))
        return ordered

    def _assign_sequences(self, conditions: List[Dict[str, Any]]) -> List[SingleCodeResult]:
        results: List[SingleCodeResult] = []
        for idx, cond in enumerate(conditions):
            seq = "PRIMARY" if idx == 0 else "SECONDARY"
            reasoning = [
                {"step": "1", "action": "Entity extraction", "detail": f"Found '{cond['keyword']}' in {cond['section']}"},
                {"step": "2", "action": "Code lookup", "detail": f"Matched to {cond['code']} - {cond['description']}"},
                {"step": "3", "action": "Specificity check", "detail": "Most specific code available"},
                {"step": "4", "action": "Negation check", "detail": "No negation detected"},
            ]
            results.append(
                SingleCodeResult(
                    code=cond["code"],
                    code_type="ICD-10-CM",
                    description=cond["description"],
                    sequence=seq,
                    confidence=cond["confidence"],
                    reasoning_chain=reasoning,
                    source_evidence={
                        "section": cond["section"],
                        "snippet": cond["snippet"],
                        "keyword": cond["keyword"],
                    },
                    guidelines_cited=["OCG Section II", "OCG Section IV"],
                )
            )
        return results

    def _procedures_to_results(self, procedures: List[Dict[str, Any]]) -> List[SingleCodeResult]:
        results: List[SingleCodeResult] = []
        for idx, proc in enumerate(procedures, start=1):
            reasoning = [
                {"step": "1", "action": "Entity extraction", "detail": f"Found '{proc['keyword']}'"},
                {"step": "2", "action": "Code lookup", "detail": f"Matched to {proc['code']} - {proc['description']}"},
            ]
            results.append(
                SingleCodeResult(
                    code=proc["code"],
                    code_type="CPT",
                    description=proc["description"],
                    sequence=str(idx),
                    confidence=0.9,
                    reasoning_chain=reasoning,
                    source_evidence={"snippet": proc["snippet"], "keyword": proc["keyword"]},
                    guidelines_cited=["CPT 2026"],
                )
            )
        return results

    def _compliance_checks(
        self,
        diagnosis_codes: List[SingleCodeResult],
        procedure_codes: List[SingleCodeResult],
        encounter_type: EncounterType,
        patient_age: Optional[int],
        patient_sex: Optional[str],
    ) -> Tuple[List[ComplianceCheckResult], float, bool]:
        checks: List[ComplianceCheckResult] = []

        def add_check(name: str, result: str, detail: Optional[str] = None, ref: Optional[str] = None) -> None:
            checks.append(ComplianceCheckResult(check_name=name, result=result, detail=detail, reference=ref))

        all_codes = [c.code for c in diagnosis_codes] + [p.code for p in procedure_codes]
        missing = [code for code in all_codes if code not in self.VALID_CODES]
        add_check("CODE_EXISTS", "PASS" if not missing else "HARD_FAIL", detail="; ".join(missing) or None)

        if {"93306", "93000"}.issubset({p.code for p in procedure_codes}):
            add_check("NCCI_EDITS", "SOFT_FAIL", detail="Potential bundling between echocardiogram and ECG", ref="NCCI")
        else:
            add_check("NCCI_EDITS", "PASS")

        if "I21.3" in all_codes and "I21.4" in all_codes:
            add_check("EXCLUDES_1", "HARD_FAIL", detail="Cannot code STEMI and NSTEMI together", ref="ICD-10 Excludes1")
        else:
            add_check("EXCLUDES_1", "PASS")

        specificity_fail = any(code.endswith(".9") for code in all_codes)
        add_check("SPECIFICITY", "SOFT_FAIL" if specificity_fail else "PASS", detail=".9 codes detected" if specificity_fail else None)

        age_sex_fail = False
        if patient_sex == "M" and any(code.startswith("O") for code in all_codes):
            age_sex_fail = True
        if patient_age is not None and patient_age < 18 and any(code.startswith("I21") for code in all_codes):
            age_sex_fail = True
        add_check("AGE_SEX", "HARD_FAIL" if age_sex_fail else "PASS")

        if procedure_codes and not diagnosis_codes:
            add_check("MEDICAL_NECESSITY", "HARD_FAIL", detail="Procedures without diagnoses")
        else:
            add_check("MEDICAL_NECESSITY", "PASS")

        evidence_missing = any(c.source_evidence is None for c in diagnosis_codes + procedure_codes)
        add_check("EVIDENCE_LINKED", "HARD_FAIL" if evidence_missing else "PASS")

        if diagnosis_codes and diagnosis_codes[0].sequence != "PRIMARY":
            add_check("SEQUENCING", "SOFT_FAIL", detail="Primary diagnosis not first")
        else:
            add_check("SEQUENCING", "PASS")

        avg_conf = 0.0
        all_conf = [c.confidence for c in diagnosis_codes + procedure_codes]
        if all_conf:
            avg_conf = sum(all_conf) / len(all_conf)
        low_conf = avg_conf < 0.85
        add_check("CONFIDENCE_THRESHOLD", "SOFT_FAIL" if low_conf else "PASS", detail=f"avg={avg_conf:.2f}")

        phi_present = any("name" in (c.source_evidence or {}).get("snippet", "").lower() for c in diagnosis_codes)
        add_check("PHI_CHECK", "SOFT_FAIL" if phi_present else "PASS")

        human_review = any(check.result == "HARD_FAIL" for check in checks) or low_conf
        return checks, avg_conf, human_review

    def _drg_for_inpatient(self, diagnosis_codes: List[SingleCodeResult]) -> Optional[Dict[str, Any]]:
        if not diagnosis_codes:
            return None
        primary_code = diagnosis_codes[0].code
        weight = 1.2 if primary_code.startswith("I21") else 0.9
        return {"drg_code": "MS-291", "description": "Heart failure & shock", "relative_weight": weight}

    def process_clinical_document(self, request: CodingProcessRequest) -> CodingProcessResponse:
        sections = self._detect_sections(request.clinical_document)
        document_lower = request.clinical_document.lower()
        found_conditions = self._match_conditions(sections, document_lower, request.clinical_document)
        deduped_conditions = self._deduplicate_conditions(found_conditions)
        diagnosis_results = self._assign_sequences(deduped_conditions)

        procedures_found = self._match_procedures(document_lower, request.clinical_document, request.encounter_type)
        procedure_results = self._procedures_to_results(procedures_found)

        if request.encounter_type == EncounterType.OUTPATIENT:
            diagnosis_results = [d for d in diagnosis_results if "suspected" not in d.description.lower()]
        else:
            for diag in diagnosis_results:
                if "suspected" in diag.description.lower():
                    diag.reasoning_chain = (diag.reasoning_chain or []) + [
                        {"step": "5", "action": "OCG II.H", "detail": "Treated as confirmed for inpatient"}
                    ]

        compliance_checks, avg_conf, human_review = self._compliance_checks(
            diagnosis_results, procedure_results, request.encounter_type, request.patient_age, request.patient_sex
        )

        drg_payload = None
        if request.include_drg and request.encounter_type == EncounterType.INPATIENT:
            drg_payload = self._drg_for_inpatient(diagnosis_results)

        audit_id = f"AUD-{uuid.uuid4().hex[:10].upper()}"
        risk_score = 1.0 - avg_conf if avg_conf else 1.0

        audit_record = AuditRetrieveResponse(
            audit_id=audit_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            processing_time_ms=0,
            knowledge_base_version="KB-2025-Q1",
            model_versions={"coding_model": "v1.0-simplified"},
            input_reference={"encounter_id": request.encounter_id, "document_hash": self._hash_text(request.clinical_document)},
            extraction_results={
                "conditions": [d.model_dump() for d in diagnosis_results],
                "procedures": [p.model_dump() for p in procedure_results],
            },
            coding_decisions=[d.model_dump() for d in diagnosis_results + procedure_results],
            compliance_checks=compliance_checks,
            overall_risk_score=risk_score,
            escalation_triggered=human_review,
            human_review_required=human_review,
            digital_signature="",
        )
        audit_record.digital_signature = self._hash_text(json.dumps(audit_record.model_dump(), sort_keys=True))
        self._audit_store[audit_id] = audit_record
        status = "COMPLETED" if not human_review else "ESCALATED"
        escalation_reason = None
        if human_review:
            failures = [c.check_name for c in compliance_checks if c.result == "HARD_FAIL"]
            escalation_reason = ", ".join(failures) if failures else "Confidence below threshold"

        response = CodingProcessResponse(
            encounter_id=request.encounter_id or "",
            status=status,
            diagnosis_codes=diagnosis_results,
            procedure_codes=procedure_results,
            drg=drg_payload,
            compliance_report={
                "checks": [c.model_dump() for c in compliance_checks],
                "knowledge_base_version": "KB-2025-Q1",
            },
            overall_confidence=avg_conf,
            audit_id=audit_id,
            human_review_required=human_review,
            escalation_reason=escalation_reason,
            warnings=[c.detail for c in compliance_checks if c.result != "PASS" and c.detail],
        )
        return response

    def validate_codes(self, request: CodingValidateRequest) -> CodingValidateResponse:
        document_lower = request.clinical_document.lower()
        validation_results: List[CodeValidationResult] = []
        suggestions: List[str] = []

        for proposed in request.proposed_codes:
            code = proposed.get("code", "")
            code_type = proposed.get("code_type", "").upper() or "UNKNOWN"
            exists = code in self.VALID_CODES
            is_billable = "." in code or code_type == "CPT"
            specificity_ok = not code.endswith(".9")

            evidence_supports = any(keyword in document_lower for keyword in self.CONDITION_CODE_MAP.keys())
            compliance_checks = [ComplianceCheckResult(check_name="CODE_EXISTS", result="PASS" if exists else "HARD_FAIL"), ComplianceCheckResult(check_name="SPECIFICITY", result="PASS" if specificity_ok else "SOFT_FAIL"), ComplianceCheckResult(check_name="EVIDENCE_LINKED", result="PASS" if evidence_supports else "SOFT_FAIL")]

            rec_alt = None
            code_lower = code.lower()
            if code_lower in {"e11.9", "i10", "j18.9"}:
                for keyword, meta in self.CONDITION_CODE_MAP.items():
                    if meta["code"].lower() == code_lower and not specificity_ok:
                        rec_alt = meta["code"]
                        break

            sug: List[str] = []
            if not exists:
                sug.append("Code not found in knowledge base")
            if not specificity_ok:
                sug.append("Consider more specific child code")
            if not evidence_supports:
                sug.append("Provide clinical evidence supporting the code")

            validation_results.append(
                CodeValidationResult(
                    code=code,
                    code_type=code_type,
                    is_valid=exists and specificity_ok and evidence_supports,
                    exists_in_database=exists,
                    is_billable=is_billable,
                    specificity_adequate=specificity_ok,
                    evidence_supports=evidence_supports,
                    compliance_checks=compliance_checks,
                    suggestions=sug,
                    recommended_alternative=rec_alt,
                )
            )
            suggestions.extend(sug)

        overall_valid = all(result.is_valid for result in validation_results) if validation_results else True
        return CodingValidateResponse(validation_results=validation_results, overall_valid=overall_valid, suggestions=suggestions)
    def get_audit_record(self, audit_id: str) -> Optional[AuditRetrieveResponse]:
        return self._audit_store.get(audit_id)


def _get_processor() -> CodingProcessor:
    return _processor

_processor = CodingProcessor()

@router.post(
    "/process",
    response_model=CodingProcessResponse,
    summary="Process clinical document into ICD-10/CPT codes",
    description="Accepts a clinical document and returns validated ICD-10-CM diagnosis codes and CPT procedure codes with full compliance verification and audit trail.",
)
async def process_coding(request: CodingProcessRequest) -> CodingProcessResponse:
    start_time = time.time()
    try:
        result = _get_processor().process_clinical_document(request)
        result.processing_time_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Coding processed: %s - %s dx, %s px",
            result.request_id,
            len(result.diagnosis_codes),
            len(result.procedure_codes),
        )
        return result
    except Exception as exc:
        logger.error("Coding processing error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Processing error: {str(exc)}")


@router.get(
    "/audit/{audit_id}",
    response_model=AuditRetrieveResponse,
    summary="Retrieve coding audit record",
    description="Returns the complete audit trail for a coding decision including reasoning chains, compliance checks, and evidence mappings.",
)
async def get_coding_audit(audit_id: str = Path(..., description="Audit trail identifier")) -> AuditRetrieveResponse:
    record = _get_processor().get_audit_record(audit_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Audit record '{audit_id}' not found")
    return record


@router.post(
    "/validate",
    response_model=CodingValidateResponse,
    summary="Validate proposed medical codes",
    description="Validates proposed ICD-10/CPT codes against clinical documentation. Returns validation results with suggestions.",
)
async def validate_codes(request: CodingValidateRequest) -> CodingValidateResponse:
    try:
        result = _get_processor().validate_codes(request)
        logger.info(
            "Validation: %s - %s codes, overall_valid=%s",
            result.request_id,
            len(result.validation_results),
            result.overall_valid,
        )
        return result
    except Exception as exc:
        logger.error("Validation error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Validation error: {str(exc)}")
