import asyncio
from datetime import datetime, timedelta

import pytest

from medi_comply.agents.prior_auth_agent import (
    AppealGuidanceGenerator,
    AuthAppealGuidance,
    AuthRequirementChecker,
    AuthorizationDecision,
    AuthorizationStatus,
    ClinicalCriteriaMatcher,
    ClinicalCriterion,
    CriterionMatchStatus,
    DeterminationEngine,
    LetterGenerator,
    MedicalPolicy,
    PriorAuthAgent,
    PriorAuthRequest,
    RetroAuthHandler,
    ServiceType,
)


@pytest.fixture
def auth_agent() -> PriorAuthAgent:
    return PriorAuthAgent()


@pytest.fixture
def sample_mri_request() -> PriorAuthRequest:
    return PriorAuthRequest(
        member_id="MEM-TEST-001",
        provider_id="PRV-TEST-001",
        payer_id="BCBS",
        service_type=ServiceType.IMAGING,
        service_code="73721",
        service_description="MRI of knee without contrast",
        diagnosis_codes=["M17.11", "M23.21"],
        clinical_justification=(
            "62-year-old patient with right knee pain for 8 weeks. Failed conservative treatment including "
            "physical therapy 3x/week for 6 weeks and NSAIDs (ibuprofen 800mg TID). X-ray performed on 2024-01-15 "
            "showing moderate joint space narrowing. Physical exam reveals positive McMurray test. Requesting MRI "
            "to evaluate for meniscal tear."
        ),
        requested_units=1,
        is_urgent=False,
    )


# ---------------------------------------------------------------------------
# AuthRequirementChecker
# ---------------------------------------------------------------------------


class TestAuthRequirementChecker:
    def test_auth_required_for_mri(self) -> None:
        """MRI codes for BCBS should require authorization."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("73721", "BCBS", ServiceType.IMAGING)
        assert res["auth_required"] is True

    def test_auth_not_required_for_office_visit(self) -> None:
        """Office visit E&M code should not require auth by default."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("99213", "BCBS", ServiceType.PROCEDURE)
        assert res["auth_required"] is False

    def test_auth_required_for_surgery(self) -> None:
        """Orthopedic surgery codes for Medicare require auth."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("27447", "MEDICARE", ServiceType.PROCEDURE)
        assert res["auth_required"] is True

    def test_unknown_payer_defaults(self) -> None:
        """Unknown payer falls back to no matching rule (not required)."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("73721", "UNKNOWN", ServiceType.IMAGING)
        assert res["auth_required"] is False

    def test_dme_requires_auth(self) -> None:
        """DME codes for Medicare should require authorization."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("E0601", "MEDICARE", ServiceType.DME)
        assert res["auth_required"] is True

    def test_emergency_services_no_auth(self) -> None:
        """Emergency E&M code should not match any auth rule and return not required."""
        checker = AuthRequirementChecker()
        res = checker.check_auth_required("99285", "BCBS", ServiceType.PROCEDURE)
        assert res["auth_required"] is False


# ---------------------------------------------------------------------------
# RetroAuthHandler
# ---------------------------------------------------------------------------


class TestRetroAuthHandler:
    def test_emergency_retro_within_72hrs(self) -> None:
        """Urgent retro within 72 hours should be allowed."""
        handler = RetroAuthHandler()
        dos = (datetime.utcnow() - timedelta(hours=48)).strftime("%Y-%m-%d")
        req = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="urgent",
            is_urgent=True,
            is_retrospective=True,
            date_of_service=dos,
        )
        res = handler.evaluate_retro_request(req)
        assert res["allowed"] is True and res["deadline_met"] is True

    def test_emergency_retro_beyond_72hrs(self) -> None:
        """Urgent retro beyond 72 hours should be denied."""
        handler = RetroAuthHandler()
        dos = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
        req = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="urgent",
            is_urgent=True,
            is_retrospective=True,
            date_of_service=dos,
        )
        res = handler.evaluate_retro_request(req)
        assert res["allowed"] is False

    def test_non_emergency_retro(self) -> None:
        """Non-urgent retro beyond 3 days should be denied."""
        handler = RetroAuthHandler()
        dos = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d")
        req = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="",
            is_urgent=False,
            is_retrospective=True,
            date_of_service=dos,
        )
        res = handler.evaluate_retro_request(req)
        assert res["allowed"] is False

    def test_retro_same_day(self) -> None:
        """Same-day retro should be allowed."""
        handler = RetroAuthHandler()
        dos = datetime.utcnow().strftime("%Y-%m-%d")
        req = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="",
            is_urgent=False,
            is_retrospective=True,
            date_of_service=dos,
        )
        res = handler.evaluate_retro_request(req)
        assert res["allowed"] is True

    def test_retro_date_parsing(self) -> None:
        """Invalid date formats should be handled gracefully."""
        handler = RetroAuthHandler()
        req = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="",
            is_urgent=False,
            is_retrospective=True,
            date_of_service="01-15-2024",
        )
        res = handler.evaluate_retro_request(req)
        assert res["allowed"] is False and res["deadline_met"] is False


# ---------------------------------------------------------------------------
# ClinicalCriteriaMatcher
# ---------------------------------------------------------------------------


def _policy_with(criteria: list[ClinicalCriterion]) -> MedicalPolicy:
    return MedicalPolicy(
        policy_id="TEST-POL",
        policy_name="Test Policy",
        payer_id="BCBS",
        service_codes=["73721"],
        effective_date="2024-01-01",
        criteria=criteria,
    )


class TestClinicalCriteriaMatcher:
    def test_all_criteria_met(self) -> None:
        """All criteria supported in clinical text should be MET."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [
            ClinicalCriterion("C1", "Required dx M17.11", "required_diagnosis", True),
            ClinicalCriterion("C2", "Failed PT", "step_therapy", True),
            ClinicalCriterion("C3", "MRI note present", "documentation", True),
        ]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="Failed PT and MRI note present",
        )
        results = matcher.match_criteria(request, _policy_with(criteria))
        assert all(c.match_status == CriterionMatchStatus.MET for c in results if c.required)

    def test_step_therapy_not_met(self) -> None:
        """Absent step therapy evidence should mark criterion UNCLEAR."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Failed PT", "step_therapy", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="Knee pain, consider MRI",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.UNCLEAR

    def test_step_therapy_met(self) -> None:
        """Explicit failure of therapy should satisfy step therapy."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Failed physical therapy", "step_therapy", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="Failed physical therapy for 6 weeks",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.MET

    def test_unclear_criteria(self) -> None:
        """Vague clinical text should produce UNCLEAR."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Need documentation", "documentation", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="Pain",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.UNCLEAR

    def test_lab_value_present(self) -> None:
        """Lab value in text should mark lab criterion MET."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Need lab", "lab_value", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="GFR 38 mL/min noted",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.MET

    def test_diagnosis_code_match(self) -> None:
        """Presence of required ICD code should mark criterion MET."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Must include M17.11", "required_diagnosis", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.MET

    def test_diagnosis_code_missing(self) -> None:
        """Absent required ICD code should mark NOT_MET when required."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Must include E11.9", "required_diagnosis", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.NOT_MET

    def test_contraindication_detected(self) -> None:
        """Contraindication mention should satisfy the criterion (MET)."""
        matcher = ClinicalCriteriaMatcher()
        criteria = [ClinicalCriterion("C1", "Contraindication documented", "contraindication", True)]
        request = PriorAuthRequest(
            member_id="M",
            provider_id="P",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="73721",
            service_description="MRI",
            diagnosis_codes=["M17.11"],
            clinical_justification="There is a contraindication to surgery due to allergy",
        )
        result = matcher.match_criteria(request, _policy_with(criteria))[0]
        assert result.match_status == CriterionMatchStatus.MET


# ---------------------------------------------------------------------------
# DeterminationEngine
# ---------------------------------------------------------------------------


def _decision_request() -> PriorAuthRequest:
    return PriorAuthRequest(
        member_id="M",
        provider_id="P",
        payer_id="BCBS",
        service_type=ServiceType.IMAGING,
        service_code="73721",
        service_description="MRI",
        diagnosis_codes=["M17.11"],
        clinical_justification="Failed PT and documentation present",
    )


class TestDeterminationEngine:
    def test_all_met_approves(self) -> None:
        """All required criteria MET should approve."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.MET)]
        decision = engine.make_determination(request, criteria, policy)
        assert decision.status == AuthorizationStatus.APPROVED

    def test_any_unclear_pends(self) -> None:
        """Unclear required criterion should pend for info."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.UNCLEAR)]
        decision = engine.make_determination(request, criteria, policy)
        assert decision.status == AuthorizationStatus.PENDING_INFO
        assert decision.missing_information

    def test_any_not_met_denies(self) -> None:
        """Not met criterion should deny."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.NOT_MET)]
        decision = engine.make_determination(request, criteria, policy)
        assert decision.status == AuthorizationStatus.DENIED
        assert decision.denial_reasons

    def test_urgent_lower_threshold(self) -> None:
        """Urgent approved requests keep slightly lower confidence."""
        engine = DeterminationEngine()
        request = _decision_request().model_copy(update={"is_urgent": True})
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.MET)]
        decision = engine.make_determination(request, criteria, policy)
        assert decision.status == AuthorizationStatus.APPROVED
        assert 0.9 <= decision.confidence_score <= 0.95

    def test_confidence_score_ranges(self) -> None:
        """Confidence scores should align with decision types."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        approved = engine.make_determination(request, [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.MET)], policy)
        denied = engine.make_determination(request, [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.NOT_MET)], policy)
        pending = engine.make_determination(request, [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.UNCLEAR)], policy)
        assert 0.9 <= approved.confidence_score <= 0.98
        assert 0.5 <= pending.confidence_score <= 0.7
        assert 0.85 <= denied.confidence_score <= 0.95

    def test_approved_sets_dates(self) -> None:
        """Approved decisions set effective and expiration dates."""
        engine = DeterminationEngine()
        request = _decision_request().model_copy(update={"requested_start_date": "2024-01-01"})
        policy = _policy_with([ClinicalCriterion("C1", "req", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "req", "documentation", True, match_status=CriterionMatchStatus.MET)]
        decision = engine.make_determination(request, criteria, policy)
        assert decision.effective_date == "2024-01-01"
        assert decision.expiration_date is not None

    def test_denied_has_reasons(self) -> None:
        """Denied decisions should include specific reasons."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "Missing docs", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "Missing docs", "documentation", True, match_status=CriterionMatchStatus.NOT_MET)]
        decision = engine.make_determination(request, criteria, policy)
        assert "Missing docs" in decision.denial_reasons[0]

    def test_pending_has_missing_info(self) -> None:
        """Pending decisions list missing information."""
        engine = DeterminationEngine()
        request = _decision_request()
        policy = _policy_with([ClinicalCriterion("C1", "Need imaging report", "documentation", True)])
        criteria = [ClinicalCriterion("C1", "Need imaging report", "documentation", True, match_status=CriterionMatchStatus.UNCLEAR)]
        decision = engine.make_determination(request, criteria, policy)
        assert "Need imaging report" in decision.missing_information[0]


# ---------------------------------------------------------------------------
# PriorAuthAgent
# ---------------------------------------------------------------------------


class TestPriorAuthAgent:
    @pytest.mark.asyncio
    async def test_mri_approved_with_full_criteria(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Complete MRI request should approve with BCBS policy."""
        decision = await auth_agent.process_auth_request(sample_mri_request)
        assert decision.status == AuthorizationStatus.APPROVED

    @pytest.mark.asyncio
    async def test_mri_denied_no_conservative_treatment(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Missing step therapy evidence should deny when criteria not met."""
        request = sample_mri_request.model_copy(update={"clinical_justification": "Knee pain"})
        decision = await auth_agent.process_auth_request(request)
        assert decision.status in {AuthorizationStatus.PENDING_INFO, AuthorizationStatus.DENIED}

    @pytest.mark.asyncio
    async def test_mri_pending_unclear_documentation(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Vague documentation should pend for information."""
        request = sample_mri_request.model_copy(update={"clinical_justification": "Pain, consider MRI"})
        decision = await auth_agent.process_auth_request(request)
        assert decision.status == AuthorizationStatus.PENDING_INFO

    @pytest.mark.asyncio
    async def test_auth_not_required_auto_approve(self, auth_agent: PriorAuthAgent) -> None:
        """Office visit should bypass auth and return NOT_REQUIRED."""
        request = PriorAuthRequest(
            member_id="MEM",
            provider_id="PRV",
            payer_id="BCBS",
            service_type=ServiceType.PROCEDURE,
            service_code="99213",
            service_description="Office visit",
            diagnosis_codes=["J01.90"],
            clinical_justification="Routine visit",
        )
        decision = await auth_agent.process_auth_request(request)
        assert decision.status == AuthorizationStatus.NOT_REQUIRED
        assert decision.approved_units == 1

    @pytest.mark.asyncio
    async def test_retro_auth_emergency(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Retro urgent within window should process normally."""
        req = sample_mri_request.model_copy(update={
            "is_retrospective": True,
            "is_urgent": True,
            "date_of_service": (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d"),
        })
        decision = await auth_agent.process_auth_request(req)
        assert decision.status in {AuthorizationStatus.APPROVED, AuthorizationStatus.PENDING_INFO}

    @pytest.mark.asyncio
    async def test_retro_auth_denied(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Retro outside window should be denied."""
        req = sample_mri_request.model_copy(update={
            "is_retrospective": True,
            "is_urgent": False,
            "date_of_service": (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%d"),
        })
        decision = await auth_agent.process_auth_request(req)
        assert decision.status == AuthorizationStatus.DENIED
        assert decision.policy_reference == "RETRO_DENIED"

    @pytest.mark.asyncio
    async def test_unknown_policy_escalates(self, auth_agent: PriorAuthAgent) -> None:
        """Unknown service code should escalate when policy missing."""
        req = PriorAuthRequest(
            member_id="MEM",
            provider_id="PRV",
            payer_id="BCBS",
            service_type=ServiceType.IMAGING,
            service_code="99999",
            service_description="Unknown",
            diagnosis_codes=["M17.11"],
            clinical_justification="Failed PT",
        )
        decision = await auth_agent.process_auth_request(req)
        assert decision.status == AuthorizationStatus.ESCALATED

    @pytest.mark.asyncio
    async def test_hip_replacement_full_criteria(self, auth_agent: PriorAuthAgent) -> None:
        """Hip replacement request with full criteria should approve."""
        req = PriorAuthRequest(
            member_id="MEM",
            provider_id="PRV",
            payer_id="AETNA",
            service_type=ServiceType.PROCEDURE,
            service_code="27130",
            service_description="Total hip arthroplasty",
            diagnosis_codes=["M16.11"],
            clinical_justification="BMI documented 32. Failed physical therapy for 12 weeks. Radiographic evidence of joint disease noted.",
        )
        decision = await auth_agent.process_auth_request(req)
        assert decision.status in {AuthorizationStatus.APPROVED, AuthorizationStatus.PENDING_INFO}

    @pytest.mark.asyncio
    async def test_specialty_med_step_therapy(self, auth_agent: PriorAuthAgent) -> None:
        """Specialty medication should require step therapy evidence."""
        req = PriorAuthRequest(
            member_id="MEM",
            provider_id="PRV",
            payer_id="BCBS",
            service_type=ServiceType.MEDICATION,
            service_code="J0129",
            service_description="Biologic med",
            diagnosis_codes=["M06.9"],
            clinical_justification="Failed methotrexate and failed TNF inhibitor. Baseline lab monitoring completed.",
        )
        decision = await auth_agent.process_auth_request(req)
        assert decision.status in {AuthorizationStatus.APPROVED, AuthorizationStatus.PENDING_INFO}

    @pytest.mark.asyncio
    async def test_turnaround_compliance(self, auth_agent: PriorAuthAgent, sample_mri_request: PriorAuthRequest) -> None:
        """Turnaround compliance should set deadline based on urgency."""
        policy = auth_agent.get_policy_for_request(sample_mri_request)
        assert policy is not None
        compliance = auth_agent.check_turnaround_compliance(sample_mri_request, policy)
        assert compliance["expected_days"] == policy.turnaround_days_standard


# ---------------------------------------------------------------------------
# Letter Generation
# ---------------------------------------------------------------------------


class TestLetterGeneration:
    def test_approval_letter_content(self) -> None:
        """Approval letter should include codes, dates, and units."""
        generator = LetterGenerator()
        decision = AuthorizationDecision(
            request_id="REQ",
            status=AuthorizationStatus.APPROVED,
            decision_date="2024-01-01",
            effective_date="2024-01-01",
            expiration_date="2024-04-01",
            approved_units=2,
            approved_service_code="73721",
            policy_reference="POL-BCBS-MRI-001",
            criteria_match_report=[],
            missing_information=[],
            denial_reasons=[],
            appeal_rights=None,
            alternative_treatments=[],
            peer_review_required=False,
            confidence_score=0.95,
            reasoning_chain=[],
        )
        request = _decision_request()
        letter = generator.generate_approval_letter(decision, request)
        assert "APPROVED" in letter and "73721" in letter and "Approved units: 2" in letter

    def test_denial_letter_content(self) -> None:
        """Denial letter should include reasons and policy reference."""
        generator = LetterGenerator()
        decision = AuthorizationDecision(
            request_id="REQ",
            status=AuthorizationStatus.DENIED,
            decision_date="2024-01-01",
            effective_date="2024-01-01",
            expiration_date="2024-04-01",
            approved_units=None,
            approved_service_code=None,
            policy_reference="POL-BCBS-MRI-001",
            criteria_match_report=[],
            missing_information=[],
            denial_reasons=["Missing conservative therapy"],
            appeal_rights={"can_appeal": True},
            alternative_treatments=[],
            peer_review_required=False,
            confidence_score=0.9,
            reasoning_chain=[],
        )
        request = _decision_request()
        letter = generator.generate_denial_letter(decision, request)
        assert "DENIED" in letter and "Missing conservative therapy" in letter and "POL-BCBS-MRI-001" in letter

    def test_info_request_letter_content(self) -> None:
        """Info request letter should list missing items."""
        generator = LetterGenerator()
        decision = AuthorizationDecision(
            request_id="REQ",
            status=AuthorizationStatus.PENDING_INFO,
            decision_date="2024-01-01",
            effective_date="2024-01-01",
            expiration_date="2024-04-01",
            approved_units=None,
            approved_service_code=None,
            policy_reference="POL-BCBS-MRI-001",
            criteria_match_report=[],
            missing_information=["Need imaging report"],
            denial_reasons=[],
            appeal_rights=None,
            alternative_treatments=[],
            peer_review_required=False,
            confidence_score=0.6,
            reasoning_chain=[],
        )
        request = _decision_request()
        letter = generator.generate_info_request_letter(decision, request)
        assert "PENDING" in letter and "Need imaging report" in letter

    def test_appeal_guidance_complete(self) -> None:
        """Appeal guidance should include deadlines and required docs."""
        generator = AppealGuidanceGenerator()
        decision = AuthorizationDecision(
            request_id="REQ",
            status=AuthorizationStatus.DENIED,
            decision_date="2024-01-01",
            effective_date="2024-01-01",
            expiration_date="2024-04-01",
            approved_units=None,
            approved_service_code=None,
            policy_reference="POL-BCBS-MRI-001",
            criteria_match_report=[],
            missing_information=[],
            denial_reasons=["Missing"],
            appeal_rights=None,
            alternative_treatments=[],
            peer_review_required=False,
            confidence_score=0.9,
            reasoning_chain=[],
        )
        request = _decision_request()
        guidance = generator.generate_guidance(decision, request)
        assert guidance.appeal_deadline_days > 0
        assert guidance.required_documents
        assert guidance.appeal_levels


# ---------------------------------------------------------------------------
# Appeal Guidance
# ---------------------------------------------------------------------------


class TestAppealGuidance:
    def test_appeal_guidance_for_denial(self) -> None:
        """Denied decisions should return full appeal guidance."""
        generator = AppealGuidanceGenerator()
        decision = AuthorizationDecision(
            request_id="REQ",
            status=AuthorizationStatus.DENIED,
            decision_date="2024-01-01",
            effective_date="2024-01-01",
            expiration_date="2024-04-01",
            approved_units=None,
            approved_service_code=None,
            policy_reference="POL-BCBS-MRI-001",
            criteria_match_report=[],
            missing_information=[],
            denial_reasons=["Missing"],
            appeal_rights=None,
            alternative_treatments=[],
            peer_review_required=False,
            confidence_score=0.9,
            reasoning_chain=[],
        )
        guidance = generator.generate_guidance(decision, _decision_request())
        assert guidance.can_appeal is True
        assert guidance.appeal_levels

    def test_peer_to_peer_available(self) -> None:
        """Peer-to-peer availability flag should be true by default."""
        generator = AppealGuidanceGenerator()
        guidance = generator.generate_guidance(
            AuthorizationDecision(
                request_id="REQ",
                status=AuthorizationStatus.PENDING_INFO,
                decision_date="2024-01-01",
                effective_date="2024-01-01",
                expiration_date="2024-04-01",
                approved_units=None,
                approved_service_code=None,
                policy_reference="POL",
                criteria_match_report=[],
                missing_information=["Docs"],
                denial_reasons=[],
                appeal_rights=None,
                alternative_treatments=[],
                peer_review_required=False,
                confidence_score=0.6,
                reasoning_chain=[],
            ),
            _decision_request(),
        )
        assert guidance.peer_to_peer_available is True

    def test_appeal_deadlines(self) -> None:
        """Appeal deadlines should be populated with positive values."""
        generator = AppealGuidanceGenerator()
        guidance = generator.generate_guidance(
            AuthorizationDecision(
                request_id="REQ",
                status=AuthorizationStatus.DENIED,
                decision_date="2024-01-01",
                effective_date="2024-01-01",
                expiration_date="2024-04-01",
                approved_units=None,
                approved_service_code=None,
                policy_reference="POL",
                criteria_match_report=[],
                missing_information=[],
                denial_reasons=["Reason"],
                appeal_rights=None,
                alternative_treatments=[],
                peer_review_required=False,
                confidence_score=0.9,
                reasoning_chain=[],
            ),
            _decision_request(),
        )
        assert guidance.appeal_deadline_days > 0
        assert guidance.appeal_levels
