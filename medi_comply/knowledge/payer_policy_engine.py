"""Payer policy engine for MEDI-COMPLY.

This module models payer-specific rules (prior authorization, coverage,
fee schedules, site of service, formularies, and appeals) and provides a
single in-memory engine for claim-line adjudication.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PayerType(str, Enum):
    """Supported payers."""

    MEDICARE = "MEDICARE"
    MEDICAID = "MEDICAID"
    UHC = "UHC"
    AETNA = "AETNA"
    BCBS = "BCBS"
    CIGNA = "CIGNA"
    HUMANA = "HUMANA"
    TRICARE = "TRICARE"
    GENERIC = "GENERIC"


class NetworkStatus(str, Enum):
    """Provider network status."""

    IN_NETWORK = "IN_NETWORK"
    OUT_OF_NETWORK = "OUT_OF_NETWORK"
    OUT_OF_AREA = "OUT_OF_AREA"
    UNKNOWN = "UNKNOWN"


class AuthRequirement(str, Enum):
    """Prior authorization requirement."""

    REQUIRED = "REQUIRED"
    NOT_REQUIRED = "NOT_REQUIRED"
    REQUIRED_URGENT = "REQUIRED_URGENT"
    REQUIRED_RETRO = "REQUIRED_RETRO"
    NOTIFICATION_ONLY = "NOTIFICATION_ONLY"
    VARIES = "VARIES"


class ServiceCategory(str, Enum):
    """Service categories used by payers."""

    PROCEDURE = "PROCEDURE"
    IMAGING = "IMAGING"
    LAB = "LAB"
    DME = "DME"
    MEDICATION = "MEDICATION"
    THERAPY = "THERAPY"
    BEHAVIORAL_HEALTH = "BEHAVIORAL_HEALTH"
    HOME_HEALTH = "HOME_HEALTH"
    SKILLED_NURSING = "SKILLED_NURSING"
    INPATIENT = "INPATIENT"
    OUTPATIENT = "OUTPATIENT"
    EMERGENCY = "EMERGENCY"
    PREVENTIVE = "PREVENTIVE"
    TELEHEALTH = "TELEHEALTH"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AgeLimit(BaseModel):
    min_age: Optional[int] = None
    max_age: Optional[int] = None


class AuthRequirementRule(BaseModel):
    cpt_codes: List[str]
    service_category: Optional[ServiceCategory] = None
    auth_requirement: AuthRequirement
    auth_turnaround_standard_days: int
    auth_turnaround_urgent_hours: int
    retro_auth_window_hours: Optional[int] = None
    required_clinical_info: List[str] = Field(default_factory=list)
    exceptions: List[str] = Field(default_factory=list)
    effective_date: Optional[str] = None
    policy_reference: Optional[str] = None


class CoveredServiceRule(BaseModel):
    cpt_code: str
    is_covered: bool
    coverage_conditions: List[str] = Field(default_factory=list)
    age_limits: Optional[AgeLimit] = None
    gender_limit: Optional[str] = None
    diagnosis_requirements: List[str] = Field(default_factory=list)
    place_of_service_restrictions: List[str] = Field(default_factory=list)
    exclusions: List[str] = Field(default_factory=list)


class FeeScheduleEntry(BaseModel):
    cpt_code: str
    allowed_amount: float
    facility_rate: Optional[float] = None
    non_facility_rate: Optional[float] = None
    modifier_adjustments: Dict[str, float] = Field(default_factory=dict)


class FormularyTier(BaseModel):
    tier_number: int
    tier_name: str
    copay: Optional[float] = None
    coinsurance_percent: Optional[float] = None
    requires_prior_auth: bool = False
    requires_step_therapy: bool = False
    quantity_limit: Optional[str] = None


class FormularyPolicy(BaseModel):
    tiers: Dict[int, FormularyTier] = Field(default_factory=dict)
    preferred_drugs: Dict[str, int] = Field(default_factory=dict)
    non_preferred_drugs: Dict[str, int] = Field(default_factory=dict)
    specialty_drugs: Dict[str, int] = Field(default_factory=dict)
    excluded_drugs: List[str] = Field(default_factory=list)


class StepTherapyProtocol(BaseModel):
    protocol_id: str
    target_drug: str
    target_drug_code: Optional[str] = None
    required_first_line: List[str] = Field(default_factory=list)
    required_duration_days: int = 30
    required_failure_documentation: List[str] = Field(default_factory=list)
    exceptions: List[str] = Field(default_factory=list)


class QuantityLimit(BaseModel):
    code: str
    max_quantity: int
    time_period: str
    max_refills: Optional[int] = None
    override_criteria: List[str] = Field(default_factory=list)


class SiteOfServiceRule(BaseModel):
    cpt_codes: List[str]
    preferred_pos: List[str] = Field(default_factory=list)
    restricted_pos: List[str] = Field(default_factory=list)
    differential_payment: Optional[str] = None


class NetworkRequirements(BaseModel):
    requires_in_network: bool
    out_of_network_covered: bool
    out_of_network_penalty_percent: float
    requires_referral: bool
    requires_pcp_selection: bool
    surprise_billing_protection: bool


class MemberCostSharing(BaseModel):
    copay: Optional[float] = None
    coinsurance_percent: Optional[float] = None
    deductible_applies: bool = True
    deductible_amount: Optional[float] = None
    out_of_pocket_max: Optional[float] = None
    in_network: bool = True


class PayerPolicy(BaseModel):
    payer_id: str
    payer_type: PayerType
    payer_name: str
    plan_name: Optional[str] = None
    effective_date: str
    end_date: Optional[str] = None
    state: Optional[str] = None
    auth_requirements: Dict[str, AuthRequirementRule] = Field(default_factory=dict)
    covered_services: Dict[str, CoveredServiceRule] = Field(default_factory=dict)
    fee_schedule: Dict[str, FeeScheduleEntry] = Field(default_factory=dict)
    formulary: Optional[FormularyPolicy] = None
    step_therapy_protocols: List[StepTherapyProtocol] = Field(default_factory=list)
    quantity_limits: Dict[str, QuantityLimit] = Field(default_factory=dict)
    site_of_service_rules: List[SiteOfServiceRule] = Field(default_factory=list)
    timely_filing_limit_days: int = 90
    appeal_timeline_days: int = 180
    appeal_levels: List[str] = Field(default_factory=list)
    coordination_of_benefits_rules: Optional[dict] = None
    network_requirements: NetworkRequirements = Field(default_factory=lambda: NetworkRequirements(
        requires_in_network=True,
        out_of_network_covered=False,
        out_of_network_penalty_percent=50.0,
        requires_referral=False,
        requires_pcp_selection=False,
        surprise_billing_protection=True,
    ))
    clinical_editing_rules: List[str] = Field(default_factory=list)
    additional_policies: Dict[str, str] = Field(default_factory=dict)


class PayerClaimCheckResult(BaseModel):
    payer_id: str
    payer_name: str
    cpt_code: str
    is_covered: bool
    auth_required: AuthRequirement
    auth_on_file: Optional[bool] = None
    fee_schedule_amount: Optional[float] = None
    member_cost_sharing: Optional[MemberCostSharing] = None
    site_of_service_compliant: bool = True
    timely_filing_compliant: bool = True
    payer_specific_edits_passed: bool = True
    denial_reasons: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    policy_references: List[str] = Field(default_factory=list)
    appeal_guidance: Optional[str] = None
    recommendations: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class PayerPolicyDatabase:
    """In-memory storage of payer policies."""

    def __init__(self) -> None:
        self._policies: Dict[str, PayerPolicy] = {}

    def add_policy(self, policy: PayerPolicy) -> None:
        self._policies[policy.payer_id] = policy

    def get_policy(self, payer_id: str) -> Optional[PayerPolicy]:
        return self._policies.get(payer_id)

    def get_policies_by_type(self, payer_type: PayerType) -> List[PayerPolicy]:
        return [p for p in self._policies.values() if p.payer_type == payer_type]

    def get_all_payer_ids(self) -> List[str]:
        return list(self._policies.keys())

    def get_policy_count(self) -> int:
        return len(self._policies)

    def find_policy(
        self,
        payer_type: PayerType,
        state: Optional[str] = None,
        plan_name: Optional[str] = None,
    ) -> Optional[PayerPolicy]:
        for policy in self._policies.values():
            if policy.payer_type != payer_type:
                continue
            if state and policy.state and policy.state.upper() != state.upper():
                continue
            if plan_name and policy.plan_name and policy.plan_name != plan_name:
                continue
            return policy
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PayerPolicyEngine:
    """Main payer policy engine for claim-line adjudication."""

    def __init__(self, database: Optional[PayerPolicyDatabase] = None) -> None:
        self.database = database or PayerPolicyDatabase()
        if not self.database.get_policy_count():
            seed_payer_policies(self.database)

    # -- Core helpers -----------------------------------------------------

    def _get_policy_or_raise(self, payer_id: str) -> PayerPolicy:
        policy = self.database.get_policy(payer_id)
        if not policy:
            raise ValueError(f"Unknown payer_id: {payer_id}")
        return policy

    def _match_auth_rule(
        self,
        policy: PayerPolicy,
        cpt_code: str,
        service_category: Optional[ServiceCategory],
    ) -> Optional[AuthRequirementRule]:
        if cpt_code in policy.auth_requirements:
            return policy.auth_requirements[cpt_code]
        for rule in policy.auth_requirements.values():
            if "*" in rule.cpt_codes and (not service_category or rule.service_category == service_category):
                return rule
        return None

    def _match_coverage_rule(self, policy: PayerPolicy, cpt_code: str) -> Optional[CoveredServiceRule]:
        if cpt_code in policy.covered_services:
            return policy.covered_services[cpt_code]
        return policy.covered_services.get("*")

    # -- Public API -------------------------------------------------------

    def check_auth_requirement(
        self,
        payer_id: str,
        cpt_code: str,
        service_category: Optional[ServiceCategory] = None,
        is_emergency: bool = False,
    ) -> AuthRequirementRule:
        """Return the auth requirement rule for a CPT code.

        Emergency services bypass prior auth when declared.
        """

        policy = self._get_policy_or_raise(payer_id)
        rule = self._match_auth_rule(policy, cpt_code, service_category)
        if is_emergency:
            return AuthRequirementRule(
                cpt_codes=[cpt_code],
                service_category=ServiceCategory.EMERGENCY,
                auth_requirement=AuthRequirement.NOT_REQUIRED,
                auth_turnaround_standard_days=0,
                auth_turnaround_urgent_hours=0,
                required_clinical_info=["Emergency stabilization"],
                exceptions=[],
            )
        if rule:
            return rule
        return AuthRequirementRule(
            cpt_codes=[cpt_code],
            service_category=service_category,
            auth_requirement=AuthRequirement.NOT_REQUIRED,
            auth_turnaround_standard_days=0,
            auth_turnaround_urgent_hours=0,
            exceptions=[],
            required_clinical_info=[],
        )

    def check_coverage(
        self,
        payer_id: str,
        cpt_code: str,
        icd10_codes: List[str],
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        place_of_service: Optional[str] = None,
    ) -> CoveredServiceRule:
        """Evaluate coverage constraints for a CPT code."""

        policy = self._get_policy_or_raise(payer_id)
        rule = self._match_coverage_rule(policy, cpt_code)
        if not rule:
            return CoveredServiceRule(
                cpt_code=cpt_code,
                is_covered=False,
                coverage_conditions=["Policy not found"],
            )

        # Age check
        age_limits = rule.age_limits
        if age_limits:
            if age_limits.min_age is not None and patient_age is not None and patient_age < age_limits.min_age:
                rule = rule.model_copy(update={"is_covered": False, "exclusions": rule.exclusions + ["Below age limit"]})
            if age_limits.max_age is not None and patient_age is not None and patient_age > age_limits.max_age:
                rule = rule.model_copy(update={"is_covered": False, "exclusions": rule.exclusions + ["Above age limit"]})

        # Gender check
        if rule.gender_limit and patient_gender:
            if rule.gender_limit.upper() not in {"ANY", "BOTH", patient_gender.upper()}:
                rule = rule.model_copy(update={"is_covered": False, "exclusions": rule.exclusions + ["Gender restriction"]})

        # POS check (restrictions are disallowed POS codes)
        if place_of_service and rule.place_of_service_restrictions:
            if place_of_service in rule.place_of_service_restrictions:
                rule = rule.model_copy(update={"is_covered": False, "exclusions": rule.exclusions + ["Place of service restriction"]})

        # Diagnosis requirements
        if rule.diagnosis_requirements:
            match = any(dx.upper() in {d.upper() for d in icd10_codes} for dx in rule.diagnosis_requirements)
            if not match:
                rule = rule.model_copy(update={"is_covered": False, "exclusions": rule.exclusions + ["Diagnosis requirement not met"]})

        return rule

    def get_allowed_amount(
        self,
        payer_id: str,
        cpt_code: str,
        modifier: Optional[str] = None,
        is_facility: bool = False,
    ) -> Optional[float]:
        """Return allowed amount with modifier adjustments."""

        policy = self._get_policy_or_raise(payer_id)
        entry = policy.fee_schedule.get(cpt_code)
        if not entry:
            return None

        base = entry.allowed_amount
        if is_facility and entry.facility_rate is not None:
            base = entry.facility_rate
        elif not is_facility and entry.non_facility_rate is not None:
            base = entry.non_facility_rate

        if modifier and modifier in entry.modifier_adjustments:
            base = base * entry.modifier_adjustments[modifier]
        return round(base, 2)

    def calculate_member_responsibility(
        self,
        payer_id: str,
        cpt_code: str,
        is_in_network: bool,
        deductible_met: bool = False,
    ) -> MemberCostSharing:
        """Compute member cost sharing heuristically using payer type and network."""

        policy = self._get_policy_or_raise(payer_id)
        base_copay = 20.0 if is_in_network else 60.0
        coinsurance = 0.2 if is_in_network else 0.4

        if policy.payer_type == PayerType.MEDICARE:
            base_copay = 0.0
            coinsurance = 0.2
        elif policy.payer_type == PayerType.MEDICAID:
            base_copay = 0.0
            coinsurance = 0.0
        elif policy.payer_type == PayerType.UHC:
            base_copay = 35.0 if is_in_network else 75.0
            coinsurance = 0.25 if is_in_network else 0.45
        elif policy.payer_type == PayerType.AETNA:
            base_copay = 30.0 if is_in_network else 70.0
            coinsurance = 0.2 if is_in_network else 0.5
        elif policy.payer_type == PayerType.BCBS:
            base_copay = 25.0 if is_in_network else 65.0
            coinsurance = 0.2 if is_in_network else 0.5

        deductible_applies = not deductible_met
        return MemberCostSharing(
            copay=base_copay,
            coinsurance_percent=coinsurance * 100,
            deductible_applies=deductible_applies,
            deductible_amount=500.0 if deductible_applies else 0.0,
            out_of_pocket_max=6000.0,
            in_network=is_in_network,
        )

    def check_timely_filing(self, payer_id: str, date_of_service: str, submission_date: str) -> bool:
        """Check if claim was filed within the payer's limit."""

        policy = self._get_policy_or_raise(payer_id)
        dos = datetime.strptime(date_of_service, "%Y-%m-%d")
        sub = datetime.strptime(submission_date, "%Y-%m-%d")
        delta = (sub - dos).days
        return delta <= policy.timely_filing_limit_days

    def check_step_therapy(
        self,
        payer_id: str,
        drug_name: str,
        prior_medications: List[str],
    ) -> dict:
        """Determine whether step therapy prerequisites are met."""

        policy = self._get_policy_or_raise(payer_id)
        prior_set = {p.lower() for p in prior_medications}
        for protocol in policy.step_therapy_protocols:
            if protocol.target_drug.lower() != drug_name.lower():
                continue
            missing = [d for d in protocol.required_first_line if d.lower() not in prior_set]
            return {
                "met": not missing,
                "required_first": protocol.required_first_line,
                "tried": prior_medications,
                "missing": missing,
                "protocol_id": protocol.protocol_id,
            }
        return {"met": True, "required_first": [], "tried": prior_medications, "missing": []}

    def check_quantity_limits(self, payer_id: str, code: str, requested_quantity: int) -> dict:
        """Check requested quantity against payer limit."""

        policy = self._get_policy_or_raise(payer_id)
        limit = policy.quantity_limits.get(code)
        if not limit:
            return {"within_limit": True, "max_allowed": requested_quantity, "override_criteria": []}
        within = requested_quantity <= limit.max_quantity
        return {
            "within_limit": within,
            "max_allowed": limit.max_quantity,
            "override_criteria": limit.override_criteria,
        }

    def get_appeal_info(self, payer_id: str) -> dict:
        """Return appeal information for a payer."""

        policy = self._get_policy_or_raise(payer_id)
        return {
            "appeal_timeline_days": policy.appeal_timeline_days,
            "appeal_levels": policy.appeal_levels,
        }

    def run_payer_claim_check(
        self,
        payer_id: str,
        cpt_code: str,
        icd10_codes: List[str],
        date_of_service: str,
        submission_date: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        place_of_service: Optional[str] = None,
        is_in_network: bool = True,
        auth_on_file: Optional[bool] = None,
    ) -> PayerClaimCheckResult:
        """Comprehensive claim-line evaluation."""

        policy = self._get_policy_or_raise(payer_id)
        denial_reasons: List[str] = []
        warnings: List[str] = []

        coverage_rule = self.check_coverage(payer_id, cpt_code, icd10_codes, patient_age, patient_gender, place_of_service)
        auth_rule = self.check_auth_requirement(payer_id, cpt_code, None, False)
        auth_required = auth_rule.auth_requirement not in {AuthRequirement.NOT_REQUIRED, AuthRequirement.NOTIFICATION_ONLY}

        allowed_amount = self.get_allowed_amount(payer_id, cpt_code, None, place_of_service in {"21", "22", "23"})
        timely_filing_compliant = True
        if submission_date:
            timely_filing_compliant = self.check_timely_filing(payer_id, date_of_service, submission_date)
            if not timely_filing_compliant:
                denial_reasons.append("Timely filing limit exceeded")

        site_of_service_compliant = self._evaluate_site_of_service(policy, cpt_code, place_of_service, denial_reasons)

        network_issue = self._evaluate_network(policy, is_in_network)
        if network_issue:
            denial_reasons.append(network_issue)

        if auth_required and not auth_on_file:
            denial_reasons.append("Prior authorization required")

        if not coverage_rule.is_covered:
            denial_reasons.append("Service not covered under payer policy")

        payer_specific_edits_passed = not policy.clinical_editing_rules or True
        member_cost = self.calculate_member_responsibility(payer_id, cpt_code, is_in_network, deductible_met=False)

        appeal_guidance = self._build_appeal_guidance(policy)
        recommendations = self._build_recommendations(coverage_rule, auth_rule, policy)

        return PayerClaimCheckResult(
            payer_id=policy.payer_id,
            payer_name=policy.payer_name,
            cpt_code=cpt_code,
            is_covered=not denial_reasons,
            auth_required=auth_rule.auth_requirement,
            auth_on_file=auth_on_file,
            fee_schedule_amount=allowed_amount,
            member_cost_sharing=member_cost,
            site_of_service_compliant=site_of_service_compliant,
            timely_filing_compliant=timely_filing_compliant,
            payer_specific_edits_passed=payer_specific_edits_passed,
            denial_reasons=denial_reasons,
            warnings=warnings,
            policy_references=[auth_rule.policy_reference] if auth_rule.policy_reference else [],
            appeal_guidance=appeal_guidance,
            recommendations=recommendations,
        )

    def get_auth_matrix(self, payer_id: str) -> Dict[str, AuthRequirement]:
        """Return CPT-to-auth requirement mapping."""

        policy = self._get_policy_or_raise(payer_id)
        return {code: rule.auth_requirement for code, rule in policy.auth_requirements.items()}

    def compare_payers(self, cpt_code: str, icd10_codes: List[str], payer_ids: List[str]) -> List[dict]:
        """Compare coverage and cost across payers."""

        today = datetime.utcnow().strftime("%Y-%m-%d")
        comparisons: List[dict] = []
        for pid in payer_ids:
            result = self.run_payer_claim_check(
                payer_id=pid,
                cpt_code=cpt_code,
                icd10_codes=icd10_codes,
                date_of_service=today,
                submission_date=today,
                is_in_network=True,
            )
            comparisons.append(result.model_dump())
        return comparisons

    # -- Internal helpers --------------------------------------------------

    def _evaluate_site_of_service(
        self,
        policy: PayerPolicy,
        cpt_code: str,
        place_of_service: Optional[str],
        denial_reasons: List[str],
    ) -> bool:
        if not place_of_service:
            return True
        compliant = True
        for rule in policy.site_of_service_rules:
            if cpt_code not in rule.cpt_codes and "*" not in rule.cpt_codes:
                continue
            if place_of_service in rule.restricted_pos:
                denial_reasons.append("Place of service not allowed")
                compliant = False
            if rule.preferred_pos and place_of_service not in rule.preferred_pos:
                denial_reasons.append("Use preferred site of service")
                compliant = False
        return compliant

    def _evaluate_network(self, policy: PayerPolicy, is_in_network: bool) -> Optional[str]:
        nr = policy.network_requirements
        if not is_in_network and nr.requires_in_network and not nr.out_of_network_covered:
            return "Out-of-network not covered"
        if not is_in_network and nr.out_of_network_penalty_percent:
            return f"Out-of-network penalty {nr.out_of_network_penalty_percent}% applies"
        return None

    def _build_appeal_guidance(self, policy: PayerPolicy) -> str:
        levels = " -> ".join(policy.appeal_levels) if policy.appeal_levels else "Internal review"
        return f"File within {policy.appeal_timeline_days} days. Levels: {levels}."

    def _build_recommendations(
        self,
        coverage_rule: CoveredServiceRule,
        auth_rule: AuthRequirementRule,
        policy: PayerPolicy,
    ) -> List[str]:
        recs: List[str] = []
        if not coverage_rule.is_covered:
            recs.append("Verify diagnosis support or alternative covered service")
        if auth_rule.auth_requirement not in {AuthRequirement.NOT_REQUIRED, AuthRequirement.NOTIFICATION_ONLY}:
            recs.append("Obtain prior authorization before service")
        if policy.timely_filing_limit_days < 120:
            recs.append(f"Submit claims within {policy.timely_filing_limit_days} days of service")
        return recs


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


def seed_payer_policies(database: PayerPolicyDatabase) -> None:
    """Populate the database with baseline payer policies."""

    database.add_policy(_build_medicare_policy())
    database.add_policy(_build_medicaid_policy())
    database.add_policy(_build_uhc_policy())
    database.add_policy(_build_aetna_policy())
    database.add_policy(_build_bcbs_policy())
    database.add_policy(_build_generic_policy())


# -- Payer builders -------------------------------------------------------


def _build_common_auth_rules(
    turnaround_days: int,
    urgent_hours: int,
    retro_hours: Optional[int] = None,
) -> Dict[str, AuthRequirementRule]:
    return {
        "*": AuthRequirementRule(
            cpt_codes=["*"],
            service_category=ServiceCategory.PROCEDURE,
            auth_requirement=AuthRequirement.NOT_REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            exceptions=["Emergency services exempt"],
            required_clinical_info=["Order", "Diagnosis"],
        ),
        "70553": AuthRequirementRule(
            cpt_codes=["70553"],
            service_category=ServiceCategory.IMAGING,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Recent imaging", "Neurologic symptoms"],
        ),
        "73721": AuthRequirementRule(
            cpt_codes=["73721"],
            service_category=ServiceCategory.IMAGING,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Orthopedic evaluation", "Conservative therapy trial"],
        ),
        "27447": AuthRequirementRule(
            cpt_codes=["27447"],
            service_category=ServiceCategory.PROCEDURE,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Radiographic OA", "Failed conservative management"],
        ),
        "27130": AuthRequirementRule(
            cpt_codes=["27130"],
            service_category=ServiceCategory.PROCEDURE,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Hip OA", "Function limitation"],
        ),
        "95811": AuthRequirementRule(
            cpt_codes=["95811"],
            service_category=ServiceCategory.IMAGING,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Sleep study questionnaire"],
        ),
        "93015": AuthRequirementRule(
            cpt_codes=["93015"],
            service_category=ServiceCategory.IMAGING,
            auth_requirement=AuthRequirement.NOTIFICATION_ONLY,
            auth_turnaround_standard_days=0,
            auth_turnaround_urgent_hours=0,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Indication", "Prior EKG"],
        ),
        "E0601": AuthRequirementRule(
            cpt_codes=["E0601"],
            service_category=ServiceCategory.DME,
            auth_requirement=AuthRequirement.REQUIRED,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Sleep apnea diagnosis", "AHI results"],
        ),
        "99223": AuthRequirementRule(
            cpt_codes=["99223"],
            service_category=ServiceCategory.INPATIENT,
            auth_requirement=AuthRequirement.VARIES,
            auth_turnaround_standard_days=turnaround_days,
            auth_turnaround_urgent_hours=urgent_hours,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=["Admission order"],
        ),
        "G0101": AuthRequirementRule(
            cpt_codes=["G0101"],
            service_category=ServiceCategory.PREVENTIVE,
            auth_requirement=AuthRequirement.NOT_REQUIRED,
            auth_turnaround_standard_days=0,
            auth_turnaround_urgent_hours=0,
            retro_auth_window_hours=retro_hours,
            required_clinical_info=[],
        ),
    }


def _build_common_coverage() -> Dict[str, CoveredServiceRule]:
    return {
        "70553": CoveredServiceRule(
            cpt_code="70553",
            is_covered=True,
            coverage_conditions=["Medically necessary neuro MRI"],
            diagnosis_requirements=["G40.909", "R51.9"],
            place_of_service_restrictions=["23"],
        ),
        "73721": CoveredServiceRule(
            cpt_code="73721",
            is_covered=True,
            coverage_conditions=["Failure of conservative therapy"],
            diagnosis_requirements=["M17.11", "M17.12"],
            place_of_service_restrictions=["11", "22"],
        ),
        "27447": CoveredServiceRule(
            cpt_code="27447",
            is_covered=True,
            coverage_conditions=["Severe OA"],
            diagnosis_requirements=["M17.0", "M17.9"],
            age_limits=AgeLimit(min_age=40),
        ),
        "27130": CoveredServiceRule(
            cpt_code="27130",
            is_covered=True,
            coverage_conditions=["End-stage hip OA"],
            diagnosis_requirements=["M16.0", "M16.9"],
            age_limits=AgeLimit(min_age=40),
        ),
        "95811": CoveredServiceRule(
            cpt_code="95811",
            is_covered=True,
            coverage_conditions=["Sleep apnea evaluation"],
            diagnosis_requirements=["G47.33"],
            place_of_service_restrictions=["22", "49"],
        ),
        "93015": CoveredServiceRule(
            cpt_code="93015",
            is_covered=True,
            coverage_conditions=["Chest pain evaluation"],
            diagnosis_requirements=["R07.9", "I20.9"],
        ),
        "E0601": CoveredServiceRule(
            cpt_code="E0601",
            is_covered=True,
            coverage_conditions=["CPAP for OSA"],
            diagnosis_requirements=["G47.33"],
        ),
        "99213": CoveredServiceRule(
            cpt_code="99213",
            is_covered=True,
            coverage_conditions=["Office visit"],
        ),
        "99214": CoveredServiceRule(
            cpt_code="99214",
            is_covered=True,
            coverage_conditions=["Office visit"],
        ),
        "G0101": CoveredServiceRule(
            cpt_code="G0101",
            is_covered=True,
            coverage_conditions=["Preventive"],
            gender_limit="FEMALE",
        ),
        "*": CoveredServiceRule(
            cpt_code="*",
            is_covered=False,
            coverage_conditions=["Default non-covered"],
        ),
    }


def _build_fee_schedule(multiplier: float) -> Dict[str, FeeScheduleEntry]:
    base_rates = {
        "70553": 450.0,
        "73721": 380.0,
        "27447": 1600.0,
        "27130": 1800.0,
        "95811": 900.0,
        "93015": 200.0,
        "E0601": 780.0,
        "99213": 90.0,
        "99214": 135.0,
        "G0101": 70.0,
    }
    schedule: Dict[str, FeeScheduleEntry] = {}
    for code, amt in base_rates.items():
        schedule[code] = FeeScheduleEntry(
            cpt_code=code,
            allowed_amount=round(amt * multiplier, 2),
            facility_rate=round(amt * multiplier * 0.95, 2),
            non_facility_rate=round(amt * multiplier * 1.05, 2),
            modifier_adjustments={"26": 0.4, "TC": 0.6},
        )
    return schedule


def _build_site_rules() -> List[SiteOfServiceRule]:
    return [
        SiteOfServiceRule(cpt_codes=["27447", "27130"], preferred_pos=["21"], restricted_pos=["11"], differential_payment="50% reduction if outpatient hospital"),
        SiteOfServiceRule(cpt_codes=["70553", "73721"], preferred_pos=["11", "22"], restricted_pos=["23"], differential_payment="10% reduction in hospital"),
        SiteOfServiceRule(cpt_codes=["99213", "99214"], preferred_pos=["11", "02"], restricted_pos=[], differential_payment=None),
    ]


def _build_quantity_limits() -> Dict[str, QuantityLimit]:
    return {
        "E0601": QuantityLimit(code="E0601", max_quantity=1, time_period="per_year", override_criteria=["Replacement after 5 years"]),
        "95811": QuantityLimit(code="95811", max_quantity=1, time_period="per_year", override_criteria=["Medical director approval"]),
        "93015": QuantityLimit(code="93015", max_quantity=2, time_period="per_year", override_criteria=["Cardiology documentation"]),
        "70553": QuantityLimit(code="70553", max_quantity=3, time_period="per_year", override_criteria=["Progression documentation"]),
        "73721": QuantityLimit(code="73721", max_quantity=2, time_period="per_year", override_criteria=["Orthopedic note"]),
    }


def _build_step_therapy(prefix: str) -> List[StepTherapyProtocol]:
    return [
        StepTherapyProtocol(
            protocol_id=f"{prefix}-ST-001",
            target_drug="Adalimumab",
            required_first_line=["Methotrexate", "Sulfasalazine"],
            required_duration_days=56,
            required_failure_documentation=["Disease activity", "Dose and duration"],
            exceptions=["Contraindication to MTX"],
        ),
        StepTherapyProtocol(
            protocol_id=f"{prefix}-ST-002",
            target_drug="Etanercept",
            required_first_line=["Methotrexate"],
            required_duration_days=56,
            required_failure_documentation=["TNF trial notes"],
            exceptions=["Heart failure"],
        ),
        StepTherapyProtocol(
            protocol_id=f"{prefix}-ST-003",
            target_drug="Ozempic",
            required_first_line=["Metformin"],
            required_duration_days=90,
            required_failure_documentation=["A1c logs"],
            exceptions=["Metformin intolerance"],
        ),
    ]


def _build_formulary() -> FormularyPolicy:
    return FormularyPolicy(
        tiers={
            1: FormularyTier(tier_number=1, tier_name="Generic", copay=10.0, coinsurance_percent=0.0, requires_prior_auth=False, requires_step_therapy=False),
            2: FormularyTier(tier_number=2, tier_name="Preferred Brand", copay=35.0, coinsurance_percent=20.0, requires_prior_auth=False, requires_step_therapy=False),
            3: FormularyTier(tier_number=3, tier_name="Non-Preferred", copay=60.0, coinsurance_percent=40.0, requires_prior_auth=True, requires_step_therapy=True),
            4: FormularyTier(tier_number=4, tier_name="Specialty", copay=None, coinsurance_percent=30.0, requires_prior_auth=True, requires_step_therapy=True, quantity_limit="per_month"),
        },
        preferred_drugs={"Metformin": 1, "Atorvastatin": 1, "Lisinopril": 1},
        non_preferred_drugs={"Ozempic": 3},
        specialty_drugs={"Adalimumab": 4, "Etanercept": 4},
        excluded_drugs=["Cosmetic fillers"],
    )


def _build_medicare_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="MEDICARE",
        payer_type=PayerType.MEDICARE,
        payer_name="Medicare (CMS)",
        effective_date="2020-01-01",
        auth_requirements=_build_common_auth_rules(turnaround_days=14, urgent_hours=72, retro_hours=None),
        covered_services=_build_common_coverage(),
        fee_schedule=_build_fee_schedule(multiplier=1.0),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("MED"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=365,
        appeal_timeline_days=120,
        appeal_levels=["Redetermination", "Reconsideration (QIC)", "ALJ", "Medicare Appeals Council", "Federal Court"],
        network_requirements=NetworkRequirements(
            requires_in_network=False,
            out_of_network_covered=True,
            out_of_network_penalty_percent=0.0,
            requires_referral=False,
            requires_pcp_selection=False,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["NCCI applies", "Frequency edits"],
        additional_policies={"Assignment": "Accepts assignment rules"},
    )


def _build_medicaid_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="MEDICAID_STATE",
        payer_type=PayerType.MEDICAID,
        payer_name="Medicaid State Plan",
        plan_name="Standard Medicaid",
        state="CA",
        effective_date="2020-01-01",
        auth_requirements=_build_common_auth_rules(turnaround_days=10, urgent_hours=48, retro_hours=24),
        covered_services=_build_common_coverage(),
        fee_schedule=_build_fee_schedule(multiplier=0.7),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("MCD"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=180,
        appeal_timeline_days=90,
        appeal_levels=["Fair Hearing", "State review"],
        network_requirements=NetworkRequirements(
            requires_in_network=True,
            out_of_network_covered=False,
            out_of_network_penalty_percent=100.0,
            requires_referral=True,
            requires_pcp_selection=True,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["Utilization controls", "PA for many services"],
        additional_policies={"EPSDT": "Early and Periodic Screening, Diagnostic, and Treatment"},
    )


def _build_uhc_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="UHC_COMMERCIAL",
        payer_type=PayerType.UHC,
        payer_name="UnitedHealthcare",
        plan_name="Choice Plus",
        effective_date="2020-01-01",
        auth_requirements=_build_common_auth_rules(turnaround_days=15, urgent_hours=72, retro_hours=48),
        covered_services=_build_common_coverage(),
        fee_schedule=_build_fee_schedule(multiplier=1.25),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("UHC"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=90,
        appeal_timeline_days=180,
        appeal_levels=["Internal appeal", "External review", "State insurance commissioner"],
        network_requirements=NetworkRequirements(
            requires_in_network=True,
            out_of_network_covered=True,
            out_of_network_penalty_percent=40.0,
            requires_referral=False,
            requires_pcp_selection=False,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["Site of service reduction", "High-tech imaging review"],
        additional_policies={"Retro auth": "48 hours after emergency"},
    )


def _build_aetna_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="AETNA_COMMERCIAL",
        payer_type=PayerType.AETNA,
        payer_name="Aetna",
        plan_name="Open Access",
        effective_date="2020-01-01",
        auth_requirements=_build_common_auth_rules(turnaround_days=14, urgent_hours=24, retro_hours=72),
        covered_services=_build_common_coverage(),
        fee_schedule=_build_fee_schedule(multiplier=1.15),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("AET"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=90,
        appeal_timeline_days=180,
        appeal_levels=["Internal appeal", "External review"],
        network_requirements=NetworkRequirements(
            requires_in_network=True,
            out_of_network_covered=False,
            out_of_network_penalty_percent=100.0,
            requires_referral=False,
            requires_pcp_selection=False,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["Precertification for elective surgery"],
        additional_policies={"Retro auth": "72 hours"},
    )


def _build_bcbs_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="BCBS_STATE",
        payer_type=PayerType.BCBS,
        payer_name="Blue Cross Blue Shield",
        plan_name="PPO",
        state="TX",
        effective_date="2020-01-01",
        auth_requirements=_build_common_auth_rules(turnaround_days=14, urgent_hours=48, retro_hours=48),
        covered_services=_build_common_coverage(),
        fee_schedule=_build_fee_schedule(multiplier=1.1),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("BCBS"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=120,
        appeal_timeline_days=180,
        appeal_levels=["Internal appeal", "External review", "State insurance commissioner"],
        network_requirements=NetworkRequirements(
            requires_in_network=True,
            out_of_network_covered=True,
            out_of_network_penalty_percent=50.0,
            requires_referral=False,
            requires_pcp_selection=False,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["High-cost procedure review"],
        additional_policies={"Preauth": "Certain imaging"},
    )


def _build_generic_policy() -> PayerPolicy:
    return PayerPolicy(
        payer_id="GENERIC",
        payer_type=PayerType.GENERIC,
        payer_name="Generic Payer",
        plan_name="Default",
        effective_date="2020-01-01",
        auth_requirements={
            "*": AuthRequirementRule(
                cpt_codes=["*"],
                service_category=ServiceCategory.PROCEDURE,
                auth_requirement=AuthRequirement.REQUIRED,
                auth_turnaround_standard_days=10,
                auth_turnaround_urgent_hours=48,
                retro_auth_window_hours=24,
                required_clinical_info=["Order", "Diagnosis", "Clinical rationale"],
                exceptions=["Emergency"],
            )
        },
        covered_services={"*": CoveredServiceRule(cpt_code="*", is_covered=False, coverage_conditions=["Manual review"])} ,
        fee_schedule=_build_fee_schedule(multiplier=0.9),
        formulary=_build_formulary(),
        step_therapy_protocols=_build_step_therapy("GEN"),
        quantity_limits=_build_quantity_limits(),
        site_of_service_rules=_build_site_rules(),
        timely_filing_limit_days=90,
        appeal_timeline_days=180,
        appeal_levels=["Internal review"],
        network_requirements=NetworkRequirements(
            requires_in_network=True,
            out_of_network_covered=False,
            out_of_network_penalty_percent=100.0,
            requires_referral=True,
            requires_pcp_selection=True,
            surprise_billing_protection=True,
        ),
        clinical_editing_rules=["Most restrictive rules"],
        additional_policies={"Fallback": "Use most restrictive interpretation"},
    )
