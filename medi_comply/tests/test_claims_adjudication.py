from datetime import datetime, timedelta, timezone

import pytest

from medi_comply.agents.claims_adjudication_agent import (
    AppealGuidance,
    CARC_CODES,
    ClaimAdjudicationResult,
    ClaimInput,
    ClaimLevelDeterminator,
    ClaimValidator,
    CodeLevelAdjudicator,
    EligibilityChecker,
    LineAdjudicationResult,
    LineDisposition,
    MemberEligibility,
    ProviderChecker,
    create_sample_claim,
    create_sample_claim_clean,
    create_sample_claim_denied,
    create_sample_claim_partial,
)
from medi_comply.agents.claims_adjudication_agent import DenialReasonCategory as DRC
from medi_comply.agents.claims_adjudication_agent import ClaimsAdjudicationAgent
from medi_comply.agents.claims_adjudication_agent import LineDisposition as LD


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def validator() -> ClaimValidator:
    return ClaimValidator()


@pytest.fixture
def eligibility_checker() -> EligibilityChecker:
    return EligibilityChecker()


@pytest.fixture
def provider_checker() -> ProviderChecker:
    return ProviderChecker()


@pytest.fixture
def adjudicator() -> CodeLevelAdjudicator:
    return CodeLevelAdjudicator()


@pytest.fixture
def determinator() -> ClaimLevelDeterminator:
    return ClaimLevelDeterminator()


@pytest.fixture
def agent() -> ClaimsAdjudicationAgent:
    return ClaimsAdjudicationAgent()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_claim(**updates) -> ClaimInput:
    claim = create_sample_claim()
    for k, v in updates.items():
        setattr(claim, k, v)
    return claim


def _eligibility_with_remaining() -> MemberEligibility:
    base = EligibilityChecker().check_eligibility("MEM-ABC", datetime.now(timezone.utc).date().isoformat(), "PYR")
    base.deductible_remaining = 200.0
    base.deductible_met = base.deductible_total - base.deductible_remaining
    return base


def _eligibility_met_deductible() -> MemberEligibility:
    base = EligibilityChecker().check_eligibility("MEM-ABC", datetime.now(timezone.utc).date().isoformat(), "PYR")
    base.deductible_remaining = 0.0
    base.deductible_met = base.deductible_total
    return base


# ---------------------------------------------------------------------------
# ClaimValidator tests
# ---------------------------------------------------------------------------


def test_valid_claim_passes(validator: ClaimValidator):
    claim = create_sample_claim()
    result = validator.validate(claim)
    assert result["valid"] is True


def test_missing_member_id_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.member_id = ""
    result = validator.validate(claim)
    assert result["valid"] is False


def test_missing_payer_id_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.payer_id = ""
    result = validator.validate(claim)
    assert result["valid"] is False


def test_missing_diagnosis_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.primary_diagnosis = ""
    result = validator.validate(claim)
    assert result["valid"] is False


def test_no_line_items_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.line_items = []
    result = validator.validate(claim)
    assert result["valid"] is False


def test_negative_charge_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.line_items[0].charge_amount = -1.0
    result = validator.validate(claim)
    assert result["valid"] is False


def test_invalid_date_format_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.date_of_service_from = "03-01-2026"
    result = validator.validate(claim)
    assert result["valid"] is False


def test_future_dos_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.date_of_service_from = (datetime.now(timezone.utc).date() + timedelta(days=2)).isoformat()
    result = validator.validate(claim)
    assert result["valid"] is False


def test_invalid_diagnosis_pointer_fails(validator: ClaimValidator):
    claim = create_sample_claim()
    claim.line_items[0].diagnosis_pointers = [5]
    result = validator.validate(claim)
    assert result["valid"] is False


def test_valid_claim_with_warnings(validator: ClaimValidator):
    claim = create_sample_claim()
    result = validator.validate(claim)
    assert result["valid"] is True
    assert isinstance(result.get("warnings", []), list)


# ---------------------------------------------------------------------------
# EligibilityChecker tests
# ---------------------------------------------------------------------------


def test_eligible_member_passes(eligibility_checker: EligibilityChecker):
    elig = eligibility_checker.check_eligibility("MEM-123", datetime.now(timezone.utc).date().isoformat(), "PYR-1")
    assert elig.is_eligible is True


def test_ineligible_member_fails(eligibility_checker: EligibilityChecker):
    elig = eligibility_checker.check_eligibility("MEM-123X", datetime.now(timezone.utc).date().isoformat(), "PYR-1")
    assert elig.is_eligible is False


def test_eligibility_has_deductible(eligibility_checker: EligibilityChecker):
    elig = eligibility_checker.check_eligibility("MEM-123", datetime.now(timezone.utc).date().isoformat(), "PYR-1")
    assert elig.deductible_total > 0
    assert elig.deductible_remaining >= 0


def test_cob_detected(eligibility_checker: EligibilityChecker):
    elig = eligibility_checker.check_eligibility("COB-MEMBER", datetime.now(timezone.utc).date().isoformat(), "PYR-1")
    assert elig.coordination_of_benefits is not None


# ---------------------------------------------------------------------------
# ProviderChecker tests
# ---------------------------------------------------------------------------


def test_in_network_provider(provider_checker: ProviderChecker):
    prov = provider_checker.verify_provider("PRV-1", "PYR-1", datetime.now(timezone.utc).date().isoformat())
    assert prov.is_in_network is True


def test_out_of_network_provider(provider_checker: ProviderChecker):
    prov = provider_checker.verify_provider("PRV-1-OON", "PYR-1", datetime.now(timezone.utc).date().isoformat())
    assert prov.is_in_network is False


def test_credentialed_provider(provider_checker: ProviderChecker):
    prov = provider_checker.verify_provider("PRV-1", "PYR-1", datetime.now(timezone.utc).date().isoformat())
    assert prov.is_credentialed is True


# ---------------------------------------------------------------------------
# CodeLevelAdjudicator tests
# ---------------------------------------------------------------------------


def test_covered_service_approved(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.APPROVED


def test_not_covered_service_denied(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "01234"  # invalid CPT starts with 0
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.DENIED
    assert result.denial_reason_code == "96"


def test_auth_required_no_auth_denied(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "99299"
    claim.authorization_number = None
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.DENIED
    assert result.denial_reason_code == "197"


def test_auth_on_file_approved(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "99299"
    claim.authorization_number = "AUTH-1"
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.APPROVED


def test_ncci_edit_bundles_line(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    # Duplicate CPT without modifier 59 triggers bundling in _run_ncci_edits
    claim.line_items.append(claim.line_items[0].model_copy(update={"line_number": 3}))
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[-1], claim, elig, prov)
    assert result.disposition == LD.BUNDLED
    assert result.denial_reason_code == "97"


def test_mue_exceeded_denied(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].units = 10
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.DENIED
    assert result.denial_reason_code == "119"


def test_medical_necessity_passes(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "99358"
    claim.primary_diagnosis = "F32.9"  # mental health diagnosis
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.APPROVED


def test_medical_necessity_fails(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "99358"
    claim.primary_diagnosis = "R07.9"  # not F* so fails med necessity for 99* CPT
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.disposition == LD.DENIED
    assert result.denial_reason_code == "50"


def test_fee_schedule_applied(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "93000"
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.allowed_amount == 75.0


def test_cost_sharing_calculated(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    elig = _eligibility_with_remaining()
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.member_responsibility is not None
    assert result.paid_amount is not None


def test_modifier_validation(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "93000"
    claim.line_items[0].modifiers = ["50"]
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.allowed_amount == pytest.approx(112.5)


def test_deductible_applied(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    elig = _eligibility_with_remaining()
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.deductible_applied is not None and result.deductible_applied > 0


def test_deductible_already_met(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    elig = _eligibility_met_deductible()
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.deductible_applied == 0.0


def test_line_reasoning_generated(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.reasoning != ""


# ---------------------------------------------------------------------------
# ClaimLevelDeterminator tests
# ---------------------------------------------------------------------------


def test_all_lines_approved_claim_approved(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert summary["claim_status"].value == LD.APPROVED.value


def test_all_lines_denied_claim_denied(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "01234"
    claim.line_items[1].cpt_code = "01235"
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert summary["claim_status"].value == "DENIED"


def test_mixed_lines_partially_approved(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim_partial()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert summary["claim_status"].value == "PARTIALLY_APPROVED"


def test_any_pended_claim_pended(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    line_results = [
        LineAdjudicationResult(
            line_number=1,
            cpt_code="99213",
            disposition=LD.PENDED,
            reasoning="Needs manual review",
        )
    ]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert summary["claim_status"].value == "PENDED"


def test_timely_filing_within_limit(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    claim.submission_date = (datetime.fromisoformat(claim.date_of_service_from) + timedelta(days=10)).date().isoformat()
    assert determinator._check_timely_filing(claim) is True


def test_timely_filing_past_limit(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    claim.submission_date = (datetime.fromisoformat(claim.date_of_service_from) + timedelta(days=400)).date().isoformat()
    assert determinator._check_timely_filing(claim) is False


def test_duplicate_claim_denied(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    claim.prior_claim_id = "OLD"
    duplicate = determinator._check_duplicate(claim)
    assert duplicate is False


def test_totals_calculated(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert summary["total_allowed"] >= 0
    assert summary["total_paid"] >= 0


def test_eob_generated(determinator: ClaimLevelDeterminator):
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determinator.determine(claim, line_results, elig, prov, claim.payer_id)
    assert "EXPLANATION OF BENEFITS" in summary["eob_summary"]


# ---------------------------------------------------------------------------
# ClaimsAdjudicationAgent tests (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clean_claim_fully_approved(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert result.claim_status.value == "APPROVED"


@pytest.mark.asyncio
async def test_clean_claim_has_eob(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert result.eob_summary


@pytest.mark.asyncio
async def test_denied_claim_has_reasons(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_denied()
    result = await agent.adjudicate_claim(claim)
    assert result.claim_status.value == "DENIED"
    assert result.claim_level_denial_reasons


@pytest.mark.asyncio
async def test_denied_claim_has_appeal_guidance(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_denied()
    result = await agent.adjudicate_claim(claim)
    assert result.appeal_guidance is not None


@pytest.mark.asyncio
async def test_denied_claim_cites_policy(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_denied()
    result = await agent.adjudicate_claim(claim)
    assert any(" - " in reason for reason in result.claim_level_denial_reasons)


@pytest.mark.asyncio
async def test_partially_approved_claim(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_partial()
    result = await agent.adjudicate_claim(claim)
    assert result.claim_status.value == "PARTIALLY_APPROVED"


@pytest.mark.asyncio
async def test_ineligible_member_denied(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim()
    claim.member_id = "MEM-XYZX"
    result = await agent.adjudicate_claim(claim)
    assert result.claim_status.value == "DENIED"


@pytest.mark.asyncio
async def test_late_filing_denied(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim()
    claim.submission_date = (datetime.fromisoformat(claim.date_of_service_from) + timedelta(days=400)).date().isoformat()
    result = await agent.adjudicate_claim(claim)
    assert result.timely_filing_check is False


@pytest.mark.asyncio
async def test_duplicate_claim_detected(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim()
    claim.prior_claim_id = "PRIOR"
    result = await agent.adjudicate_claim(claim)
    assert result.duplicate_check is False


@pytest.mark.asyncio
async def test_audit_trail_created(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert result.audit_trail_id


@pytest.mark.asyncio
async def test_reasoning_chain_complete(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert all(lr.reasoning for lr in result.line_results)


@pytest.mark.asyncio
async def test_fraud_check_runs():
    class Fraud:
        def detect(self, payload):
            return ["flagged"]
    agent = ClaimsAdjudicationAgent(fraud_detector=Fraud())
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert any("Fraud detector" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_parity_check_runs():
    class Parity:
        def check_parity(self, payload):
            return {"compliant": False}
    agent = ClaimsAdjudicationAgent(parity_checker=Parity())
    claim = create_sample_claim_clean()
    result = await agent.adjudicate_claim(claim)
    assert any("Parity" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_batch_adjudication(agent: ClaimsAdjudicationAgent):
    claims = [create_sample_claim_clean(), create_sample_claim_clean()]
    results = await agent.adjudicate_batch(claims)
    assert len(results) == 2


# ---------------------------------------------------------------------------
# EOB generation tests
# ---------------------------------------------------------------------------


def test_eob_shows_all_lines():
    determ = ClaimLevelDeterminator()
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determ.determine(claim, line_results, elig, prov, claim.payer_id)
    for li in claim.line_items:
        assert f"Line {li.line_number}" in summary["eob_summary"]


def test_eob_shows_amounts():
    determ = ClaimLevelDeterminator()
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determ.determine(claim, line_results, elig, prov, claim.payer_id)
    assert "Total Charged" in summary["eob_summary"]


def test_eob_shows_denial_reasons():
    determ = ClaimLevelDeterminator()
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "01234"
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determ.determine(claim, line_results, elig, prov, claim.payer_id)
    assert "DENIED" in summary["eob_summary"]


def test_eob_shows_appeal_rights():
    determ = ClaimLevelDeterminator()
    claim = create_sample_claim()
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    adjud = CodeLevelAdjudicator()
    line_results = [adjud.adjudicate_line(li, claim, elig, prov) for li in claim.line_items]
    summary = determ.determine(claim, line_results, elig, prov, claim.payer_id)
    assert "APPEAL RIGHTS" in summary["eob_summary"]


# ---------------------------------------------------------------------------
# Appeal guidance tests
# ---------------------------------------------------------------------------


def test_appeal_has_deadline():
    ag = AppealGuidance(
        has_appeal_rights=True,
        appeal_deadline_days=180,
        appeal_deadline_date=(datetime.now(timezone.utc).date() + timedelta(days=180)).isoformat(),
        appeal_levels=["Internal", "External"],
        appeal_instructions="Submit docs",
        required_documentation=["Notes"],
        peer_to_peer_available=True,
    )
    assert ag.appeal_deadline_date is not None


def test_appeal_has_levels():
    ag = ClaimsAdjudicationAgent().generate_appeal_guidance(create_sample_claim(), "DENIED")
    assert ag.appeal_levels


def test_appeal_has_required_docs():
    ag = ClaimsAdjudicationAgent().generate_appeal_guidance(create_sample_claim(), "DENIED")
    assert ag.required_documentation


def test_peer_to_peer_offered():
    ag = ClaimsAdjudicationAgent().generate_appeal_guidance(create_sample_claim(), "DENIED")
    assert ag.peer_to_peer_available is True


# ---------------------------------------------------------------------------
# CARC / RARC codes tests
# ---------------------------------------------------------------------------


def test_carc_code_for_not_covered():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.COVERAGE, "Non covered")
    assert carc == "96"


def test_carc_code_for_not_necessary():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.MEDICAL_NECESSITY, "Not necessary")
    assert carc == "50"


def test_carc_code_for_no_auth():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.AUTHORIZATION, "Missing auth")
    assert carc == "197"


def test_carc_code_for_timely_filing():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.TIMELY_FILING, "Late")
    assert carc == "29"


def test_carc_code_for_duplicate():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.DUPLICATE, "Duplicate")
    assert carc == "18"


def test_carc_code_for_bundling():
    adjud = CodeLevelAdjudicator()
    carc, _, _ = adjud._generate_denial_codes(DRC.BUNDLING, "Bundled")
    assert carc == "97"


def test_rarc_codes_included():
    adjud = CodeLevelAdjudicator()
    _, _, rarcs = adjud._generate_denial_codes(DRC.COVERAGE, "Non covered")
    assert rarcs


# ---------------------------------------------------------------------------
# Edge cases and serialization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sample_claim_clean_approved(agent: ClaimsAdjudicationAgent):
    result = await agent.adjudicate_claim(create_sample_claim_clean())
    assert result.claim_status.value == "APPROVED"


@pytest.mark.asyncio
async def test_sample_claim_denied_denied(agent: ClaimsAdjudicationAgent):
    result = await agent.adjudicate_claim(create_sample_claim_denied())
    assert result.claim_status.value == "DENIED"


@pytest.mark.asyncio
async def test_sample_claim_partial(agent: ClaimsAdjudicationAgent):
    result = await agent.adjudicate_claim(create_sample_claim_partial())
    assert result.claim_status.value in {"PARTIALLY_APPROVED", "DENIED"}


@pytest.mark.asyncio
async def test_result_serialization(agent: ClaimsAdjudicationAgent):
    result = await agent.adjudicate_claim(create_sample_claim_clean())
    dumped = result.model_dump()
    assert isinstance(dumped, dict)
    assert dumped["claim_id"]


@pytest.mark.asyncio
async def test_empty_line_items_handled(agent: ClaimsAdjudicationAgent):
    claim = create_sample_claim()
    claim.line_items = []
    result = await agent.adjudicate_claim(claim)
    assert result.claim_status.value == "DENIED"


def test_multiple_denials_per_line(adjudicator: CodeLevelAdjudicator):
    claim = create_sample_claim()
    claim.line_items[0].cpt_code = "01234"
    elig = EligibilityChecker().check_eligibility(claim.member_id, claim.date_of_service_from, claim.payer_id)
    prov = ProviderChecker().verify_provider(claim.provider_id, claim.payer_id, claim.date_of_service_from)
    result = adjudicator.adjudicate_line(claim.line_items[0], claim, elig, prov)
    assert result.denial_reason_code is not None
    assert result.denial_reason_description is not None
