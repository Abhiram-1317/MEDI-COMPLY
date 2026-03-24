import re
import statistics
from typing import Dict, List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from medi_comply.api.routes.coding import router as coding_router

app = FastAPI()
app.include_router(coding_router)
client = TestClient(app)


GOLDEN_TEST_CASES: List[Dict] = [
    {
        "clinical_document": "ASSESSMENT: 1. Essential hypertension",
        "encounter_type": "outpatient",
        "expected_primary": "I10",
        "expected_codes": ["I10"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Type 2 diabetes mellitus without complications",
        "encounter_type": "outpatient",
        "expected_primary": "E11.9",
        "expected_codes": ["E11.9"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Acute NSTEMI\n2. Essential hypertension",
        "encounter_type": "inpatient",
        "expected_primary": "I21.4",
        "expected_codes": ["I21.4", "I10"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Community acquired pneumonia\n2. COPD exacerbation",
        "encounter_type": "inpatient",
        "expected_primary": "J18.9",
        "expected_codes": ["J18.9", "J44.1"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Type 2 diabetes with diabetic chronic kidney disease\n2. CKD stage 3b",
        "encounter_type": "outpatient",
        "expected_primary": "E11.22",
        "expected_codes": ["E11.22", "N18.32"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Chest pain, unspecified",
        "encounter_type": "outpatient",
        "expected_primary": "R07.9",
        "expected_codes": ["R07.9"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Congestive heart failure\n2. Atrial fibrillation",
        "encounter_type": "inpatient",
        "expected_primary": "I50.9",
        "expected_codes": ["I50.9", "I48.91"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Urinary tract infection\n2. Type 2 diabetes",
        "encounter_type": "outpatient",
        "expected_primary": "N39.0",
        "expected_codes": ["N39.0", "E11.9"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Low back pain",
        "encounter_type": "outpatient",
        "expected_primary": "M54.5",
        "expected_codes": ["M54.5"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Major depressive disorder\n2. Generalized anxiety disorder",
        "encounter_type": "outpatient",
        "expected_primary": "F32.9",
        "expected_codes": ["F32.9", "F41.1"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. GERD with esophagitis",
        "encounter_type": "outpatient",
        "expected_primary": "K21.0",
        "expected_codes": ["K21.0"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Sepsis, unspecified\n2. Pneumonia\n3. Acute kidney injury",
        "encounter_type": "inpatient",
        "expected_primary": "A41.9",
        "expected_codes": ["A41.9", "J18.9", "N17.9"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Pulmonary embolism\n2. Deep vein thrombosis",
        "encounter_type": "inpatient",
        "expected_primary": "I26.99",
        "expected_codes": ["I26.99", "I82.40"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Morbid obesity\n2. Essential hypertension\n3. Type 2 diabetes",
        "encounter_type": "outpatient",
        "expected_primary": "E66.01",
        "expected_codes": ["E66.01", "I10", "E11.9"],
    },
    {
        "clinical_document": "ASSESSMENT: 1. Hypothyroidism\n2. Anemia",
        "encounter_type": "outpatient",
        "expected_primary": "E03.9",
        "expected_codes": ["E03.9", "D64.9"],
    },
]


def _process(doc: str, encounter_type: str) -> Dict:
    resp = client.post("/api/v1/coding/process", json={"clinical_document": doc, "encounter_type": encounter_type})
    assert resp.status_code == 200
    return resp.json()


ICD_PATTERN = re.compile(r"^[A-Z][0-9A-Z]{1,3}(?:\.[0-9A-Z]{1,4})?$")


class TestCodingAccuracy:
    def test_primary_diagnosis_accuracy(self):
        correct = 0
        total = len(GOLDEN_TEST_CASES)
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            returned_codes = {d["code"] for d in body["diagnosis_codes"]}
            if case["expected_primary"] in returned_codes:
                correct += 1
        accuracy = correct / total
        assert accuracy >= 0.8

    def test_code_recall(self):
        total_expected = 0
        total_found = 0
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            returned_codes = {d["code"] for d in body["diagnosis_codes"]}
            for code in case["expected_codes"]:
                total_expected += 1
                if code in returned_codes:
                    total_found += 1
        recall = total_found / total_expected if total_expected else 1.0
        assert recall >= 0.7

    def test_no_hallucinated_codes(self):
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            for entry in body["diagnosis_codes"]:
                code = entry["code"]
                assert ICD_PATTERN.match(code)

    def test_sequencing_correctness(self):
        for case in GOLDEN_TEST_CASES:
            if len(case["expected_codes"]) < 2:
                continue
            body = _process(case["clinical_document"], case["encounter_type"])
            primary = body["diagnosis_codes"][0]["code"] if body["diagnosis_codes"] else None
            assert primary in case["expected_codes"]

    def test_specificity_level(self):
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            for entry in body["diagnosis_codes"]:
                code = entry["code"]
                assert len(code) >= 3

    def test_overall_accuracy_threshold(self):
        matches = 0
        total = len(GOLDEN_TEST_CASES)
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            returned_codes = {d["code"] for d in body["diagnosis_codes"]}
            if case["expected_primary"] in returned_codes:
                matches += 1
        overall = matches / total
        assert overall >= 0.75


class TestConfidenceCalibration:
    def test_confidence_range(self):
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            for entry in body["diagnosis_codes"]:
                assert 0.0 <= entry["confidence"] <= 1.0

    def test_high_confidence_correlates_with_accuracy(self):
        high_conf_correct = 0
        high_conf_total = 0
        low_conf_correct = 0
        low_conf_total = 0
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            if not body["diagnosis_codes"]:
                continue
            primary_entry = body["diagnosis_codes"][0]
            conf = primary_entry["confidence"]
            is_correct = primary_entry["code"] == case["expected_primary"]
            if conf >= 0.9:
                high_conf_total += 1
                if is_correct:
                    high_conf_correct += 1
            else:
                low_conf_total += 1
                if is_correct:
                    low_conf_correct += 1
        if high_conf_total > 0 and low_conf_total > 0:
            high_acc = high_conf_correct / high_conf_total
            low_acc = low_conf_correct / low_conf_total
            assert high_acc >= low_acc

    def test_clear_cases_higher_confidence(self):
        simple_doc = "ASSESSMENT: 1. Essential hypertension"
        complex_doc = "ASSESSMENT: 1. Possible COPD vs asthma. 2. Suspected sleep apnea. 3. Obesity."
        simple_result = _process(simple_doc, "outpatient")
        complex_result = _process(complex_doc, "outpatient")
        assert simple_result["overall_confidence"] >= complex_result["overall_confidence"]

    def test_single_diagnosis_higher_confidence(self):
        single_doc = "ASSESSMENT: 1. Essential hypertension"
        multi_doc = "ASSESSMENT: 1. Hypertension 2. Diabetes 3. COPD 4. Heart failure 5. CKD"
        single_result = _process(single_doc, "outpatient")
        multi_result = _process(multi_doc, "outpatient")
        assert single_result["overall_confidence"] >= 0.5
        assert single_result["overall_confidence"] >= multi_result["overall_confidence"]

    def test_confidence_consistency(self):
        clinical_document = "ASSESSMENT: 1. Type 2 diabetes mellitus"
        results = [_process(clinical_document, "outpatient") for _ in range(3)]
        confidences = [r["overall_confidence"] for r in results]
        assert max(confidences) - min(confidences) < 0.1

    def test_average_confidence_reasonable(self):
        all_confidences = []
        for case in GOLDEN_TEST_CASES:
            result = _process(case["clinical_document"], case["encounter_type"])
            all_confidences.append(result["overall_confidence"])
        avg = statistics.mean(all_confidences)
        assert 0.8 <= avg <= 0.99


class TestComplianceCheckCompleteness:
    def test_all_10_checks_present(self):
        body = _process("ASSESSMENT: Chest pain and hypertension.", "outpatient")
        expected_checks = {
            "CODE_EXISTS",
            "NCCI_EDITS",
            "EXCLUDES_1",
            "SPECIFICITY",
            "AGE_SEX",
            "MEDICAL_NECESSITY",
            "EVIDENCE_LINKED",
            "SEQUENCING",
            "CONFIDENCE_THRESHOLD",
            "PHI_CHECK",
        }
        names = {c["check_name"] for c in body["compliance_report"]["checks"]}
        assert expected_checks.issubset(names)

    def test_compliance_pass_rate(self):
        pass_count = 0
        total_checks = 0
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            for check in body["compliance_report"]["checks"]:
                total_checks += 1
                if check["result"] in {"PASS", "SOFT_FAIL"}:
                    pass_count += 1
        pass_rate = pass_count / total_checks if total_checks else 1.0
        assert pass_rate >= 0.9

    def test_no_hard_fail_on_golden(self):
        for case in GOLDEN_TEST_CASES:
            body = _process(case["clinical_document"], case["encounter_type"])
            assert all(check["result"] != "HARD_FAIL" for check in body["compliance_report"]["checks"])

    def test_compliance_report_complete(self):
        body = _process("ASSESSMENT: COPD exacerbation with pneumonia.", "inpatient")
        assert isinstance(body.get("compliance_report"), dict)
        assert body["compliance_report"].get("checks")