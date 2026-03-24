import json
from datetime import datetime, timedelta

import pytest

from medi_comply.knowledge.payer_policy_engine import (
    AuthRequirement,
    PayerPolicy,
    PayerPolicyDatabase,
    PayerPolicyEngine,
    PayerType,
    ServiceCategory,
    seed_payer_policies,
)


@pytest.fixture(scope="module")
def seeded_db() -> PayerPolicyDatabase:
    db = PayerPolicyDatabase()
    seed_payer_policies(db)
    return db


@pytest.fixture(scope="module")
def engine(seeded_db: PayerPolicyDatabase) -> PayerPolicyEngine:
    return PayerPolicyEngine(database=seeded_db)


# ---------------------------------------------------------------------------
# PayerPolicyDatabase basics
# ---------------------------------------------------------------------------


def test_add_and_retrieve_policy() -> None:
    db = PayerPolicyDatabase()
    policy = PayerPolicy(
        payer_id="TEST",
        payer_type=PayerType.UHC,
        payer_name="Test Plan",
        effective_date="2024-01-01",
        auth_requirements={},
        covered_services={},
        fee_schedule={},
        step_therapy_protocols=[],
        quantity_limits={},
        site_of_service_rules=[],
        timely_filing_limit_days=90,
        appeal_timeline_days=180,
        appeal_levels=["Internal"],
    )
    db.add_policy(policy)
    retrieved = db.get_policy("TEST")
    assert retrieved is not None
    assert retrieved.payer_id == "TEST"


def test_get_policies_by_type() -> None:
    db = PayerPolicyDatabase()
    policy_a = PayerPolicy(
        payer_id="A",
        payer_type=PayerType.UHC,
        payer_name="A",
        effective_date="2024-01-01",
        auth_requirements={},
        covered_services={},
        fee_schedule={},
        step_therapy_protocols=[],
        quantity_limits={},
        site_of_service_rules=[],
        timely_filing_limit_days=90,
        appeal_timeline_days=180,
        appeal_levels=["Internal"],
    )
    policy_b = policy_a.model_copy(update={"payer_id": "B", "payer_type": PayerType.MEDICARE})
    db.add_policy(policy_a)
    db.add_policy(policy_b)
    uhc = db.get_policies_by_type(PayerType.UHC)
    assert len(uhc) == 1
    assert uhc[0].payer_id == "A"


def test_get_all_payer_ids() -> None:
    db = PayerPolicyDatabase()
    seed_payer_policies(db)
    ids = db.get_all_payer_ids()
    assert "MEDICARE" in ids
    assert len(ids) >= 5


def test_find_policy_by_type_and_state() -> None:
    db = PayerPolicyDatabase()
    seed_payer_policies(db)
    policy = db.find_policy(PayerType.BCBS, state="TX")
    assert policy is not None
    assert policy.state == "TX"


def test_nonexistent_payer_returns_none(seeded_db: PayerPolicyDatabase) -> None:
    assert seeded_db.get_policy("UNKNOWN") is None


# ---------------------------------------------------------------------------
# Seed data presence
# ---------------------------------------------------------------------------


def test_seed_loads_at_least_5_payers(seeded_db: PayerPolicyDatabase) -> None:
    assert seeded_db.get_policy_count() >= 5


def test_medicare_policy_exists(seeded_db: PayerPolicyDatabase) -> None:
    policy = seeded_db.get_policy("MEDICARE")
    assert policy is not None
    assert policy.payer_type == PayerType.MEDICARE
    assert policy.timely_filing_limit_days == 365


def test_uhc_policy_exists(seeded_db: PayerPolicyDatabase) -> None:
    policy = seeded_db.get_policy("UHC_COMMERCIAL")
    assert policy is not None
    assert policy.payer_name == "UnitedHealthcare"


def test_aetna_policy_exists(seeded_db: PayerPolicyDatabase) -> None:
    assert seeded_db.get_policy("AETNA_COMMERCIAL") is not None


def test_bcbs_policy_exists(seeded_db: PayerPolicyDatabase) -> None:
    assert seeded_db.get_policy("BCBS_STATE") is not None


def test_medicaid_policy_exists(seeded_db: PayerPolicyDatabase) -> None:
    assert seeded_db.get_policy("MEDICAID_STATE") is not None


# ---------------------------------------------------------------------------
# Auth requirements
# ---------------------------------------------------------------------------


def test_uhc_auth_required_advanced_imaging(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("UHC_COMMERCIAL", "70553", ServiceCategory.IMAGING, False)
    assert rule.auth_requirement == AuthRequirement.REQUIRED


def test_medicare_no_auth_office_visit(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("MEDICARE", "99213", ServiceCategory.PROCEDURE, False)
    assert rule.auth_requirement == AuthRequirement.NOT_REQUIRED


def test_emergency_exemption(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("UHC_COMMERCIAL", "70553", ServiceCategory.IMAGING, True)
    assert rule.auth_requirement == AuthRequirement.NOT_REQUIRED


def test_auth_turnaround_times(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("UHC_COMMERCIAL", "70553", ServiceCategory.IMAGING, False)
    assert rule.auth_turnaround_standard_days > 0
    assert rule.auth_turnaround_urgent_hours > 0


def test_retro_auth_window(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("AETNA_COMMERCIAL", "70553", ServiceCategory.IMAGING, False)
    assert rule.retro_auth_window_hours is not None
    assert rule.retro_auth_window_hours > 0


def test_auth_required_clinical_info(engine: PayerPolicyEngine) -> None:
    rule = engine.check_auth_requirement("UHC_COMMERCIAL", "70553", ServiceCategory.IMAGING, False)
    assert rule.required_clinical_info


def test_get_auth_matrix(engine: PayerPolicyEngine) -> None:
    matrix = engine.get_auth_matrix("UHC_COMMERCIAL")
    assert "70553" in matrix
    assert matrix["70553"] == AuthRequirement.REQUIRED


# ---------------------------------------------------------------------------
# Coverage rules
# ---------------------------------------------------------------------------


def test_covered_service_returns_true(engine: PayerPolicyEngine) -> None:
    rule = engine.check_coverage("UHC_COMMERCIAL", "70553", ["R51.9"], patient_age=40, place_of_service="22")
    assert rule.is_covered is True


def test_age_restricted_service(engine: PayerPolicyEngine) -> None:
    rule = engine.check_coverage("UHC_COMMERCIAL", "27447", ["M17.0"], patient_age=30)
    assert rule.is_covered is False


def test_gender_restricted_service(engine: PayerPolicyEngine) -> None:
    rule = engine.check_coverage("MEDICARE", "G0101", ["Z12.4"], patient_gender="MALE")
    assert rule.is_covered is False


def test_pos_restriction(engine: PayerPolicyEngine) -> None:
    rule = engine.check_coverage("UHC_COMMERCIAL", "95811", ["G47.33"], place_of_service="22")
    assert rule.is_covered is False


def test_diagnosis_requirement_check(engine: PayerPolicyEngine) -> None:
    rule = engine.check_coverage("UHC_COMMERCIAL", "70553", ["Z99.1"], patient_age=50)
    assert rule.is_covered is False


# ---------------------------------------------------------------------------
# Fee schedule
# ---------------------------------------------------------------------------


def test_get_allowed_amount_exists(engine: PayerPolicyEngine) -> None:
    amount = engine.get_allowed_amount("MEDICARE", "99213")
    assert amount is not None
    assert amount > 0


def test_get_allowed_amount_unknown(engine: PayerPolicyEngine) -> None:
    assert engine.get_allowed_amount("MEDICARE", "00000") is None


def test_modifier_adjustment(engine: PayerPolicyEngine) -> None:
    amount = engine.get_allowed_amount("MEDICARE", "70553", modifier="26")
    assert amount == pytest.approx(189.0, rel=0.01)


def test_facility_vs_non_facility_rate(engine: PayerPolicyEngine) -> None:
    facility = engine.get_allowed_amount("MEDICARE", "99213", is_facility=True)
    office = engine.get_allowed_amount("MEDICARE", "99213", is_facility=False)
    assert facility != office


# ---------------------------------------------------------------------------
# Member cost sharing
# ---------------------------------------------------------------------------


def test_in_network_copay(engine: PayerPolicyEngine) -> None:
    cost = engine.calculate_member_responsibility("UHC_COMMERCIAL", "99213", is_in_network=True)
    assert cost.copay == pytest.approx(35.0)


def test_out_of_network_higher_cost(engine: PayerPolicyEngine) -> None:
    cost = engine.calculate_member_responsibility("UHC_COMMERCIAL", "99213", is_in_network=False)
    assert cost.copay is not None
    assert cost.copay > 35.0


def test_deductible_applies(engine: PayerPolicyEngine) -> None:
    cost = engine.calculate_member_responsibility("UHC_COMMERCIAL", "99213", is_in_network=True, deductible_met=True)
    assert cost.deductible_applies is False
    assert cost.deductible_amount == 0.0


# ---------------------------------------------------------------------------
# Timely filing
# ---------------------------------------------------------------------------


def test_timely_filing_within_limit(engine: PayerPolicyEngine) -> None:
    dos = "2024-01-01"
    submission = "2024-02-15"
    assert engine.check_timely_filing("UHC_COMMERCIAL", dos, submission) is True


def test_timely_filing_past_limit(engine: PayerPolicyEngine) -> None:
    dos = "2024-01-01"
    submission = "2024-07-15"
    assert engine.check_timely_filing("UHC_COMMERCIAL", dos, submission) is False


def test_medicare_365_day_limit(engine: PayerPolicyEngine) -> None:
    dos = "2024-01-01"
    submission = "2024-12-15"
    assert engine.check_timely_filing("MEDICARE", dos, submission) is True


def test_commercial_90_day_limit(engine: PayerPolicyEngine) -> None:
    dos = "2024-01-01"
    submission = "2024-04-15"
    assert engine.check_timely_filing("UHC_COMMERCIAL", dos, submission) is False


# ---------------------------------------------------------------------------
# Step therapy
# ---------------------------------------------------------------------------


def test_step_therapy_met(engine: PayerPolicyEngine) -> None:
    result = engine.check_step_therapy("UHC_COMMERCIAL", "Adalimumab", ["Methotrexate", "Sulfasalazine"])
    assert result["met"] is True
    assert not result["missing"]


def test_step_therapy_not_met(engine: PayerPolicyEngine) -> None:
    result = engine.check_step_therapy("UHC_COMMERCIAL", "Adalimumab", ["Methotrexate"])
    assert result["met"] is False
    assert "Sulfasalazine" in result["missing"]


def test_step_therapy_exception(engine: PayerPolicyEngine) -> None:
    policy = engine.database.get_policy("UHC_COMMERCIAL")
    assert policy is not None
    assert policy.step_therapy_protocols is not None
    protocol = next(p for p in policy.step_therapy_protocols if p.target_drug == "Adalimumab")
    assert "Contraindication to MTX" in protocol.exceptions


# ---------------------------------------------------------------------------
# Quantity limits
# ---------------------------------------------------------------------------


def test_within_quantity_limit(engine: PayerPolicyEngine) -> None:
    result = engine.check_quantity_limits("UHC_COMMERCIAL", "E0601", 1)
    assert result["within_limit"] is True


def test_exceeds_quantity_limit(engine: PayerPolicyEngine) -> None:
    result = engine.check_quantity_limits("UHC_COMMERCIAL", "E0601", 2)
    assert result["within_limit"] is False


def test_quantity_override_criteria(engine: PayerPolicyEngine) -> None:
    result = engine.check_quantity_limits("UHC_COMMERCIAL", "E0601", 2)
    assert result["override_criteria"]


# ---------------------------------------------------------------------------
# Comprehensive claim check
# ---------------------------------------------------------------------------


def _date(days_offset: int) -> str:
    return (datetime.utcnow() + timedelta(days=days_offset)).strftime("%Y-%m-%d")


def test_full_claim_check_approved(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="70553",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=True,
    )
    assert result.denial_reasons == []
    assert result.is_covered is True
    assert result.fee_schedule_amount is not None


def test_full_claim_check_denied_no_auth(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="70553",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=False,
    )
    assert "Prior authorization required" in result.denial_reasons


def test_full_claim_check_denied_not_covered(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="00000",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=True,
    )
    assert "Service not covered under payer policy" in result.denial_reasons


def test_full_claim_check_denied_timely_filing(engine: PayerPolicyEngine) -> None:
    dos = _date(-200)
    submission = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="70553",
        icd10_codes=["R51.9"],
        date_of_service=dos,
        submission_date=submission,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=True,
    )
    assert "Timely filing limit exceeded" in result.denial_reasons


def test_claim_check_has_appeal_guidance(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="00000",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=True,
    )
    assert result.appeal_guidance is not None
    assert "File within" in result.appeal_guidance


def test_claim_check_has_denial_reasons(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="00000",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service=None,
        is_in_network=True,
        auth_on_file=False,
    )
    assert result.denial_reasons


def test_claim_check_has_fee_schedule(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="99213",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service="11",
        is_in_network=True,
        auth_on_file=True,
    )
    assert result.fee_schedule_amount is not None


def test_claim_check_result_serialization(engine: PayerPolicyEngine) -> None:
    today = _date(0)
    result = engine.run_payer_claim_check(
        payer_id="UHC_COMMERCIAL",
        cpt_code="99213",
        icd10_codes=["R51.9"],
        date_of_service=today,
        submission_date=today,
        place_of_service="11",
        is_in_network=True,
        auth_on_file=True,
    )
    as_dict = result.model_dump()
    assert isinstance(as_dict, dict)
    json_str = json.dumps(as_dict)
    assert "payer_id" in json_str


# ---------------------------------------------------------------------------
# Helper methods
# ---------------------------------------------------------------------------


def test_get_appeal_info(engine: PayerPolicyEngine) -> None:
    info = engine.get_appeal_info("UHC_COMMERCIAL")
    assert info["appeal_levels"]
    assert info["appeal_timeline_days"] == 180


def test_compare_payers(engine: PayerPolicyEngine) -> None:
    comparisons = engine.compare_payers("99213", ["R51.9"], ["MEDICARE", "UHC_COMMERCIAL"])
    assert len(comparisons) == 2
    payer_ids = {entry["payer_id"] for entry in comparisons}
    assert {"MEDICARE", "UHC_COMMERCIAL"}.issubset(payer_ids)
