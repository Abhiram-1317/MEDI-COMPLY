"""Mental Health Parity checker for MEDI-COMPLY.

Implements MHPAEA (Mental Health Parity and Addiction Equity Act) parity checks
between mental health/substance use disorder (MH/SUD) benefits and medical/
surgical (M/S) benefits. Provides service classification, benefit comparison,
parity violation modeling, and reporting utilities for claims adjudication and
plan-level audits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ServiceClassification(str, Enum):
    """Classification of a service for parity comparison."""

    MEDICAL_SURGICAL = "MEDICAL_SURGICAL"
    MENTAL_HEALTH = "MENTAL_HEALTH"
    SUBSTANCE_USE_DISORDER = "SUBSTANCE_USE_DISORDER"
    MH_SUD = "MH_SUD"
    UNCLASSIFIED = "UNCLASSIFIED"


class BenefitCategory(str, Enum):
    """Benefit categories subject to parity checks."""

    INPATIENT_IN_NETWORK = "INPATIENT_IN_NETWORK"
    INPATIENT_OUT_OF_NETWORK = "INPATIENT_OUT_OF_NETWORK"
    OUTPATIENT_IN_NETWORK = "OUTPATIENT_IN_NETWORK"
    OUTPATIENT_OUT_OF_NETWORK = "OUTPATIENT_OUT_OF_NETWORK"
    EMERGENCY = "EMERGENCY"
    PRESCRIPTION_DRUGS = "PRESCRIPTION_DRUGS"


class LimitationType(str, Enum):
    """Types of limitations considered for parity."""

    FINANCIAL_COPAY = "FINANCIAL_COPAY"
    FINANCIAL_COINSURANCE = "FINANCIAL_COINSURANCE"
    FINANCIAL_DEDUCTIBLE = "FINANCIAL_DEDUCTIBLE"
    FINANCIAL_OUT_OF_POCKET_MAX = "FINANCIAL_OUT_OF_POCKET_MAX"
    QUANTITATIVE_VISIT_LIMIT = "QUANTITATIVE_VISIT_LIMIT"
    QUANTITATIVE_DAY_LIMIT = "QUANTITATIVE_DAY_LIMIT"
    QUANTITATIVE_EPISODE_LIMIT = "QUANTITATIVE_EPISODE_LIMIT"
    NON_QUANTITATIVE_PRIOR_AUTH = "NON_QUANTITATIVE_PRIOR_AUTH"
    NON_QUANTITATIVE_MEDICAL_NECESSITY = "NON_QUANTITATIVE_MEDICAL_NECESSITY"
    NON_QUANTITATIVE_PROVIDER_NETWORK = "NON_QUANTITATIVE_PROVIDER_NETWORK"
    NON_QUANTITATIVE_STEP_THERAPY = "NON_QUANTITATIVE_STEP_THERAPY"
    NON_QUANTITATIVE_FORMULARY = "NON_QUANTITATIVE_FORMULARY"
    NON_QUANTITATIVE_CONCURRENT_REVIEW = "NON_QUANTITATIVE_CONCURRENT_REVIEW"
    NON_QUANTITATIVE_REIMBURSEMENT_RATES = "NON_QUANTITATIVE_REIMBURSEMENT_RATES"


class ParityViolationType(str, Enum):
    """Parity violation categories."""

    FINANCIAL_MORE_RESTRICTIVE = "FINANCIAL_MORE_RESTRICTIVE"
    VISIT_LIMIT_MORE_RESTRICTIVE = "VISIT_LIMIT_MORE_RESTRICTIVE"
    DAY_LIMIT_MORE_RESTRICTIVE = "DAY_LIMIT_MORE_RESTRICTIVE"
    PRIOR_AUTH_MORE_RESTRICTIVE = "PRIOR_AUTH_MORE_RESTRICTIVE"
    MEDICAL_NECESSITY_MORE_RESTRICTIVE = "MEDICAL_NECESSITY_MORE_RESTRICTIVE"
    NETWORK_INADEQUATE = "NETWORK_INADEQUATE"
    STEP_THERAPY_MORE_RESTRICTIVE = "STEP_THERAPY_MORE_RESTRICTIVE"
    REIMBURSEMENT_RATE_LOWER = "REIMBURSEMENT_RATE_LOWER"
    SEPARATE_DEDUCTIBLE = "SEPARATE_DEDUCTIBLE"
    SEPARATE_OOP_MAX = "SEPARATE_OOP_MAX"
    EXCLUDED_CONDITION = "EXCLUDED_CONDITION"
    SCOPE_LIMITATION = "SCOPE_LIMITATION"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class BenefitComparison(BaseModel):
    """Comparison of MH/SUD versus M/S for a specific limitation."""

    benefit_category: BenefitCategory
    limitation_type: LimitationType
    medical_surgical_value: Optional[float] = None
    mh_sud_value: Optional[float] = None
    medical_surgical_description: str
    mh_sud_description: str
    is_parity_compliant: bool
    violation_type: Optional[ParityViolationType] = None
    disparity_amount: Optional[float] = None
    disparity_percentage: Optional[float] = None


class ParityViolation(BaseModel):
    """Represents a parity violation detected during comparison."""

    violation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    violation_type: ParityViolationType
    severity: str
    description: str
    medical_surgical_benchmark: str
    mh_sud_restriction: str
    remediation: str
    regulatory_reference: str
    financial_impact: Optional[str] = None


class ParityCheckResult(BaseModel):
    """Aggregate result of a parity check for a claim/service."""

    check_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payer_id: str
    plan_name: Optional[str] = None
    service_checked: str
    service_classification: ServiceClassification
    benefit_category: BenefitCategory
    is_parity_compliant: bool
    comparisons: List[BenefitComparison] = Field(default_factory=list)
    violations: List[ParityViolation] = Field(default_factory=list)
    total_violations: int = 0
    risk_level: str = "LOW"
    recommendations: List[str] = Field(default_factory=list)
    regulatory_references: List[str] = Field(default_factory=list)
    summary: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class BenefitDetail(BaseModel):
    """Plan benefit detail for a benefit category."""

    benefit_category: BenefitCategory
    copay: Optional[float] = None
    coinsurance_percent: Optional[float] = None
    deductible: Optional[float] = None
    out_of_pocket_max: Optional[float] = None
    visit_limit_annual: Optional[int] = None
    visit_limit_lifetime: Optional[int] = None
    day_limit_annual: Optional[int] = None
    requires_prior_auth: bool = False
    prior_auth_turnaround_days: Optional[int] = None
    requires_step_therapy: bool = False
    medical_necessity_review: bool = False
    concurrent_review_frequency: Optional[str] = None
    provider_network_size: Optional[int] = None
    reimbursement_rate_percent_of_medicare: Optional[float] = None


class PlanBenefits(BaseModel):
    """Plan-level benefits for both M/S and MH/SUD."""

    payer_id: str
    plan_name: str
    medical_surgical_benefits: Dict[str, BenefitDetail]
    mh_sud_benefits: Dict[str, BenefitDetail]
    has_separate_mh_deductible: bool = False
    has_separate_mh_oop_max: bool = False
    excluded_mh_conditions: List[str] = Field(default_factory=list)
    excluded_sud_conditions: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Comparable service mappings
# ---------------------------------------------------------------------------


COMPARABLE_SERVICES: Dict[str, str] = {
    "90834": "99214",
    "90837": "99215",
    "90847": "99215",
    "90832": "99213",
    "90791": "99205",
    "90792": "99205",
    "96130": "95816",
    "96131": "95816",
    "96132": "93000",
    "96133": "93000",
    "96156": "99213",
    "96157": "99214",
    "96158": "99214",
    "96159": "99214",
    "96164": "99214",
    "96165": "99214",
    "96167": "99214",
    "96168": "99214",
    "H0015": "G0420",
    "H0020": "G0436",
}


# ---------------------------------------------------------------------------
# Service Classifier
# ---------------------------------------------------------------------------


class ServiceClassifier:
    """Classifies services as MH/SUD or Medical/Surgical for parity checks."""

    def classify_by_cpt(self, cpt_code: str) -> ServiceClassification:
        code = (cpt_code or "").upper()
        if not code:
            return ServiceClassification.UNCLASSIFIED
        if self._is_substance_use_cpt(code):
            return ServiceClassification.SUBSTANCE_USE_DISORDER
        if self._is_mental_health_cpt(code):
            return ServiceClassification.MENTAL_HEALTH
        return ServiceClassification.MEDICAL_SURGICAL

    def classify_by_icd10(self, icd10_code: str) -> ServiceClassification:
        code = (icd10_code or "").upper()
        if not code:
            return ServiceClassification.UNCLASSIFIED
        if self._is_substance_use_icd10(code):
            return ServiceClassification.SUBSTANCE_USE_DISORDER
        if self._is_mental_health_icd10(code):
            return ServiceClassification.MENTAL_HEALTH
        return ServiceClassification.MEDICAL_SURGICAL

    def classify_service(
        self,
        cpt_code: Optional[str] = None,
        icd10_codes: Optional[List[str]] = None,
        service_description: Optional[str] = None,
    ) -> ServiceClassification:
        cpt_class = self.classify_by_cpt(cpt_code) if cpt_code else ServiceClassification.UNCLASSIFIED
        icd_codes = icd10_codes or []
        icd_classes = [self.classify_by_icd10(code) for code in icd_codes if code]
        description_text = (service_description or "").lower()

        if cpt_class in {ServiceClassification.MENTAL_HEALTH, ServiceClassification.SUBSTANCE_USE_DISORDER}:
            return ServiceClassification.MH_SUD
        if icd_classes and any(cls in {ServiceClassification.MENTAL_HEALTH, ServiceClassification.SUBSTANCE_USE_DISORDER} for cls in icd_classes):
            return ServiceClassification.MH_SUD
        if "therapy" in description_text or "psych" in description_text:
            return ServiceClassification.MH_SUD
        if cpt_code and cpt_class == ServiceClassification.MEDICAL_SURGICAL:
            if not cpt_code.isdigit() and not cpt_code[:1].isdigit():
                return ServiceClassification.UNCLASSIFIED
        return ServiceClassification.MEDICAL_SURGICAL

    def _is_mental_health_cpt(self, cpt_code: str) -> bool:
        if cpt_code.startswith("H"):
            return False
        try:
            numeric = int(cpt_code[:5])
        except ValueError:
            numeric = None
        if numeric and 90785 <= numeric <= 90899:
            return True
        if numeric and 90901 <= numeric <= 90911:
            return True
        if numeric and 96130 <= numeric <= 96139:
            return True
        if numeric and 96156 <= numeric <= 96171:
            return True
        return False

    def _is_substance_use_cpt(self, cpt_code: str) -> bool:
        code = cpt_code.upper()
        try:
            numeric = int(code[:5])
        except ValueError:
            numeric = None
        if numeric and 99408 <= numeric <= 99409:
            return True
        if code.startswith("H"):
            try:
                h_val = int(code[1:])
                return 1 <= h_val <= 50
            except ValueError:
                return False
        return False

    def _is_mental_health_icd10(self, icd10_code: str) -> bool:
        code = icd10_code.upper()
        if not (code.startswith("F") and len(code) >= 3):
            return False
        try:
            prefix = int(code[1:3])
        except ValueError:
            return False
        return 1 <= prefix <= 99

    def _is_substance_use_icd10(self, icd10_code: str) -> bool:
        code = icd10_code.upper()
        if not (code.startswith("F1") and len(code) >= 3):
            return False
        try:
            suffix = int(code[2:3])
        except ValueError:
            return False
        return 0 <= suffix <= 9


# ---------------------------------------------------------------------------
# Parity Comparator
# ---------------------------------------------------------------------------


class ParityComparator:
    """Compares MH/SUD benefits with M/S benefits for parity compliance."""

    def compare_financial_requirements(
        self,
        ms_benefits: BenefitDetail,
        mh_sud_benefits: BenefitDetail,
        benefit_category: BenefitCategory,
        has_separate_mh_deductible: bool = False,
        has_separate_mh_oop: bool = False,
    ) -> List[BenefitComparison]:
        comparisons: List[BenefitComparison] = []
        comparisons.extend(
            self._compare_financial_pair(
                ms_benefits.copay,
                mh_sud_benefits.copay,
                LimitationType.FINANCIAL_COPAY,
                benefit_category,
                "Copay",
                "Copay",
            )
        )
        comparisons.extend(
            self._compare_financial_pair(
                ms_benefits.coinsurance_percent,
                mh_sud_benefits.coinsurance_percent,
                LimitationType.FINANCIAL_COINSURANCE,
                benefit_category,
                "Coinsurance",
                "Coinsurance",
            )
        )
        comparisons.extend(
            self._compare_financial_pair(
                ms_benefits.deductible,
                mh_sud_benefits.deductible,
                LimitationType.FINANCIAL_DEDUCTIBLE,
                benefit_category,
                "Deductible",
                "Deductible",
            )
        )
        comparisons.extend(
            self._compare_financial_pair(
                ms_benefits.out_of_pocket_max,
                mh_sud_benefits.out_of_pocket_max,
                LimitationType.FINANCIAL_OUT_OF_POCKET_MAX,
                benefit_category,
                "Out-of-pocket max",
                "Out-of-pocket max",
            )
        )

        if has_separate_mh_deductible:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.FINANCIAL_DEDUCTIBLE,
                    medical_surgical_value=ms_benefits.deductible,
                    mh_sud_value=mh_sud_benefits.deductible,
                    medical_surgical_description="Combined deductible",
                    mh_sud_description="Separate MH/SUD deductible",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.SEPARATE_DEDUCTIBLE,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )
        if has_separate_mh_oop:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.FINANCIAL_OUT_OF_POCKET_MAX,
                    medical_surgical_value=ms_benefits.out_of_pocket_max,
                    mh_sud_value=mh_sud_benefits.out_of_pocket_max,
                    medical_surgical_description="Combined out-of-pocket max",
                    mh_sud_description="Separate MH/SUD out-of-pocket max",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.SEPARATE_OOP_MAX,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )

        return comparisons

    def compare_quantitative_limits(
        self,
        ms_benefits: BenefitDetail,
        mh_sud_benefits: BenefitDetail,
        benefit_category: BenefitCategory,
    ) -> List[BenefitComparison]:
        comparisons: List[BenefitComparison] = []

        def compare_limit(ms_val: Optional[int], mh_val: Optional[int], limitation: LimitationType, label: str, violation: ParityViolationType) -> None:
            more_restrictive = self._is_more_restrictive(ms_val, mh_val, higher_is_worse=False)
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=limitation,
                    medical_surgical_value=ms_val if ms_val is None else float(ms_val),
                    mh_sud_value=mh_val if mh_val is None else float(mh_val),
                    medical_surgical_description=f"M/S {label}: {'No limit' if ms_val is None else ms_val}",
                    mh_sud_description=f"MH/SUD {label}: {'No limit' if mh_val is None else mh_val}",
                    is_parity_compliant=not more_restrictive,
                    violation_type=violation if more_restrictive else None,
                    disparity_amount=None if ms_val is None or mh_val is None else float(ms_val - mh_val),
                    disparity_percentage=None,
                )
            )

        compare_limit(ms_benefits.visit_limit_annual, mh_sud_benefits.visit_limit_annual, LimitationType.QUANTITATIVE_VISIT_LIMIT, "annual visit limit", ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE)
        compare_limit(ms_benefits.visit_limit_lifetime, mh_sud_benefits.visit_limit_lifetime, LimitationType.QUANTITATIVE_VISIT_LIMIT, "lifetime visit limit", ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE)
        compare_limit(ms_benefits.day_limit_annual, mh_sud_benefits.day_limit_annual, LimitationType.QUANTITATIVE_DAY_LIMIT, "annual day limit", ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE)

        return comparisons

    def compare_non_quantitative_limits(
        self,
        ms_benefits: BenefitDetail,
        mh_sud_benefits: BenefitDetail,
        benefit_category: BenefitCategory,
    ) -> List[BenefitComparison]:
        comparisons: List[BenefitComparison] = []

        # Prior authorization
        if mh_sud_benefits.requires_prior_auth and not ms_benefits.requires_prior_auth:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.NON_QUANTITATIVE_PRIOR_AUTH,
                    medical_surgical_value=0.0,
                    mh_sud_value=1.0,
                    medical_surgical_description="No prior auth required",
                    mh_sud_description="Prior auth required",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )
        elif mh_sud_benefits.requires_prior_auth and ms_benefits.requires_prior_auth:
            ms_turnaround = ms_benefits.prior_auth_turnaround_days or 0
            mh_turnaround = mh_sud_benefits.prior_auth_turnaround_days or 0
            more_restrictive = mh_turnaround > ms_turnaround and ms_turnaround > 0
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.NON_QUANTITATIVE_PRIOR_AUTH,
                    medical_surgical_value=float(ms_turnaround) if ms_turnaround else None,
                    mh_sud_value=float(mh_turnaround) if mh_turnaround else None,
                    medical_surgical_description=f"Prior auth turnaround {ms_turnaround} days" if ms_turnaround else "Prior auth required",
                    mh_sud_description=f"Prior auth turnaround {mh_turnaround} days" if mh_turnaround else "Prior auth required",
                    is_parity_compliant=not more_restrictive,
                    violation_type=ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE if more_restrictive else None,
                    disparity_amount=float(mh_turnaround - ms_turnaround) if more_restrictive else None,
                    disparity_percentage=None,
                )
            )

        # Step therapy
        if mh_sud_benefits.requires_step_therapy and not ms_benefits.requires_step_therapy:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.NON_QUANTITATIVE_STEP_THERAPY,
                    medical_surgical_value=0.0,
                    mh_sud_value=1.0,
                    medical_surgical_description="No step therapy",
                    mh_sud_description="Step therapy required",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.STEP_THERAPY_MORE_RESTRICTIVE,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )

        # Medical necessity
        if mh_sud_benefits.medical_necessity_review and not ms_benefits.medical_necessity_review:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.NON_QUANTITATIVE_MEDICAL_NECESSITY,
                    medical_surgical_value=0.0,
                    mh_sud_value=1.0,
                    medical_surgical_description="No additional medical necessity review",
                    mh_sud_description="Medical necessity review required",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.MEDICAL_NECESSITY_MORE_RESTRICTIVE,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )

        # Concurrent review frequency heuristic: if mh has value and ms none or shorter interval
        if mh_sud_benefits.concurrent_review_frequency and not ms_benefits.concurrent_review_frequency:
            comparisons.append(
                BenefitComparison(
                    benefit_category=benefit_category,
                    limitation_type=LimitationType.NON_QUANTITATIVE_CONCURRENT_REVIEW,
                    medical_surgical_value=None,
                    mh_sud_value=1.0,
                    medical_surgical_description="No concurrent review schedule",
                    mh_sud_description=f"Concurrent review: {mh_sud_benefits.concurrent_review_frequency}",
                    is_parity_compliant=False,
                    violation_type=ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE,
                    disparity_amount=None,
                    disparity_percentage=None,
                )
            )

        # Network adequacy / reimbursement rate heuristics
        if mh_sud_benefits.provider_network_size and ms_benefits.provider_network_size:
            ms_size = ms_benefits.provider_network_size
            mh_size = mh_sud_benefits.provider_network_size
            if self._is_more_restrictive(ms_size, mh_size, higher_is_worse=False):
                comparisons.append(
                    BenefitComparison(
                        benefit_category=benefit_category,
                        limitation_type=LimitationType.NON_QUANTITATIVE_PROVIDER_NETWORK,
                        medical_surgical_value=float(ms_size),
                        mh_sud_value=float(mh_size),
                        medical_surgical_description=f"Network size {ms_size}",
                        mh_sud_description=f"Network size {mh_size}",
                        is_parity_compliant=False,
                        violation_type=ParityViolationType.NETWORK_INADEQUATE,
                        disparity_amount=float(ms_size - mh_size),
                        disparity_percentage=float((ms_size - mh_size) / ms_size) if ms_size else None,
                    )
                )
        if mh_sud_benefits.reimbursement_rate_percent_of_medicare and ms_benefits.reimbursement_rate_percent_of_medicare:
            ms_rate = ms_benefits.reimbursement_rate_percent_of_medicare
            mh_rate = mh_sud_benefits.reimbursement_rate_percent_of_medicare
            if self._is_more_restrictive(ms_rate, mh_rate, higher_is_worse=False):
                comparisons.append(
                    BenefitComparison(
                        benefit_category=benefit_category,
                        limitation_type=LimitationType.NON_QUANTITATIVE_REIMBURSEMENT_RATES,
                        medical_surgical_value=float(ms_rate),
                        mh_sud_value=float(mh_rate),
                        medical_surgical_description=f"Reimburses {ms_rate}% of Medicare",
                        mh_sud_description=f"Reimburses {mh_rate}% of Medicare",
                        is_parity_compliant=False,
                        violation_type=ParityViolationType.REIMBURSEMENT_RATE_LOWER,
                        disparity_amount=float(ms_rate - mh_rate),
                        disparity_percentage=float((ms_rate - mh_rate) / ms_rate) if ms_rate else None,
                    )
                )

        return comparisons

    def _compare_financial_pair(
        self,
        ms_value: Optional[float],
        mh_value: Optional[float],
        limitation: LimitationType,
        benefit_category: BenefitCategory,
        ms_label: str,
        mh_label: str,
    ) -> List[BenefitComparison]:
        comparisons: List[BenefitComparison] = []
        more_restrictive = self._is_more_restrictive(ms_value, mh_value, higher_is_worse=True)
        disparity_amount = None
        disparity_percentage = None
        if more_restrictive and ms_value is not None and mh_value is not None:
            disparity_amount = mh_value - ms_value
            disparity_percentage = (disparity_amount / ms_value) if ms_value else None

        comparisons.append(
            BenefitComparison(
                benefit_category=benefit_category,
                limitation_type=limitation,
                medical_surgical_value=ms_value,
                mh_sud_value=mh_value,
                medical_surgical_description=f"M/S {ms_label}: {ms_value if ms_value is not None else 'N/A'}",
                mh_sud_description=f"MH/SUD {mh_label}: {mh_value if mh_value is not None else 'N/A'}",
                is_parity_compliant=not more_restrictive,
                violation_type=ParityViolationType.FINANCIAL_MORE_RESTRICTIVE if more_restrictive else None,
                disparity_amount=disparity_amount,
                disparity_percentage=disparity_percentage,
            )
        )
        return comparisons

    def _is_more_restrictive(self, ms_value: Optional[float], mh_value: Optional[float], higher_is_worse: bool = True) -> bool:
        if ms_value is None and mh_value is None:
            return False
        if ms_value is None and mh_value is not None:
            return True
        if ms_value is not None and mh_value is None:
            return False
        if higher_is_worse:
            return mh_value > ms_value  # type: ignore[operator]
        return mh_value < ms_value  # type: ignore[operator]


# ---------------------------------------------------------------------------
# Sample plan data
# ---------------------------------------------------------------------------


def get_sample_plan_benefits() -> Dict[str, PlanBenefits]:
    """Return sample plan benefits for testing parity checks."""

    def benefit(cat: BenefitCategory, copay: float = 30.0, coins: float = 20.0, deductible: float = 1000.0, oop: float = 4000.0) -> BenefitDetail:
        return BenefitDetail(
            benefit_category=cat,
            copay=copay,
            coinsurance_percent=coins,
            deductible=deductible,
            out_of_pocket_max=oop,
            visit_limit_annual=None,
            visit_limit_lifetime=None,
            day_limit_annual=None,
            requires_prior_auth=False,
            prior_auth_turnaround_days=2,
            requires_step_therapy=False,
            medical_necessity_review=False,
            concurrent_review_frequency=None,
            provider_network_size=500,
            reimbursement_rate_percent_of_medicare=110.0,
        )

    # Compliant plan
    ms_bens = {cat.value: benefit(cat) for cat in BenefitCategory}
    mh_bens = {cat.value: benefit(cat) for cat in BenefitCategory}
    compliant = PlanBenefits(
        payer_id="COMPLIANT_PLAN",
        plan_name="Compliant Plan",
        medical_surgical_benefits=ms_bens,
        mh_sud_benefits=mh_bens,
        has_separate_mh_deductible=False,
        has_separate_mh_oop_max=False,
        excluded_mh_conditions=[],
        excluded_sud_conditions=[],
    )

    # Non-compliant plan
    ms_nc = {cat.value: benefit(cat) for cat in BenefitCategory}
    mh_nc = {cat.value: benefit(cat, copay=50.0) for cat in BenefitCategory}
    mh_nc[BenefitCategory.OUTPATIENT_IN_NETWORK.value].visit_limit_annual = 20
    mh_nc[BenefitCategory.OUTPATIENT_IN_NETWORK.value].requires_prior_auth = True
    nc_plan = PlanBenefits(
        payer_id="NON_COMPLIANT_PLAN",
        plan_name="Non-Compliant Plan",
        medical_surgical_benefits=ms_nc,
        mh_sud_benefits=mh_nc,
        has_separate_mh_deductible=True,
        has_separate_mh_oop_max=False,
        excluded_mh_conditions=["PERSONALITY_DISORDER"],
        excluded_sud_conditions=["OPIOID_USE"],
    )

    # Partially compliant plan
    ms_pc = {cat.value: benefit(cat) for cat in BenefitCategory}
    mh_pc = {cat.value: benefit(cat, copay=35.0, coins=25.0) for cat in BenefitCategory}
    mh_pc[BenefitCategory.INPATIENT_IN_NETWORK.value].requires_prior_auth = True
    mh_pc[BenefitCategory.INPATIENT_IN_NETWORK.value].prior_auth_turnaround_days = 4
    mh_pc[BenefitCategory.OUTPATIENT_IN_NETWORK.value].reimbursement_rate_percent_of_medicare = 90.0
    partial_plan = PlanBenefits(
        payer_id="PARTIALLY_COMPLIANT_PLAN",
        plan_name="Partially Compliant Plan",
        medical_surgical_benefits=ms_pc,
        mh_sud_benefits=mh_pc,
        has_separate_mh_deductible=False,
        has_separate_mh_oop_max=False,
        excluded_mh_conditions=[],
        excluded_sud_conditions=[],
    )

    return {
        compliant.payer_id: compliant,
        nc_plan.payer_id: nc_plan,
        partial_plan.payer_id: partial_plan,
    }


# ---------------------------------------------------------------------------
# Parity Checker Engine
# ---------------------------------------------------------------------------


class ParityChecker:
    """Main entrypoint for MHPAEA parity checks in claims adjudication."""

    def __init__(self, plan_benefits: Optional[Dict[str, PlanBenefits]] = None) -> None:
        self.plan_benefits = plan_benefits or get_sample_plan_benefits()
        self.classifier = ServiceClassifier()
        self.comparator = ParityComparator()

    def check_claim_parity(
        self,
        payer_id: str,
        cpt_code: str,
        icd10_codes: List[str],
        is_in_network: bool = True,
        is_inpatient: bool = False,
    ) -> ParityCheckResult:
        classification = self.classifier.classify_service(cpt_code=cpt_code, icd10_codes=icd10_codes)
        benefit_category = self._determine_benefit_category(is_in_network=is_in_network, is_inpatient=is_inpatient)
        plan = self.plan_benefits.get(payer_id) or get_sample_plan_benefits().get("COMPLIANT_PLAN")

        if classification == ServiceClassification.MEDICAL_SURGICAL:
            return self._build_result(
                payer_id,
                plan.plan_name if plan else None,
                cpt_code,
                classification,
                benefit_category,
                [],
                [],
                True,
            )

        if plan is None:
            raise RuntimeError("Plan benefits not available for parity check")

        ms_detail, mh_detail = self._get_benefit_pair(plan, benefit_category)
        comparisons: List[BenefitComparison] = []
        comparisons.extend(
            self.comparator.compare_financial_requirements(ms_detail, mh_detail, benefit_category, plan.has_separate_mh_deductible, plan.has_separate_mh_oop_max)
        )
        comparisons.extend(self.comparator.compare_quantitative_limits(ms_detail, mh_detail, benefit_category))
        comparisons.extend(self.comparator.compare_non_quantitative_limits(ms_detail, mh_detail, benefit_category))

        violations = self._comparisons_to_violations(comparisons, classification, benefit_category)
        violations.extend(self.check_excluded_conditions(payer_id, icd10_codes))

        pri = self.check_prior_auth_parity(payer_id, cpt_code, classification)
        if pri:
            violations.append(pri)
        visit_violation = self.check_visit_limit_parity(payer_id, benefit_category)
        if visit_violation:
            violations.append(visit_violation)

        is_compliant = len(violations) == 0 and all(c.is_parity_compliant for c in comparisons)
        return self._build_result(
            payer_id,
            plan.plan_name if plan else None,
            cpt_code,
            classification,
            benefit_category,
            comparisons,
            violations,
            is_compliant,
        )

    def run_plan_parity_audit(self, payer_id: str) -> Dict[str, object]:
        plan = self.plan_benefits.get(payer_id)
        if not plan:
            return {"payer_id": payer_id, "error": "Plan not found"}

        category_results: Dict[str, int] = {}
        violations: List[ParityViolation] = []
        for cat in BenefitCategory:
            ms_detail, mh_detail = self._get_benefit_pair(plan, cat)
            comps: List[BenefitComparison] = []
            comps.extend(
                self.comparator.compare_financial_requirements(ms_detail, mh_detail, cat, plan.has_separate_mh_deductible, plan.has_separate_mh_oop_max)
            )
            comps.extend(self.comparator.compare_quantitative_limits(ms_detail, mh_detail, cat))
            comps.extend(self.comparator.compare_non_quantitative_limits(ms_detail, mh_detail, cat))
            cat_violations = self._comparisons_to_violations(comps, ServiceClassification.MH_SUD, cat)
            violations.extend(cat_violations)
            category_results[cat.value] = len(cat_violations)

        return {
            "payer_id": payer_id,
            "plan_name": plan.plan_name,
            "violations_per_category": category_results,
            "total_violations": len(violations),
        }

    def check_excluded_conditions(self, payer_id: str, icd10_codes: List[str]) -> List[ParityViolation]:
        plan = self.plan_benefits.get(payer_id)
        if not plan:
            return []
        violations: List[ParityViolation] = []
        for code in icd10_codes:
            code_upper = code.upper()
            if code_upper in plan.excluded_mh_conditions or code_upper in plan.excluded_sud_conditions:
                violation_type = ParityViolationType.EXCLUDED_CONDITION
                violations.append(
                    ParityViolation(
                        violation_type=violation_type,
                        severity="ERROR",
                        description=f"Condition {code_upper} excluded from coverage, potential MHPAEA violation",
                        medical_surgical_benchmark="Comparable M/S conditions covered",
                        mh_sud_restriction=f"Exclusion of {code_upper}",
                        remediation="Cover MH/SUD condition consistent with M/S coverage",
                        regulatory_reference=self.get_regulatory_reference(violation_type),
                        financial_impact="Patient pays full cost",
                    )
                )
        return violations

    def check_prior_auth_parity(
        self,
        payer_id: str,
        cpt_code: str,
        service_classification: ServiceClassification,
    ) -> Optional[ParityViolation]:
        if service_classification == ServiceClassification.MEDICAL_SURGICAL:
            return None
        plan = self.plan_benefits.get(payer_id)
        if not plan:
            return None
        benefit_category = BenefitCategory.OUTPATIENT_IN_NETWORK
        ms, mh = self._get_benefit_pair(plan, benefit_category)
        if mh.requires_prior_auth and not ms.requires_prior_auth:
            violation_type = ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE
            return ParityViolation(
                violation_type=violation_type,
                severity="ERROR",
                description="MH/SUD requires prior auth when comparable M/S does not",
                medical_surgical_benchmark="No prior auth for comparable M/S",
                mh_sud_restriction="Prior auth mandated for MH/SUD",
                remediation="Align prior auth rules between MH/SUD and M/S",
                regulatory_reference=self.get_regulatory_reference(violation_type),
                financial_impact="Delayed or denied access to care",
            )
        if mh.requires_prior_auth and ms.requires_prior_auth:
            if (mh.prior_auth_turnaround_days or 0) > (ms.prior_auth_turnaround_days or 0):
                violation_type = ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE
                return ParityViolation(
                    violation_type=violation_type,
                    severity="WARNING",
                    description="MH/SUD prior auth turnaround longer than M/S",
                    medical_surgical_benchmark=f"Turnaround {ms.prior_auth_turnaround_days} days",
                    mh_sud_restriction=f"Turnaround {mh.prior_auth_turnaround_days} days",
                    remediation="Match MH/SUD turnaround to M/S standard",
                    regulatory_reference=self.get_regulatory_reference(violation_type),
                    financial_impact="Potential treatment delay",
                )
        return None

    def check_visit_limit_parity(self, payer_id: str, benefit_category: BenefitCategory) -> Optional[ParityViolation]:
        plan = self.plan_benefits.get(payer_id)
        if not plan:
            return None
        ms, mh = self._get_benefit_pair(plan, benefit_category)
        if self.comparator._is_more_restrictive(ms.visit_limit_annual, mh.visit_limit_annual, higher_is_worse=False):
            violation_type = ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE
            return ParityViolation(
                violation_type=violation_type,
                severity="ERROR",
                description="MH/SUD annual visit limit more restrictive than M/S",
                medical_surgical_benchmark=f"M/S visit limit: {'No limit' if ms.visit_limit_annual is None else ms.visit_limit_annual}",
                mh_sud_restriction=f"MH/SUD visit limit: {'No limit' if mh.visit_limit_annual is None else mh.visit_limit_annual}",
                remediation="Remove or relax MH/SUD visit limits to match M/S",
                regulatory_reference=self.get_regulatory_reference(violation_type),
                financial_impact="Potential early exhaustion of benefits",
            )
        return None

    def get_comparable_ms_service(self, cpt_code: str) -> Optional[str]:
        return COMPARABLE_SERVICES.get(cpt_code.upper())

    def generate_parity_report(self, result: ParityCheckResult) -> str:
        lines = [
            f"Parity Check {result.check_id}",
            f"Plan: {result.plan_name or 'Unknown'} | Payer: {result.payer_id}",
            f"Service: {result.service_checked} ({result.service_classification.value}) in {result.benefit_category.value}",
            f"Compliant: {result.is_parity_compliant} | Risk: {result.risk_level} | Violations: {result.total_violations}",
        ]
        for v in result.violations:
            lines.append(
                f"- {v.severity}: {v.violation_type.value} — {v.description} | Benchmark: {v.medical_surgical_benchmark} | Restriction: {v.mh_sud_restriction} | Ref: {v.regulatory_reference}"
            )
        if result.recommendations:
            lines.append("Recommendations:")
            for rec in result.recommendations:
                lines.append(f"  * {rec}")
        return "\n".join(lines)

    def get_regulatory_reference(self, violation_type: ParityViolationType) -> str:
        refs = {
            ParityViolationType.FINANCIAL_MORE_RESTRICTIVE: "42 U.S.C. § 300gg-26(a)(3)(A)(ii)",
            ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE: "45 CFR § 146.136(c)(2)(i)",
            ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE: "45 CFR § 146.136(c)(2)(i)",
            ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE: "45 CFR § 146.136(c)(4)(i)",
            ParityViolationType.MEDICAL_NECESSITY_MORE_RESTRICTIVE: "45 CFR § 146.136(c)(4)",
            ParityViolationType.NETWORK_INADEQUATE: "45 CFR § 146.136(c)(4)",
            ParityViolationType.STEP_THERAPY_MORE_RESTRICTIVE: "45 CFR § 146.136(c)(4)",
            ParityViolationType.REIMBURSEMENT_RATE_LOWER: "45 CFR § 146.136(c)(4)",
            ParityViolationType.SEPARATE_DEDUCTIBLE: "45 CFR § 146.136(b)(3)",
            ParityViolationType.SEPARATE_OOP_MAX: "45 CFR § 146.136(b)(3)",
            ParityViolationType.EXCLUDED_CONDITION: "45 CFR § 146.136(c)(1)",
            ParityViolationType.SCOPE_LIMITATION: "45 CFR § 146.136(c)(2)",
        }
        return refs.get(violation_type, "MHPAEA")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _determine_benefit_category(self, is_in_network: bool, is_inpatient: bool) -> BenefitCategory:
        if is_inpatient and is_in_network:
            return BenefitCategory.INPATIENT_IN_NETWORK
        if is_inpatient and not is_in_network:
            return BenefitCategory.INPATIENT_OUT_OF_NETWORK
        if not is_inpatient and is_in_network:
            return BenefitCategory.OUTPATIENT_IN_NETWORK
        return BenefitCategory.OUTPATIENT_OUT_OF_NETWORK

    def _get_benefit_pair(self, plan: PlanBenefits, category: BenefitCategory) -> Tuple[BenefitDetail, BenefitDetail]:
        ms = plan.medical_surgical_benefits.get(category.value)
        mh = plan.mh_sud_benefits.get(category.value)
        if not ms or not mh:
            # Fallback defaults to avoid crashes; still mark restrictions later
            default = BenefitDetail(benefit_category=category)
            return ms or default, mh or default
        return ms, mh

    def _comparisons_to_violations(
        self,
        comparisons: List[BenefitComparison],
        service_classification: ServiceClassification,
        benefit_category: BenefitCategory,
    ) -> List[ParityViolation]:
        violations: List[ParityViolation] = []
        for comp in comparisons:
            if comp.is_parity_compliant or not comp.violation_type:
                continue
            severity = "WARNING"
            if comp.violation_type in {ParityViolationType.FINANCIAL_MORE_RESTRICTIVE, ParityViolationType.SEPARATE_DEDUCTIBLE, ParityViolationType.SEPARATE_OOP_MAX}:
                severity = "ERROR"
            if comp.violation_type in {ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE, ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE, ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE}:
                severity = "ERROR"
            if comp.violation_type in {ParityViolationType.NETWORK_INADEQUATE, ParityViolationType.REIMBURSEMENT_RATE_LOWER}:
                severity = "WARNING"

            violations.append(
                ParityViolation(
                    violation_type=comp.violation_type,
                    severity=severity,
                    description=f"{comp.violation_type.value} detected for {benefit_category.value}",
                    medical_surgical_benchmark=comp.medical_surgical_description,
                    mh_sud_restriction=comp.mh_sud_description,
                    remediation=self._recommendation_for_violation(comp.violation_type),
                    regulatory_reference=self.get_regulatory_reference(comp.violation_type),
                    financial_impact=self._financial_impact_for_violation(comp.violation_type),
                )
            )
        return violations

    def _recommendation_for_violation(self, violation_type: ParityViolationType) -> str:
        recommendations = {
            ParityViolationType.FINANCIAL_MORE_RESTRICTIVE: "Align MH/SUD financial requirements to M/S levels.",
            ParityViolationType.SEPARATE_DEDUCTIBLE: "Combine deductibles across MH/SUD and M/S.",
            ParityViolationType.SEPARATE_OOP_MAX: "Use a unified out-of-pocket maximum.",
            ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE: "Remove or relax MH/SUD visit limits.",
            ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE: "Remove or relax MH/SUD day limits.",
            ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE: "Match prior auth rules between MH/SUD and M/S.",
            ParityViolationType.MEDICAL_NECESSITY_MORE_RESTRICTIVE: "Harmonize medical necessity criteria.",
            ParityViolationType.STEP_THERAPY_MORE_RESTRICTIVE: "Eliminate step therapy differentials.",
            ParityViolationType.NETWORK_INADEQUATE: "Expand MH/SUD network to M/S adequacy levels.",
            ParityViolationType.REIMBURSEMENT_RATE_LOWER: "Adjust MH/SUD reimbursement to M/S benchmarks.",
            ParityViolationType.EXCLUDED_CONDITION: "Cover excluded MH/SUD conditions consistent with M/S.",
            ParityViolationType.SCOPE_LIMITATION: "Broaden MH/SUD coverage scope to M/S equivalent.",
        }
        return recommendations.get(violation_type, "Resolve parity discrepancy.")

    def _financial_impact_for_violation(self, violation_type: ParityViolationType) -> Optional[str]:
        impacts = {
            ParityViolationType.FINANCIAL_MORE_RESTRICTIVE: "Higher patient cost-sharing for MH/SUD.",
            ParityViolationType.SEPARATE_DEDUCTIBLE: "Patients meet two deductibles instead of one.",
            ParityViolationType.SEPARATE_OOP_MAX: "Higher cumulative out-of-pocket exposure.",
            ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE: "Patients may exhaust MH/SUD visits early.",
            ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE: "Shorter covered stays for MH/SUD.",
            ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE: "Treatment delays or denials.",
            ParityViolationType.NETWORK_INADEQUATE: "Less access to in-network MH/SUD providers.",
            ParityViolationType.REIMBURSEMENT_RATE_LOWER: "Potential provider access issues.",
        }
        return impacts.get(violation_type)

    def _build_result(
        self,
        payer_id: str,
        plan_name: Optional[str],
        service_checked: str,
        classification: ServiceClassification,
        benefit_category: BenefitCategory,
        comparisons: List[BenefitComparison],
        violations: List[ParityViolation],
        is_compliant: bool,
    ) -> ParityCheckResult:
        total_violations = len(violations)
        risk_level = self._determine_risk_level(total_violations)
        regulatory_refs = [v.regulatory_reference for v in violations]
        summary = self._summarize_result(is_compliant, total_violations, benefit_category)
        recommendations = [v.remediation for v in violations]
        return ParityCheckResult(
            payer_id=payer_id,
            plan_name=plan_name,
            service_checked=service_checked,
            service_classification=classification,
            benefit_category=benefit_category,
            is_parity_compliant=is_compliant,
            comparisons=comparisons,
            violations=violations,
            total_violations=total_violations,
            risk_level=risk_level,
            recommendations=recommendations,
            regulatory_references=regulatory_refs,
            summary=summary,
        )

    def _determine_risk_level(self, total_violations: int) -> str:
        if total_violations == 0:
            return "LOW"
        if total_violations == 1:
            return "MEDIUM"
        if total_violations <= 3:
            return "HIGH"
        return "CRITICAL"

    def _summarize_result(self, is_compliant: bool, total_violations: int, category: BenefitCategory) -> str:
        if is_compliant:
            return f"No parity issues detected for {category.value}."
        return f"Detected {total_violations} parity issues in {category.value}."


__all__ = [
    "ServiceClassification",
    "BenefitCategory",
    "LimitationType",
    "ParityViolationType",
    "BenefitComparison",
    "ParityViolation",
    "ParityCheckResult",
    "BenefitDetail",
    "PlanBenefits",
    "ServiceClassifier",
    "ParityComparator",
    "ParityChecker",
    "COMPARABLE_SERVICES",
    "get_sample_plan_benefits",
]
