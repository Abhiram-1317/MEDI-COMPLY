"""Prior authorization API routes for MEDI-COMPLY."""
from __future__ import annotations

import logging
import re
import time
import traceback
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Path, Query, Depends
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/v1/prior-auth", tags=["Prior Authorization"])


class ServiceCategory(str, Enum):
    PROCEDURE = "procedure"
    MEDICATION = "medication"
    DME = "dme"
    IMAGING = "imaging"
    LAB = "lab"
    THERAPY = "therapy"
    INPATIENT_ADMISSION = "inpatient_admission"
    OTHER = "other"


class AuthDecisionStatus(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PENDING_INFO = "PENDING_INFO"
    NOT_REQUIRED = "NOT_REQUIRED"
    ESCALATED = "ESCALATED"


class CriterionStatus(str, Enum):
    MET = "MET"
    NOT_MET = "NOT_MET"
    UNCLEAR = "UNCLEAR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class UrgencyLevel(str, Enum):
    STANDARD = "standard"
    URGENT = "urgent"
    EMERGENT = "emergent"


class AuthSubmitRequest(BaseModel):
    member_id: str = Field(..., description="Insurance member ID")
    provider_id: str = Field(..., description="Requesting provider NPI or ID")
    payer_id: str = Field(..., description="Insurance payer identifier")
    service_type: ServiceCategory = Field(..., description="Type of service requiring auth")
    service_code: str = Field(..., description="CPT/HCPCS code for the service")
    service_description: Optional[str] = Field(None, description="Human-readable service description")
    diagnosis_codes: List[str] = Field(..., min_length=1, description="Supporting ICD-10 diagnosis codes")
    clinical_justification: str = Field(..., min_length=20, description="Clinical rationale for the service")
    clinical_documents: Optional[List[str]] = Field(None, description="Additional clinical document texts")
    requested_units: int = Field(default=1, ge=1, description="Number of units/visits requested")
    requested_start_date: Optional[str] = Field(None, description="Requested start date (YYYY-MM-DD)")
    requested_end_date: Optional[str] = Field(None, description="Requested end date (YYYY-MM-DD)")
    urgency: UrgencyLevel = Field(default=UrgencyLevel.STANDARD)
    is_retrospective: bool = Field(default=False, description="True if service already rendered")
    date_of_service: Optional[str] = Field(None, description="Date of service if retrospective (YYYY-MM-DD)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "member_id": "MEM-12345",
                "provider_id": "1234567890",
                "payer_id": "BCBS",
                "service_type": "imaging",
                "service_code": "73721",
                "service_description": "MRI of knee without contrast",
                "diagnosis_codes": ["M17.11", "M23.21"],
                "clinical_justification": "62-year-old patient with right knee pain for 8 weeks. Failed conservative treatment including physical therapy 3x/week for 6 weeks and NSAIDs (ibuprofen 800mg TID). X-ray performed showing moderate joint space narrowing. Physical exam reveals positive McMurray test. Requesting MRI to evaluate for meniscal tear.",
                "requested_units": 1,
                "urgency": "standard",
                "is_retrospective": False,
            }
        }
    )


class CriterionMatchResult(BaseModel):
    criterion_id: str
    description: str
    category: str
    required: bool
    status: CriterionStatus
    evidence_found: Optional[str] = None
    notes: Optional[str] = None


class AppealInfo(BaseModel):
    can_appeal: bool = True
    appeal_deadline_days: int = 180
    appeal_levels: List[str] = Field(
        default_factory=lambda: [
            "Internal Review (Level 1)",
            "External Independent Review (Level 2)",
            "State Insurance Commissioner (Level 3)",
        ]
    )
    peer_to_peer_available: bool = True
    required_documents: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


class AuthSubmitResponse(BaseModel):
    auth_request_id: str = Field(default_factory=lambda: f"AUTH-{uuid.uuid4().hex[:8].upper()}")
    status: AuthDecisionStatus
    decision_date: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    approved_units: Optional[int] = None
    approved_service_code: Optional[str] = None
    policy_reference: Optional[str] = None
    criteria_match_report: List[CriterionMatchResult] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    denial_reasons: List[str] = Field(default_factory=list)
    appeal_rights: Optional[AppealInfo] = None
    alternative_treatments: List[str] = Field(default_factory=list)
    peer_review_required: bool = False
    determination_letter: Optional[str] = None
    confidence_score: float = 0.0
    reasoning_chain: List[Dict[str, str]] = Field(default_factory=list)
    processing_time_ms: int = 0
    audit_id: str = Field(default_factory=lambda: f"AUD-AUTH-{uuid.uuid4().hex[:8].upper()}")
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class AuthCheckRequest(BaseModel):
    service_code: str = Field(..., description="CPT/HCPCS code")
    payer_id: str = Field(..., description="Payer identifier")
    service_type: Optional[ServiceCategory] = None
    diagnosis_codes: Optional[List[str]] = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "service_code": "73721",
                "payer_id": "BCBS",
                "service_type": "imaging",
            }
        }
    )


class AuthCheckResponse(BaseModel):
    service_code: str
    payer_id: str
    auth_required: bool
    reason: str
    policy_reference: Optional[str] = None
    estimated_turnaround: Optional[str] = None
    required_documentation: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class PriorAuthProcessor:
    """Self-contained prior authorization processor."""

    def __init__(self) -> None:
        self.AUTH_REQUIREMENT_MATRIX: Dict[str, Dict[str, Dict[str, Any]]] = {
            "MEDICARE": {
                "imaging": {"codes": ["70000-79999"], "description": "Advanced imaging requires auth"},
                "procedure": {"codes": ["27000-27899", "29800-29999"], "description": "Major surgeries require auth"},
                "dme": {"codes": ["E0100-E9999"], "description": "DME requires auth"},
                "medication": {"codes": ["J0000-J9999"], "description": "Specialty injectables require auth"},
                "inpatient_admission": {"codes": ["99221-99223"], "description": "Elective admissions require auth"},
            },
            "BCBS": {
                "imaging": {"codes": ["70000-79999"], "description": "Advanced imaging requires auth"},
                "procedure": {
                    "codes": ["27000-27899", "29800-29999", "22000-22899"],
                    "description": "Orthopedic/spine surgeries require auth",
                },
                "dme": {"codes": ["E0100-E9999"], "description": "DME requires auth"},
                "medication": {"codes": ["J0000-J9999"], "description": "Specialty medications require auth"},
                "therapy": {"codes": ["97110-97542"], "description": "PT/OT beyond 12 visits requires auth"},
            },
            "AETNA": {
                "imaging": {"codes": ["70000-79999"], "description": "Advanced imaging requires auth"},
                "procedure": {"codes": ["27000-27899"], "description": "Joint procedures require auth"},
                "medication": {"codes": ["J0000-J9999"], "description": "Specialty meds require auth"},
                "inpatient_admission": {"codes": ["99221-99223"], "description": "All admissions require auth"},
            },
            "UNITED": {
                "imaging": {"codes": ["70000-79999"], "description": "Advanced imaging requires auth"},
                "procedure": {"codes": ["27000-27899", "29800-29999"], "description": "Surgeries require auth"},
                "dme": {"codes": ["E0100-E9999"], "description": "DME requires auth"},
            },
        }

        self.MEDICAL_POLICIES: List[Dict[str, Any]] = [
            {
                "policy_id": "POL-BCBS-MSK-MRI",
                "policy_name": "MRI Musculoskeletal",
                "payer_id": "BCBS",
                "service_codes": ["73721", "73723", "73722", "73718", "73720"],
                "criteria": [
                    {"criterion_id": "C1", "description": "Conservative treatment failed for minimum 6 weeks", "category": "step_therapy", "required": True},
                    {"criterion_id": "C2", "description": "Clinical examination findings documented", "category": "documentation", "required": True},
                    {"criterion_id": "C3", "description": "X-ray performed prior to MRI request", "category": "step_therapy", "required": False},
                    {"criterion_id": "C4", "description": "Pain or functional limitation documented", "category": "documentation", "required": True},
                ],
                "turnaround_standard": 3,
                "turnaround_urgent": 1,
            },
            {
                "policy_id": "POL-BCBS-ORTHO-REPL",
                "policy_name": "Hip/Knee Replacement",
                "payer_id": "BCBS",
                "service_codes": ["27447", "27130"],
                "criteria": [
                    {"criterion_id": "C1", "description": "BMI documented", "category": "documentation", "required": True},
                    {"criterion_id": "C2", "description": "Failed physical therapy minimum 3 months", "category": "step_therapy", "required": True},
                    {"criterion_id": "C3", "description": "Imaging confirms joint degeneration", "category": "documentation", "required": True},
                    {"criterion_id": "C4", "description": "Failed pharmacologic management", "category": "step_therapy", "required": True},
                    {"criterion_id": "C5", "description": "No active infection", "category": "contraindication", "required": True},
                ],
                "turnaround_standard": 5,
                "turnaround_urgent": 2,
            },
            {
                "policy_id": "POL-MED-NEURO-MRI",
                "policy_name": "MRI Brain",
                "payer_id": "MEDICARE",
                "service_codes": ["70551", "70553"],
                "criteria": [
                    {"criterion_id": "C1", "description": "Neurological symptoms documented", "category": "documentation", "required": True},
                    {"criterion_id": "C2", "description": "CT scan performed first unless contraindicated", "category": "step_therapy", "required": False},
                    {"criterion_id": "C3", "description": "Clinical indication matches LCD criteria", "category": "medical_necessity", "required": True},
                ],
                "turnaround_standard": 3,
                "turnaround_urgent": 1,
            },
            {
                "policy_id": "POL-AET-HUMIRA",
                "policy_name": "Specialty Medication - Humira",
                "payer_id": "AETNA",
                "service_codes": ["J0135"],
                "criteria": [
                    {"criterion_id": "C1", "description": "Diagnosis of rheumatoid arthritis, psoriasis, or Crohn's confirmed", "category": "required_diagnosis", "required": True},
                    {"criterion_id": "C2", "description": "Failed or intolerant to methotrexate", "category": "step_therapy", "required": True},
                    {"criterion_id": "C3", "description": "TB test performed within 6 months", "category": "lab_value", "required": True},
                    {"criterion_id": "C4", "description": "No active serious infection", "category": "contraindication", "required": True},
                ],
                "turnaround_standard": 5,
                "turnaround_urgent": 2,
            },
            {
                "policy_id": "POL-UNI-CT",
                "policy_name": "CT Scan",
                "payer_id": "UNITED",
                "service_codes": ["70450", "70460", "74176", "74177", "74178"],
                "criteria": [
                    {"criterion_id": "C1", "description": "Clinical indication documented", "category": "documentation", "required": True},
                    {"criterion_id": "C2", "description": "X-ray or ultrasound performed first if applicable", "category": "step_therapy", "required": False},
                ],
                "turnaround_standard": 2,
                "turnaround_urgent": 1,
            },
            {
                "policy_id": "POL-BCBS-PT-EXT",
                "policy_name": "Physical Therapy Extension",
                "payer_id": "BCBS",
                "service_codes": ["97110", "97140", "97530", "97542"],
                "criteria": [
                    {"criterion_id": "C1", "description": "Initial 12 visits completed", "category": "step_therapy", "required": True},
                    {"criterion_id": "C2", "description": "Documented functional improvement", "category": "documentation", "required": True},
                    {"criterion_id": "C3", "description": "Treatment plan with goals submitted", "category": "documentation", "required": True},
                ],
                "turnaround_standard": 3,
                "turnaround_urgent": 1,
            },
        ]

        self.RETRO_AUTH_RULES: Dict[str, Dict[str, Any]] = {
            "MEDICARE": {"allowed": True, "emergency_window_hours": 72, "non_emergency": False},
            "BCBS": {"allowed": True, "emergency_window_hours": 48, "non_emergency": False},
            "AETNA": {"allowed": True, "emergency_window_hours": 72, "non_emergency": True, "non_emergency_window_days": 14},
            "UNITED": {"allowed": True, "emergency_window_hours": 72, "non_emergency": False},
        }

        self.STEP_THERAPY_KEYWORDS: List[str] = [
            "failed",
            "tried",
            "intolerant",
            "did not respond",
            "no improvement",
            "inadequate response",
            "contraindicated",
            "adverse reaction",
            "discontinued",
            "completed",
            "finished",
            "undergone",
            "received",
            "trialed",
        ]

        self.DOCUMENT_KEYWORDS: List[str] = [
            "exam",
            "document",
            "documented",
            "finding",
            "imaging",
            "x-ray",
            "bmi",
            "range of motion",
            "ct",
            "mri",
            "assessment",
            "plan",
            "physical therapy",
            "functional",
            "strength",
            "gait",
            "neurological",
            "reflex",
            "sensation",
        ]

        self.AMBIGUOUS_PHRASES: List[str] = [
            "details unavailable",
            "awaiting records",
            "awaiting documentation",
            "pending documentation",
            "pending records",
            "no structured note",
            "lacks objective detail",
            "insufficient detail",
            "unable to verify",
            "caregiver reported",
        ]

        self.CONTRAINDICATION_KEYWORDS: List[str] = [
            "infection",
            "sepsis",
            "cellulitis",
            "abscess",
            "allergy",
            "anaphylaxis",
            "hypersensitivity",
            "contraindication",
        ]

        self.DIAGNOSIS_KEYWORDS: Dict[str, List[str]] = {
            "rheumatoid": ["rheumatoid", "ra"],
            "psoriasis": ["psoriasis", "psoriatic"],
            "crohn": ["crohn", "crohns", "crohn's"],
            "arthritis": ["arthritis", "osteoarthritis", "arthropathy"],
        }

        self.LAB_PATTERNS: List[str] = [
            r"GFR\s*(?:of\s*)?\d+",
            r"HbA1c\s*(?:of\s*)?\d+\.?\d*",
            r"creatinine\s*(?:of\s*)?\d+\.?\d*",
            r"troponin\s*(?:of\s*)?\d+\.?\d*",
            r"BMI\s*(?:of\s*)?\d+\.?\d*",
            r"TB\s*(?:test|screen)\s*(?:negative|positive)",
            r"blood pressure\s*\d+/\d+",
            r"weight\s*\d+",
        ]

        self._audit_store: Dict[str, Dict[str, Any]] = {}

    def _normalize_text(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return " ".join(text.strip().split())

    def _combine_documents(self, request: AuthSubmitRequest) -> str:
        combined_parts = [request.clinical_justification]
        if request.clinical_documents:
            combined_parts.extend(request.clinical_documents)
        return "\n".join(part for part in combined_parts if part)

    def _document_has_keyword(self, text_lower: str, keywords: List[str]) -> Optional[str]:
        for kw in keywords:
            if kw in text_lower:
                return kw
        return None

    def _keyword_present(self, text_lower: str, keyword: str) -> bool:
        """Match keyword as whole term to avoid false positives (e.g., 'ct' in 'structured')."""
        pattern = rf"(?<!\\w){re.escape(keyword)}(?!\\w)"
        return re.search(pattern, text_lower) is not None

    def _build_reasoning_entry(self, step: str, action: str, detail: str) -> Dict[str, str]:
        return {"step": step, "action": action, "detail": detail}

    def _log_criteria_summary(self, results: List[CriterionMatchResult]) -> None:
        met = sum(1 for c in results if c.status == CriterionStatus.MET)
        not_met = sum(1 for c in results if c.status == CriterionStatus.NOT_MET)
        unclear = sum(1 for c in results if c.status == CriterionStatus.UNCLEAR)
        logger.info(
            "Criteria summary: total=%s met=%s not_met=%s unclear=%s",
            len(results),
            met,
            not_met,
            unclear,
        )

    def _confidence_from_counts(self, met_count: int, total_required: int, base: float, increment: float, cap: float) -> float:
        if total_required <= 0:
            return base
        score = base + increment * met_count / total_required
        return min(score, cap)

    def check_auth_required(self, request: AuthCheckRequest) -> AuthCheckResponse:
        payer_matrix = self.AUTH_REQUIREMENT_MATRIX.get(request.payer_id.upper()) if request.payer_id else None
        reason = "Auth requirement not found; defaulting to require." if payer_matrix is None else ""
        service_type = request.service_type
        if service_type is None:
            service_type = self._infer_service_type(request.service_code)
        reference = None
        auth_required = True

        if payer_matrix is None:
            auth_required = True
            reason = "Unknown payer; conservative auth required."
        else:
            category_key = service_type.value if isinstance(service_type, Enum) else str(service_type)
            category_rules = payer_matrix.get(category_key)
            if category_rules:
                for range_str in category_rules.get("codes", []):
                    if self._code_in_range(request.service_code, range_str):
                        auth_required = True
                        reference = category_rules.get("description")
                        reason = f"Payer rule: {category_rules.get('description', 'Auth required')}"
                        break
                else:
                    auth_required = False
                    reason = "Code not in auth-required ranges for payer"
            else:
                auth_required = True
                reason = "Service category not configured; require auth"

        required_docs = []
        estimated_turnaround = None
        if auth_required:
            if service_type == ServiceCategory.IMAGING:
                required_docs = ["Clinical notes", "Imaging indication", "Prior imaging results"]
                estimated_turnaround = "1-3 business days"
            elif service_type == ServiceCategory.PROCEDURE:
                required_docs = ["Clinical notes", "Conservative therapy details", "Imaging reports"]
                estimated_turnaround = "2-5 business days"
            elif service_type == ServiceCategory.MEDICATION:
                required_docs = ["Diagnosis confirmation", "Previous medications tried", "Lab results"]
                estimated_turnaround = "1-2 business days"
            elif service_type == ServiceCategory.THERAPY:
                required_docs = ["Therapy progress notes", "Plan of care", "Visit counts"]
                estimated_turnaround = "1-3 business days"
            else:
                required_docs = ["Clinical documentation"]
                estimated_turnaround = "2-4 business days"

        return AuthCheckResponse(
            service_code=request.service_code,
            payer_id=request.payer_id,
            auth_required=auth_required,
            reason=reason,
            policy_reference=reference,
            estimated_turnaround=estimated_turnaround,
            required_documentation=required_docs,
        )

    def process_auth_request(self, request: AuthSubmitRequest) -> AuthSubmitResponse:
        start_time = time.time()
        reasoning_chain: List[Dict[str, str]] = []

        combined_text = self._normalize_text(self._combine_documents(request))
        combined_lower = combined_text.lower()

        auth_check = self.check_auth_required(
            AuthCheckRequest(
                service_code=request.service_code,
                payer_id=request.payer_id,
                service_type=request.service_type,
                diagnosis_codes=request.diagnosis_codes,
            )
        )
        reasoning_chain.append(self._build_reasoning_entry("classification", "auth_requirement", auth_check.reason))

        if not auth_check.auth_required:
            response = AuthSubmitResponse(
                status=AuthDecisionStatus.NOT_REQUIRED,
                decision_date=datetime.utcnow().strftime("%Y-%m-%d"),
                effective_date=request.requested_start_date or datetime.utcnow().strftime("%Y-%m-%d"),
                expiration_date=None,
                approved_units=request.requested_units,
                approved_service_code=request.service_code,
                policy_reference=auth_check.policy_reference,
                criteria_match_report=[],
                confidence_score=0.97,
                reasoning_chain=reasoning_chain,
            )
            response.processing_time_ms = int((time.time() - start_time) * 1000)
            self._store_audit(response, request)
            return response

        if request.is_retrospective:
            retro_status, retro_reason = self._evaluate_retro(request)
            reasoning_chain.append(self._build_reasoning_entry("retro", retro_status, retro_reason))
            if retro_status == "denied":
                response = AuthSubmitResponse(
                    status=AuthDecisionStatus.DENIED,
                    policy_reference=None,
                    criteria_match_report=[],
                    denial_reasons=[retro_reason],
                    appeal_rights=self._generate_appeal_info(),
                    confidence_score=0.66,
                    reasoning_chain=reasoning_chain,
                )
                response.determination_letter = self._build_letter(response, request)
                response.processing_time_ms = int((time.time() - start_time) * 1000)
                self._store_audit(response, request)
                return response

        policy = self._find_policy(request.payer_id, request.service_code)
        if policy is None:
            reasoning_chain.append(self._build_reasoning_entry("policy_lookup", "no_policy", "Escalated: no matching policy"))
            response = AuthSubmitResponse(
                status=AuthDecisionStatus.ESCALATED,
                policy_reference=None,
                criteria_match_report=[],
                confidence_score=0.45,
                reasoning_chain=reasoning_chain,
            )
            response.determination_letter = self._build_letter(response, request)
            response.processing_time_ms = int((time.time() - start_time) * 1000)
            self._store_audit(response, request)
            return response

        metadata = self._policy_metadata(policy)
        reasoning_chain.append(
            self._build_reasoning_entry(
                "policy_lookup",
                "found_policy",
                f"{metadata.get('policy_id')} ({metadata.get('criteria_total')} criteria)",
            )
        )

        criteria_results: List[CriterionMatchResult] = []
        document_lower = combined_lower
        for criterion in policy.get("criteria", []):
            result = self._evaluate_criterion(criterion, request, document_lower)
            criteria_results.append(result)

        self._log_criteria_summary(criteria_results)

        required_results = [c for c in criteria_results if c.required]
        met_count = sum(1 for c in required_results if c.status == CriterionStatus.MET)
        not_met = [c for c in required_results if c.status == CriterionStatus.NOT_MET]
        unclear = [c for c in required_results if c.status == CriterionStatus.UNCLEAR]

        status = AuthDecisionStatus.PENDING_INFO
        missing_information: List[str] = []
        denial_reasons: List[str] = []
        alternative_treatments: List[str] = []
        appeal_rights: Optional[AppealInfo] = None
        effective_date = None
        expiration_date = None
        approved_units = None
        approved_service_code = None
        confidence = 0.0

        total_required = max(len(required_results), 1)

        if len(not_met) == 0 and len(unclear) == 0:
            status = AuthDecisionStatus.APPROVED
            effective_date = request.requested_start_date or datetime.utcnow().strftime("%Y-%m-%d")
            exp_dt = self._calculate_expiration(effective_date)
            expiration_date = exp_dt.strftime("%Y-%m-%d") if exp_dt else None
            approved_units = min(request.requested_units, 10)
            approved_service_code = request.service_code
            confidence = min(0.92 + 0.01 * met_count, 0.98)
            reasoning_chain.append(self._build_reasoning_entry("determination", "approved", "All required criteria met"))
        elif len(not_met) > 0:
            status = AuthDecisionStatus.DENIED
            denial_reasons = [c.description for c in not_met]
            alternative_treatments = self._generate_alternatives(request.service_type.value, request.service_code)
            appeal_rights = self._generate_appeal_info(denial_reasons)
            confidence = self._confidence_from_counts(met_count, total_required, 0.85, 0.02, 0.95)
            reasoning_chain.append(self._build_reasoning_entry("determination", "denied", "; ".join(denial_reasons)))
        else:
            status = AuthDecisionStatus.PENDING_INFO
            missing_information = [c.description for c in unclear]
            confidence = 0.5 + 0.05 * met_count / total_required
            reasoning_chain.append(self._build_reasoning_entry("determination", "pending_info", "; ".join(missing_information)))

        if status == AuthDecisionStatus.PENDING_INFO and request.urgency == UrgencyLevel.URGENT and met_count > total_required / 2:
            status = AuthDecisionStatus.APPROVED
            confidence = max(confidence, 0.7)
            effective_date = request.requested_start_date or datetime.utcnow().strftime("%Y-%m-%d")
            exp_dt = self._calculate_expiration(effective_date)
            expiration_date = exp_dt.strftime("%Y-%m-%d") if exp_dt else None
            approved_units = min(request.requested_units, 6)
            approved_service_code = request.service_code
            reasoning_chain.append(self._build_reasoning_entry("urgency_override", "upgrade", "Urgent request with majority criteria met"))

        letter = None
        if status in {AuthDecisionStatus.APPROVED, AuthDecisionStatus.DENIED, AuthDecisionStatus.PENDING_INFO}:
            letter = self._build_letter_from_status(
                status=status,
                auth_request_id="",
                service_code=request.service_code,
                service_description=request.service_description,
                effective_date=effective_date,
                expiration_date=expiration_date,
                approved_units=approved_units,
                policy_reference=policy.get("policy_id"),
                denial_reasons=denial_reasons,
                missing_information=missing_information,
                appeal_rights=appeal_rights,
            )

        response = AuthSubmitResponse(
            status=status,
            decision_date=datetime.utcnow().strftime("%Y-%m-%d"),
            effective_date=effective_date,
            expiration_date=expiration_date,
            approved_units=approved_units,
            approved_service_code=approved_service_code,
            policy_reference=policy.get("policy_id"),
            criteria_match_report=criteria_results,
            missing_information=missing_information,
            denial_reasons=denial_reasons,
            appeal_rights=appeal_rights,
            alternative_treatments=alternative_treatments,
            peer_review_required=status == AuthDecisionStatus.DENIED,
            determination_letter=letter,
            confidence_score=round(confidence, 3),
            reasoning_chain=reasoning_chain,
        )

        turnaround_window = self._compute_turnaround_window(policy, request.urgency)
        response.reasoning_chain.append(
            self._build_reasoning_entry(
                "turnaround",
                "expected_window",
                f"Estimated decision window: {turnaround_window}",
            )
        )

        self._check_turnaround(response, policy, request.urgency, start_time)
        response.processing_time_ms = int((time.time() - start_time) * 1000)
        self._store_audit(response, request)
        return response

    def _calculate_expiration(self, effective_date: str) -> Optional[datetime]:
        try:
            eff = datetime.strptime(effective_date, "%Y-%m-%d")
            return eff + timedelta(days=90)
        except Exception:
            return None

    def _evaluate_retro(self, request: AuthSubmitRequest) -> (str, str):
        rules = self.RETRO_AUTH_RULES.get(request.payer_id.upper(), {})
        if not rules:
            return "denied", "Payer does not support retrospective auth"
        if not rules.get("allowed", False):
            return "denied", "Retrospective auth not allowed"

        if not request.date_of_service:
            return "denied", "Date of service required for retrospective review"

        try:
            dos = datetime.strptime(request.date_of_service, "%Y-%m-%d")
        except Exception:
            return "denied", "Invalid date_of_service format"

        now = datetime.utcnow()
        hours_since = (now - dos).total_seconds() / 3600.0
        if hours_since <= rules.get("emergency_window_hours", 0):
            return "allowed", "Within emergency window"

        if rules.get("non_emergency", False):
            days_since = (now - dos).days
            if days_since <= rules.get("non_emergency_window_days", 0):
                return "allowed", "Within non-emergency window"
            return "denied", "Outside non-emergency retrospective window"

        return "denied", "Outside emergency retrospective window"

    def _evaluate_criterion(self, criterion: Dict[str, Any], request: AuthSubmitRequest, document_lower: str) -> CriterionMatchResult:
        category = criterion.get("category", "")
        status = CriterionStatus.UNCLEAR
        evidence = None
        notes = None

        if category == "required_diagnosis":
            status, evidence, notes = self._eval_required_diagnosis(request, document_lower)
        elif category == "step_therapy":
            status, evidence, notes = self._eval_step_therapy(document_lower)
        elif category == "documentation":
            status, evidence, notes = self._eval_documentation(document_lower)
        elif category == "lab_value":
            status, evidence, notes = self._eval_lab_value(document_lower)
        elif category == "contraindication":
            status, evidence, notes = self._eval_contraindication(document_lower)
        elif category == "medical_necessity":
            status, evidence, notes = self._eval_medical_necessity(request)
        else:
            status = CriterionStatus.NOT_APPLICABLE

        return CriterionMatchResult(
            criterion_id=criterion.get("criterion_id", ""),
            description=criterion.get("description", ""),
            category=category,
            required=bool(criterion.get("required", False)),
            status=status,
            evidence_found=evidence,
            notes=notes,
        )

    def _eval_required_diagnosis(self, request: AuthSubmitRequest, document_lower: str):
        codes = [code.lower() for code in request.diagnosis_codes]
        keywords = ["arthritis", "psoriasis", "crohn", "rheumatoid", "ankylosing", "spondylitis"]
        if any(k in document_lower for k in keywords):
            return CriterionStatus.MET, "diagnosis referenced in note", None
        if any(code.startswith("K50") or code.startswith("L40") or code.startswith("M05") for code in request.diagnosis_codes):
            return CriterionStatus.MET, "diagnosis code supports condition", None
        return CriterionStatus.NOT_MET, None, "Required diagnosis not demonstrated"

    def _eval_step_therapy(self, document_lower: str):
        has_ambiguous = any(phrase in document_lower for phrase in self.AMBIGUOUS_PHRASES)
        for kw in self.STEP_THERAPY_KEYWORDS:
            if self._keyword_present(document_lower, kw):
                snippet = self._extract_snippet(document_lower, kw)
                if has_ambiguous:
                    return CriterionStatus.UNCLEAR, snippet, "Prior therapy referenced but not verifiable"
                return CriterionStatus.MET, snippet, None
        if has_ambiguous:
            return CriterionStatus.UNCLEAR, None, "Prior therapy referenced but not verifiable"
        if len(document_lower) < 50:
            return CriterionStatus.UNCLEAR, None, "Insufficient detail for step therapy"
        return CriterionStatus.NOT_MET, None, "No evidence of prior therapy"

    def _eval_documentation(self, document_lower: str):
        has_ambiguous = any(phrase in document_lower for phrase in self.AMBIGUOUS_PHRASES)
        for kw in self.DOCUMENT_KEYWORDS:
            if self._keyword_present(document_lower, kw):
                snippet = self._extract_snippet(document_lower, kw)
                if has_ambiguous:
                    return CriterionStatus.UNCLEAR, snippet, "Documentation noted but lacks detail"
                return CriterionStatus.MET, snippet, None
        if has_ambiguous:
            return CriterionStatus.UNCLEAR, None, "Documentation referenced but insufficient detail"
        if len(document_lower) > 120:
            return CriterionStatus.UNCLEAR, None, "Documentation implied but not explicit"
        return CriterionStatus.NOT_MET, None, "No documentation evidence found"

    def _eval_lab_value(self, document_lower: str):
        for pattern in self.LAB_PATTERNS:
            match = re.search(pattern, document_lower, flags=re.IGNORECASE)
            if match:
                snippet = match.group(0)
                return CriterionStatus.MET, snippet, None
        return CriterionStatus.NOT_MET, None, "Lab values not found"

    def _eval_contraindication(self, document_lower: str):
        negative_phrases = ["no infection", "no active infection", "no contraindication", "cleared"]
        for phrase in negative_phrases:
            if phrase in document_lower:
                return CriterionStatus.MET, phrase, None
        if any(kw in document_lower for kw in self.CONTRAINDICATION_KEYWORDS):
            return CriterionStatus.NOT_MET, "contraindication present", "Infection, allergy, or contraindication noted"
        return CriterionStatus.MET, None, "No contraindication noted"

    def _eval_medical_necessity(self, request: AuthSubmitRequest):
        if request.diagnosis_codes:
            return CriterionStatus.MET, "Diagnosis codes provided", None
        return CriterionStatus.UNCLEAR, None, "Medical necessity not fully established"

    def _extract_snippet(self, document_lower: str, keyword: str, window: int = 40) -> str:
        idx = document_lower.find(keyword)
        if idx == -1:
            return keyword
        start = max(0, idx - window)
        end = min(len(document_lower), idx + len(keyword) + window)
        return document_lower[start:end].strip()

    def _infer_service_type(self, code: str) -> ServiceCategory:
        if self._code_in_range(code, "70000-79999"):
            return ServiceCategory.IMAGING
        if self._code_in_range(code, "27000-27899") or self._code_in_range(code, "29800-29999"):
            return ServiceCategory.PROCEDURE
        if self._code_in_range(code, "E0100-E9999"):
            return ServiceCategory.DME
        if self._code_in_range(code, "J0000-J9999"):
            return ServiceCategory.MEDICATION
        if self._code_in_range(code, "97110-97542"):
            return ServiceCategory.THERAPY
        if self._code_in_range(code, "99221-99223"):
            return ServiceCategory.INPATIENT_ADMISSION
        if self._code_in_range(code, "99211-99215"):
            return ServiceCategory.OTHER
        return ServiceCategory.OTHER

    def _code_in_range(self, code: str, range_str: str) -> bool:
        code = code.strip().upper()
        if "-" not in range_str:
            return code == range_str.upper()
        start, end = range_str.split("-")
        if code[0].isalpha():
            prefix = code[0]
            try:
                code_num = int(code[1:])
                start_num = int(start[1:])
                end_num = int(end[1:])
                return prefix == start[0] and start_num <= code_num <= end_num
            except Exception:
                return False
        try:
            code_num = int(code)
            start_num = int(start)
            end_num = int(end)
            return start_num <= code_num <= end_num
        except Exception:
            return False

    def _find_policy(self, payer_id: str, service_code: str) -> Optional[Dict[str, Any]]:
        for policy in self.MEDICAL_POLICIES:
            if policy.get("payer_id", "").upper() != payer_id.upper():
                continue
            if service_code in policy.get("service_codes", []):
                return policy
        return None

    def _policy_metadata(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "policy_id": policy.get("policy_id"),
            "policy_name": policy.get("policy_name"),
            "payer_id": policy.get("payer_id"),
            "criteria_total": len(policy.get("criteria", [])),
        }

    def _compute_turnaround_window(self, policy: Dict[str, Any], urgency: UrgencyLevel) -> str:
        standard = policy.get("turnaround_standard")
        urgent = policy.get("turnaround_urgent")
        if urgency == UrgencyLevel.URGENT and urgent:
            return f"{urgent} day(s)"
        if standard:
            return f"{standard} day(s)"
        return "unspecified"

    def _generate_alternatives(self, service_type: str, service_code: str) -> List[str]:
        if service_type == ServiceCategory.IMAGING.value:
            return ["Consider X-ray if not already performed", "Ultrasound may be appropriate"]
        if service_type == ServiceCategory.PROCEDURE.value:
            return ["Consider conservative management", "Physical therapy trial"]
        if service_type == ServiceCategory.MEDICATION.value:
            return ["Consider formulary alternative", "Step therapy with first-line agent"]
        if service_type == ServiceCategory.THERAPY.value:
            return ["Home exercise program", "Group therapy sessions"]
        return ["Consult payer guidelines for alternatives"]

    def _generate_appeal_info(self, denial_reasons: Optional[List[str]] = None) -> AppealInfo:
        tips = ["Submit clinical notes", "Include imaging and lab reports", "Provide prior treatment history"]
        if denial_reasons:
            tips.append("Address each denial reason with evidence")
        return AppealInfo(required_documents=["Clinical notes", "Supporting imaging", "Lab results"], tips=tips)

    def _build_letter_from_status(
        self,
        status: AuthDecisionStatus,
        auth_request_id: str,
        service_code: str,
        service_description: Optional[str],
        effective_date: Optional[str],
        expiration_date: Optional[str],
        approved_units: Optional[int],
        policy_reference: Optional[str],
        denial_reasons: List[str],
        missing_information: List[str],
        appeal_rights: Optional[AppealInfo],
    ) -> str:
        desc = service_description or ""
        if status == AuthDecisionStatus.APPROVED:
            lines = [
                "AUTHORIZATION APPROVED",
                f"Auth Number: {auth_request_id or '[pending id]'}",
                f"Service: {service_code} - {desc}",
                f"Effective: {effective_date} to {expiration_date}",
                f"Approved Units: {approved_units}",
                f"Policy: {policy_reference}",
                "This authorization is valid for the specified dates and service.",
            ]
            return "\n".join(lines)

        if status == AuthDecisionStatus.DENIED:
            denial_lines = [f"{idx + 1}. {reason}" for idx, reason in enumerate(denial_reasons)] or ["No reasons listed"]
            lines = [
                "AUTHORIZATION DENIED",
                f"Service: {service_code} - {desc}",
                "Denial Reasons:",
                *denial_lines,
                f"Policy Reference: {policy_reference}",
                "",
                "APPEAL RIGHTS:",
                f"You have the right to appeal this decision within {appeal_rights.appeal_deadline_days if appeal_rights else 180} days.",
                "Contact: [Payer appeals department]",
                "Peer-to-peer review available upon request.",
            ]
            return "\n".join(lines)

        if status == AuthDecisionStatus.PENDING_INFO:
            missing_lines = [f"{idx + 1}. {item}" for idx, item in enumerate(missing_information)] or ["No items listed"]
            lines = [
                "ADDITIONAL INFORMATION REQUIRED",
                f"Service: {service_code} - {desc}",
                "The following information is needed to complete review:",
                *missing_lines,
                "Please submit within 14 days to avoid denial.",
                "Fax to: [Payer fax number]",
            ]
            return "\n".join(lines)
        return ""

    def _build_letter(self, response: AuthSubmitResponse, request: AuthSubmitRequest) -> str:
        return self._build_letter_from_status(
            status=response.status,
            auth_request_id=response.auth_request_id,
            service_code=request.service_code,
            service_description=request.service_description,
            effective_date=response.effective_date,
            expiration_date=response.expiration_date,
            approved_units=response.approved_units,
            policy_reference=response.policy_reference,
            denial_reasons=response.denial_reasons,
            missing_information=response.missing_information,
            appeal_rights=response.appeal_rights,
        )

    def _check_turnaround(self, response: AuthSubmitResponse, policy: Dict[str, Any], urgency: UrgencyLevel, start_time: float) -> None:
        elapsed_ms = int((time.time() - start_time) * 1000)
        expected_days = policy.get("turnaround_urgent") if urgency == UrgencyLevel.URGENT else policy.get("turnaround_standard")
        if expected_days:
            expected_ms = expected_days * 24 * 60 * 60 * 1000
            if elapsed_ms > expected_ms:
                logger.warning(
                    "Processing exceeded expected turnaround: policy=%s status=%s elapsed_ms=%s expected_days=%s",
                    policy.get("policy_id"),
                    response.status.value,
                    elapsed_ms,
                    expected_days,
                )

    def _store_audit(self, response: AuthSubmitResponse, request: AuthSubmitRequest) -> None:
        audit_record = {
            "audit_id": response.audit_id,
            "auth_request_id": response.auth_request_id,
            "request": request.model_dump(),
            "response": response.model_dump(),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        self._audit_store[response.audit_id] = audit_record


_processor = PriorAuthProcessor()


@router.post(
    "/submit",
    response_model=AuthSubmitResponse,
    summary="Submit prior authorization request",
    description="Submits a prior authorization request for clinical review. Returns approval, denial, or request for additional information with cited policy criteria.",
)
async def submit_auth_request(request: AuthSubmitRequest):
    start_time = time.time()
    try:
        result = _processor.process_auth_request(request)
        result.processing_time_ms = int((time.time() - start_time) * 1000)
        logger.info(
            "Prior auth processed: %s - status=%s confidence=%.2f service=%s payer=%s",
            result.auth_request_id,
            result.status,
            result.confidence_score,
            request.service_code,
            request.payer_id,
        )
        return result
    except Exception as e:
        logger.error("Prior auth error: %s\n%s", e, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Processing error: {str(e)}")


@router.post(
    "/check-required",
    response_model=AuthCheckResponse,
    summary="Check if prior authorization is required",
    description="Checks whether prior authorization is required for a specific service code and payer combination. Returns requirement status with policy reference.",
)
async def check_auth_required(request: AuthCheckRequest):
    try:
        result = _processor.check_auth_required(request)
        logger.info("Auth check: service=%s payer=%s required=%s", request.service_code, request.payer_id, result.auth_required)
        return result
    except Exception as e:
        logger.error("Auth check error: %s", e)
        raise HTTPException(status_code=500, detail=f"Check error: {str(e)}")
