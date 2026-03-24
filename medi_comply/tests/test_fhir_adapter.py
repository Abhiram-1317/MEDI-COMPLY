import json
from datetime import date

import pytest

from medi_comply.integrations.fhir_adapter import (
    FHIRAdapter,
    FHIRConversionResult,
    FHIRResourceType,
    InternalClaim,
    InternalCondition,
    InternalEncounter,
    InternalMedicationRequest,
    InternalObservation,
    InternalPatient,
    InternalProcedure,
)


@pytest.fixture
def adapter() -> FHIRAdapter:
    return FHIRAdapter()


@pytest.fixture
def sample_patient_fhir() -> dict:
    return {
        "resourceType": "Patient",
        "id": "test-patient-001",
        "name": [{"family": "Doe", "given": ["Jane", "Marie"]}],
        "birthDate": "1960-03-22",
        "gender": "female",
        "address": [
            {"line": ["456 Oak Ave"], "city": "Chicago", "state": "IL", "postalCode": "60601"}
        ],
        "telecom": [{"system": "phone", "value": "555-987-6543"}],
        "identifier": [{"type": {"coding": [{"code": "MR"}]}, "value": "MRN-12345"}],
    }


@pytest.fixture
def sample_condition_fhir() -> dict:
    return {
        "resourceType": "Condition",
        "id": "test-condition-001",
        "subject": {"reference": "Patient/test-patient-001"},
        "encounter": {"reference": "Encounter/test-enc-001"},
        "code": {
            "coding": [
                {
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": "I21.4",
                    "display": "Non-ST elevation myocardial infarction",
                }
            ]
        },
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "category": [{"coding": [{"code": "encounter-diagnosis"}]}],
        "onsetDateTime": "2024-01-15T10:30:00Z",
        "recordedDate": "2024-01-15",
    }


@pytest.fixture
def sample_encounter_fhir() -> dict:
    return {
        "resourceType": "Encounter",
        "id": "test-enc-001",
        "status": "in-progress",
        "class": {"code": "IMP", "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode"},
        "subject": {"reference": "Patient/test-patient-001"},
        "period": {"start": "2024-01-15T08:00:00Z"},
        "participant": [{"individual": {"reference": "Practitioner/dr-smith"}}],
        "reasonCode": [{"coding": [{"code": "R07.9", "display": "Chest pain"}]}],
        "diagnosis": [{"condition": {"reference": "Condition/test-condition-001"}}],
    }


@pytest.fixture
def sample_procedure_fhir() -> dict:
    return {
        "resourceType": "Procedure",
        "id": "proc-001",
        "status": "completed",
        "subject": {"reference": "Patient/test-patient-001"},
        "encounter": {"reference": "Encounter/test-enc-001"},
        "code": {
            "coding": [
                {"system": "http://www.ama-assn.org/go/cpt", "code": "93010", "display": "ECG"}
            ]
        },
        "performedDateTime": "2024-01-15T09:00:00Z",
        "performer": [{"actor": {"reference": "Practitioner/dr-smith"}}],
        "bodySite": [{"coding": [{"display": "Left arm"}]}],
    }


@pytest.fixture
def sample_claim_fhir() -> dict:
    return {
        "resourceType": "Claim",
        "id": "claim-001",
        "status": "active",
        "type": {"coding": [{"code": "professional"}]},
        "patient": {"reference": "Patient/test-patient-001"},
        "provider": {"reference": "Organization/provider-1"},
        "insurer": {"reference": "Organization/payer-1"},
        "created": "2024-01-16",
        "billablePeriod": {"start": "2024-01-15", "end": "2024-01-16"},
        "diagnosis": [
            {
                "sequence": 1,
                "diagnosisCodeableConcept": {"coding": [{"code": "I21.4"}]},
                "type": [{"coding": [{"code": "principal"}]}],
            }
        ],
        "item": [
            {
                "sequence": 1,
                "productOrService": {"coding": [{"code": "93010"}]},
                "quantity": {"value": 2},
                "unitPrice": {"value": 100.0},
            }
        ],
    }


@pytest.fixture
def sample_observation_fhir() -> dict:
    return {
        "resourceType": "Observation",
        "id": "obs-001",
        "status": "final",
        "subject": {"reference": "Patient/test-patient-001"},
        "encounter": {"reference": "Encounter/test-enc-001"},
        "code": {"coding": [{"system": "http://loinc.org", "code": "718-7", "display": "Hemoglobin"}]},
        "valueQuantity": {"value": 12.5, "unit": "g/dL"},
        "referenceRange": [{"text": "12-16 g/dL"}],
        "effectiveDateTime": "2024-01-15T11:00:00Z",
        "interpretation": [{"coding": [{"code": "normal"}]}],
    }


@pytest.fixture
def sample_medication_request_fhir() -> dict:
    return {
        "resourceType": "MedicationRequest",
        "id": "rx-001",
        "status": "active",
        "subject": {"reference": "Patient/test-patient-001"},
        "encounter": {"reference": "Encounter/test-enc-001"},
        "medicationCodeableConcept": {
            "coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm", "code": "12345", "display": "Atorvastatin"}]
        },
        "dosageInstruction": [
            {
                "text": "Take one tablet nightly",
                "timing": {"code": {"text": "once daily"}},
                "route": {"coding": [{"display": "oral"}]},
            }
        ],
        "requester": {"reference": "Practitioner/dr-smith"},
        "authoredOn": "2024-01-15",
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestFHIRValidation:
    def test_valid_patient_resource(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Valid patient passes validation."""
        result = adapter.validate_fhir_resource(sample_patient_fhir)
        assert result.is_valid is True and result.errors == []

    def test_valid_condition_resource(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Valid condition passes validation."""
        result = adapter.validate_fhir_resource(sample_condition_fhir)
        assert result.is_valid is True and result.resource_type == "Condition"

    def test_missing_resource_type(self, adapter: FHIRAdapter) -> None:
        """Resource without resourceType should be invalid."""
        result = adapter.validate_fhir_resource({"id": "x"})
        assert result.is_valid is False
        assert "Missing resourceType" in result.errors[0]

    def test_unsupported_resource_type(self, adapter: FHIRAdapter) -> None:
        """Unknown resource type should fail validation."""
        result = adapter.validate_fhir_resource({"resourceType": "Foo"})
        assert result.is_valid is False
        assert "Unsupported" in result.errors[0]

    def test_missing_required_fields(self, adapter: FHIRAdapter) -> None:
        """Patient missing id should be invalid."""
        result = adapter.validate_fhir_resource({"resourceType": "Patient"})
        assert result.is_valid is False
        assert any("id" in err for err in result.errors)

    def test_empty_resource(self, adapter: FHIRAdapter) -> None:
        """Empty dict is invalid."""
        result = adapter.validate_fhir_resource({})
        assert result.is_valid is False


# ---------------------------------------------------------------------------
# Patient parsing
# ---------------------------------------------------------------------------


class TestParsePatient:
    def test_parse_complete_patient(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """All patient fields should be parsed."""
        patient = adapter.parse_patient(sample_patient_fhir)
        assert patient.patient_id == "test-patient-001"
        assert patient.first_name == "Jane"
        assert patient.last_name == "Doe"
        assert patient.gender == "F"
        assert patient.phone == "555-987-6543"
        assert patient.mrn == "MRN-12345"

    def test_parse_patient_name(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """First and last names should be extracted."""
        patient = adapter.parse_patient(sample_patient_fhir)
        assert patient.first_name == "Jane" and patient.last_name == "Doe"

    def test_parse_patient_gender(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Gender mapping male/female/other works."""
        sample_patient_fhir["gender"] = "male"
        patient = adapter.parse_patient(sample_patient_fhir)
        assert patient.gender == "M"
        sample_patient_fhir["gender"] = "other"
        patient_other = adapter.parse_patient(sample_patient_fhir)
        assert patient_other.gender == "U"

    def test_parse_patient_age_calculation(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Age should be calculated from birthDate."""
        today = date.today()
        birth = today.replace(year=today.year - 30)
        sample_patient_fhir["birthDate"] = birth.isoformat()
        patient = adapter.parse_patient(sample_patient_fhir)
        expected_age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        assert patient.age == expected_age

    def test_parse_patient_address(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Address fields are parsed correctly."""
        patient = adapter.parse_patient(sample_patient_fhir)
        assert patient.address["city"] == "Chicago"
        assert patient.address["postalCode"] == "60601"

    def test_parse_patient_mrn(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """MRN identifier should be captured."""
        patient = adapter.parse_patient(sample_patient_fhir)
        assert patient.mrn == "MRN-12345"

    def test_parse_patient_missing_optional_fields(self, adapter: FHIRAdapter) -> None:
        """Missing optional fields should not crash."""
        minimal = {"resourceType": "Patient", "id": "p1"}
        patient = adapter.parse_patient(minimal)
        assert patient.patient_id == "p1"
        assert patient.first_name is None
        assert patient.address is None

    def test_parse_patient_minimal(self, adapter: FHIRAdapter) -> None:
        """Minimal patient still parses."""
        patient = adapter.parse_patient({"resourceType": "Patient", "id": "only-id"})
        assert patient.patient_id == "only-id"


# ---------------------------------------------------------------------------
# Encounter parsing
# ---------------------------------------------------------------------------


class TestParseEncounter:
    def test_parse_inpatient_encounter(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """IMP class should map to inpatient."""
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.encounter_type == "inpatient"

    def test_parse_outpatient_encounter(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """AMB class should map to outpatient."""
        sample_encounter_fhir["class"] = {"code": "AMB"}
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.encounter_type == "outpatient"

    def test_parse_emergency_encounter(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """EMER class should map to emergency."""
        sample_encounter_fhir["class"] = {"code": "EMER"}
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.encounter_type == "emergency"

    def test_parse_encounter_dates(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """Period start/end should be parsed."""
        sample_encounter_fhir["period"]["end"] = "2024-01-15T12:00:00Z"
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.start_date == "2024-01-15" and encounter.end_date == "2024-01-15"

    def test_parse_encounter_provider(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """Participant provider is extracted."""
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.provider_id == "dr-smith"

    def test_parse_encounter_reason_codes(self, adapter: FHIRAdapter, sample_encounter_fhir: dict) -> None:
        """Reason codes should be captured."""
        encounter = adapter.parse_encounter(sample_encounter_fhir)
        assert encounter.reason_codes == ["R07.9"]


# ---------------------------------------------------------------------------
# Condition parsing
# ---------------------------------------------------------------------------


class TestParseCondition:
    def test_parse_condition_icd10(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """ICD-10 coding should be extracted."""
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.icd10_code == "I21.4"

    def test_parse_condition_status(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Clinical and verification statuses extracted."""
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.clinical_status == "active" and condition.verification_status == "confirmed"

    def test_parse_condition_patient_reference(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Patient reference parsed from subject."""
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.patient_id == "test-patient-001"

    def test_parse_condition_onset(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Onset date normalized to date component."""
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.onset_date == "2024-01-15"

    def test_parse_condition_no_icd10(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Handles missing ICD-10 coding gracefully."""
        sample_condition_fhir["code"] = {"coding": [{"system": "http://snomed.info/sct", "code": "123"}]}
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.icd10_code == "123"

    def test_parse_condition_multiple_codings(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Selects ICD-10 when multiple codings present."""
        sample_condition_fhir["code"]["coding"].insert(
            0, {"system": "http://snomed.info/sct", "code": "SCT-1", "display": "SNOMED"}
        )
        condition = adapter.parse_condition(sample_condition_fhir)
        assert condition.icd10_code == "I21.4"


# ---------------------------------------------------------------------------
# Procedure parsing
# ---------------------------------------------------------------------------


class TestParseProcedure:
    def test_parse_procedure_cpt(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """CPT code extracted from procedure."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        assert procedure.cpt_code == "93010"

    def test_parse_procedure_status(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """Status field captured."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        assert procedure.status == "completed"

    def test_parse_procedure_performer(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """Performer reference parsed."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        assert procedure.performer_id == "dr-smith"

    def test_parse_procedure_body_site(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """Body site and laterality inferred."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        assert procedure.body_site == "Left arm"
        assert procedure.laterality == "left"

    def test_parse_procedure_dates(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """Performed date normalized to date string."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        assert procedure.performed_date == "2024-01-15"


# ---------------------------------------------------------------------------
# Claim parsing
# ---------------------------------------------------------------------------


class TestParseClaim:
    def test_parse_claim_basic(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Basic claim fields extracted."""
        claim = adapter.parse_claim(sample_claim_fhir)
        assert claim.claim_id == "claim-001"
        assert claim.patient_id == "test-patient-001"
        assert claim.payer_id == "payer-1"
        assert claim.provider_id == "provider-1"

    def test_parse_claim_line_items(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Line items captured with codes and prices."""
        claim = adapter.parse_claim(sample_claim_fhir)
        assert claim.line_items[0]["cpt_code"] == "93010"
        assert claim.line_items[0]["line_total"] == 200.0

    def test_parse_claim_diagnosis(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Diagnosis codes extracted."""
        claim = adapter.parse_claim(sample_claim_fhir)
        assert claim.diagnosis_codes[0]["code"] == "I21.4"
        assert claim.diagnosis_codes[0]["type"] == "principal"

    def test_parse_claim_total(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Total amount calculated when not provided."""
        claim = adapter.parse_claim(sample_claim_fhir)
        assert claim.total_amount == 200.0

    def test_parse_claim_type(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Claim type mapping retained."""
        claim = adapter.parse_claim(sample_claim_fhir)
        assert claim.claim_type == "professional"


# ---------------------------------------------------------------------------
# Observation parsing
# ---------------------------------------------------------------------------


class TestParseObservation:
    def test_parse_observation_numeric(self, adapter: FHIRAdapter, sample_observation_fhir: dict) -> None:
        """Numeric value with unit parsed."""
        obs = adapter.parse_observation(sample_observation_fhir)
        assert obs.numeric_value == 12.5 and obs.unit == "g/dL"

    def test_parse_observation_string(self, adapter: FHIRAdapter, sample_observation_fhir: dict) -> None:
        """String value handled when present."""
        sample_observation_fhir.pop("valueQuantity", None)
        sample_observation_fhir["valueString"] = "Positive"
        obs = adapter.parse_observation(sample_observation_fhir)
        assert obs.value == "Positive"

    def test_parse_observation_loinc(self, adapter: FHIRAdapter, sample_observation_fhir: dict) -> None:
        """LOINC code extracted from coding."""
        obs = adapter.parse_observation(sample_observation_fhir)
        assert obs.code == "718-7"

    def test_parse_observation_interpretation(self, adapter: FHIRAdapter, sample_observation_fhir: dict) -> None:
        """Interpretation mapped to expected value."""
        sample_observation_fhir["interpretation"] = [{"coding": [{"code": "H"}]}]
        obs = adapter.parse_observation(sample_observation_fhir)
        assert obs.interpretation == "high"

    def test_parse_observation_reference_range(self, adapter: FHIRAdapter, sample_observation_fhir: dict) -> None:
        """Reference range captured."""
        obs = adapter.parse_observation(sample_observation_fhir)
        assert obs.reference_range == "12-16 g/dL"


# ---------------------------------------------------------------------------
# MedicationRequest parsing
# ---------------------------------------------------------------------------


class TestParseMedicationRequest:
    def test_parse_medication_basic(self, adapter: FHIRAdapter, sample_medication_request_fhir: dict) -> None:
        """Medication code and name extracted."""
        rx = adapter.parse_medication_request(sample_medication_request_fhir)
        assert rx.medication_code == "12345" and rx.medication_name == "Atorvastatin"

    def test_parse_medication_dosage(self, adapter: FHIRAdapter, sample_medication_request_fhir: dict) -> None:
        """Dosage instructions extracted."""
        rx = adapter.parse_medication_request(sample_medication_request_fhir)
        assert rx.dosage == "Take one tablet nightly"
        assert rx.frequency == "once daily"
        assert rx.route == "oral"

    def test_parse_medication_status(self, adapter: FHIRAdapter, sample_medication_request_fhir: dict) -> None:
        """Status is captured."""
        rx = adapter.parse_medication_request(sample_medication_request_fhir)
        assert rx.status == "active"

    def test_parse_medication_prescriber(self, adapter: FHIRAdapter, sample_medication_request_fhir: dict) -> None:
        """Prescriber reference extracted."""
        rx = adapter.parse_medication_request(sample_medication_request_fhir)
        assert rx.prescriber_id == "dr-smith"


# ---------------------------------------------------------------------------
# Bundle parsing
# ---------------------------------------------------------------------------


class TestParseBundle:
    def test_parse_bundle_multiple_resources(
        self,
        adapter: FHIRAdapter,
        sample_patient_fhir: dict,
        sample_condition_fhir: dict,
        sample_encounter_fhir: dict,
    ) -> None:
        """Bundle with multiple resources returns results for each."""
        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {"resource": sample_patient_fhir},
                {"resource": sample_condition_fhir},
                {"resource": sample_encounter_fhir},
            ],
        }
        results = adapter.parse_bundle(bundle)
        assert len(results) == 3
        assert all(isinstance(r, FHIRConversionResult) for r in results)
        assert {r.resource_type for r in results} >= {"Patient", "Condition", "Encounter"}

    def test_parse_bundle_empty(self, adapter: FHIRAdapter) -> None:
        """Empty bundle yields empty results."""
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": []}
        results = adapter.parse_bundle(bundle)
        assert results == []

    def test_parse_bundle_with_errors(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Bundle with bad resource returns partial success."""
        bundle = {
            "resourceType": "Bundle",
            "type": "collection",
            "entry": [
                {"resource": sample_patient_fhir},
                {"resource": {"resourceType": "Unknown"}},
            ],
        }
        results = adapter.parse_bundle(bundle)
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False

    def test_parse_bundle_single_resource(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Single resource bundle parsed."""
        bundle = {"resourceType": "Bundle", "type": "collection", "entry": [{"resource": sample_patient_fhir}]}
        results = adapter.parse_bundle(bundle)
        assert len(results) == 1 and results[0].success is True


# ---------------------------------------------------------------------------
# To FHIR conversions
# ---------------------------------------------------------------------------


class TestToFHIR:
    def test_to_fhir_patient(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Internal patient converts to FHIR Patient."""
        patient = adapter.parse_patient(sample_patient_fhir)
        fhir = adapter.to_fhir_patient(patient)
        assert fhir["resourceType"] == "Patient"
        assert fhir["id"] == patient.patient_id
        assert fhir["gender"] == "female"

    def test_to_fhir_condition(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Internal condition converts to FHIR Condition with ICD-10 system."""
        condition = adapter.parse_condition(sample_condition_fhir)
        fhir = adapter.to_fhir_condition(condition)
        coding = fhir["code"]["coding"][0]
        assert coding["system"] == "http://hl7.org/fhir/sid/icd-10-cm"
        assert coding["code"] == "I21.4"

    def test_to_fhir_procedure(self, adapter: FHIRAdapter, sample_procedure_fhir: dict) -> None:
        """Internal procedure converts to FHIR Procedure with CPT system."""
        procedure = adapter.parse_procedure(sample_procedure_fhir)
        fhir = adapter.to_fhir_procedure(procedure)
        coding = fhir["code"]["coding"][0]
        assert coding["system"] == "http://www.ama-assn.org/go/cpt"
        assert coding["code"] == "93010"

    def test_to_fhir_claim(self, adapter: FHIRAdapter, sample_claim_fhir: dict) -> None:
        """Internal claim converts to FHIR Claim."""
        claim = adapter.parse_claim(sample_claim_fhir)
        fhir = adapter.to_fhir_claim(claim)
        assert fhir["resourceType"] == "Claim"
        assert fhir["patient"]["reference"].endswith(claim.patient_id)
        assert fhir["item"][0]["productOrService"]["coding"][0]["code"] == "93010"

    def test_to_fhir_claim_response(self, adapter: FHIRAdapter) -> None:
        """Adjudication result renders as ClaimResponse."""
        adjudication = {
            "claim_id": "claim-001",
            "patient_id": "test-patient-001",
            "status": "active",
            "claim_type": "professional",
            "outcome": "complete",
            "disposition": "Processed",
            "items": [
                {
                    "adjudication": [
                        {"category": "submitted", "amount": 100.0},
                        {"category": "benefit", "amount": 80.0},
                    ]
                }
            ],
            "payment": {"amount": 80.0, "date": "2024-01-20"},
        }
        fhir = adapter.to_fhir_claim_response(adjudication)
        assert fhir["resourceType"] == "ClaimResponse"
        assert fhir["id"].startswith("cr-")
        assert fhir["patient"]["reference"].endswith("test-patient-001")

    def test_to_fhir_bundle(self, adapter: FHIRAdapter, sample_patient_fhir: dict, sample_condition_fhir: dict) -> None:
        """Multiple resources wrapped in Bundle."""
        patient = adapter.parse_patient(sample_patient_fhir)
        condition = adapter.parse_condition(sample_condition_fhir)
        bundle = adapter.to_fhir_bundle([adapter.to_fhir_patient(patient), adapter.to_fhir_condition(condition)])
        assert bundle["resourceType"] == "Bundle"
        assert len(bundle["entry"]) == 2

    def test_roundtrip_patient(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """Patient roundtrip preserves id and gender."""
        patient = adapter.parse_patient(sample_patient_fhir)
        fhir = adapter.to_fhir_patient(patient)
        assert fhir["id"] == sample_patient_fhir["id"]
        assert fhir["gender"] == sample_patient_fhir["gender"]

    def test_roundtrip_condition(self, adapter: FHIRAdapter, sample_condition_fhir: dict) -> None:
        """Condition roundtrip preserves ICD-10 code."""
        condition = adapter.parse_condition(sample_condition_fhir)
        fhir = adapter.to_fhir_condition(condition)
        assert fhir["code"]["coding"][0]["code"] == sample_condition_fhir["code"]["coding"][0]["code"]


# ---------------------------------------------------------------------------
# Universal parser
# ---------------------------------------------------------------------------


class TestUniversalParser:
    def test_parse_resource_routes_correctly(self, adapter: FHIRAdapter, sample_patient_fhir: dict) -> None:
        """parse_resource should route to patient parser."""
        result = adapter.parse_resource(sample_patient_fhir)
        assert result.success is True
        assert isinstance(result.internal_data, InternalPatient)

    def test_parse_resource_invalid(self, adapter: FHIRAdapter) -> None:
        """Invalid resource returns success False."""
        result = adapter.parse_resource({"id": "x"})
        assert result.success is False

    def test_parse_resource_unknown_type(self, adapter: FHIRAdapter) -> None:
        """Unknown resource type returns error."""
        result = adapter.parse_resource({"resourceType": "Unknown"})
        assert result.success is False
        assert "No parser" in result.errors[0]

    def test_conversion_stats_tracked(
        self,
        adapter: FHIRAdapter,
        sample_patient_fhir: dict,
        sample_condition_fhir: dict,
        sample_encounter_fhir: dict,
    ) -> None:
        """Stats increment after conversions."""
        adapter.parse_resource(sample_patient_fhir)
        adapter.parse_resource(sample_condition_fhir)
        adapter.parse_resource(sample_encounter_fhir)
        stats = adapter.get_conversion_stats()
        assert stats["Patient"] >= 1
        assert stats["Condition"] >= 1
        assert stats["Encounter"] >= 1


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class TestUtilities:
    def test_extract_reference_id(self, adapter: FHIRAdapter) -> None:
        """Reference string split returns id."""
        assert adapter._extract_reference_id("Patient/123") == "123"

    def test_extract_reference_id_none(self, adapter: FHIRAdapter) -> None:
        """None reference returns None."""
        assert adapter._extract_reference_id(None) is None

    def test_build_reference(self, adapter: FHIRAdapter) -> None:
        """Build reference from type and id."""
        assert adapter._build_reference("Patient", "123") == "Patient/123"

    def test_parse_date_formats(self, adapter: FHIRAdapter) -> None:
        """Various ISO formats normalized to date."""
        assert adapter._parse_date("2024-01-15") == "2024-01-15"
        assert adapter._parse_date("2024-01-15T10:30:00Z") == "2024-01-15"
        assert adapter._parse_date("2024-01-15T10:30:00+05:30") == "2024-01-15"

    def test_calculate_age(self, adapter: FHIRAdapter) -> None:
        """Age calculation matches expected."""
        today = date.today()
        birth = today.replace(year=today.year - 25)
        expected = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        assert adapter._calculate_age(birth.isoformat()) == expected
