from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.coding import router, _get_processor

app = FastAPI()
app.include_router(router)
client = TestClient(app)


def _clear_audits() -> None:
    try:
        _get_processor()._audit_store.clear()  # type: ignore[attr-defined]
    except Exception:
        pass


class TestCodingProcess:
    def setup_method(self) -> None:
        _clear_audits()

    def test_process_basic_note(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nChest pain with shortness of breath.",
                "encounter_type": "outpatient",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["diagnosis_codes"]

    def test_process_inpatient(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nAcute NSTEMI with CKD stage 3b and hypertension.",
                "encounter_type": "inpatient",
                "patient_age": 70,
                "patient_sex": "M",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert any(code["code"] == "I21.4" for code in data["diagnosis_codes"])
        assert any(code["code"] == "N18.32" for code in data["diagnosis_codes"])

    def test_process_outpatient(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nHypertension follow up visit.",
                "encounter_type": "outpatient",
                "patient_age": 55,
                "patient_sex": "F",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in {"COMPLETED", "ESCALATED"}

    def test_process_returns_diagnosis_codes(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nHypertension and chest pain.",
                "encounter_type": "outpatient",
            },
        )
        assert resp.status_code == 200
        assert isinstance(resp.json()["diagnosis_codes"], list)

    def test_process_returns_procedure_codes(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nAcute NSTEMI. Troponin ordered.",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 200
        assert isinstance(resp.json()["procedure_codes"], list)

    def test_process_nstemi_primary(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nNSTEMI with chest pain.",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 200
        primary = resp.json()["diagnosis_codes"][0]["code"]
        assert primary == "I21.4"

    def test_process_diabetes_with_ckd(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nType 2 diabetes with diabetic nephropathy and CKD stage 3b.",
                "encounter_type": "outpatient",
            },
        )
        assert resp.status_code == 200
        codes = [c["code"] for c in resp.json()["diagnosis_codes"]]
        assert "E11.22" in codes
        assert "E11.9" not in codes

    def test_process_negation(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nPatient denies chest pain or shortness of breath.",
                "encounter_type": "outpatient",
            },
        )
        assert resp.status_code == 200
        codes = [c["code"] for c in resp.json()["diagnosis_codes"]]
        assert "R07.9" not in codes
        assert "R06.02" not in codes

    def test_process_compliance_report(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nHypertension and CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["compliance_report"]

    def test_process_audit_id_generated(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nHypertension and CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["audit_id"]

    def test_process_confidence_scores(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nAcute NSTEMI with CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 200
        confidences = [c["confidence"] for c in resp.json()["diagnosis_codes"] + resp.json()["procedure_codes"]]
        assert all(0.0 <= c <= 1.0 for c in confidences)

    def test_process_empty_document_fails(self) -> None:
        resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "",
                "encounter_type": "inpatient",
            },
        )
        assert resp.status_code == 422


class TestCodingValidate:
    def setup_method(self) -> None:
        _clear_audits()

    def test_validate_valid_codes(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={
                "proposed_codes": [
                    {"code": "E11.22", "code_type": "ICD-10-CM"},
                    {"code": "99214", "code_type": "CPT"},
                ],
                "clinical_document": "Type 2 diabetes with diabetic nephropathy. Office visit moderate.",
                "encounter_type": "outpatient",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["overall_valid"] is True

    def test_validate_invalid_code(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={
                "proposed_codes": [{"code": "BADCODE", "code_type": "ICD-10-CM"}],
                "clinical_document": "Hypertension noted in clinic visit with elevated pressures.",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["overall_valid"] is False
        assert body["validation_results"][0]["exists_in_database"] is False

    def test_validate_returns_suggestions(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={
                "proposed_codes": [{"code": "NOTREAL", "code_type": "ICD-10-CM"}],
                "clinical_document": "Hypertension noted in clinic visit with elevated pressures.",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["suggestions"]

    def test_validate_specificity(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={
                "proposed_codes": [{"code": "E11.9", "code_type": "ICD-10-CM"}],
                "clinical_document": "Type 2 diabetes with nephropathy.",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["validation_results"][0]
        assert result["specificity_adequate"] is False

    def test_validate_evidence_check(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={
                "proposed_codes": [{"code": "I10", "code_type": "ICD-10-CM"}],
                "clinical_document": "No relevant findings noted.",
            },
        )
        assert resp.status_code == 200
        result = resp.json()["validation_results"][0]
        assert result["evidence_supports"] is False

    def test_validate_empty_codes_fails(self) -> None:
        resp = client.post(
            "/api/v1/coding/validate",
            json={"proposed_codes": [], "clinical_document": "Hypertension."},
        )
        assert resp.status_code == 422


class TestCodingAudit:
    def setup_method(self) -> None:
        _clear_audits()

    def test_get_audit_after_process(self) -> None:
        process_resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nNSTEMI and CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        assert process_resp.status_code == 200
        audit_id = process_resp.json()["audit_id"]

        audit_resp = client.get(f"/api/v1/coding/audit/{audit_id}")
        assert audit_resp.status_code == 200

    def test_get_audit_not_found(self) -> None:
        resp = client.get("/api/v1/coding/audit/UNKNOWN-ID")
        assert resp.status_code == 404

    def test_audit_has_reasoning(self) -> None:
        process_resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nNSTEMI and CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        audit_id = process_resp.json()["audit_id"]
        audit_resp = client.get(f"/api/v1/coding/audit/{audit_id}")
        payload = audit_resp.json()
        assert payload["coding_decisions"]

    def test_audit_has_compliance(self) -> None:
        process_resp = client.post(
            "/api/v1/coding/process",
            json={
                "clinical_document": "ASSESSMENT:\nNSTEMI and CKD stage 3b.",
                "encounter_type": "inpatient",
            },
        )
        audit_id = process_resp.json()["audit_id"]
        audit_resp = client.get(f"/api/v1/coding/audit/{audit_id}")
        payload = audit_resp.json()
        assert payload["compliance_checks"]
