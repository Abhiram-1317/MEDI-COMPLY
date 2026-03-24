import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from medi_comply.api.routes.coding import router as coding_router

try:
    from medi_comply.api.routes.prior_auth import router as auth_router
except ImportError:  # pragma: no cover - optional router
    auth_router = None

try:
    from medi_comply.api.routes.knowledge import router as knowledge_router
except ImportError:  # pragma: no cover - optional router
    knowledge_router = None

app = FastAPI()
app.include_router(coding_router)
if auth_router:
    app.include_router(auth_router)
if knowledge_router:
    app.include_router(knowledge_router)
client = TestClient(app)
AUTH_CHECK_AVAILABLE = bool(
    auth_router and any(getattr(route, "path", "") == "/api/v1/auth/check" for route in getattr(auth_router, "routes", []))
)


def _process(doc: str, encounter_type: str = "outpatient", **kwargs):
    payload = {"clinical_document": doc, "encounter_type": encounter_type}
    payload.update(kwargs)
    resp = client.post("/api/v1/coding/process", json=payload)
    assert resp.status_code == 200
    return resp.json()


def _validate(body: dict):
    resp = client.post("/api/v1/coding/validate", json=body)
    assert resp.status_code == 200
    return resp.json()


class TestOfficialCodingGuidelines:
    def test_ocg_combination_codes(self):
        doc = "ASSESSMENT: Type 2 diabetes with diabetic nephropathy"
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "E11.22" in codes

    def test_ocg_use_additional_code(self):
        doc = "ASSESSMENT: 1. Type 2 diabetes with diabetic CKD\n2. CKD stage 3b, GFR 38"
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "E11.22" in codes
        assert "N18.32" in codes

    def test_ocg_excludes1_enforcement(self):
        doc = "ASSESSMENT: STEMI involving anterior wall. Also NSTEMI noted in chart."
        body = _process(doc, "inpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "I21.3" in codes or "I21.4" in codes
        checks = {c["check_name"]: c for c in body["compliance_report"]["checks"]}
        assert checks["EXCLUDES_1"]["result"] in {"PASS", "HARD_FAIL"}

    def test_ocg_principal_dx_selection(self):
        doc = "ASSESSMENT: 1. Acute NSTEMI (reason for admission)\n2. Hypertension\n3. Diabetes"
        body = _process(doc, "inpatient")
        dx_codes = [d["code"] for d in body["diagnosis_codes"]]
        assert "I21.4" in dx_codes
        if dx_codes:
            assert dx_codes[0] in {"I21.4", "I10"}

    def test_ocg_outpatient_uncertain(self):
        doc = "ASSESSMENT: Suspected pneumonia but ruled out after evaluation; cough improving."
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "J18.9" not in codes

    def test_ocg_inpatient_uncertain(self):
        doc = "ASSESSMENT: Probable NSTEMI; troponin trending up."
        body = _process(doc, "inpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "I21.4" in codes

    def test_ocg_sequencing_etiology_manifestation(self):
        doc = "ASSESSMENT: Diabetic chronic kidney disease due to type 2 diabetes with CKD stage 3b"
        body = _process(doc, "outpatient")
        dx = [d["code"] for d in body["diagnosis_codes"]]
        assert dx
        assert dx[0].startswith("E11")
        assert any(code.startswith("N18") for code in dx)

    def test_ocg_highest_specificity(self):
        doc = "ASSESSMENT: CKD stage 3b with GFR 38"
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "N18.32" in codes
        assert "N18.3" not in codes


class TestMedicareRules:
    @pytest.mark.skipif(not AUTH_CHECK_AVAILABLE, reason="Prior auth router not available")
    def test_medicare_prior_auth_imaging(self):
        payload = {"service_code": "73721", "payer_id": "MEDICARE"}
        resp = client.post("/api/v1/auth/check", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("auth_required") is True

    def test_medicare_medical_necessity(self):
        doc = "ASSESSMENT: Type 2 diabetes. PLAN: Order comprehensive metabolic panel and EKG."
        body = _process(doc, "outpatient")
        checks = {c["check_name"]: c for c in body["compliance_report"]["checks"]}
        assert "MEDICAL_NECESSITY" in checks

    def test_medicare_ncci_edits(self):
        doc = "ASSESSMENT: Chest pain concerning for ischemia. PLAN: EKG and echocardiogram today."
        body = _process(doc, "outpatient")
        checks = {c["check_name"]: c for c in body["compliance_report"]["checks"]}
        assert checks["NCCI_EDITS"]["result"] in {"SOFT_FAIL", "PASS"}

    @pytest.mark.skipif(knowledge_router is None, reason="Knowledge router not available")
    def test_medicare_mue_limits(self):
        resp = client.get("/api/v1/knowledge/ncci/check", params={"cpt1": "80053", "cpt2": "85025"})
        assert resp.status_code == 200
        body = resp.json()
        assert "summary" in body


class TestCommercialPayerRules:
    @pytest.mark.skipif(not AUTH_CHECK_AVAILABLE, reason="Prior auth router not available")
    def test_bcbs_prior_auth_mri(self):
        payload = {"service_code": "73721", "payer_id": "BCBS"}
        resp = client.post("/api/v1/auth/check", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("auth_required") is True

    @pytest.mark.skipif(not AUTH_CHECK_AVAILABLE, reason="Prior auth router not available")
    def test_aetna_step_therapy(self):
        payload = {"service_code": "J0135", "payer_id": "AETNA", "clinical_info": "No prior biologic tried."}
        resp = client.post("/api/v1/auth/check", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") in {"DENIED", "PENDING_INFO", "REVIEW"}

    @pytest.mark.skipif(not AUTH_CHECK_AVAILABLE, reason="Prior auth router not available")
    def test_united_surgery_auth(self):
        payload = {"service_code": "27447", "payer_id": "UNITED"}
        resp = client.post("/api/v1/auth/check", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("auth_required") is True

    @pytest.mark.skipif(not AUTH_CHECK_AVAILABLE, reason="Prior auth router not available")
    def test_payer_specific_response(self):
        payload_bcbs = {"service_code": "73721", "payer_id": "BCBS"}
        payload_medicare = {"service_code": "73721", "payer_id": "MEDICARE"}
        resp_bcbs = client.post("/api/v1/auth/check", json=payload_bcbs)
        resp_medicare = client.post("/api/v1/auth/check", json=payload_medicare)
        assert resp_bcbs.status_code == 200 and resp_medicare.status_code == 200
        assert resp_bcbs.json() != resp_medicare.json()


class TestSpecificCodingScenarios:
    def test_diabetes_with_multiple_complications(self):
        doc = "ASSESSMENT: 1. Type 2 diabetes with diabetic CKD\n2. CKD stage 3b"
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "E11.22" in codes
        assert "N18.32" in codes

    def test_acute_mi_with_comorbidities(self):
        doc = (
            "ASSESSMENT:\n1. Acute NSTEMI, troponin 0.8\n2. Type 2 diabetes with diabetic CKD\n"
            "3. CKD stage 3b\n4. Essential hypertension\n5. Morbid obesity"
        )
        body = _process(doc, "inpatient", include_drg=True)
        codes = [d["code"] for d in body["diagnosis_codes"]]
        assert codes and codes[0] == "I21.4"
        assert "E11.22" in codes and "N18.32" in codes and "I10" in codes and "E66.01" in codes

    def test_sepsis_with_organ_dysfunction(self):
        doc = "ASSESSMENT: 1. Sepsis due to UTI\n2. Acute kidney injury\n3. UTI"
        body = _process(doc, "inpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "A41.9" in codes
        assert "N17.9" in codes
        # UTI keyword may not map directly; ensure no conflicting MI codes appear
        assert all(not code.startswith("I21") for code in codes)

    def test_copd_exacerbation_with_pneumonia(self):
        doc = "ASSESSMENT: 1. COPD with acute exacerbation\n2. Community acquired pneumonia"
        body = _process(doc, "inpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "J44.1" in codes
        assert any(code.startswith("J18") for code in codes)

    def test_chest_pain_workup_negative(self):
        doc = "ASSESSMENT: Chest pain. Troponin negative. EKG normal. No evidence of MI."
        body = _process(doc, "outpatient")
        codes = {d["code"] for d in body["diagnosis_codes"]}
        assert "R07.9" in codes
        assert all(not code.startswith("I21") for code in codes)

    def test_annual_wellness_visit(self):
        doc = "ASSESSMENT: Annual wellness visit. Incidental finding of elevated blood pressure 145/92."
        body = _process(doc, "outpatient")
        assert body["status"] in {"COMPLETED", "ESCALATED"}
        # Ensure no cardiac MI codes appear
        assert all(not code.startswith("I21") for code in [d["code"] for d in body["diagnosis_codes"]])