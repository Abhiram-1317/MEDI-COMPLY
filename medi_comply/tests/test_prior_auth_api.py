import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.prior_auth import (
    AuthDecisionStatus,
    ServiceCategory,
    UrgencyLevel,
    router,
)

app = FastAPI()
app.include_router(router)
client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _submit(payload: dict):
    return client.post("/api/v1/prior-auth/submit", json=payload)


def _check_required(payload: dict):
    return client.post("/api/v1/prior-auth/check-required", json=payload)


def _base_request(**overrides):
    data = {
        "member_id": "MEM-12345",
        "provider_id": "1234567890",
        "payer_id": "BCBS",
        "service_type": "imaging",
        "service_code": "73721",
        "diagnosis_codes": ["M17.11"],
        "clinical_justification": (
            "Patient with right knee pain for 8 weeks. Failed physical therapy 3x/week for 6 weeks. "
            "Failed NSAIDs. X-ray shows joint space narrowing. Positive McMurray test. Requesting MRI."
        ),
        "requested_units": 1,
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# TestCheckAuthRequired
# ---------------------------------------------------------------------------


class TestCheckAuthRequired:
    def test_mri_requires_auth_bcbs(self):
        resp = _check_required({"service_code": "73721", "payer_id": "BCBS", "service_type": "imaging"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["auth_required"] is True

    def test_office_visit_no_auth(self):
        resp = _check_required({"service_code": "99213", "payer_id": "BCBS", "service_type": "procedure"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["auth_required"] is False

    def test_dme_requires_auth(self):
        resp = _check_required({"service_code": "E0601", "payer_id": "BCBS", "service_type": "dme"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["auth_required"] is True

    def test_surgery_requires_auth_medicare(self):
        resp = _check_required({"service_code": "27447", "payer_id": "MEDICARE", "service_type": "procedure"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["auth_required"] is True

    def test_unknown_payer_defaults_required(self):
        resp = _check_required({"service_code": "73721", "payer_id": "UNKNOWN", "service_type": "imaging"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["auth_required"] is True

    def test_response_has_policy_ref(self):
        resp = _check_required({"service_code": "73721", "payer_id": "BCBS", "service_type": "imaging"})
        body = resp.json()
        assert resp.status_code == 200
        assert body["policy_reference"] is not None

    def test_response_has_documentation_list(self):
        resp = _check_required({"service_code": "73721", "payer_id": "BCBS", "service_type": "imaging"})
        body = resp.json()
        assert resp.status_code == 200
        assert len(body.get("required_documentation", [])) > 0

    def test_response_has_turnaround(self):
        resp = _check_required({"service_code": "73721", "payer_id": "BCBS", "service_type": "imaging"})
        body = resp.json()
        assert resp.status_code == 200
        assert body.get("estimated_turnaround") is not None


# ---------------------------------------------------------------------------
# TestSubmitAuthApproved
# ---------------------------------------------------------------------------


class TestSubmitAuthApproved:
    def test_mri_approved_full_criteria(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == AuthDecisionStatus.APPROVED

    def test_approved_has_effective_dates(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert body.get("effective_date") is not None
        assert body.get("expiration_date") is not None

    def test_approved_has_auth_number(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert body.get("auth_request_id")

    def test_approved_has_approved_units(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert body.get("approved_units") == 1

    def test_approved_confidence_high(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert body.get("confidence_score", 0) >= 0.90

    def test_approved_letter_generated(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert "APPROVED" in (body.get("determination_letter") or "")


# ---------------------------------------------------------------------------
# TestSubmitAuthDenied
# ---------------------------------------------------------------------------


class TestSubmitAuthDenied:
    def test_mri_denied_no_conservative_treatment(self):
        payload = _base_request(
            clinical_justification=(
                "Knee pain for 4 weeks without any prior therapy documented. "
                "Patient requests MRI but no conservative care noted in chart. "
                "No PT, no NSAIDs, no supportive measures recorded."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] == AuthDecisionStatus.DENIED

    def test_denied_has_reasons(self):
        payload = _base_request(
            clinical_justification=(
                "Knee pain persists; no therapy attempts documented. "
                "Requesting MRI for further evaluation without prior conservative management."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert len(body.get("denial_reasons", [])) > 0

    def test_denied_has_appeal_rights(self):
        payload = _base_request(
            clinical_justification=(
                "Chronic knee pain; no prior PT or meds tried. No treatment failures listed."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        appeal = body.get("appeal_rights")
        assert appeal is not None
        assert len(appeal.get("appeal_levels", [])) >= 1

    def test_denied_has_alternatives(self):
        payload = _base_request(
            clinical_justification=(
                "Persistent knee pain; no conservative steps taken. No PT, no meds."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert len(body.get("alternative_treatments", [])) > 0

    def test_denied_letter_generated(self):
        payload = _base_request(
            clinical_justification=(
                "Knee pain, imaging requested, no conservative therapy documented."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert "DENIED" in (body.get("determination_letter") or "")

    def test_denied_confidence_appropriate(self):
        payload = _base_request(
            clinical_justification=(
                "Knee pain without prior therapy documented. MRI requested."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert 0.85 <= body.get("confidence_score", 0) <= 0.95


# ---------------------------------------------------------------------------
# TestSubmitAuthPending
# ---------------------------------------------------------------------------


class TestSubmitAuthPending:
    def test_pending_unclear_documentation(self):
        payload = _base_request(
            clinical_justification=(
                "Failed prior treatment reported by caregiver; details unavailable. Pain ongoing for several months. "
                "Narrative lacks objective detail; awaiting records for verification. No structured note provided yet."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert body["status"] == AuthDecisionStatus.PENDING_INFO

    def test_pending_has_missing_info(self):
        payload = _base_request(
            clinical_justification=(
                "Failed prior treatment reported by caregiver; details unavailable. Pain ongoing for several months. "
                "Narrative lacks objective detail; awaiting records for verification. No structured note provided yet."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert len(body.get("missing_information", [])) > 0

    def test_pending_letter_generated(self):
        payload = _base_request(
            clinical_justification=(
                "Failed prior treatment reported by caregiver; details unavailable. Pain ongoing for several months. "
                "Narrative lacks objective detail; awaiting records for verification. No structured note provided yet."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        letter = body.get("determination_letter") or ""
        assert "ADDITIONAL INFORMATION" in letter

    def test_pending_confidence_moderate(self):
        payload = _base_request(
            clinical_justification=(
                "Failed prior treatment reported by caregiver; details unavailable. Pain ongoing for several months. "
                "Narrative lacks objective detail; awaiting records for verification. No structured note provided yet."
            )
        )
        resp = _submit(payload)
        body = resp.json()
        assert 0.5 <= body.get("confidence_score", 0) <= 0.7


# ---------------------------------------------------------------------------
# TestNotRequired
# ---------------------------------------------------------------------------


class TestNotRequired:
    def test_office_visit_auto_approve(self):
        payload = _base_request(service_code="99213", service_type="procedure")
        resp = _submit(payload)
        body = resp.json()
        assert body["status"] == AuthDecisionStatus.NOT_REQUIRED

    def test_not_required_high_confidence(self):
        payload = _base_request(service_code="99213", service_type="procedure")
        resp = _submit(payload)
        body = resp.json()
        assert body.get("confidence_score", 0) >= 0.9

    def test_not_required_fast_processing(self):
        payload = _base_request(service_code="99213", service_type="procedure")
        resp = _submit(payload)
        body = resp.json()
        assert body.get("processing_time_ms", 9999) < 1500


# ---------------------------------------------------------------------------
# TestRetroAuth
# ---------------------------------------------------------------------------


class TestRetroAuth:
    def test_retro_emergency_allowed(self):
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        payload = _base_request(is_retrospective=True, date_of_service=today)
        resp = _submit(payload)
        body = resp.json()
        assert resp.status_code == 200
        assert body["status"] in {AuthDecisionStatus.APPROVED, AuthDecisionStatus.PENDING_INFO}

    def test_retro_non_emergency_denied(self):
        old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        payload = _base_request(is_retrospective=True, date_of_service=old_date)
        resp = _submit(payload)
        body = resp.json()
        assert body["status"] == AuthDecisionStatus.DENIED
        assert any("retro" in reason.lower() or "window" in reason.lower() for reason in body.get("denial_reasons", []))

    def test_retro_outside_window_denied(self):
        old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        payload = _base_request(is_retrospective=True, date_of_service=old_date, payer_id="MEDICARE")
        resp = _submit(payload)
        body = resp.json()
        assert body["status"] == AuthDecisionStatus.DENIED

    def test_retro_reasoning_documented(self):
        old_date = (datetime.datetime.utcnow() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        payload = _base_request(is_retrospective=True, date_of_service=old_date)
        resp = _submit(payload)
        body = resp.json()
        assert any(step.get("step") == "retro" for step in body.get("reasoning_chain", []))


# ---------------------------------------------------------------------------
# TestCriteriaMatching
# ---------------------------------------------------------------------------


class TestCriteriaMatching:
    def test_step_therapy_detected(self):
        payload = _base_request(clinical_justification="Failed physical therapy and medication trial with no relief.")
        resp = _submit(payload)
        body = resp.json()
        step = next((c for c in body.get("criteria_match_report", []) if c.get("category") == "step_therapy"), None)
        assert step and step.get("status") == "MET"

    def test_step_therapy_missing(self):
        payload = _base_request(
            clinical_justification="Knee pain persists; imaging requested; no conservative details provided in note."
        )
        resp = _submit(payload)
        body = resp.json()
        step = next((c for c in body.get("criteria_match_report", []) if c.get("category") == "step_therapy"), None)
        assert step and step.get("status") in {"NOT_MET", "UNCLEAR"}

    def test_documentation_present(self):
        payload = _base_request(
            clinical_justification="Failed therapy; examination findings reviewed; imaging planned for meniscus."
        )
        resp = _submit(payload)
        body = resp.json()
        doc = next((c for c in body.get("criteria_match_report", []) if c.get("category") == "documentation"), None)
        assert doc and doc.get("status") == "MET"

    def test_lab_value_found(self):
        payload = _base_request(
            service_code="J0135",
            payer_id="AETNA",
            service_type="medication",
            diagnosis_codes=["M05.79"],
            clinical_justification="Rheumatoid arthritis diagnosed, failed methotrexate, TB test negative within 3 months, no infection.",
        )
        resp = _submit(payload)
        body = resp.json()
        lab = next((c for c in body.get("criteria_match_report", []) if c.get("category") == "lab_value"), None)
        assert lab and lab.get("status") == "MET"

    def test_contraindication_absent(self):
        payload = _base_request(
            service_code="27447",
            service_type="procedure",
            clinical_justification="Failed therapy, no active infection, requesting joint replacement; prior PT and meds failed."
        )
        resp = _submit(payload)
        body = resp.json()
        contra = next((c for c in body.get("criteria_match_report", []) if c.get("category") == "contraindication"), None)
        assert contra and contra.get("status") == "MET"


# ---------------------------------------------------------------------------
# TestReasoningChain
# ---------------------------------------------------------------------------


class TestReasoningChain:
    def test_reasoning_has_steps(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert len(body.get("reasoning_chain", [])) >= 3

    def test_reasoning_has_policy(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert any("policy" in entry.get("step") or "policy" in entry.get("action") for entry in body.get("reasoning_chain", []))

    def test_reasoning_has_criteria(self):
        resp = _submit(_base_request())
        body = resp.json()
        assert len(body.get("criteria_match_report", [])) >= 1


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_clinical_justification_fails(self):
        resp = _submit(_base_request(clinical_justification="too short"))
        assert resp.status_code == 422

    def test_missing_diagnosis_codes_fails(self):
        resp = _submit(_base_request(diagnosis_codes=[]))
        assert resp.status_code == 422

    def test_unknown_service_code(self):
        payload = _base_request(service_code="99999", service_type="procedure")
        resp = _submit(payload)
        body = resp.json()
        assert resp.status_code == 200
        assert body.get("status") in {
            AuthDecisionStatus.ESCALATED,
            AuthDecisionStatus.PENDING_INFO,
            AuthDecisionStatus.DENIED,
            AuthDecisionStatus.APPROVED,
            AuthDecisionStatus.NOT_REQUIRED,
        }
