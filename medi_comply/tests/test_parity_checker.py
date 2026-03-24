"""Tests for Mental Health Parity Checker."""

from __future__ import annotations

import pytest

from medi_comply.compliance.parity_checker import (
    BenefitCategory,
    BenefitDetail,
    BenefitComparison,
    ParityChecker,
    ParityComparator,
    ParityViolationType,
    ServiceClassification,
    ServiceClassifier,
    COMPARABLE_SERVICES,
    get_sample_plan_benefits,
)


# Helpers ------------------------------------------------------------------


def _benefit(
    cat: BenefitCategory,
    copay: float = 30.0,
    coins: float = 20.0,
    ded: float = 1000.0,
    oop: float = 4000.0,
    visits: int | None = None,
    days: int | None = None,
    pa: bool = False,
    pa_days: int | None = None,
    step: bool = False,
    med_nec: bool = False,
    network: int | None = None,
    reimburse: float | None = None,
) -> BenefitDetail:
    return BenefitDetail(
        benefit_category=cat,
        copay=copay,
        coinsurance_percent=coins,
        deductible=ded,
        out_of_pocket_max=oop,
        visit_limit_annual=visits,
        visit_limit_lifetime=None,
        day_limit_annual=days,
        requires_prior_auth=pa,
        prior_auth_turnaround_days=pa_days,
        requires_step_therapy=step,
        medical_necessity_review=med_nec,
        concurrent_review_frequency=None,
        provider_network_size=network,
        reimbursement_rate_percent_of_medicare=reimburse,
    )


@pytest.fixture()
def classifier() -> ServiceClassifier:
    return ServiceClassifier()


@pytest.fixture()
def comparator() -> ParityComparator:
    return ParityComparator()


@pytest.fixture()
def parity_checker() -> ParityChecker:
    return ParityChecker(get_sample_plan_benefits())


@pytest.fixture()
def sample_plans():
    return get_sample_plan_benefits()


# ServiceClassifier tests ---------------------------------------------------


def test_classify_psychotherapy_cpt(classifier: ServiceClassifier):
    assert classifier.classify_by_cpt("90834") == ServiceClassification.MENTAL_HEALTH


def test_classify_psychiatric_eval_cpt(classifier: ServiceClassifier):
    assert classifier.classify_by_cpt("90791") == ServiceClassification.MENTAL_HEALTH


def test_classify_substance_use_cpt(classifier: ServiceClassifier):
    assert classifier.classify_by_cpt("99408") == ServiceClassification.SUBSTANCE_USE_DISORDER


def test_classify_office_visit_cpt(classifier: ServiceClassifier):
    assert classifier.classify_by_cpt("99213") == ServiceClassification.MEDICAL_SURGICAL


def test_classify_surgery_cpt(classifier: ServiceClassifier):
    assert classifier.classify_by_cpt("27447") == ServiceClassification.MEDICAL_SURGICAL


def test_classify_mental_health_icd10(classifier: ServiceClassifier):
    assert classifier.classify_by_icd10("F32.1") == ServiceClassification.MENTAL_HEALTH


def test_classify_substance_use_icd10(classifier: ServiceClassifier):
    assert classifier.classify_by_icd10("F10.20") == ServiceClassification.SUBSTANCE_USE_DISORDER


def test_classify_medical_icd10(classifier: ServiceClassifier):
    assert classifier.classify_by_icd10("E11.22") == ServiceClassification.MEDICAL_SURGICAL


def test_classify_combined_mh_cpt_with_mh_dx(classifier: ServiceClassifier):
    assert classifier.classify_service(cpt_code="90834", icd10_codes=["F32.1"]) == ServiceClassification.MH_SUD


def test_classify_ms_cpt_with_mh_dx(classifier: ServiceClassifier):
    assert classifier.classify_service(cpt_code="99213", icd10_codes=["F32.1"]) == ServiceClassification.MH_SUD


def test_classify_unknown(classifier: ServiceClassifier):
    assert classifier.classify_service(cpt_code="ZZZ") == ServiceClassification.UNCLASSIFIED


# ParityComparator — Financial ---------------------------------------------


def test_equal_copays_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=30)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=30)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_COPAY)


def test_higher_mh_copay_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=30)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=50)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    copay_comp = [c for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_COPAY][0]
    assert copay_comp.violation_type == ParityViolationType.FINANCIAL_MORE_RESTRICTIVE


def test_lower_mh_copay_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=30)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, copay=20)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_COPAY)


def test_equal_coinsurance_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, coins=20)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, coins=20)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_COINSURANCE)


def test_higher_mh_coinsurance_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, coins=20)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, coins=30)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    coins_comp = [c for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_COINSURANCE][0]
    assert coins_comp.violation_type == ParityViolationType.FINANCIAL_MORE_RESTRICTIVE


def test_separate_deductible_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, ded=1000)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, ded=1500)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK, has_separate_mh_deductible=True)
    assert any(c.violation_type == ParityViolationType.SEPARATE_DEDUCTIBLE for c in comps)


def test_separate_oop_max_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, oop=4000)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, oop=5000)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK, has_separate_mh_deductible=False, has_separate_mh_oop=True)
    assert any(c.violation_type == ParityViolationType.SEPARATE_OOP_MAX for c in comps)


def test_combined_deductible_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, ded=1000)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, ded=1000)
    comps = comparator.compare_financial_requirements(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps if c.limitation_type == c.limitation_type.FINANCIAL_DEDUCTIBLE)


# ParityComparator — Quantitative ------------------------------------------


def test_equal_visit_limits_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=20)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=20)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps if c.limitation_type == c.limitation_type.QUANTITATIVE_VISIT_LIMIT)


def test_lower_mh_visit_limit_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=None)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=20)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    visit_comp = [c for c in comps if c.limitation_type == c.limitation_type.QUANTITATIVE_VISIT_LIMIT][0]
    assert visit_comp.violation_type == ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE


def test_no_limits_either_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=None)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=None)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert all(c.is_parity_compliant for c in comps)


def test_mh_limit_ms_no_limit_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=None)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, visits=10)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert any(c.violation_type == ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE for c in comps)


def test_equal_day_limits_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.INPATIENT_IN_NETWORK, days=60)
    mh = _benefit(BenefitCategory.INPATIENT_IN_NETWORK, days=60)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.INPATIENT_IN_NETWORK)
    day_comp = [c for c in comps if c.limitation_type == c.limitation_type.QUANTITATIVE_DAY_LIMIT][0]
    assert day_comp.is_parity_compliant


def test_lower_mh_day_limit_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.INPATIENT_IN_NETWORK, days=60)
    mh = _benefit(BenefitCategory.INPATIENT_IN_NETWORK, days=30)
    comps = comparator.compare_quantitative_limits(ms, mh, BenefitCategory.INPATIENT_IN_NETWORK)
    assert any(c.violation_type == ParityViolationType.DAY_LIMIT_MORE_RESTRICTIVE for c in comps)


# ParityComparator — Non-Quantitative --------------------------------------


def test_both_require_auth_compliant(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=True, pa_days=14)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=True, pa_days=14)
    comps = comparator.compare_non_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    auth_comp = [c for c in comps if c.limitation_type == c.limitation_type.NON_QUANTITATIVE_PRIOR_AUTH]
    assert not auth_comp or all(c.is_parity_compliant for c in auth_comp)


def test_mh_auth_ms_no_auth_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=False)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=True)
    comps = comparator.compare_non_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert any(c.violation_type == ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE for c in comps)


def test_mh_longer_auth_turnaround_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=True, pa_days=14)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, pa=True, pa_days=30)
    comps = comparator.compare_non_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    auth = [c for c in comps if c.limitation_type == c.limitation_type.NON_QUANTITATIVE_PRIOR_AUTH][0]
    assert auth.violation_type == ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE


def test_mh_step_therapy_ms_none_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, step=False)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, step=True)
    comps = comparator.compare_non_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert any(c.violation_type == ParityViolationType.STEP_THERAPY_MORE_RESTRICTIVE for c in comps)


def test_smaller_mh_network_violation(comparator: ParityComparator):
    ms = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, network=500)
    mh = _benefit(BenefitCategory.OUTPATIENT_IN_NETWORK, network=100)
    comps = comparator.compare_non_quantitative_limits(ms, mh, BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert any(c.violation_type == ParityViolationType.NETWORK_INADEQUATE for c in comps)


# ParityChecker — Main ------------------------------------------------------


def test_medical_surgical_service_always_compliant(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("COMPLIANT_PLAN", "99213", ["E11.9"], is_in_network=True, is_inpatient=False)
    assert result.is_parity_compliant
    assert result.total_violations == 0


def test_compliant_plan_passes(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.is_parity_compliant


def test_non_compliant_plan_fails(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert not result.is_parity_compliant
    assert result.total_violations > 0


def test_partially_compliant_plan(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("PARTIALLY_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.total_violations >= 1


def test_check_result_has_violations(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.violations


def test_check_result_has_recommendations(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.recommendations


def test_check_result_has_regulatory_refs(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.regulatory_references


def test_risk_level_determination(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.risk_level in {"MEDIUM", "HIGH", "CRITICAL"}


# Specific parity checks ----------------------------------------------------


def test_excluded_mh_condition_violation(parity_checker: ParityChecker):
    violations = parity_checker.check_excluded_conditions("NON_COMPLIANT_PLAN", ["PERSONALITY_DISORDER"])
    assert violations
    assert violations[0].violation_type == ParityViolationType.EXCLUDED_CONDITION


def test_prior_auth_parity_check(parity_checker: ParityChecker):
    violation = parity_checker.check_prior_auth_parity("NON_COMPLIANT_PLAN", "90834", ServiceClassification.MH_SUD)
    assert violation is not None
    assert violation.violation_type == ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE


def test_visit_limit_parity_check(parity_checker: ParityChecker):
    violation = parity_checker.check_visit_limit_parity("NON_COMPLIANT_PLAN", BenefitCategory.OUTPATIENT_IN_NETWORK)
    assert violation is not None
    assert violation.violation_type == ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE


# Comparable service mappings ----------------------------------------------


def test_comparable_service_psychotherapy(parity_checker: ParityChecker):
    assert parity_checker.get_comparable_ms_service("90834") == "99214"


def test_comparable_service_psych_eval(parity_checker: ParityChecker):
    assert parity_checker.get_comparable_ms_service("90791") == "99205"


def test_comparable_service_unknown(parity_checker: ParityChecker):
    assert parity_checker.get_comparable_ms_service("99999") is None


# Plan audit ---------------------------------------------------------------


def test_plan_parity_audit_compliant(parity_checker: ParityChecker):
    audit = parity_checker.run_plan_parity_audit("COMPLIANT_PLAN")
    total_raw = audit.get("total_violations", 0)
    assert isinstance(total_raw, int)
    assert total_raw == 0


def test_plan_parity_audit_non_compliant(parity_checker: ParityChecker):
    audit = parity_checker.run_plan_parity_audit("NON_COMPLIANT_PLAN")
    total_raw = audit.get("total_violations", 0)
    assert isinstance(total_raw, int)
    assert total_raw > 0


def test_audit_covers_all_categories(parity_checker: ParityChecker):
    audit = parity_checker.run_plan_parity_audit("NON_COMPLIANT_PLAN")
    vpc = audit.get("violations_per_category", {})
    assert isinstance(vpc, dict)
    assert len(vpc) == len(BenefitCategory)


# Regulatory references ----------------------------------------------------


def test_financial_violation_reference(parity_checker: ParityChecker):
    ref = parity_checker.get_regulatory_reference(ParityViolationType.FINANCIAL_MORE_RESTRICTIVE)
    assert "300gg" in ref or "45 CFR" in ref


def test_quantitative_violation_reference(parity_checker: ParityChecker):
    ref = parity_checker.get_regulatory_reference(ParityViolationType.VISIT_LIMIT_MORE_RESTRICTIVE)
    assert "45 CFR" in ref


def test_nqtl_violation_reference(parity_checker: ParityChecker):
    ref = parity_checker.get_regulatory_reference(ParityViolationType.PRIOR_AUTH_MORE_RESTRICTIVE)
    assert "45 CFR" in ref


# Report generation --------------------------------------------------------


def test_parity_report_generated(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    report = parity_checker.generate_parity_report(result)
    assert "Parity Check" in report


def test_report_includes_violations(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    report = parity_checker.generate_parity_report(result)
    assert any(v.description in report for v in result.violations)


def test_report_includes_remediation(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("NON_COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    report = parity_checker.generate_parity_report(result)
    assert any("Recommendations" in line or "Align" in line for line in report.splitlines())


# Edge cases ---------------------------------------------------------------


def test_unknown_payer_handled(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("UNKNOWN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result is not None
    assert isinstance(result.is_parity_compliant, bool)


def test_empty_icd10_codes(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("COMPLIANT_PLAN", "90834", [], is_in_network=True, is_inpatient=False)
    assert result.is_parity_compliant


def test_result_serialization(parity_checker: ParityChecker):
    result = parity_checker.check_claim_parity("COMPLIANT_PLAN", "90834", ["F32.1"], is_in_network=True, is_inpatient=False)
    assert result.model_dump()


def test_both_mh_and_sud_classified(classifier: ServiceClassifier):
    classification = classifier.classify_service(cpt_code="H0020", icd10_codes=["F10.20"])
    assert classification == ServiceClassification.MH_SUD


def test_comparable_service_mapping_dict():
    assert COMPARABLE_SERVICES.get("90834") == "99214"
