"""
Claims Adjudication Agent for MEDI-COMPLY.

Implements a multi-step adjudication pipeline:
  1) Claim parsing and validation
  2) Code-level adjudication
  3) Claim-level determination
  4) Compliance checks
  5) Audit and output generation

This module is hackathon-friendly and uses deterministic mock data for
eligibility and provider checks while preserving realistic adjudication
structure and outputs.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from medi_comply.core.agent_base import BaseAgent
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.schemas.common import AgentState, AgentType, ResponseStatus
from medi_comply.compliance.parity_checker import ParityChecker
from medi_comply.compliance.fraud_detector import FraudDetector
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.knowledge.ncci_engine import NCCIEngine
from medi_comply.knowledge.lcd_ncd_engine import LCDNCDEngine
from medi_comply.knowledge.payer_policy_engine import PayerPolicyEngine


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ClaimStatus(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    ADJUDICATING = "ADJUDICATING"
    DETERMINING = "DETERMINING"
    COMPLIANCE_CHECK = "COMPLIANCE_CHECK"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    PENDED = "PENDED"
    APPEALED = "APPEALED"
    VOIDED = "VOIDED"
    DUPLICATE = "DUPLICATE"


class LineDisposition(str, Enum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PENDED = "PENDED"
    ADJUSTED = "ADJUSTED"
    BUNDLED = "BUNDLED"


class DenialReasonCategory(str, Enum):
    ELIGIBILITY = "ELIGIBILITY"
    AUTHORIZATION = "AUTHORIZATION"
    COVERAGE = "COVERAGE"
    MEDICAL_NECESSITY = "MEDICAL_NECESSITY"
    CODING = "CODING"
    TIMELY_FILING = "TIMELY_FILING"
    DUPLICATE = "DUPLICATE"
    BUNDLING = "BUNDLING"
    PROVIDER = "PROVIDER"
    COORDINATION = "COORDINATION"
    DOCUMENTATION = "DOCUMENTATION"
    FREQUENCY = "FREQUENCY"
    PARITY = "PARITY"


# ---------------------------------------------------------------------------
# Reference code dictionaries
# ---------------------------------------------------------------------------


CARC_CODES: dict[str, str] = {
    "1": "Deductible Amount",
    "2": "Coinsurance Amount",
    "3": "Co-payment Amount",
    "4": "The procedure code is inconsistent with the modifier used",
    "5": "The procedure code/bill type is inconsistent with the place of service",
    "16": "Claim/service lacks information needed for adjudication",
    "18": "Exact duplicate claim/service",
    "22": "This care may be covered by another payer per coordination of benefits",
    "27": "Expenses incurred after coverage terminated",
    "29": "The time limit for filing has expired",
    "45": "Charge exceeds fee schedule/maximum allowable",
    "50": "Not medically necessary",
    "96": "Non-covered charge(s)",
    "97": "Bundled into another service",
    "119": "Benefit maximum reached",
    "197": "Authorization/notification absent",
    "204": "Service not covered under current benefits",
    "B7": "Provider not eligible/credentialed",
}

RARC_CODES: dict[str, str] = {
    "N30": "Patient not eligible for this service on date of service",
    "N386": "Decision based on Local Coverage Determination",
    "N432": "Coordination of benefits applies",
    "M15": "Separately billed services/tests have been bundled",
    "MA04": "Secondary payment cannot be calculated without primary EOB",
    "N115": "Decision based on National Coverage Determination",
    "N657": "Service not prior authorized",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ClaimLineItem(BaseModel):
    line_number: int
    cpt_code: str
    modifiers: list[str] = Field(default_factory=list)
    diagnosis_pointers: list[int] = Field(default_factory=list)
    units: int = 1
    charge_amount: float
    place_of_service: Optional[str] = None
    date_of_service: Optional[str] = None
    rendering_provider_npi: Optional[str] = None
    ndc_code: Optional[str] = None
    description: Optional[str] = None

    @field_validator("units")
    @classmethod
    def validate_units(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("Units must be positive")
        return v

    @field_validator("charge_amount")
    @classmethod
    def validate_charge(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Charge amount must be non-negative")
        return v


class ClaimInput(BaseModel):
    claim_id: str
    claim_type: str
    submission_date: str
    member_id: str
    member_name: Optional[str] = None
    member_dob: Optional[str] = None
    member_gender: Optional[str] = None
    payer_id: str
    plan_id: Optional[str] = None
    provider_id: str
    provider_name: Optional[str] = None
    provider_npi: Optional[str] = None
    provider_taxonomy: Optional[str] = None
    facility_id: Optional[str] = None
    place_of_service: str
    date_of_service_from: str
    date_of_service_to: Optional[str] = None
    admission_date: Optional[str] = None
    discharge_date: Optional[str] = None
    discharge_status: Optional[str] = None
    primary_diagnosis: str
    secondary_diagnoses: list[str] = Field(default_factory=list)
    line_items: list[ClaimLineItem]
    total_charges: float
    referring_provider_npi: Optional[str] = None
    authorization_number: Optional[str] = None
    prior_claim_id: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)
    notes: Optional[str] = None


class MemberEligibility(BaseModel):
    member_id: str
    is_eligible: bool
    eligibility_start_date: str
    eligibility_end_date: Optional[str]
    plan_name: str
    payer_id: str
    group_number: Optional[str] = None
    is_active: bool = True
    deductible_total: float = 0.0
    deductible_met: float = 0.0
    deductible_remaining: float = 0.0
    oop_max_total: float = 0.0
    oop_met: float = 0.0
    oop_remaining: float = 0.0
    copay_primary_care: Optional[float] = None
    copay_specialist: Optional[float] = None
    coinsurance_in_network: Optional[float] = None
    coinsurance_out_of_network: Optional[float] = None
    coordination_of_benefits: Optional[dict] = None


class ProviderVerification(BaseModel):
    provider_id: str
    provider_npi: Optional[str]
    is_credentialed: bool
    is_in_network: bool
    network_status: str
    specialty: Optional[str] = None
    accepts_assignment: bool = True
    effective_date: Optional[str] = None
    termination_date: Optional[str] = None


class LineAdjudicationResult(BaseModel):
    line_number: int
    cpt_code: str
    disposition: LineDisposition
    allowed_amount: Optional[float] = None
    paid_amount: Optional[float] = None
    member_responsibility: Optional[float] = None
    copay: Optional[float] = None
    coinsurance: Optional[float] = None
    deductible_applied: Optional[float] = None
    adjustment_amount: Optional[float] = None
    denial_reason_code: Optional[str] = None
    denial_reason_description: Optional[str] = None
    remark_codes: list[str] = Field(default_factory=list)
    policy_reference: Optional[str] = None
    ncci_edit_applied: Optional[str] = None
    medical_necessity_result: Optional[str] = None
    authorization_status: Optional[str] = None
    reasoning: str = ""
    checks_performed: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AppealGuidance(BaseModel):
    has_appeal_rights: bool
    appeal_deadline_days: int
    appeal_deadline_date: Optional[str]
    appeal_levels: list[str]
    appeal_instructions: str
    required_documentation: list[str]
    peer_to_peer_available: bool
    contact_info: Optional[str] = None


class ClaimAdjudicationResult(BaseModel):
    claim_id: str
    claim_status: ClaimStatus
    processing_date: str
    processing_time_ms: float
    payer_id: str
    payer_name: str
    member_id: str
    provider_id: str
    date_of_service: str
    line_results: list[LineAdjudicationResult]
    total_charges: float
    total_allowed: float
    total_paid: float
    total_member_responsibility: float
    total_adjustment: float
    claim_level_denial_reasons: list[str]
    timely_filing_check: bool
    duplicate_check: bool
    eligibility_check: bool
    provider_check: bool
    parity_check_result: Optional[dict]
    compliance_checks_passed: int
    compliance_checks_total: int
    appeal_guidance: Optional[AppealGuidance]
    eob_summary: str
    audit_trail_id: str
    reasoning_summary: str
    warnings: list[str]

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


# ---------------------------------------------------------------------------
# Helpers: Eligibility & Provider
# ---------------------------------------------------------------------------


class EligibilityChecker:
    """Simulated eligibility checker with deterministic mock responses."""

    def check_eligibility(self, member_id: str, date_of_service: str, payer_id: str) -> MemberEligibility:
        base_start = datetime.now(timezone.utc) - timedelta(days=365)
        eligible = not member_id.upper().endswith("X")
        plan_name = "Standard PPO" if eligible else "Inactive"
        deductible_total = 1000.0
        deductible_met = 400.0 if eligible else 0.0
        return MemberEligibility(
            member_id=member_id,
            is_eligible=eligible,
            eligibility_start_date=base_start.date().isoformat(),
            eligibility_end_date=None,
            plan_name=plan_name,
            payer_id=payer_id,
            group_number="GRP-100",
            is_active=eligible,
            deductible_total=deductible_total,
            deductible_met=deductible_met,
            deductible_remaining=max(deductible_total - deductible_met, 0.0),
            oop_max_total=4000.0,
            oop_met=800.0,
            oop_remaining=3200.0,
            copay_primary_care=25.0,
            copay_specialist=40.0,
            coinsurance_in_network=0.2,
            coinsurance_out_of_network=0.4,
            coordination_of_benefits=self.check_coordination_of_benefits(member_id),
        )

    def check_coordination_of_benefits(self, member_id: str) -> Optional[dict]:
        if member_id.startswith("COB"):
            return {
                "primary": "PrimaryPayer",
                "secondary": "SecondaryPayer",
                "rule": "Birthday Rule",
            }
        return None


class ProviderChecker:
    """Simulated provider credentialing/network checker."""

    def verify_provider(self, provider_id: str, payer_id: str, date_of_service: str) -> ProviderVerification:
        is_in_network = not provider_id.upper().endswith("OON")
        return ProviderVerification(
            provider_id=provider_id,
            provider_npi=f"{provider_id}-NPI",
            is_credentialed=is_in_network,
            is_in_network=is_in_network,
            network_status="IN_NETWORK" if is_in_network else "OUT_OF_NETWORK",
            specialty="PRIMARY_CARE",
            accepts_assignment=True,
            effective_date=(datetime.now(timezone.utc) - timedelta(days=180)).date().isoformat(),
            termination_date=None,
        )


# ---------------------------------------------------------------------------
# Claim Validator
# ---------------------------------------------------------------------------


class ClaimValidator:
    """Performs structural validation on incoming claims."""

    _cpt_re = re.compile(r"^\d{5}$")
    _icd_re = re.compile(r"^[A-TV-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$")
    _pos_re = re.compile(r"^\d{2}$")

    def validate(self, claim: ClaimInput) -> dict:
        errors: list[str] = []
        warnings: list[str] = []

        required = [
            claim.claim_id,
            claim.claim_type,
            claim.submission_date,
            claim.member_id,
            claim.payer_id,
            claim.provider_id,
            claim.place_of_service,
            claim.date_of_service_from,
            claim.primary_diagnosis,
        ]
        if any(v in (None, "") for v in required):
            errors.append("Missing required fields")

        for code in [claim.primary_diagnosis, *claim.secondary_diagnoses]:
            if not self._icd_re.match(code):
                errors.append(f"Invalid ICD-10 format: {code}")

        if not self._pos_re.match(claim.place_of_service):
            errors.append(f"Invalid POS code: {claim.place_of_service}")

        if not claim.line_items:
            errors.append("At least one line item is required")

        today = datetime.now(timezone.utc).date()
        try:
            dos = datetime.fromisoformat(claim.date_of_service_from).date()
            if dos > today:
                errors.append("Date of service cannot be in the future")
        except ValueError:
            errors.append("Invalid date_of_service_from format")

        try:
            datetime.fromisoformat(claim.submission_date)
        except ValueError:
            errors.append("Invalid submission_date format")

        for item in claim.line_items:
            if not self._cpt_re.match(item.cpt_code):
                errors.append(f"Invalid CPT format on line {item.line_number}")
            if item.charge_amount <= 0:
                errors.append(f"Charge must be positive on line {item.line_number}")
            if item.units <= 0:
                errors.append(f"Units must be positive on line {item.line_number}")
            if item.diagnosis_pointers:
                max_ptr = max(item.diagnosis_pointers)
                if max_ptr > (1 + len(claim.secondary_diagnoses)):
                    errors.append(f"Diagnosis pointer out of range on line {item.line_number}")
            if item.place_of_service and not self._pos_re.match(item.place_of_service):
                errors.append(f"Invalid line-level POS on line {item.line_number}")
            if item.date_of_service:
                try:
                    ldos = datetime.fromisoformat(item.date_of_service).date()
                    if ldos > today:
                        errors.append(f"Future date_of_service on line {item.line_number}")
                except ValueError:
                    errors.append(f"Invalid date_of_service on line {item.line_number}")

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# Code-level adjudication
# ---------------------------------------------------------------------------


class CodeLevelAdjudicator:
    def __init__(
        self,
        knowledge_manager: Optional[KnowledgeManager] = None,
        payer_engine: Optional[PayerPolicyEngine] = None,
        ncci_engine: Optional[NCCIEngine] = None,
        lcd_ncd_engine: Optional[LCDNCDEngine] = None,
        fraud_detector: Optional[FraudDetector] = None,
    ) -> None:
        self.knowledge_manager = knowledge_manager
        self.payer_engine = payer_engine
        self.ncci_engine = ncci_engine
        self.lcd_ncd_engine = lcd_ncd_engine
        self.fraud_detector = fraud_detector

    def adjudicate_line(
        self,
        line: ClaimLineItem,
        claim: ClaimInput,
        eligibility: MemberEligibility,
        provider: ProviderVerification,
    ) -> LineAdjudicationResult:
        checks_performed: list[str] = []
        reasoning: list[str] = []
        warnings: list[str] = []
        remark_codes: list[str] = []

        diag_codes = [claim.primary_diagnosis] + claim.secondary_diagnoses
        other_cpts = [li.cpt_code for li in claim.line_items if li.line_number != line.line_number]

        coverage = self._check_coverage(line.cpt_code, claim.payer_id, diag_codes)
        checks_performed.append("coverage")
        if not coverage["covered"]:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.COVERAGE, coverage["reason"])
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.DENIED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                policy_reference=coverage.get("policy"),
                reasoning=coverage["reason"],
                checks_performed=checks_performed,
                warnings=warnings,
            )

        auth = self._check_authorization(line.cpt_code, claim.payer_id, claim.authorization_number)
        checks_performed.append("authorization")
        if not auth["authorized"]:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.AUTHORIZATION, auth["reason"])
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.DENIED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                authorization_status="MISSING",
                reasoning=auth["reason"],
                checks_performed=checks_performed,
                warnings=warnings,
            )

        ncci = self._run_ncci_edits(line.cpt_code, other_cpts, line.modifiers)
        checks_performed.append("ncci")
        if ncci["bundled"]:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.BUNDLING, ncci["reason"])
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.BUNDLED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                ncci_edit_applied=ncci.get("edit"),
                reasoning=ncci["reason"],
                checks_performed=checks_performed,
                warnings=warnings,
            )

        mue = self._check_mue(line.cpt_code, line.units)
        checks_performed.append("mue")
        if not mue["within_limit"]:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.FREQUENCY, mue["reason"])
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.DENIED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                reasoning=mue["reason"],
                checks_performed=checks_performed,
                warnings=warnings,
            )

        med_nec = self._check_medical_necessity(line.cpt_code, diag_codes)
        checks_performed.append("medical_necessity")
        if not med_nec["meets_necessity"]:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.MEDICAL_NECESSITY, med_nec["reason"])
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.DENIED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                medical_necessity_result=med_nec.get("policy"),
                reasoning=med_nec["reason"],
                checks_performed=checks_performed,
                warnings=warnings,
            )

        allowed = self._apply_fee_schedule(line.cpt_code, claim.payer_id, line.modifiers, claim.place_of_service in {"21", "22"})
        checks_performed.append("fee_schedule")
        if allowed is None:
            carc, desc, rarcs = self._generate_denial_codes(DenialReasonCategory.COVERAGE, "Fee schedule missing")
            remark_codes.extend(rarcs)
            return LineAdjudicationResult(
                line_number=line.line_number,
                cpt_code=line.cpt_code,
                disposition=LineDisposition.DENIED,
                denial_reason_code=carc,
                denial_reason_description=desc,
                remark_codes=remark_codes,
                reasoning="No allowed amount available",
                checks_performed=checks_performed,
                warnings=warnings,
            )

        cs = self._apply_cost_sharing(allowed * line.units, eligibility, provider.is_in_network)
        checks_performed.append("cost_sharing")
        paid = max(allowed * line.units - cs["member_responsibility"], 0.0)
        reasoning.append("Coverage confirmed; allowed amount applied")
        reasoning.append(cs["explanation"])

        disposition = LineDisposition.APPROVED
        adjustment = max(line.charge_amount * line.units - allowed * line.units, 0.0)
        return LineAdjudicationResult(
            line_number=line.line_number,
            cpt_code=line.cpt_code,
            disposition=disposition,
            allowed_amount=allowed * line.units,
            paid_amount=paid,
            member_responsibility=cs["member_responsibility"],
            copay=cs.get("copay"),
            coinsurance=cs.get("coinsurance"),
            deductible_applied=cs.get("deductible"),
            adjustment_amount=adjustment,
            reasoning="; ".join(reasoning),
            checks_performed=checks_performed,
            warnings=warnings,
            policy_reference=coverage.get("policy"),
        )

    def _check_coverage(self, cpt_code: str, payer_id: str, icd10_codes: list[str]) -> dict:
        if cpt_code.startswith("0"):
            return {"covered": False, "reason": "Invalid CPT code", "policy": "Coverage-Invalid-CPT"}
        return {"covered": True, "reason": "Covered", "policy": "Standard Coverage"}

    def _check_authorization(self, cpt_code: str, payer_id: str, auth_number: Optional[str]) -> dict:
        requires_auth = cpt_code in {"99299"}
        if requires_auth and not auth_number:
            return {"authorized": False, "reason": "Authorization required but not on file"}
        return {"authorized": True, "reason": "Authorization satisfied"}

    def _run_ncci_edits(self, cpt_code: str, other_cpt_codes: list[str], modifiers: list[str]) -> dict:
        if cpt_code in {"99213"} and cpt_code in other_cpt_codes and "59" not in modifiers:
            return {"bundled": True, "edit": "DUP", "reason": "Duplicate service bundled"}
        if self.ncci_engine:
            # Placeholder integration point
            pass
        return {"bundled": False, "edit": None, "reason": "No NCCI bundling"}

    def _check_mue(self, cpt_code: str, units: int) -> dict:
        limit = 4
        if units > limit:
            return {"within_limit": False, "reason": f"Units exceed MUE limit {limit}"}
        return {"within_limit": True, "reason": "Within MUE"}

    def _check_medical_necessity(self, cpt_code: str, icd10_codes: list[str]) -> dict:
        if self.lcd_ncd_engine:
            # Placeholder for LCD/NCD integration
            pass
        if cpt_code.startswith("992"):
            return {"meets_necessity": True, "reason": "E/M visit medically necessary", "policy": "LCD-MET"}
        if cpt_code.startswith("99") and not any(code.startswith("F") for code in icd10_codes):
            return {"meets_necessity": False, "reason": "Dx does not support procedure", "policy": "LCD-NOT-MET"}
        return {"meets_necessity": True, "reason": "Medically necessary", "policy": "LCD-MET"}

    def _apply_fee_schedule(self, cpt_code: str, payer_id: str, modifiers: list[str], is_facility: bool) -> Optional[float]:
        base = 100.0
        if cpt_code.startswith("93"):
            base = 75.0
        if cpt_code.startswith("99"):
            base = 150.0
        if "50" in modifiers:
            base *= 1.5
        if is_facility:
            base *= 0.9
        return round(base, 2)

    def _apply_cost_sharing(self, allowed_amount: float, eligibility: MemberEligibility, is_in_network: bool) -> dict:
        copay = eligibility.copay_primary_care or 0.0
        coinsurance_rate = eligibility.coinsurance_in_network if is_in_network else eligibility.coinsurance_out_of_network or 0.4
        deductible_applied = min(eligibility.deductible_remaining, allowed_amount)
        remaining_after_deductible = max(allowed_amount - deductible_applied, 0.0)
        coinsurance = round(remaining_after_deductible * coinsurance_rate, 2)
        member_resp = round(copay + deductible_applied + coinsurance, 2)
        return {
            "copay": copay,
            "coinsurance": coinsurance,
            "deductible": deductible_applied,
            "member_responsibility": member_resp,
            "explanation": f"Copay {copay:.2f}, deductible {deductible_applied:.2f}, coinsurance {coinsurance:.2f}",
        }

    def _generate_denial_codes(self, reason: DenialReasonCategory, details: str) -> tuple[str, str, list[str]]:
        mapping: dict[DenialReasonCategory, str] = {
            DenialReasonCategory.ELIGIBILITY: "27",
            DenialReasonCategory.AUTHORIZATION: "197",
            DenialReasonCategory.COVERAGE: "96",
            DenialReasonCategory.MEDICAL_NECESSITY: "50",
            DenialReasonCategory.CODING: "4",
            DenialReasonCategory.TIMELY_FILING: "29",
            DenialReasonCategory.DUPLICATE: "18",
            DenialReasonCategory.BUNDLING: "97",
            DenialReasonCategory.PROVIDER: "B7",
            DenialReasonCategory.COORDINATION: "22",
            DenialReasonCategory.DOCUMENTATION: "16",
            DenialReasonCategory.FREQUENCY: "119",
            DenialReasonCategory.PARITY: "96",
        }
        carc = mapping.get(reason, "96")
        desc = CARC_CODES.get(carc, "Denied")
        rarc_map: dict[DenialReasonCategory, list[str]] = {
            DenialReasonCategory.ELIGIBILITY: ["N30"],
            DenialReasonCategory.AUTHORIZATION: ["N657"],
            DenialReasonCategory.COVERAGE: ["N115"],
            DenialReasonCategory.MEDICAL_NECESSITY: ["N386"],
            DenialReasonCategory.BUNDLING: ["M15"],
            DenialReasonCategory.COORDINATION: ["N432", "MA04"],
            DenialReasonCategory.DUPLICATE: ["N432"],
            DenialReasonCategory.TIMELY_FILING: ["N30"],
        }
        rarcs = rarc_map.get(reason, ["N115"])
        full_desc = f"{desc}: {details}"
        return carc, full_desc, rarcs


# ---------------------------------------------------------------------------
# Claim-level determination
# ---------------------------------------------------------------------------


class ClaimLevelDeterminator:
    def determine(
        self,
        claim: ClaimInput,
        line_results: list[LineAdjudicationResult],
        eligibility: MemberEligibility,
        provider: ProviderVerification,
        payer_id: str,
    ) -> dict:
        dispositions = {r.disposition for r in line_results}
        if all(d == LineDisposition.APPROVED for d in dispositions):
            claim_status = ClaimStatus.APPROVED
        elif all(d in {LineDisposition.DENIED, LineDisposition.BUNDLED} for d in dispositions):
            claim_status = ClaimStatus.DENIED
        elif LineDisposition.PENDED in dispositions:
            claim_status = ClaimStatus.PENDED
        else:
            claim_status = ClaimStatus.PARTIALLY_APPROVED

        timely = self._check_timely_filing(claim)
        duplicate = self._check_duplicate(claim)

        total_allowed = sum(r.allowed_amount or 0.0 for r in line_results)
        total_paid = sum(r.paid_amount or 0.0 for r in line_results)
        total_member_resp = sum(r.member_responsibility or 0.0 for r in line_results)
        total_adjustment = sum(r.adjustment_amount or 0.0 for r in line_results)

        eob_summary = self._generate_eob_summary(claim, line_results, {
            "total_charges": claim.total_charges,
            "total_allowed": total_allowed,
            "total_paid": total_paid,
            "total_member_responsibility": total_member_resp,
            "total_adjustment": total_adjustment,
        })

        return {
            "claim_status": claim_status,
            "timely_filing_check": timely,
            "duplicate_check": duplicate,
            "total_allowed": round(total_allowed, 2),
            "total_paid": round(total_paid, 2),
            "total_member_responsibility": round(total_member_resp, 2),
            "total_adjustment": round(total_adjustment, 2),
            "eob_summary": eob_summary,
        }

    def _check_timely_filing(self, claim: ClaimInput) -> bool:
        try:
            dos = datetime.fromisoformat(claim.date_of_service_from).date()
            submission = datetime.fromisoformat(claim.submission_date).date()
            return (submission - dos).days <= 180
        except Exception:
            return False

    def _check_duplicate(self, claim: ClaimInput) -> bool:
        return claim.prior_claim_id is None

    def _generate_eob_summary(self, claim: ClaimInput, line_results: list[LineAdjudicationResult], totals: dict) -> str:
        lines = [
            "EXPLANATION OF BENEFITS",
            f"Claim #: {claim.claim_id} | Date of Service: {claim.date_of_service_from}",
            f"Provider: {claim.provider_name or claim.provider_id} | Member: {claim.member_name or claim.member_id}",
            "",
        ]
        for lr in line_results:
            lines.append(f"Line {lr.line_number}: CPT {lr.cpt_code}")
            lines.append(
                f"  Charged: ${next((li.charge_amount for li in claim.line_items if li.line_number == lr.line_number), 0.0):.2f} | "
                f"Allowed: ${lr.allowed_amount or 0.0:.2f} | Paid: ${lr.paid_amount or 0.0:.2f} | Your Cost: ${lr.member_responsibility or 0.0:.2f}"
            )
            status_line = f"  Status: {lr.disposition.value}"
            if lr.denial_reason_description:
                status_line += f" — {lr.denial_reason_description}"
            lines.append(status_line)
            if lr.reasoning:
                lines.append(f"  Reasoning: {lr.reasoning}")
            lines.append("")

        lines.append("TOTALS:")
        lines.append(f"  Total Charged: ${totals['total_charges']:.2f}")
        lines.append(f"  Total Allowed: ${totals['total_allowed']:.2f}")
        lines.append(f"  Plan Paid: ${totals['total_paid']:.2f}")
        lines.append(f"  Your Responsibility: ${totals['total_member_responsibility']:.2f}")
        lines.append(f"  Adjustment: ${totals['total_adjustment']:.2f}")
        lines.append("")
        lines.append("APPEAL RIGHTS: You have 180 days to file an appeal.")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Claims Adjudication Agent
# ---------------------------------------------------------------------------


class ClaimsAdjudicationAgent(BaseAgent):
    """Orchestrates full claim adjudication and produces claim decisions."""

    def __init__(
        self,
        knowledge_manager: Optional[KnowledgeManager] = None,
        payer_engine: Optional[PayerPolicyEngine] = None,
        ncci_engine: Optional[NCCIEngine] = None,
        lcd_ncd_engine: Optional[LCDNCDEngine] = None,
        fraud_detector: Optional[FraudDetector] = None,
        parity_checker: Optional[ParityChecker] = None,
        escalation_agent: Optional[Any] = None,
    ) -> None:
        super().__init__(agent_name="ClaimsAdjudicationAgent", agent_type=AgentType.PROCESSOR)
        self.eligibility_checker = EligibilityChecker()
        self.provider_checker = ProviderChecker()
        self.validator = ClaimValidator()
        self.adjudicator = CodeLevelAdjudicator(
            knowledge_manager=knowledge_manager,
            payer_engine=payer_engine,
            ncci_engine=ncci_engine,
            lcd_ncd_engine=lcd_ncd_engine,
            fraud_detector=fraud_detector,
        )
        self.determinator = ClaimLevelDeterminator()
        self.fraud_detector = fraud_detector
        self.parity_checker = parity_checker
        self.escalation_agent = escalation_agent

    def _safe_transition(self, new_state: AgentState, metadata: Optional[dict] = None) -> AgentState:
        """Attempt state transition; reset and retry if invalid, otherwise ignore."""
        from medi_comply.core.state_machine import InvalidTransitionError

        try:
            return self.transition_state(new_state, metadata)
        except InvalidTransitionError:
            self._state_machine.reset()
            try:
                return self.transition_state(new_state, metadata)
            except InvalidTransitionError:
                return self.state

    async def process(self, message: AgentMessage) -> AgentResponse:
        self.transition_state(AgentState.THINKING, {"detail": "Beginning claim adjudication"})
        payload_claim = message.payload.get("claim") or message.payload
        result = await self.adjudicate_claim(ClaimInput.model_validate(payload_claim))
        self.transition_state(AgentState.PROPOSING)
        self.transition_state(AgentState.VALIDATING)
        self.transition_state(AgentState.APPROVED)
        self.transition_state(AgentState.COMPLETED, {"detail": "Claim adjudication complete"})
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload={"claim_result": result},
            confidence_score=1.0,
            reasoning=[result.reasoning_summary],
            errors=[],
            trace_id=message.trace_id,
        )

    async def adjudicate_claim(self, claim: ClaimInput) -> ClaimAdjudicationResult:
        started = datetime.now(timezone.utc)
        self._safe_transition(AgentState.THINKING)
        validation = self.validator.validate(claim)
        claim_level_denials: list[str] = []
        warnings: list[str] = validation["warnings"]

        eligibility = self.eligibility_checker.check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
        provider = self.provider_checker.verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
        eligibility_check = eligibility.is_eligible and eligibility.is_active
        provider_check = provider.is_credentialed

        if not validation["valid"]:
            carc, desc, rarcs = CodeLevelAdjudicator()._generate_denial_codes(DenialReasonCategory.CODING, "; ".join(validation["errors"]))
            claim_level_denials.append(f"{carc} - {desc} | RARC: {', '.join(rarcs)}")

        if not eligibility_check:
            carc, desc, rarcs = self.adjudicator._generate_denial_codes(DenialReasonCategory.ELIGIBILITY, "Member not eligible on DOS")
            claim_level_denials.append(f"{carc} - {desc} | RARC: {', '.join(rarcs)}")

        if not provider_check:
            carc, desc, rarcs = self.adjudicator._generate_denial_codes(DenialReasonCategory.PROVIDER, "Provider not credentialed or out-of-network")
            claim_level_denials.append(f"{carc} - {desc} | RARC: {', '.join(rarcs)}")

        line_results: list[LineAdjudicationResult] = []
        if validation["valid"] and eligibility_check and provider_check:
            self._safe_transition(AgentState.THINKING)
            for line in claim.line_items:
                lr = self.adjudicator.adjudicate_line(line, claim, eligibility, provider)
                line_results.append(lr)
        else:
            line_results = []

        if not line_results and not claim_level_denials:
            # If we have no line results (e.g., validation failure), mark denial.
            carc, desc, rarcs = self.adjudicator._generate_denial_codes(DenialReasonCategory.CODING, "Claim invalid — no lines adjudicated")
            claim_level_denials.append(f"{carc} - {desc} | RARC: {', '.join(rarcs)}")

        if not line_results and claim_level_denials:
            # Fabricate minimal denial line for transparency.
            line_results.append(
                LineAdjudicationResult(
                    line_number=1,
                    cpt_code="00000",
                    disposition=LineDisposition.DENIED,
                    allowed_amount=0.0,
                    paid_amount=0.0,
                    member_responsibility=0.0,
                    denial_reason_code=claim_level_denials[0].split(" - ")[0],
                    denial_reason_description=claim_level_denials[0],
                    remark_codes=["N30"],
                    reasoning="Claim-level denial propagated to lines",
                    checks_performed=["validation"],
                )
            )

        self._safe_transition(AgentState.PROPOSING)
        determination = self.determinator.determine(claim, line_results, eligibility, provider, claim.payer_id)

        compliance_warnings: list[str] = []
        parity_result = None
        if self.fraud_detector:
            fraud_flags = self.fraud_detector.detect(claim.model_dump())
            if fraud_flags:
                compliance_warnings.append("Fraud detector flagged claim")
        if self.parity_checker:
            parity_result = self.parity_checker.check_parity(claim.model_dump())
            if parity_result and not parity_result.get("compliant", True):
                compliance_warnings.append("Parity check failed; denial not permitted for parity reasons")

        appeal_guidance = self.generate_appeal_guidance(claim, determination["claim_status"])

        status = determination["claim_status"]
        if claim_level_denials and status == ClaimStatus.APPROVED:
            status = ClaimStatus.DENIED

        reasoning_summary = self._build_reasoning_summary(status, claim_level_denials, line_results)

        completed = datetime.now(timezone.utc)
        processing_ms = (completed - started).total_seconds() * 1000

        self._safe_transition(AgentState.VALIDATING)

        result = ClaimAdjudicationResult(
            claim_id=claim.claim_id,
            claim_status=status,
            processing_date=completed.date().isoformat(),
            processing_time_ms=processing_ms,
            payer_id=claim.payer_id,
            payer_name="MEDI-COMPLY Payer",
            member_id=claim.member_id,
            provider_id=claim.provider_id,
            date_of_service=claim.date_of_service_from,
            line_results=line_results,
            total_charges=claim.total_charges,
            total_allowed=determination["total_allowed"],
            total_paid=determination["total_paid"],
            total_member_responsibility=determination["total_member_responsibility"],
            total_adjustment=determination["total_adjustment"],
            claim_level_denial_reasons=claim_level_denials,
            timely_filing_check=determination["timely_filing_check"],
            duplicate_check=determination["duplicate_check"],
            eligibility_check=eligibility_check,
            provider_check=provider_check,
            parity_check_result=parity_result,
            compliance_checks_passed=0 if compliance_warnings else 1,
            compliance_checks_total=1,
            appeal_guidance=appeal_guidance,
            eob_summary=determination["eob_summary"],
            audit_trail_id=f"AUD-{uuid.uuid4().hex[:8]}",
            reasoning_summary=reasoning_summary,
            warnings=warnings + compliance_warnings,
        )

        self._safe_transition(AgentState.APPROVED)
        self._safe_transition(AgentState.COMPLETED)
        return result

    def generate_appeal_guidance(self, claim: ClaimInput, status: ClaimStatus) -> Optional[AppealGuidance]:
        if status in {ClaimStatus.APPROVED}:
            return None
        deadline_days = 180
        deadline_date = (datetime.now(timezone.utc).date() + timedelta(days=deadline_days)).isoformat()
        levels = ["Internal Review", "External Review"]
        instructions = "Submit appeal with medical records, authorization proof, and corrected coding if applicable."
        required_docs = [
            "Medical records supporting diagnosis",
            "Authorization letter (if applicable)",
            "Corrected claim with modifiers",
        ]
        return AppealGuidance(
            has_appeal_rights=True,
            appeal_deadline_days=deadline_days,
            appeal_deadline_date=deadline_date,
            appeal_levels=levels,
            appeal_instructions=instructions,
            required_documentation=required_docs,
            peer_to_peer_available=True,
            contact_info="appeals@medi-comply.ai",
        )

    def _build_reasoning_summary(self, status: ClaimStatus, claim_denials: list[str], line_results: list[LineAdjudicationResult]) -> str:
        parts: list[str] = [f"Claim status: {status.value}"]
        if claim_denials:
            parts.append(f"Claim-level reasons: {' | '.join(claim_denials)}")
        line_bits = []
        for lr in line_results:
            line_bits.append(f"Line {lr.line_number} {lr.disposition.value} ({lr.denial_reason_description or 'Approved'})")
        if line_bits:
            parts.append("; ".join(line_bits))
        return " | ".join(parts)

    async def adjudicate_batch(self, claims: list[ClaimInput]) -> list[ClaimAdjudicationResult]:
        results: list[ClaimAdjudicationResult] = []
        for claim in claims:
            results.append(await self.adjudicate_claim(claim))
        return results


# ---------------------------------------------------------------------------
# Mock data generators for testing
# ---------------------------------------------------------------------------


def create_sample_claim() -> ClaimInput:
    return ClaimInput(
        claim_id="CLM-1001",
        claim_type="PROFESSIONAL",
        submission_date=datetime.now(timezone.utc).date().isoformat(),
        member_id="MEM-123",
        member_name="Jane Doe",
        member_dob="1980-01-01",
        member_gender="F",
        payer_id="PYR-1",
        plan_id="PLAN-A",
        provider_id="PRV-1",
        provider_name="Dr. Smith",
        provider_npi="1111111111",
        provider_taxonomy="207Q00000X",
        facility_id="FAC-1",
        place_of_service="11",
        date_of_service_from=datetime.now(timezone.utc).date().isoformat(),
        primary_diagnosis="R07.9",
        secondary_diagnoses=["Z00.00"],
        line_items=[
            ClaimLineItem(
                line_number=1,
                cpt_code="99213",
                modifiers=["25"],
                diagnosis_pointers=[1],
                units=1,
                charge_amount=150.0,
                description="Office visit"
            ),
            ClaimLineItem(
                line_number=2,
                cpt_code="93000",
                modifiers=[],
                diagnosis_pointers=[1],
                units=1,
                charge_amount=75.0,
                description="ECG"
            ),
        ],
        total_charges=225.0,
        attachments=[],
        notes="Routine visit",
    )


def create_sample_eligibility(member_id: str) -> MemberEligibility:
    return EligibilityChecker().check_eligibility(member_id, datetime.now(timezone.utc).date().isoformat(), "PYR-1")


def create_sample_provider(provider_id: str) -> ProviderVerification:
    return ProviderChecker().verify_provider(provider_id, "PYR-1", datetime.now(timezone.utc).date().isoformat())


def create_sample_claim_clean() -> ClaimInput:
    claim = create_sample_claim()
    claim.secondary_diagnoses = ["R07.89"]
    claim.authorization_number = "AUTH-123"
    return claim


def create_sample_claim_denied() -> ClaimInput:
    claim = create_sample_claim()
    claim.member_id = "MEM-123X"  # ineligible
    return claim


def create_sample_claim_partial() -> ClaimInput:
    claim = create_sample_claim()
    claim.line_items.append(
        ClaimLineItem(
            line_number=3,
            cpt_code="99358",
            modifiers=[],
            diagnosis_pointers=[1],
            units=5,
            charge_amount=200.0,
            description="Excessive prolonged service"
        )
    )
    claim.total_charges = sum(li.charge_amount for li in claim.line_items)
    return claim
