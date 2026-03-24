"""
Prior Authorization Agent for MEDI-COMPLY.

Implements a rule-driven prior authorization workflow that mirrors existing
agent patterns in the system. The pipeline is intentionally deterministic and
mock-friendly for hackathon use while preserving realistic structure:

1) Request classification and authorization requirement check
2) Retrospective eligibility handling
3) Policy lookup and clinical criteria matching
4) Determination and confidence scoring
5) Letter and appeal guidance generation
6) Compliance and audit logging

This module does not require LLM access; all logic is rules-based.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
import hashlib
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from medi_comply.core.agent_base import BaseAgent
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.schemas.common import AgentType, ResponseStatus

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ServiceType(str, Enum):
    """Supported service categories for authorization decisions."""

    PROCEDURE = "PROCEDURE"
    MEDICATION = "MEDICATION"
    DME = "DME"
    IMAGING = "IMAGING"
    LAB = "LAB"
    THERAPY = "THERAPY"
    INPATIENT_ADMISSION = "INPATIENT_ADMISSION"
    OTHER = "OTHER"


class AuthorizationStatus(str, Enum):
    """Final disposition of a prior authorization request."""

    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PENDING_INFO = "PENDING_INFO"
    ESCALATED = "ESCALATED"
    NOT_REQUIRED = "NOT_REQUIRED"


class CriterionMatchStatus(str, Enum):
    """Criterion matching outcomes."""

    MET = "MET"
    NOT_MET = "NOT_MET"
    UNCLEAR = "UNCLEAR"
    NOT_APPLICABLE = "NOT_APPLICABLE"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


def _today_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d")


def _uuid_str() -> str:
    return str(uuid.uuid4())


class PriorAuthRequest(BaseModel):
    """Incoming prior authorization request payload."""

    request_id: str = Field(default_factory=_uuid_str)
    member_id: str
    provider_id: str
    payer_id: str
    service_type: ServiceType
    service_code: str
    service_description: str
    diagnosis_codes: List[str]
    clinical_justification: str
    clinical_documents: Optional[List[str]] = None
    requested_units: int = 1
    requested_start_date: Optional[str] = None
    requested_end_date: Optional[str] = None
    is_urgent: bool = False
    is_retrospective: bool = False
    date_of_service: Optional[str] = None
    submission_date: str = Field(default_factory=_today_str)


class ClinicalCriterion(BaseModel):
    """Policy criterion that must be evaluated for an auth request."""

    criterion_id: str = Field(default_factory=lambda: f"C-{uuid.uuid4().hex[:6]}")
    description: str = ""
    category: str = "general"
    required: bool = True
    match_status: CriterionMatchStatus = CriterionMatchStatus.NOT_APPLICABLE
    evidence_text: Optional[str] = None
    evidence_source: Optional[str] = None
    notes: Optional[str] = None

    def __init__(
        self,
        criterion_id: str,
        description: str,
        category: str,
        required: bool = True,
        match_status: CriterionMatchStatus = CriterionMatchStatus.NOT_APPLICABLE,
        **data: Any,
    ) -> None:
        # Allow positional construction expected by tests while keeping BaseModel validation.
        super().__init__(
            criterion_id=criterion_id,
            description=description,
            category=category,
            required=required,
            match_status=match_status,
            **data,
        )


class MedicalPolicy(BaseModel):
    """Policy container with criteria and turnaround settings."""

    policy_id: str
    policy_name: str
    payer_id: str
    service_codes: List[str]
    effective_date: str
    expiration_date: Optional[str] = None
    criteria: List[ClinicalCriterion]
    requires_peer_review: bool = False
    turnaround_days_standard: int = 14
    turnaround_days_urgent: int = 1


class AuthorizationDecision(BaseModel):
    """Final decision payload shared with downstream systems."""

    auth_id: str = Field(default_factory=_uuid_str)
    request_id: str = Field(default_factory=_uuid_str)
    status: AuthorizationStatus = AuthorizationStatus.PENDING_INFO
    decision_date: str = Field(default_factory=_today_str)
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    approved_units: Optional[int] = None
    approved_service_code: Optional[str] = None
    policy_reference: Optional[str] = None
    criteria_match_report: List[ClinicalCriterion] = Field(default_factory=list)
    missing_information: List[str] = Field(default_factory=list)
    denial_reasons: List[str] = Field(default_factory=list)
    appeal_rights: Optional[Dict[str, Any]] = None
    alternative_treatments: List[str] = Field(default_factory=list)
    peer_review_required: bool = False
    confidence_score: float = 0.0
    reasoning_chain: List[Dict[str, str]] = Field(default_factory=list)
    processing_time_ms: Optional[int] = None
    audit_trail_id: Optional[str] = None


class AuthAppealGuidance(BaseModel):
    """Guidance block returned with denied or pended decisions."""

    can_appeal: bool = True
    appeal_deadline_days: int = 180
    appeal_levels: List[str] = Field(
        default_factory=lambda: [
            "Internal Review",
            "External Review",
            "State Insurance Commissioner",
        ]
    )
    peer_to_peer_available: bool = True
    required_documents: List[str] = Field(default_factory=list)
    tips: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper classes
# ---------------------------------------------------------------------------


class AuthRequirementChecker:
    """Determines whether prior authorization is required for a service."""

    AUTH_REQUIREMENTS_MATRIX: Dict[str, Dict[ServiceType, List[Dict[str, Any]]]] = {
        "MEDICARE": {
            ServiceType.IMAGING: [
                {"range": (70000, 79999), "reason": "Advanced imaging requires auth"},
            ],
            ServiceType.PROCEDURE: [
                {"range": (27000, 27899), "reason": "Selected orthopedic surgeries require auth"},
            ],
            ServiceType.DME: [
                {"prefix": "E0", "reason": "DME supplies require auth"},
            ],
        },
        "BCBS": {
            ServiceType.IMAGING: [
                {"range": (70000, 79999), "reason": "High-end imaging requires auth"},
            ],
            ServiceType.MEDICATION: [
                {"prefix": "J9", "reason": "Specialty medications require auth"},
            ],
            ServiceType.PROCEDURE: [
                {"range": (27000, 27899), "reason": "Joint surgeries require auth"},
            ],
        },
        "AETNA": {
            ServiceType.IMAGING: [
                {"range": (70000, 79999), "reason": "CT/MRI authorization"},
            ],
            ServiceType.MEDICATION: [
                {"prefix": "Q5", "reason": "Infusion meds require auth"},
            ],
            ServiceType.DME: [
                {"prefix": "E1", "reason": "High-cost DME requires auth"},
            ],
            ServiceType.THERAPY: [
                {"range": (97000, 97999), "reason": "Therapy visits beyond threshold require auth"},
            ],
        },
    }

    def check_auth_required(self, service_code: str, payer_id: str, service_type: ServiceType) -> Dict[str, Any]:
        """Return auth requirement determination for a given code."""

        matrix = self.AUTH_REQUIREMENTS_MATRIX.get(payer_id, {})
        entries = matrix.get(service_type, [])
        normalized_code = service_code.replace(".", "")
        result = {
            "auth_required": False,
            "policy_ref": None,
            "reason": "No matching rule",
        }
        try:
            code_int = int(re.sub(r"[^0-9]", "", normalized_code) or 0)
        except ValueError:
            code_int = 0
        for entry in entries:
            if "range" in entry:
                start, end = entry["range"]
                if start <= code_int <= end:
                    result.update(
                        {
                            "auth_required": True,
                            "policy_ref": f"RANGE_{start}_{end}",
                            "reason": entry.get("reason", "Range based requirement"),
                        }
                    )
                    break
            if "prefix" in entry:
                if normalized_code.startswith(entry["prefix"]):
                    result.update(
                        {
                            "auth_required": True,
                            "policy_ref": f"PREFIX_{entry['prefix']}",
                            "reason": entry.get("reason", "Prefix requirement"),
                        }
                    )
                    break
        logger.debug("Auth requirement check", extra={"code": service_code, "result": result})
        return result


class RetroAuthHandler:
    """Handles retrospective authorization logic."""

    def evaluate_retro_request(self, request: PriorAuthRequest) -> Dict[str, Any]:
        """Return whether a retrospective request is allowed."""

        if not request.date_of_service:
            return {
                "allowed": False,
                "reason": "Missing date of service for retro check",
                "deadline_met": False,
            }
        try:
            dos = datetime.strptime(request.date_of_service, "%Y-%m-%d")
            submission = datetime.strptime(request.submission_date, "%Y-%m-%d")
        except ValueError:
            return {
                "allowed": False,
                "reason": "Invalid date format",
                "deadline_met": False,
            }
        delta = submission - dos
        emergency_window = timedelta(hours=72)
        if request.is_urgent and delta <= emergency_window:
            return {
                "allowed": True,
                "reason": "Emergency service within 72-hour window",
                "deadline_met": True,
            }
        if delta <= timedelta(days=3):
            return {
                "allowed": True,
                "reason": "Within 3-day retro window",
                "deadline_met": True,
            }
        return {
            "allowed": False,
            "reason": "Retro authorization window exceeded",
            "deadline_met": False,
        }


class ClinicalCriteriaMatcher:
    """Matches clinical information to policy criteria using rule-based checks."""

    STEP_KEYWORDS = ["failed", "trial", "tried", "intolerant", "refractory", "no improvement"]
    LAB_PATTERN = re.compile(r"(?P<name>[A-Za-z]+)[:\s]*(?P<value>\d+(?:\.\d+)?)")

    def match_criteria(self, request: PriorAuthRequest, policy: MedicalPolicy) -> List[ClinicalCriterion]:
        """Evaluate each policy criterion against the request details."""

        clinical_text = request.clinical_justification.lower()
        diag_set = {d.lower() for d in request.diagnosis_codes}
        matched: List[ClinicalCriterion] = []
        for criterion in policy.criteria:
            criterion_copy = criterion.model_copy(deep=True)
            status = CriterionMatchStatus.NOT_APPLICABLE
            evidence_text: Optional[str] = None
            notes: Optional[str] = None

            try:
                if criterion.category == "required_diagnosis":
                    required_codes = re.findall(r"[A-Z][0-9][0-9A-Z\.]+", criterion.description)
                    if any(code.lower() in diag_set for code in required_codes):
                        status = CriterionMatchStatus.MET
                        evidence_text = f"Diagnosis present: {required_codes}"
                    else:
                        status = CriterionMatchStatus.NOT_MET if criterion.required else CriterionMatchStatus.NOT_APPLICABLE
                        notes = "Required diagnosis not found"

                elif criterion.category == "step_therapy":
                    if any(keyword in clinical_text for keyword in self.STEP_KEYWORDS):
                        status = CriterionMatchStatus.MET
                        evidence_text = "Step therapy failure documented"
                    else:
                        status = CriterionMatchStatus.UNCLEAR if criterion.required else CriterionMatchStatus.NOT_APPLICABLE
                        notes = "Step therapy mention not clear"

                elif criterion.category == "lab_value":
                    lab_matches = list(self.LAB_PATTERN.finditer(clinical_text))
                    if lab_matches:
                        status = CriterionMatchStatus.MET
                        evidence_text = lab_matches[0].group(0)
                    else:
                        status = CriterionMatchStatus.UNCLEAR if criterion.required else CriterionMatchStatus.NOT_APPLICABLE
                        notes = "No lab values found"

                elif criterion.category == "documentation":
                    if any(token in clinical_text for token in ["note", "report", "imaging", "x-ray", "mri"]):
                        status = CriterionMatchStatus.MET
                        evidence_text = "Documentation reference found"
                    else:
                        status = CriterionMatchStatus.UNCLEAR if criterion.required else CriterionMatchStatus.NOT_APPLICABLE
                        notes = "Documentation not referenced"

                elif criterion.category == "contraindication":
                    if "contraindication" in clinical_text or "allergy" in clinical_text:
                        status = CriterionMatchStatus.MET
                        evidence_text = "Contraindication noted"
                    else:
                        status = CriterionMatchStatus.NOT_MET if criterion.required else CriterionMatchStatus.NOT_APPLICABLE
                        notes = "No contraindication documented"

                else:
                    status = CriterionMatchStatus.NOT_APPLICABLE
                    notes = "Unhandled criterion category"

            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Error matching criterion", extra={"criterion": criterion.criterion_id})
                status = CriterionMatchStatus.UNCLEAR
                notes = f"Matcher error: {exc}"

            criterion_copy.match_status = status
            criterion_copy.evidence_text = evidence_text
            criterion_copy.notes = notes
            matched.append(criterion_copy)
        return matched


class DeterminationEngine:
    """Determines final auth decision based on criterion outcomes."""

    def make_determination(
        self,
        request: PriorAuthRequest,
        criteria_results: List[ClinicalCriterion],
        policy: MedicalPolicy,
    ) -> AuthorizationDecision:
        """Produce an AuthorizationDecision from criterion results."""

        required_results = [c for c in criteria_results if c.required]
        met_required = all(c.match_status == CriterionMatchStatus.MET for c in required_results) if required_results else True
        any_unclear = any(c.match_status == CriterionMatchStatus.UNCLEAR for c in required_results)
        any_not_met = any(c.match_status == CriterionMatchStatus.NOT_MET for c in required_results)

        reasoning_chain: List[Dict[str, str]] = []
        status: AuthorizationStatus
        confidence: float
        denial_reasons: List[str] = []
        missing_information: List[str] = []

        if met_required and not any_unclear and not any_not_met:
            status = AuthorizationStatus.APPROVED
            confidence = 0.95 if not request.is_urgent else 0.92
            reasoning_chain.append({"step": "criteria", "detail": "All required criteria met"})
        elif any_unclear:
            status = AuthorizationStatus.PENDING_INFO
            confidence = 0.6
            missing_information.extend([c.description for c in required_results if c.match_status == CriterionMatchStatus.UNCLEAR])
            reasoning_chain.append({"step": "criteria", "detail": "Some required criteria unclear"})
        elif any_not_met:
            status = AuthorizationStatus.DENIED
            confidence = 0.9
            denial_reasons.extend([c.description for c in required_results if c.match_status == CriterionMatchStatus.NOT_MET])
            reasoning_chain.append({"step": "criteria", "detail": "One or more required criteria not met"})
        else:
            status = AuthorizationStatus.PENDING_INFO
            confidence = 0.55
            reasoning_chain.append({"step": "criteria", "detail": "Default pending due to indeterminate state"})

        effective_date = request.requested_start_date or _today_str()
        expiration_date_dt = datetime.strptime(effective_date, "%Y-%m-%d") + timedelta(days=90)
        expiration_date = expiration_date_dt.strftime("%Y-%m-%d")

        approved_units = request.requested_units if status == AuthorizationStatus.APPROVED else None
        approved_service_code = request.service_code if status == AuthorizationStatus.APPROVED else None

        appeal_rights = {
            "can_appeal": status in {AuthorizationStatus.DENIED, AuthorizationStatus.PENDING_INFO},
            "deadline_days": 180,
            "levels": ["Internal Review", "External Review"],
        }

        decision = AuthorizationDecision(
            request_id=request.request_id,
            status=status,
            decision_date=_today_str(),
            effective_date=effective_date,
            expiration_date=expiration_date,
            approved_units=approved_units,
            approved_service_code=approved_service_code,
            policy_reference=f"{policy.policy_id} - {policy.policy_name}" if status == AuthorizationStatus.DENIED else policy.policy_id,
            criteria_match_report=criteria_results,
            missing_information=missing_information,
            denial_reasons=denial_reasons,
            appeal_rights=appeal_rights,
            alternative_treatments=self._alternative_treatments(request),
            peer_review_required=policy.requires_peer_review,
            confidence_score=confidence,
            reasoning_chain=reasoning_chain,
        )
        return decision

    def _alternative_treatments(self, request: PriorAuthRequest) -> List[str]:
        """Suggest conservative alternatives if appropriate."""

        suggestions: List[str] = []
        if request.service_type == ServiceType.IMAGING:
            suggestions.append("Continue conservative therapy for 4-6 weeks")
            suggestions.append("Order X-ray prior to MRI if not done")
        if request.service_type == ServiceType.MEDICATION:
            suggestions.append("Trial preferred formulary agent")
        if request.service_type == ServiceType.PROCEDURE:
            suggestions.append("Consider PT and injections before surgery")
        return suggestions


class LetterGenerator:
    """Generates member/provider facing letters for determinations."""

    def generate_approval_letter(self, decision: AuthorizationDecision, request: PriorAuthRequest) -> str:
        """Return an approval letter body."""

        return (
            f"Authorization APPROVED for {request.service_description} (code {request.service_code})\n"
            f"Member: {request.member_id} | Provider: {request.provider_id}\n"
            f"Effective: {decision.effective_date} through {decision.expiration_date}\n"
            f"Approved units: {decision.approved_units}\n"
            f"Policy: {decision.policy_reference}\n"
        )

    def generate_denial_letter(self, decision: AuthorizationDecision, request: PriorAuthRequest) -> str:
        """Return a denial letter body."""

        reasons = "; ".join(decision.denial_reasons or ["See policy criteria"])
        return (
            f"Authorization DENIED for {request.service_description} (code {request.service_code})\n"
            f"Member: {request.member_id} | Provider: {request.provider_id}\n"
            f"Reasons: {reasons}\n"
            f"Policy Reference: {decision.policy_reference or 'N/A'}\n"
            f"Appeal rights: {decision.appeal_rights}\n"
            f"Alternative treatments: {', '.join(decision.alternative_treatments)}\n"
        )

    def generate_info_request_letter(self, decision: AuthorizationDecision, request: PriorAuthRequest) -> str:
        """Return an additional information request letter body."""

        needed = ", ".join(decision.missing_information or ["Additional documentation"])
        return (
            f"Authorization PENDING for {request.service_description} (code {request.service_code})\n"
            f"Member: {request.member_id} | Provider: {request.provider_id}\n"
            f"Needed information: {needed}\n"
            f"Policy: {decision.policy_reference}\n"
        )


class AppealGuidanceGenerator:
    """Builds appeal guidance payloads for denied or pended cases."""

    def generate_guidance(self, decision: AuthorizationDecision, request: PriorAuthRequest) -> AuthAppealGuidance:
        """Return appeal guidance based on decision status."""

        guidance = AuthAppealGuidance()
        guidance.required_documents = [
            "Clinical notes",
            "Imaging reports",
            "Lab results",
            "Prior treatment history",
        ]
        guidance.tips = [
            "Include objective findings (imaging, labs)",
            "Document duration and failure of conservative therapy",
            "Clarify contraindications and comorbidities",
        ]
        if decision.status == AuthorizationStatus.PENDING_INFO:
            guidance.tips.append("Respond with requested documents within 10 days")
        if decision.status == AuthorizationStatus.APPROVED:
            guidance.can_appeal = False
        return guidance


# ---------------------------------------------------------------------------
# Prior Authorization Agent
# ---------------------------------------------------------------------------


class PriorAuthAgent(BaseAgent):
    """Prior authorization agent implementing MEDI-COMPLY workflow."""

    def __init__(self) -> None:
        super().__init__(agent_name="PriorAuthAgent", agent_type=AgentType.PROCESSOR)
        self.auth_checker = AuthRequirementChecker()
        self.retro_handler = RetroAuthHandler()
        self.matcher = ClinicalCriteriaMatcher()
        self.determination_engine = DeterminationEngine()
        self.letter_generator = LetterGenerator()
        self.appeal_guidance_generator = AppealGuidanceGenerator()
        self.POLICY_DATABASE: Dict[str, MedicalPolicy] = self._seed_policies()
        logger.info("PriorAuthAgent initialized with %d policies", len(self.POLICY_DATABASE))

    async def handle(self, message: AgentMessage) -> AgentResponse:
        """Handle incoming prior auth request message."""

        try:
            if isinstance(message.payload, dict):
                request = PriorAuthRequest(**message.payload)
            elif isinstance(message.payload, PriorAuthRequest):
                request = message.payload
            else:
                request = PriorAuthRequest(**message.payload)

            decision = await self.process_auth_request(request)

            return AgentResponse(
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status=ResponseStatus.SUCCESS,
                payload=decision.model_dump(),
                confidence_score=decision.confidence_score,
                reasoning=[step.get("detail", "") for step in decision.reasoning_chain],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Error handling prior auth request")
            return AgentResponse(
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status=ResponseStatus.FAILURE,
                payload={"error": str(exc)},
                confidence_score=0.0,
                reasoning=[str(exc)],
            )

    async def process(self, message: AgentMessage) -> AgentResponse:
        """BaseAgent-required processor that delegates to the auth pipeline."""

        try:
            payload = message.payload
            request = payload if isinstance(payload, PriorAuthRequest) else PriorAuthRequest(**payload)
        except Exception as exc:
            logger.exception("Failed to parse PriorAuthRequest")
            return AgentResponse(
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status=ResponseStatus.FAILURE,
                payload={},
                errors=[f"Invalid request payload: {exc}"],
            )

        decision = await self.process_auth_request(request)
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload=decision.model_dump(),
            confidence_score=decision.confidence_score,
            reasoning=[step.get("detail", "") for step in decision.reasoning_chain],
        )

    async def process_auth_request(self, request: PriorAuthRequest) -> AuthorizationDecision:
        """Main prior auth processing pipeline."""

        start_ts = datetime.utcnow()
        reasoning_chain: List[Dict[str, str]] = []

        try:
            # Step 1: Request classification / auth requirement
            auth_req = self.auth_checker.check_auth_required(request.service_code, request.payer_id, request.service_type)
            policy = self.get_policy_for_request(request)
            reasoning_chain.append({"step": "classification", "detail": str(auth_req)})
            high_risk_types = {ServiceType.IMAGING, ServiceType.MEDICATION, ServiceType.DME}
            if not auth_req.get("auth_required", False):
                if policy is None and request.service_type not in high_risk_types:
                    decision = AuthorizationDecision(
                        request_id=request.request_id,
                        status=AuthorizationStatus.NOT_REQUIRED,
                        decision_date=_today_str(),
                        effective_date=request.requested_start_date or _today_str(),
                        expiration_date=request.requested_end_date,
                        approved_units=request.requested_units,
                        approved_service_code=request.service_code,
                        policy_reference="AUTH_NOT_REQUIRED",
                        criteria_match_report=[],
                        missing_information=[],
                        denial_reasons=[],
                        appeal_rights=None,
                        alternative_treatments=[],
                        peer_review_required=False,
                        confidence_score=0.99,
                        reasoning_chain=reasoning_chain,
                    )
                    decision.audit_trail_id = self._generate_audit_id(request, decision)
                    decision.processing_time_ms = int((datetime.utcnow() - start_ts).total_seconds() * 1000)
                    return decision

                # If a policy exists or the service is high-risk, require full policy evaluation.
                auth_req["auth_required"] = True
                auth_req["reason"] = auth_req.get("reason") or "Policy evaluation required"
                reasoning_chain[-1] = {"step": "classification", "detail": str(auth_req)}

            # Step 2: Retrospective check
            if request.is_retrospective:
                retro = self.retro_handler.evaluate_retro_request(request)
                reasoning_chain.append({"step": "retro", "detail": str(retro)})
                if not retro.get("allowed"):
                    decision = AuthorizationDecision(
                        request_id=request.request_id,
                        status=AuthorizationStatus.DENIED,
                        decision_date=_today_str(),
                        effective_date=request.requested_start_date or _today_str(),
                        expiration_date=request.requested_end_date,
                        approved_units=None,
                        approved_service_code=None,
                        policy_reference="RETRO_DENIED",
                        criteria_match_report=[],
                        missing_information=[],
                        denial_reasons=[retro.get("reason", "Retro not allowed")],
                        appeal_rights={"can_appeal": True, "deadline_days": 180},
                        alternative_treatments=[],
                        peer_review_required=False,
                        confidence_score=0.88,
                        reasoning_chain=reasoning_chain,
                    )
                    decision.audit_trail_id = self._generate_audit_id(request, decision)
                    decision.processing_time_ms = int((datetime.utcnow() - start_ts).total_seconds() * 1000)
                    return decision

            # Step 3: Policy lookup
            if not policy:
                decision = AuthorizationDecision(
                    request_id=request.request_id,
                    status=AuthorizationStatus.ESCALATED,
                    decision_date=_today_str(),
                    effective_date=request.requested_start_date or _today_str(),
                    expiration_date=request.requested_end_date,
                    approved_units=None,
                    approved_service_code=None,
                    policy_reference="POLICY_NOT_FOUND",
                    criteria_match_report=[],
                    missing_information=["Policy not available"],
                    denial_reasons=[],
                    appeal_rights={"can_appeal": True, "deadline_days": 180},
                    alternative_treatments=[],
                    peer_review_required=False,
                    confidence_score=0.4,
                    reasoning_chain=reasoning_chain + [{"step": "policy", "detail": "No policy found"}],
                )
                decision.audit_trail_id = self._generate_audit_id(request, decision)
                decision.processing_time_ms = int((datetime.utcnow() - start_ts).total_seconds() * 1000)
                return decision

            # Step 4: Clinical criteria matching
            criteria_results = self.matcher.match_criteria(request, policy)

            # Step 5: Determination
            decision = self.determination_engine.make_determination(request, criteria_results, policy)
            decision.reasoning_chain = reasoning_chain + decision.reasoning_chain

            # Step 6: Letter generation (stored as notes for downstream systems)
            decision_notes = self._generate_letter(decision, request)
            decision.reasoning_chain.append({"step": "letter", "detail": decision_notes})

            # Step 7: Compliance checks
            compliance = self.check_turnaround_compliance(request, policy)
            decision.reasoning_chain.append({"step": "compliance", "detail": str(compliance)})

            # Step 7b: Appeal guidance
            if decision.status in {AuthorizationStatus.DENIED, AuthorizationStatus.PENDING_INFO}:
                guidance = self.appeal_guidance_generator.generate_guidance(decision, request)
                decision.appeal_rights = guidance.model_dump()
                decision.reasoning_chain.append({"step": "appeal", "detail": "Appeal guidance generated"})

            # Step 8: Audit trail
            decision.audit_trail_id = self._generate_audit_id(request, decision)
            decision.processing_time_ms = int((datetime.utcnow() - start_ts).total_seconds() * 1000)
            return decision
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Unhandled error in prior auth pipeline")
            decision = AuthorizationDecision(
                request_id=request.request_id,
                status=AuthorizationStatus.ESCALATED,
                decision_date=_today_str(),
                effective_date=request.requested_start_date or _today_str(),
                expiration_date=request.requested_end_date,
                approved_units=None,
                approved_service_code=None,
                policy_reference="PIPELINE_ERROR",
                criteria_match_report=[],
                missing_information=["System error"],
                denial_reasons=[str(exc)],
                appeal_rights={"can_appeal": True, "deadline_days": 180},
                alternative_treatments=[],
                peer_review_required=False,
                confidence_score=0.2,
                reasoning_chain=reasoning_chain + [{"step": "error", "detail": str(exc)}],
            )
            decision.audit_trail_id = self._generate_audit_id(request, decision)
            decision.processing_time_ms = int((datetime.utcnow() - start_ts).total_seconds() * 1000)
            return decision

    def process_auth_request_sync(self, request: PriorAuthRequest) -> AuthorizationDecision:
        """Synchronous wrapper for process_auth_request."""

        import concurrent.futures

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, self.process_auth_request(request)).result()
                return result
            return loop.run_until_complete(self.process_auth_request(request))
        except RuntimeError:
            return asyncio.run(self.process_auth_request(request))

    def check_turnaround_compliance(self, request: PriorAuthRequest, policy: MedicalPolicy) -> Dict[str, Any]:
        """Assess whether decision timing meets turnaround expectations."""

        submitted = request.submission_date
        expected_days = policy.turnaround_days_urgent if request.is_urgent else policy.turnaround_days_standard
        deadline = (datetime.strptime(submitted, "%Y-%m-%d") + timedelta(days=expected_days)).strftime("%Y-%m-%d")
        return {
            "deadline": deadline,
            "expected_days": expected_days,
            "request_is_urgent": request.is_urgent,
        }

    def get_policy_for_request(self, request: PriorAuthRequest) -> Optional[MedicalPolicy]:
        """Return the policy matching payer and service code."""

        for policy in self.POLICY_DATABASE.values():
            if policy.payer_id == request.payer_id and request.service_code in policy.service_codes:
                return policy
        return None

    def _generate_letter(self, decision: AuthorizationDecision, request: PriorAuthRequest) -> str:
        """Generate appropriate letter text based on decision status."""

        if decision.status == AuthorizationStatus.APPROVED:
            return self.letter_generator.generate_approval_letter(decision, request)
        if decision.status == AuthorizationStatus.DENIED:
            return self.letter_generator.generate_denial_letter(decision, request)
        if decision.status == AuthorizationStatus.PENDING_INFO:
            return self.letter_generator.generate_info_request_letter(decision, request)
        return "No letter generated"

    def _generate_audit_id(self, request: PriorAuthRequest, decision: AuthorizationDecision) -> str:
        """Create a deterministic audit id from request and decision."""

        payload = f"{request.request_id}|{decision.status}|{decision.policy_reference}|{decision.decision_date}"
        return hashlib.sha256(payload.encode()).hexdigest()

    def _seed_policies(self) -> Dict[str, MedicalPolicy]:
        """Create sample policies for demonstration and tests."""

        def crit(cid: str, desc: str, cat: str, required: bool = True) -> ClinicalCriterion:
            return ClinicalCriterion(criterion_id=cid, description=desc, category=cat, required=required)

        policies: List[MedicalPolicy] = [
            MedicalPolicy(
                policy_id="POL-BCBS-MRI-001",
                policy_name="MRI Authorization - Musculoskeletal",
                payer_id="BCBS",
                service_codes=["73721", "73723", "73722"],
                effective_date="2024-01-01",
                criteria=[
                    crit("C1", "Conservative treatment failed for minimum 6 weeks", "step_therapy", True),
                    crit("C2", "Clinical examination findings documented", "documentation", True),
                    crit("C3", "X-ray performed and results documented", "documentation", False),
                ],
                turnaround_days_standard=14,
                turnaround_days_urgent=1,
            ),
            MedicalPolicy(
                policy_id="POL-AETNA-HIP-001",
                policy_name="Total Hip Arthroplasty",
                payer_id="AETNA",
                service_codes=["27130", "27132"],
                effective_date="2024-01-01",
                criteria=[
                    crit("H1", "BMI documented within last 90 days", "documentation", True),
                    crit("H2", "Failed physical therapy for 12 weeks", "step_therapy", True),
                    crit("H3", "Radiographic evidence of joint disease", "documentation", True),
                ],
                turnaround_days_standard=10,
                turnaround_days_urgent=2,
                requires_peer_review=True,
            ),
            MedicalPolicy(
                policy_id="POL-BCBS-RHEUM-001",
                policy_name="Specialty Medication - Biologic",
                payer_id="BCBS",
                service_codes=["J0129", "J1602"],
                effective_date="2024-02-01",
                criteria=[
                    crit("R1", "Documented failure of methotrexate", "step_therapy", True),
                    crit("R2", "Documented failure of TNF inhibitor", "step_therapy", True),
                    crit("R3", "Baseline lab monitoring completed", "lab_value", True),
                ],
                turnaround_days_standard=7,
                turnaround_days_urgent=1,
            ),
            MedicalPolicy(
                policy_id="POL-MCR-CT-001",
                policy_name="CT Imaging Authorization",
                payer_id="MEDICARE",
                service_codes=["71250", "71260", "71270"],
                effective_date="2024-03-01",
                criteria=[
                    crit("CT1", "Clinical indication matches LCD", "required_diagnosis", True),
                    crit("CT2", "Prior chest X-ray completed", "documentation", False),
                ],
                turnaround_days_standard=14,
                turnaround_days_urgent=1,
            ),
            MedicalPolicy(
                policy_id="POL-AETNA-IP-001",
                policy_name="Inpatient Admission - Severity",
                payer_id="AETNA",
                service_codes=["IP-GEN"],
                effective_date="2024-04-01",
                criteria=[
                    crit("IP1", "Severe symptoms requiring inpatient level of care", "documentation", True),
                    crit("IP2", "Failed observation or outpatient management", "step_therapy", True),
                    crit("IP3", "Contraindications to outpatient management", "contraindication", False),
                ],
                turnaround_days_standard=1,
                turnaround_days_urgent=1,
            ),
            MedicalPolicy(
                policy_id="POL-BCBS-THERAPY-001",
                policy_name="Physical Therapy Authorization",
                payer_id="BCBS",
                service_codes=["97110", "97112", "97140"],
                effective_date="2024-05-01",
                criteria=[
                    crit("PT1", "Plan of care documented by therapist", "documentation", True),
                    crit("PT2", "Re-evaluation after 6 visits", "documentation", False),
                ],
                turnaround_days_standard=3,
                turnaround_days_urgent=1,
            ),
        ]
        return {p.policy_id: p for p in policies}


# ---------------------------------------------------------------------------
# Main demonstration
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import sys

    async def _demo() -> None:
        agent = PriorAuthAgent()
        sample_request = PriorAuthRequest(
            member_id="MEM-001",
            provider_id="PRV-001",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI of knee without contrast",
            diagnosis_codes=["M17.11"],
            clinical_justification=(
                "Patient has right knee pain for 8 weeks. Conservative treatment with physical therapy "
                "and NSAIDs for 6 weeks with no improvement. X-ray shows joint space narrowing. Requesting MRI "
                "to evaluate for meniscal tear or ligament injury."
            ),
            requested_units=1,
            is_urgent=False,
        )
        result = await agent.process_auth_request(sample_request)
        print(f"Decision: {result.status}")
        print(f"Confidence: {result.confidence_score}")
        for reason in result.reasoning_chain:
            print(f"  Step {reason.get('step')}: {reason.get('detail')}")

    try:
        asyncio.run(_demo())
    except KeyboardInterrupt:
        sys.exit(0)
