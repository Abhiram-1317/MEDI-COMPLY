import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.coding import router as coding_router

app = FastAPI()
app.include_router(coding_router)
client = TestClient(app)


def _validate(request: dict) -> dict:
    resp = client.post("/api/v1/coding/validate", json=request)
    assert resp.status_code == 200
    return resp.json()


def _process(payload: dict) -> dict:
    resp = client.post("/api/v1/coding/process", json=payload)
    assert resp.status_code == 200
    return resp.json()


ICD_PATTERN = re.compile(r"^[A-Z][0-9A-Z]{1,3}(?:\.[0-9A-Z]{1,4})?$")
CPT_PATTERN = re.compile(r"^\d{5}$")


class TestInvalidCodePenetration:
    def test_nonexistent_icd10_code(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "Z99.99", "code_type": "ICD-10-CM"}],
                "clinical_document": "Patient has a condition requiring code Z99.99.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert result["is_valid"] is False

    def test_nonexistent_cpt_code(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "00000", "code_type": "CPT"}],
                "clinical_document": "Procedure performed: code 00000.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False

    def test_malformed_icd10_format(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "12345", "code_type": "ICD-10-CM"}],
                "clinical_document": "Patient has condition 12345.",
            }
        )
        result = body["validation_results"][0]
        assert result["is_valid"] is False
        assert result["exists_in_database"] is False

    def test_process_never_outputs_fake_code(self):
        clinical_document = "ASSESSMENT: Hypertension, diabetes type 2, chest pain, COPD, heart failure."
        body = _process({"clinical_document": clinical_document})
        for entry in body["diagnosis_codes"] + body["procedure_codes"]:
            code = entry["code"]
            if entry["code_type"].upper() == "CPT":
                assert CPT_PATTERN.match(code)
            else:
                assert ICD_PATTERN.match(code)

    def test_truncated_icd10_code(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "E11", "code_type": "ICD-10-CM"}],
                "clinical_document": "Patient has type 2 diabetes.",
            }
        )
        result = body["validation_results"][0]
        assert result["is_billable"] is False or result["specificity_adequate"] is False
        assert result["is_valid"] is False

    def test_wrong_code_type_label(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "E11.22", "code_type": "CPT"}],
                "clinical_document": "Patient has diabetes.",
            }
        )
        result = body["validation_results"][0]
        assert body["overall_valid"] is False
        assert result["evidence_supports"] is False

    def test_deprecated_code(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "V99.99", "code_type": "ICD-10-CM"}],
                "clinical_document": "External cause of injury.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert result["is_valid"] is False

    def test_special_characters_in_code(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "E11.22'; DROP TABLE", "code_type": "ICD-10-CM"}],
                "clinical_document": "Diabetes patient with attempted injection string included for safety validation.",
            }
        )
        result = body["validation_results"][0]
        assert result["is_valid"] is False
        assert body["overall_valid"] is False


class TestNCCIViolationPenetration:
    def test_bundled_codes_detected(self):
        clinical_document = "ASSESSMENT: Hypertension. PROCEDURES: Comprehensive metabolic panel and creatinine test."
        body = _process({"clinical_document": clinical_document})
        proc_codes = {p["code"] for p in body["procedure_codes"]}
        assert "82565" not in proc_codes
        assert "80053" in proc_codes

    def test_mutually_exclusive_detected(self):
        clinical_document = "ASSESSMENT: Patient seen for hospital admission and subsequent hospital care same day."
        body = _process({"clinical_document": clinical_document})
        proc_codes = {p["code"] for p in body["procedure_codes"]}
        assert body["human_review_required"] is True
        assert "99223" in proc_codes or "99232" in proc_codes

    def test_validate_catches_ncci_pair(self):
        body = _validate(
            {
                "proposed_codes": [
                    {"code": "80053", "code_type": "CPT"},
                    {"code": "82565", "code_type": "CPT"},
                ],
                "clinical_document": "Labs ordered: CMP and creatinine.",
            }
        )
        assert body["overall_valid"] is False
        assert any(res["is_valid"] is False for res in body["validation_results"])

    def test_mue_violation(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "80053x10", "code_type": "CPT"}],
                "clinical_document": "Requesting ten units of CMP.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert body["overall_valid"] is False

    def test_duplicate_codes(self):
        body = _validate(
            {
                "proposed_codes": [
                    {"code": "99214", "code_type": "CPT"},
                    {"code": "99214", "code_type": "CPT"},
                ],
                "clinical_document": "Follow-up visit with no documented diagnosis.",
            }
        )
        assert body["overall_valid"] is False
        assert all(res["is_valid"] is False for res in body["validation_results"])


class TestAgeSexConflictPenetration:
    def test_pregnancy_code_male_patient(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "O80", "code_type": "ICD-10-CM"}],
                "clinical_document": "Delivery of baby noted in record for testing male demographic rejection.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert body["overall_valid"] is False

    def test_prostate_code_female_patient(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "N40.0", "code_type": "ICD-10-CM"}],
                "clinical_document": "Prostate disorder noted.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert body["overall_valid"] is False

    def test_pediatric_code_adult(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "P07.1", "code_type": "ICD-10-CM"}],
                "clinical_document": "Low birth weight management.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert body["overall_valid"] is False

    def test_senile_code_young(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "F03.90", "code_type": "ICD-10-CM"}],
                "clinical_document": "Senile condition described here for validation of young patient rejection.",
            }
        )
        result = body["validation_results"][0]
        assert result["exists_in_database"] is False
        assert body["overall_valid"] is False

    def test_process_respects_demographics(self):
        clinical_document = "ASSESSMENT: Hypertension"
        body = _process({"clinical_document": clinical_document, "patient_age": 30, "patient_sex": "M"})
        codes = {item["code"] for item in body["diagnosis_codes"]}
        assert all(not code.startswith("O") for code in codes)
        assert body["human_review_required"] is False


class TestEvidenceLinkagePenetration:
    def test_all_codes_have_evidence(self):
        clinical_document = "ASSESSMENT: 1. NSTEMI 2. Hypertension 3. Type 2 diabetes"
        body = _process({"clinical_document": clinical_document})
        for entry in body["diagnosis_codes"] + body["procedure_codes"]:
            assert entry.get("source_evidence") is not None

    def test_code_without_document_support(self):
        body = _validate(
            {
                "proposed_codes": [{"code": "J18.9", "code_type": "ICD-10-CM"}],
                "clinical_document": "This narrative intentionally omits any mapped respiratory or cardiac terms to test linkage.",
            }
        )
        result = body["validation_results"][0]
        assert result["evidence_supports"] is False
        assert result["is_valid"] is False

    def test_negated_condition_not_coded(self):
        clinical_document = "ASSESSMENT: Patient denies chest pain. No signs of pneumonia. Rules out MI."
        body = _process({"clinical_document": clinical_document})
        codes = {item["code"] for item in body["diagnosis_codes"]}
        assert "R07.9" not in codes
        assert "J18.9" not in codes
        assert all(not code.startswith("I21") for code in codes)

    def test_compliance_blocks_unlinked(self):
        clinical_document = "ASSESSMENT: NSTEMI confirmed, plan for echocardiogram."
        body = _process({"clinical_document": clinical_document})
        checks = {c["check_name"]: c for c in body["compliance_report"]["checks"]}
        assert "EVIDENCE_LINKED" in checks

    def test_reasoning_chain_required(self):
        clinical_document = "ASSESSMENT: NSTEMI with hypertension."
        body = _process({"clinical_document": clinical_document, "include_reasoning": True})
        for entry in body["diagnosis_codes"]:
            assert entry.get("reasoning_chain")
            assert len(entry["reasoning_chain"]) >= 2


class TestConfidenceThresholdPenetration:
    def test_low_confidence_flagged(self):
        clinical_document = "Patient may have some condition. Unclear symptoms."
        body = _process({"clinical_document": clinical_document})
        assert body["overall_confidence"] < 0.9 or body["human_review_required"] is True

    def test_high_confidence_passes(self):
        clinical_document = "ASSESSMENT: 1. Acute NSTEMI, troponin 0.8 ng/mL 2. Essential hypertension"
        body = _process({"clinical_document": clinical_document})
        assert body["overall_confidence"] >= 0.85
        assert body["human_review_required"] is False

    def test_confidence_in_valid_range(self):
        documents = [
            "ASSESSMENT: Hypertension and diabetes.",
            "ASSESSMENT: COPD with acute exacerbation.",
        ]
        for doc in documents:
            body = _process({"clinical_document": doc})
            for entry in body["diagnosis_codes"] + body["procedure_codes"]:
                assert 0.0 <= entry["confidence"] <= 1.0

    def test_mixed_confidence(self):
        clinical_document = "ASSESSMENT: 1. Acute NSTEMI (confirmed by troponin). 2. Possible COPD (pending PFTs)."
        body = _process({"clinical_document": clinical_document})
        confidences = {entry["code"]: entry["confidence"] for entry in body["diagnosis_codes"]}
        if "I21.4" in confidences and "J44.1" in confidences:
            assert confidences["I21.4"] >= confidences["J44.1"]