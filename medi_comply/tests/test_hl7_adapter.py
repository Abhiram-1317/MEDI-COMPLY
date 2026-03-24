"""
Test suite for the HL7 v2.x adapter.

Covers validation, parsing, building, conversions, and utilities using
pure-Python HL7 handling (no external libraries).
"""
from __future__ import annotations

from datetime import date

import pytest

from medi_comply.integrations.hl7_adapter import (
    HL7Adapter,
    HL7BuildResult,
    HL7Diagnosis,
    HL7FinancialTransaction,
    HL7Insurance,
    HL7MessageHeader,
    HL7MessageType,
    HL7Observation,
    HL7ParseResult,
    HL7Patient,
    HL7PatientClass,
    HL7Procedure,
    HL7Segment,
    HL7SegmentType,
    HL7Visit,
)


# Fixtures -----------------------------------------------------------------


@pytest.fixture
def adapter() -> HL7Adapter:
    return HL7Adapter()


@pytest.fixture
def sample_adt_message() -> str:
    return (
        "MSH|^~\\&|HIS|HOSPITAL|MEDICOMPLY|SYSTEM|20240115103000||ADT^A01|MSG001|P|2.5\r"
        "EVN|A01|20240115103000\r"
        "PID|1||MRN12345^^^HOSPITAL^MR||DOE^JANE^MARIE||19600322|F||W|456 OAK AVE^^CHICAGO^IL^60601||5559876543\r"
        "PV1|1|I|ICU^101^A|E|||DR001^SMITH^JOHN|||CAR\r"
        "DG1|1|I10|I21.4^Non-ST elevation MI^I10||20240115|A\r"
        "DG1|2|I10|E11.22^T2DM with diabetic CKD^I10||20240115|A\r"
        "OBX|1|NM|49563-0^Troponin^LN||0.8|ng/mL|<0.04|C|||F\r"
        "IN1|1|BCBS001|BCBS|Blue Cross Blue Shield\r"
        "AL1|1|DA|PCN^Penicillin|SV|Anaphylaxis\r"
    )


@pytest.fixture
def sample_patient() -> HL7Patient:
    return HL7Patient(
        patient_id="P001",
        mrn="MRN99999",
        last_name="SMITH",
        first_name="JOHN",
        date_of_birth="19620515",
        gender="M",
        address_street="123 MAIN ST",
        address_city="SPRINGFIELD",
        address_state="IL",
        address_zip="62701",
    )


@pytest.fixture
def sample_visit() -> HL7Visit:
    return HL7Visit(
        visit_id="V001",
        patient_class="I",
        assigned_location="ICU",
        room="201",
        bed="A",
        attending_doctor_id="DR001",
        attending_doctor_name="SMITH^JOHN",
    )


@pytest.fixture
def sample_diagnoses() -> list[HL7Diagnosis]:
    return [
        HL7Diagnosis(set_id=1, code="I21.4", description="NSTEMI", diagnosis_type="A"),
        HL7Diagnosis(set_id=2, code="E11.22", description="T2DM CKD", diagnosis_type="A"),
    ]


@pytest.fixture
def sample_observations() -> list[HL7Observation]:
    return [
        HL7Observation(
            set_id=1,
            observation_id="2160-0",
            observation_name="Creatinine",
            value="1.8",
            units="mg/dL",
            reference_range="0.6-1.2",
            abnormal_flags="H",
            result_status="F",
            observation_datetime="20240115103015",
        ),
        HL7Observation(
            set_id=2,
            observation_id="33762-6",
            observation_name="GFR",
            value="38",
            units="mL/min/1.73m2",
            reference_range=">60",
            abnormal_flags="L",
            result_status="F",
        ),
    ]


# Validation tests ----------------------------------------------------------


class TestHL7Validation:
    def test_valid_message(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        result = adapter.validate_message(sample_adt_message)
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["message_type"] == "ADT^A01"

    def test_empty_message(self, adapter: HL7Adapter) -> None:
        result = adapter.validate_message("")
        assert result["valid"] is False
        assert "Empty message" in result["errors"]

    def test_no_msh(self, adapter: HL7Adapter) -> None:
        msg = "PID|1|ABC"
        result = adapter.validate_message(msg)
        assert result["valid"] is False
        assert "Message must start with MSH" in result["errors"]

    def test_wrong_separator(self, adapter: HL7Adapter) -> None:
        msg = "MSH,EVN,PID"
        result = adapter.validate_message(msg)
        assert result["valid"] is False
        assert "Missing field separators '|'" in result["errors"]

    def test_minimal_msh(self, adapter: HL7Adapter) -> None:
        msg = "MSH|^~\\&"
        result = adapter.validate_message(msg)
        assert result["valid"] is True
        assert result["segment_count"] == 1
        assert result["warnings"]


# Parse message tests -------------------------------------------------------


class TestParseMessage:
    def test_parse_adt_a01(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert parsed.success is True
        assert parsed.patient is not None
        assert parsed.encounter is not None

    def test_parse_message_type(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert parsed.message_type == "ADT^A01"

    def test_parse_segment_count(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert parsed.raw_segment_count == 9

    def test_parse_multiple_dg1(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        codes = [dx["code"] for dx in parsed.diagnoses]
        assert codes == ["I21.4", "E11.22"]

    def test_parse_with_observations(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert len(parsed.observations) == 1
        assert parsed.observations[0]["value"] == "0.8"

    def test_parse_with_insurance(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert len(parsed.insurance) == 1
        assert parsed.insurance[0]["plan_id"] == "BCBS001"


# Parse MSH -----------------------------------------------------------------


class TestParseMSH:
    def test_msh_sending_app(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        fields = adapter._split_fields(sample_adt_message.split("\r")[0])
        msh = adapter._parse_msh(fields)
        assert msh.sending_application == "HIS"

    def test_msh_receiving_app(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        fields = adapter._split_fields(sample_adt_message.split("\r")[0])
        msh = adapter._parse_msh(fields)
        assert msh.receiving_application == "MEDICOMPLY"

    def test_msh_message_type(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        fields = adapter._split_fields(sample_adt_message.split("\r")[0])
        msh = adapter._parse_msh(fields)
        assert msh.message_type == "ADT^A01"

    def test_msh_control_id(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        fields = adapter._split_fields(sample_adt_message.split("\r")[0])
        msh = adapter._parse_msh(fields)
        assert msh.message_control_id == "MSG001"

    def test_msh_version(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        fields = adapter._split_fields(sample_adt_message.split("\r")[0])
        msh = adapter._parse_msh(fields)
        assert msh.version_id == "2.5"


# Parse PID -----------------------------------------------------------------


class TestParsePID:
    def test_pid_name(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.last_name == "DOE"
        assert patient.first_name == "JANE"
        assert patient.middle_name == "MARIE"

    def test_pid_mrn(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.mrn == "MRN12345"

    def test_pid_dob(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.date_of_birth == "19600322"

    def test_pid_gender(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.gender == "F"

    def test_pid_address(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.address_street == "456 OAK AVE"
        assert patient.address_city == "CHICAGO"
        assert patient.address_state == "IL"
        assert patient.address_zip == "60601"

    def test_pid_phone(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.phone == "5559876543"

    def test_pid_age_calculation(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pid_line = sample_adt_message.split("\r")[2]
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        today = date.today()
        dob = date(1960, 3, 22)
        expected = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        assert patient.age == expected

    def test_pid_minimal(self, adapter: HL7Adapter) -> None:
        pid_line = "PID|1|"  # missing most fields should not raise
        patient = adapter._parse_pid(adapter._split_fields(pid_line))
        assert patient.first_name == ""
        assert patient.mrn == ""


# Parse PV1 -----------------------------------------------------------------


class TestParsePV1:
    def test_pv1_patient_class(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pv1_line = sample_adt_message.split("\r")[3]
        visit = adapter._parse_pv1(adapter._split_fields(pv1_line))
        assert visit.patient_class == "I"

    def test_pv1_location(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pv1_line = sample_adt_message.split("\r")[3]
        visit = adapter._parse_pv1(adapter._split_fields(pv1_line))
        assert visit.assigned_location == "ICU"
        assert visit.room == "101"
        assert visit.bed == "A"

    def test_pv1_attending(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pv1_line = sample_adt_message.split("\r")[3]
        visit = adapter._parse_pv1(adapter._split_fields(pv1_line))
        assert visit.attending_doctor_id == "DR001"
        assert visit.attending_doctor_name == "SMITH^JOHN"

    def test_pv1_admit_datetime(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        pv1_line = sample_adt_message.split("\r")[3]
        visit = adapter._parse_pv1(adapter._split_fields(pv1_line))
        assert visit.admit_datetime is None

    def test_pv1_short_segment(self, adapter: HL7Adapter) -> None:
        pv1_line = "PV1|1|O"
        visit = adapter._parse_pv1(adapter._split_fields(pv1_line))
        assert visit.patient_class == "O"
        assert visit.assigned_location is None


# Parse DG1 -----------------------------------------------------------------


class TestParseDG1:
    def test_dg1_code(self, adapter: HL7Adapter) -> None:
        dg1_line = "DG1|1|I10|I10.1^Hypertension^I10||20240101|A"
        diag = adapter._parse_dg1(adapter._split_fields(dg1_line))
        assert diag.code == "I10.1"

    def test_dg1_description(self, adapter: HL7Adapter) -> None:
        dg1_line = "DG1|1|I10|I10.1^Hypertension^I10||20240101|A"
        diag = adapter._parse_dg1(adapter._split_fields(dg1_line))
        assert diag.description == "Hypertension"

    def test_dg1_type(self, adapter: HL7Adapter) -> None:
        dg1_line = "DG1|1|I10|I10.1^Hypertension^I10||20240101|F"
        diag = adapter._parse_dg1(adapter._split_fields(dg1_line))
        assert diag.diagnosis_type == "F"

    def test_dg1_multiple(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        assert len(parsed.diagnoses) == 2


# Parse OBX -----------------------------------------------------------------


class TestParseOBX:
    def test_obx_numeric(self, adapter: HL7Adapter) -> None:
        obx_line = "OBX|1|NM|2160-0^Creatinine^LN||1.8|mg/dL|0.6-1.2|H|||F||20240115"
        obs = adapter._parse_obx(adapter._split_fields(obx_line))
        assert obs.value == "1.8"
        assert obs.units == "mg/dL"

    def test_obx_abnormal_flag(self, adapter: HL7Adapter) -> None:
        obx_line = "OBX|1|NM|2160-0^Creatinine^LN||1.8|mg/dL|0.6-1.2|H|||F"
        obs = adapter._parse_obx(adapter._split_fields(obx_line))
        assert obs.abnormal_flags == "H"

    def test_obx_loinc(self, adapter: HL7Adapter) -> None:
        obx_line = "OBX|1|NM|49563-0^Troponin^LN||0.8|ng/mL|<0.04|C|||F"
        obs = adapter._parse_obx(adapter._split_fields(obx_line))
        assert obs.observation_id == "49563-0"
        assert obs.observation_name == "Troponin"

    def test_obx_reference_range(self, adapter: HL7Adapter) -> None:
        obx_line = "OBX|1|NM|2160-0^Creatinine^LN||1.8|mg/dL|0.6-1.2|H|||F"
        obs = adapter._parse_obx(adapter._split_fields(obx_line))
        assert obs.reference_range == "0.6-1.2"

    def test_obx_multiple(self, adapter: HL7Adapter) -> None:
        message = (
            "MSH|^~\\&|A|B|C|D|20240101000000||ORU^R01|1|P|2.5\r"
            "PID|1||MRN1\r"
            "OBX|1|NM|A^TestA||10|mg\r"
            "OBX|2|NM|B^TestB||20|mg\r"
        )
        parsed = adapter.parse_message(message)
        assert len(parsed.observations) == 2
        assert parsed.observations[1]["value"] == "20"


# Parse IN1 -----------------------------------------------------------------


class TestParseIN1:
    def test_in1_plan(self, adapter: HL7Adapter) -> None:
        line = "IN1|1|PLAN1|ID|Acme Health"
        ins = adapter._parse_in1(adapter._split_fields(line))
        assert ins.plan_id == "PLAN1"
        assert ins.company_name == "Acme Health"

    def test_in1_subscriber(self, adapter: HL7Adapter) -> None:
        parts = [
            "IN1",
            "1",
            "PLAN1",
            "ID",
            "Acme",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "20240101",
            "20241231",
            "",
            "",
            "DOE^JANE",
            "01",
        ]
        line = "|".join(parts)
        ins = adapter._parse_in1(adapter._split_fields(line))
        assert ins.subscriber_name == "DOE^JANE"
        assert ins.relationship == "01"

    def test_in1_dates(self, adapter: HL7Adapter) -> None:
        parts = [
            "IN1",
            "1",
            "PLAN1",
            "ID",
            "Acme",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "20240101",
            "20241231",
        ]
        line = "|".join(parts)
        ins = adapter._parse_in1(adapter._split_fields(line))
        assert ins.effective_date == "20240101"
        assert ins.expiration_date == "20241231"


# Parse AL1 -----------------------------------------------------------------


class TestParseAL1:
    def test_al1_allergen(self, adapter: HL7Adapter) -> None:
        line = "AL1|1|DA|PCN^Penicillin|SV|Anaphylaxis"
        allergy = adapter._parse_al1(adapter._split_fields(line))
        assert allergy["code"] == "PCN"
        assert allergy["description"] == "Penicillin"

    def test_al1_severity(self, adapter: HL7Adapter) -> None:
        line = "AL1|1|FA|SHRIMP^Shrimp|MO|Hives"
        allergy = adapter._parse_al1(adapter._split_fields(line))
        assert allergy["severity"] == "MO"


# Build message tests -------------------------------------------------------


class TestBuildMessage:
    def test_build_adt_a01(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        result = adapter.build_adt_message(sample_patient, sample_visit)
        assert result.success is True
        assert result.message.startswith("MSH|")

    def test_build_adt_contains_msh(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        result = adapter.build_adt_message(sample_patient, sample_visit)
        assert "MSH|" in result.message
        assert HL7MessageType.ADT_A01.value in result.message

    def test_build_adt_contains_pid(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        result = adapter.build_adt_message(sample_patient, sample_visit)
        assert "PID|" in result.message
        assert sample_patient.mrn in result.message

    def test_build_adt_contains_dg1(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit, sample_diagnoses: list[HL7Diagnosis]) -> None:
        result = adapter.build_adt_message(sample_patient, sample_visit, diagnoses=sample_diagnoses)
        assert result.message.count("DG1|") == 2
        assert "I21.4" in result.message

    def test_build_ack_accept(self, adapter: HL7Adapter) -> None:
        original = HL7MessageHeader(
            sending_application="A",
            sending_facility="B",
            receiving_application="C",
            receiving_facility="D",
            message_control_id="CTRL1",
        )
        ack = adapter.build_ack_message(original, ack_code="AA")
        assert ack.success is True
        assert "MSA|AA|CTRL1" in ack.message

    def test_build_ack_error(self, adapter: HL7Adapter) -> None:
        original = HL7MessageHeader(
            sending_application="A",
            sending_facility="B",
            receiving_application="C",
            receiving_facility="D",
            message_control_id="CTRL2",
        )
        ack = adapter.build_ack_message(original, ack_code="AE", error_message="FAIL")
        assert "MSA|AE|CTRL2|FAIL" in ack.message


# Build ORU -----------------------------------------------------------------


class TestBuildORU:
    def test_build_oru_basic(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_observations: list[HL7Observation]) -> None:
        result = adapter.build_oru_message(sample_patient, sample_observations)
        assert result.success is True
        assert result.message.startswith("MSH|")

    def test_build_oru_with_observations(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_observations: list[HL7Observation]) -> None:
        result = adapter.build_oru_message(sample_patient, sample_observations)
        assert result.message.count("OBX|") == len(sample_observations)

    def test_build_oru_parseable(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_observations: list[HL7Observation]) -> None:
        result = adapter.build_oru_message(sample_patient, sample_observations)
        parsed = adapter.parse_message(result.message)
        assert parsed.success is True
        assert len(parsed.observations) == len(sample_observations)


# Build DFT -----------------------------------------------------------------


class TestBuildDFT:
    def test_build_dft_basic(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        ft = HL7FinancialTransaction(set_id=1, transaction_type="CG", transaction_amount=100.0, quantity=1)
        result = adapter.build_dft_message(sample_patient, sample_visit, transactions=[ft])
        assert result.success is True
        assert "FT1|" in result.message

    def test_build_dft_with_transactions(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        ft1 = HL7FinancialTransaction(set_id=1, transaction_type="CG", transaction_amount=100.0, quantity=1)
        ft2 = HL7FinancialTransaction(set_id=2, transaction_type="CG", transaction_amount=200.0, quantity=2)
        result = adapter.build_dft_message(sample_patient, sample_visit, transactions=[ft1, ft2])
        assert result.message.count("FT1|") == 2


# Round-trip tests ----------------------------------------------------------


class TestRoundTrip:
    def test_roundtrip_adt(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit) -> None:
        build = adapter.build_adt_message(sample_patient, sample_visit)
        parsed = adapter.parse_message(build.message)
        assert parsed.patient["last_name"] == sample_patient.last_name
        assert parsed.patient["first_name"] == sample_patient.first_name

    def test_roundtrip_diagnoses(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_visit: HL7Visit, sample_diagnoses: list[HL7Diagnosis]) -> None:
        build = adapter.build_adt_message(sample_patient, sample_visit, diagnoses=sample_diagnoses)
        parsed = adapter.parse_message(build.message)
        parsed_codes = {d["code"] for d in parsed.diagnoses}
        assert parsed_codes == {"I21.4", "E11.22"}

    def test_roundtrip_observations(self, adapter: HL7Adapter, sample_patient: HL7Patient, sample_observations: list[HL7Observation]) -> None:
        build = adapter.build_oru_message(sample_patient, sample_observations)
        parsed = adapter.parse_message(build.message)
        values = [obs["value"] for obs in parsed.observations]
        assert "1.8" in values


# Internal conversion tests -------------------------------------------------


class TestHL7ToInternal:
    def test_to_internal_patient(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        internal = adapter.hl7_to_internal(parsed)
        assert internal["patient_id"] == "MRN12345"
        assert internal["patient_name"] == "JANE DOE"

    def test_to_internal_diagnoses(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        internal = adapter.hl7_to_internal(parsed)
        codes = [d["code"] for d in internal["diagnoses"]]
        assert codes == ["I21.4", "E11.22"]

    def test_to_internal_encounter_type(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        internal = adapter.hl7_to_internal(parsed)
        assert internal["encounter_type"] == "I"

    def test_to_internal_observations(self, adapter: HL7Adapter, sample_adt_message: str) -> None:
        parsed = adapter.parse_message(sample_adt_message)
        internal = adapter.hl7_to_internal(parsed)
        assert internal["observations"][0]["name"] == "Troponin"
        assert internal["observations"][0]["value"] == "0.8"


# FHIR conversion tests -----------------------------------------------------


class TestHL7ToFHIR:
    def test_to_fhir_patient(self, adapter: HL7Adapter, sample_patient: HL7Patient) -> None:
        fhir = adapter.to_fhir_patient(sample_patient)
        assert fhir["resourceType"] == "Patient"
        assert fhir["identifier"][0]["value"] == sample_patient.mrn
        assert fhir["name"][0]["family"] == "SMITH"

    def test_to_fhir_encounter(self, adapter: HL7Adapter, sample_visit: HL7Visit) -> None:
        fhir = adapter.to_fhir_encounter(sample_visit, patient_id="P001")
        assert fhir["resourceType"] == "Encounter"
        assert fhir["subject"]["reference"] == "Patient/P001"
        assert fhir["class"]["code"] == "IMP"

    def test_to_fhir_condition(self, adapter: HL7Adapter) -> None:
        diag = HL7Diagnosis(code="I10", description="Hypertension")
        fhir = adapter.to_fhir_condition(diag, patient_id="P001")
        coding = fhir["code"]["coding"][0]
        assert coding["system"] == "http://hl7.org/fhir/sid/icd-10"
        assert coding["code"] == "I10"


# Datetime conversion tests -------------------------------------------------


class TestDatetimeConversion:
    def test_parse_full_datetime(self, adapter: HL7Adapter) -> None:
        iso = adapter._parse_datetime("20240115103015")
        assert iso.startswith("2024-01-15T10:30:15")

    def test_parse_date_only(self, adapter: HL7Adapter) -> None:
        iso = adapter._parse_datetime("20240115")
        assert iso.startswith("2024-01-15")

    def test_parse_empty_datetime(self, adapter: HL7Adapter) -> None:
        assert adapter._parse_datetime("") is None

    def test_format_datetime(self, adapter: HL7Adapter) -> None:
        hl7 = adapter._format_datetime("2024-01-15T10:30:15")
        assert hl7 == "20240115103015"


# Utility tests -------------------------------------------------------------


class TestUtilities:
    def test_split_fields(self, adapter: HL7Adapter) -> None:
        fields = adapter._split_fields("PID|1|2|3")
        assert fields == ["PID", "1", "2", "3"]

    def test_split_components(self, adapter: HL7Adapter) -> None:
        comps = adapter._split_components("DOE^JANE^M")
        assert comps == ["DOE", "JANE", "M"]

    def test_get_field_safe(self, adapter: HL7Adapter) -> None:
        fields = ["A", "B"]
        assert adapter._get_field(fields, 5, "DEFAULT") == "DEFAULT"
