"""
HL7 v2.x Adapter for MEDI-COMPLY.

Parses and builds HL7 v2.x messages (ADT, ORU, DFT, etc.) without external
HL7 libraries. Follows patterns used by the FHIR adapter while honoring the
pipe-delimited HL7 structure and common segment types. Designed for deterministic
integration and easy unit testing.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class HL7MessageType(str, Enum):
    ADT_A01 = "ADT^A01"
    ADT_A02 = "ADT^A02"
    ADT_A03 = "ADT^A03"
    ADT_A04 = "ADT^A04"
    ADT_A08 = "ADT^A08"
    ORM_O01 = "ORM^O01"
    ORU_R01 = "ORU^R01"
    DFT_P03 = "DFT^P03"
    SIU_S12 = "SIU^S12"
    MDM_T02 = "MDM^T02"
    BAR_P01 = "BAR^P01"
    ACK = "ACK"


class HL7PatientClass(str, Enum):
    INPATIENT = "I"
    OUTPATIENT = "O"
    EMERGENCY = "E"
    PREADMIT = "P"
    RECURRING = "R"
    OBSERVATION = "B"


class HL7SegmentType(str, Enum):
    MSH = "MSH"
    EVN = "EVN"
    PID = "PID"
    PV1 = "PV1"
    PV2 = "PV2"
    NK1 = "NK1"
    DG1 = "DG1"
    PR1 = "PR1"
    GT1 = "GT1"
    IN1 = "IN1"
    IN2 = "IN2"
    OBR = "OBR"
    OBX = "OBX"
    AL1 = "AL1"
    FT1 = "FT1"
    ORC = "ORC"
    RXA = "RXA"
    NTE = "NTE"
    TXA = "TXA"
    ZCL = "ZCL"


class HL7ParseResult(BaseModel):
    success: bool
    message_type: Optional[str] = None
    message_id: Optional[str] = None
    segments: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)
    patient: Optional[Dict[str, Any]] = None
    encounter: Optional[Dict[str, Any]] = None
    diagnoses: List[Dict[str, Any]] = Field(default_factory=list)
    procedures: List[Dict[str, Any]] = Field(default_factory=list)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    insurance: List[Dict[str, Any]] = Field(default_factory=list)
    orders: List[Dict[str, Any]] = Field(default_factory=list)
    financial: List[Dict[str, Any]] = Field(default_factory=list)
    allergies: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    raw_segment_count: int = 0

class HL7Segment(BaseModel):
    segment_type: str
    fields: List[str] = Field(default_factory=list)
    raw_text: str = ""

class HL7MessageHeader(BaseModel):
    field_separator: str = "|"
    encoding_characters: str = "^~\\&"
    sending_application: str = ""
    sending_facility: str = ""
    receiving_application: str = ""
    receiving_facility: str = ""
    message_datetime: str = ""
    security: str = ""
    message_type: str = ""
    message_control_id: str = ""
    processing_id: str = "P"
    version_id: str = "2.5"

class HL7Patient(BaseModel):
    patient_id: str = ""
    mrn: str = ""
    last_name: str = ""
    first_name: str = ""
    middle_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    address_street: Optional[str] = None
    address_city: Optional[str] = None
    address_state: Optional[str] = None
    address_zip: Optional[str] = None
    phone: Optional[str] = None
    ssn: Optional[str] = None
    account_number: Optional[str] = None
    age: Optional[int] = None

class HL7Visit(BaseModel):
    visit_id: str = ""
    patient_class: str = ""
    assigned_location: Optional[str] = None
    room: Optional[str] = None
    bed: Optional[str] = None
    admission_type: Optional[str] = None
    attending_doctor_id: Optional[str] = None
    attending_doctor_name: Optional[str] = None
    hospital_service: Optional[str] = None
    visit_number: Optional[str] = None
    admit_datetime: Optional[str] = None
    discharge_datetime: Optional[str] = None
    discharge_disposition: Optional[str] = None
    admit_source: Optional[str] = None

class HL7Diagnosis(BaseModel):
    set_id: int = 1
    coding_method: str = "I10"
    code: str = ""
    description: str = ""
    diagnosis_datetime: Optional[str] = None
    diagnosis_type: str = "A"
    priority: Optional[int] = None
class HL7Procedure(BaseModel):
    set_id: int = 1
    coding_method: str = "C4"
    code: str = ""
    description: str = ""
    procedure_datetime: Optional[str] = None
    procedure_type: Optional[str] = None
    practitioner_id: Optional[str] = None
    practitioner_name: Optional[str] = None
class HL7Observation(BaseModel):
    set_id: int = 1
    observation_id: str = ""
    observation_name: str = ""
    value: str = ""
    units: Optional[str] = None
    reference_range: Optional[str] = None
    abnormal_flags: Optional[str] = None
    result_status: str = "F"
    observation_datetime: Optional[str] = None
class HL7Insurance(BaseModel):
    set_id: int = 1
    plan_id: str = ""
    company_name: str = ""
    group_number: Optional[str] = None
    subscriber_id: Optional[str] = None
    subscriber_name: Optional[str] = None
    relationship: Optional[str] = None
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
class HL7FinancialTransaction(BaseModel):
    set_id: int = 1
    transaction_type: str = "CG"
    transaction_datetime: Optional[str] = None
    transaction_amount: Optional[float] = None
    procedure_code: Optional[str] = None
    procedure_description: Optional[str] = None
    diagnosis_code: Optional[str] = None
    insurance_plan_id: Optional[str] = None
    quantity: int = 1
class HL7BuildResult(BaseModel):
    success: bool
    message: str = ""
    message_type: str = ""
    segment_count: int = 0
    errors: List[str] = Field(default_factory=list)


class HL7Adapter:
    """Utility class to parse and build HL7 v2.x messages."""

    def __init__(self) -> None:
        """Initialize adapter defaults and counters."""
        self.logger = logger
        self.field_separator = "|"
        self.component_separator = "^"
        self.repetition_separator = "~"
        self.escape_character = "\\"
        self.subcomponent_separator = "&"
        self._parse_stats: Dict[str, int] = {}
        self._build_stats: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _split_fields(self, segment_text: str) -> List[str]:
        """Split a raw segment string into fields using the field separator."""
        return segment_text.split(self.field_separator)

    def _split_components(self, field_value: str) -> List[str]:
        """Split a field value into components using the component separator."""
        return field_value.split(self.component_separator) if field_value else []

    def _split_repetitions(self, field_value: str) -> List[str]:
        """Split a repeating field into individual repetitions."""
        return field_value.split(self.repetition_separator) if field_value else []

    def _get_field(self, fields: List[str], index: int, default: str = "") -> str:
        """Safely return the field at index or a default value."""
        try:
            return fields[index]
        except IndexError:
            return default

    def _get_component(self, field_value: str, index: int, default: str = "") -> str:
        """Safely return the component at index from a field value."""
        comps = self._split_components(field_value)
        try:
            return comps[index]
        except IndexError:
            return default

    def _parse_datetime(self, hl7_datetime: str) -> Optional[str]:
        """Convert HL7 datetime formats into ISO 8601 strings."""
        if not hl7_datetime:
            return None
        try:
            fmt = "%Y%m%d%H%M%S"
            if len(hl7_datetime) == 8:
                fmt = "%Y%m%d"
            elif len(hl7_datetime) == 10:
                fmt = "%Y%m%d%H"
            elif len(hl7_datetime) == 12:
                fmt = "%Y%m%d%H%M"
            dt = datetime.strptime(hl7_datetime, fmt)
            return dt.isoformat()
        except Exception:
            self.logger.warning("Failed to parse HL7 datetime: %s", hl7_datetime)
            return None

    def _calc_age(self, dob: Optional[str]) -> Optional[int]:
        """Calculate age in years from a DOB string in yyyyMMdd format."""
        if not dob:
            return None
        try:
            dt = datetime.strptime(dob, "%Y%m%d").date()
            today = date.today()
            years = today.year - dt.year - ((today.month, today.day) < (dt.month, dt.day))
            return years
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Segment parsers
    # ------------------------------------------------------------------

    def _parse_msh(self, fields: List[str]) -> HL7MessageHeader:
        """Parse MSH fields into a header model."""
        # fields[0] == "MSH"; fields[1] is encoding characters
        return HL7MessageHeader(
            field_separator=self.field_separator,
            encoding_characters=self._get_field(fields, 1, "^~\\&"),
            sending_application=self._get_field(fields, 2),
            sending_facility=self._get_field(fields, 3),
            receiving_application=self._get_field(fields, 4),
            receiving_facility=self._get_field(fields, 5),
            message_datetime=self._get_field(fields, 6),
            security=self._get_field(fields, 7),
            message_type=self._get_field(fields, 8),
            message_control_id=self._get_field(fields, 9),
            processing_id=self._get_field(fields, 10, "P"),
            version_id=self._get_field(fields, 11, "2.5"),
        )

    def _parse_pid(self, fields: List[str]) -> HL7Patient:
        """Parse PID fields into a patient model."""
        mrn_field = self._get_field(fields, 3)
        mrn_components = self._split_components(mrn_field)
        mrn = mrn_components[0] if mrn_components else ""
        name_field = self._get_field(fields, 5)
        last = self._get_component(name_field, 0)
        first = self._get_component(name_field, 1)
        middle = self._get_component(name_field, 2) or None
        dob = self._get_field(fields, 7)
        gender = self._get_field(fields, 8) or None
        race = self._get_field(fields, 10) or None
        addr_field = self._get_field(fields, 11)
        street = self._get_component(addr_field, 0) or None
        city = self._get_component(addr_field, 2) or None
        state = self._get_component(addr_field, 3) or None
        zip_code = self._get_component(addr_field, 4) or None
        phone = self._get_field(fields, 13) or None
        ssn = self._get_field(fields, 19) or None
        account = self._get_field(fields, 18) or None
        age = self._calc_age(dob)
        return HL7Patient(
            patient_id=self._get_field(fields, 2),
            mrn=mrn,
            last_name=last,
            first_name=first,
            middle_name=middle,
            date_of_birth=dob or None,
            gender=gender,
            race=race,
            address_street=street,
            address_city=city,
            address_state=state,
            address_zip=zip_code,
            phone=phone,
            ssn=ssn,
            account_number=account,
            age=age,
        )

    def _parse_pv1(self, fields: List[str]) -> HL7Visit:
        """Parse PV1 fields into a visit model."""
        location_field = self._get_field(fields, 3)
        unit = self._get_component(location_field, 0) or None
        room = self._get_component(location_field, 1) or None
        bed = self._get_component(location_field, 2) or None
        attending_field = self._get_field(fields, 7)
        attending_id = self._get_component(attending_field, 0) or None
        attending_last = self._get_component(attending_field, 1) or None
        attending_first = self._get_component(attending_field, 2) or None
        admitting_field = self._get_field(fields, 17)
        admit_id = self._get_component(admitting_field, 0) or None
        admit_last = self._get_component(admitting_field, 1) or None
        admit_first = self._get_component(admitting_field, 2) or None
        return HL7Visit(
            visit_id=self._get_field(fields, 19),
            patient_class=self._get_field(fields, 2),
            assigned_location=unit,
            room=room,
            bed=bed,
            admission_type=self._get_field(fields, 4) or None,
            attending_doctor_id=attending_id,
            attending_doctor_name=f"{attending_last}^{attending_first}" if attending_last else None,
            hospital_service=self._get_field(fields, 10) or None,
            visit_number=self._get_field(fields, 19) or None,
            admit_datetime=self._get_field(fields, 44) or None,
            discharge_datetime=self._get_field(fields, 45) or None,
            discharge_disposition=self._get_field(fields, 36) or None,
            admit_source=self._get_field(fields, 14) or None,
        )

    def _parse_dg1(self, fields: List[str]) -> HL7Diagnosis:
        """Parse DG1 fields into a diagnosis model."""
        dx_field = self._get_field(fields, 3)
        code = self._get_component(dx_field, 0)
        desc = self._get_component(dx_field, 1)
        coding_system = self._get_component(dx_field, 2) or "I10"
        return HL7Diagnosis(
            set_id=int(self._get_field(fields, 1) or 1),
            coding_method=self._get_field(fields, 2) or coding_system,
            code=code,
            description=desc,
            diagnosis_datetime=self._get_field(fields, 5) or None,
            diagnosis_type=self._get_field(fields, 6) or "A",
            priority=int(self._get_field(fields, 15) or 0) or None,
        )

    def _parse_pr1(self, fields: List[str]) -> HL7Procedure:
        """Parse PR1 fields into a procedure model."""
        proc_field = self._get_field(fields, 3)
        code = self._get_component(proc_field, 0)
        desc = self._get_component(proc_field, 1)
        coding_system = self._get_component(proc_field, 2) or "C4"
        practitioner_field = self._get_field(fields, 8)
        practitioner_id = self._get_component(practitioner_field, 0) or None
        practitioner_last = self._get_component(practitioner_field, 1) or None
        practitioner_first = self._get_component(practitioner_field, 2) or None
        practitioner_name = None
        if practitioner_last:
            practitioner_name = f"{practitioner_last}^{practitioner_first}"
        return HL7Procedure(
            set_id=int(self._get_field(fields, 1) or 1),
            coding_method=self._get_field(fields, 2) or coding_system,
            code=code,
            description=desc,
            procedure_datetime=self._get_field(fields, 5) or None,
            procedure_type=self._get_field(fields, 6) or None,
            practitioner_id=practitioner_id,
            practitioner_name=practitioner_name,
        )

    def _parse_obx(self, fields: List[str]) -> HL7Observation:
        """Parse OBX fields into an observation model."""
        obs_id_field = self._get_field(fields, 3)
        code = self._get_component(obs_id_field, 0)
        name = self._get_component(obs_id_field, 1)
        value = self._get_field(fields, 5)
        units_field = self._get_field(fields, 6)
        units = self._get_component(units_field, 1) or self._get_component(units_field, 0) or None
        return HL7Observation(
            set_id=int(self._get_field(fields, 1) or 1),
            observation_id=code or "",
            observation_name=name or "",
            value=value or "",
            units=units,
            reference_range=self._get_field(fields, 7) or None,
            abnormal_flags=self._get_field(fields, 8) or None,
            result_status=self._get_field(fields, 11) or "F",
            observation_datetime=self._get_field(fields, 14) or None,
        )

    def _parse_in1(self, fields: List[str]) -> HL7Insurance:
        """Parse IN1 fields into an insurance model."""
        insured_name = self._get_field(fields, 16)
        last = self._get_component(insured_name, 0) or ""
        first = self._get_component(insured_name, 1) or ""
        return HL7Insurance(
            set_id=int(self._get_field(fields, 1) or 1),
            plan_id=self._get_field(fields, 2),
            company_name=self._get_field(fields, 4),
            group_number=self._get_field(fields, 8) or None,
            subscriber_id=self._get_field(fields, 36) or None,
            subscriber_name=f"{last}^{first}" if last else None,
            relationship=self._get_field(fields, 17) or None,
            effective_date=self._get_field(fields, 12) or None,
            expiration_date=self._get_field(fields, 13) or None,
        )

    def _parse_al1(self, fields: List[str]) -> Dict[str, Any]:
        """Parse AL1 fields into a lightweight allergy dict."""
        allergen_field = self._get_field(fields, 3)
        code = self._get_component(allergen_field, 0)
        desc = self._get_component(allergen_field, 1)
        return {
            "set_id": int(self._get_field(fields, 1) or 1),
            "type": self._get_field(fields, 2) or None,
            "code": code,
            "description": desc,
            "severity": self._get_field(fields, 4) or None,
            "reaction": self._get_field(fields, 5) or None,
        }

    def _parse_ft1(self, fields: List[str]) -> HL7FinancialTransaction:
        """Parse FT1 fields into a financial transaction model."""
        return HL7FinancialTransaction(
            set_id=int(self._get_field(fields, 1) or 1),
            transaction_datetime=self._get_field(fields, 4) or None,
            transaction_type=self._get_field(fields, 6) or "CG",
            quantity=int(self._get_field(fields, 10) or 1),
            transaction_amount=float(self._get_field(fields, 11) or 0.0) if self._get_field(fields, 11) else None,
            procedure_code=self._get_component(self._get_field(fields, 25), 0) or None,
            procedure_description=self._get_component(self._get_field(fields, 25), 1) or None,
            diagnosis_code=self._get_field(fields, 19) or None,
            insurance_plan_id=self._get_field(fields, 20) or None,
        )

    def _parse_nte(self, fields: List[str]) -> str:
        """Parse NTE fields and return the comment text."""
        return self._get_field(fields, 3) or ""

    def _parse_order(self, fields: List[str], segment_type: str) -> Dict[str, Any]:
        """Parse ORC/OBR fields into a simple order dict."""
        order: Dict[str, Any] = {"segment": segment_type}
        if segment_type == HL7SegmentType.ORC.value:
            order["control"] = self._get_field(fields, 1)
            order["placer_order_number"] = self._get_field(fields, 2)
            order["filler_order_number"] = self._get_field(fields, 3)
            order["status"] = self._get_field(fields, 5)
        if segment_type == HL7SegmentType.OBR.value:
            svc_field = self._get_field(fields, 4)
            order["service_code"] = self._get_component(svc_field, 0)
            order["service_desc"] = self._get_component(svc_field, 1)
            order["service_system"] = self._get_component(svc_field, 2)
        return order

    # ------------------------------------------------------------------
    # Parsing entrypoint
    # ------------------------------------------------------------------

    def parse_message(self, raw_message: str) -> HL7ParseResult:
        """Parse an HL7 v2.x message string into structured data."""

        if not raw_message or not raw_message.strip():
            return HL7ParseResult(success=False, errors=["Empty message"], raw_segment_count=0)

        lines = re.split(r"\r\n|\n|\r", raw_message)
        lines = [ln for ln in lines if ln.strip()]
        if not lines or not lines[0].startswith("MSH"):
            return HL7ParseResult(success=False, errors=["Message must start with MSH"], raw_segment_count=len(lines))

        # Initialize separators from MSH-1 and encoding characters
        first_fields = lines[0].split("|")
        if len(first_fields) > 1 and first_fields[1]:
            self.component_separator = first_fields[1][0] if len(first_fields[1]) > 0 else "^"
            self.repetition_separator = first_fields[1][1] if len(first_fields[1]) > 1 else "~"
            self.escape_character = first_fields[1][2] if len(first_fields[1]) > 2 else "\\"
            self.subcomponent_separator = first_fields[1][3] if len(first_fields[1]) > 3 else "&"

        parse_result = HL7ParseResult(success=True, raw_segment_count=len(lines))
        segments_dict: Dict[str, List[Dict[str, Any]]] = {}
        patient: Optional[HL7Patient] = None
        visit: Optional[HL7Visit] = None

        for raw_seg in lines:
            fields = self._split_fields(raw_seg)
            seg_type = fields[0] if fields else ""
            try:
                if seg_type == HL7SegmentType.MSH.value:
                    msh = self._parse_msh(fields)
                    parse_result.message_type = msh.message_type
                    parse_result.message_id = msh.message_control_id
                    segments_dict.setdefault(seg_type, []).append(msh.model_dump())
                elif seg_type == HL7SegmentType.PID.value:
                    patient = self._parse_pid(fields)
                    segments_dict.setdefault(seg_type, []).append(patient.model_dump())
                elif seg_type == HL7SegmentType.PV1.value:
                    visit = self._parse_pv1(fields)
                    segments_dict.setdefault(seg_type, []).append(visit.model_dump())
                elif seg_type == HL7SegmentType.DG1.value:
                    dx = self._parse_dg1(fields)
                    parse_result.diagnoses.append(dx.model_dump())
                    segments_dict.setdefault(seg_type, []).append(dx.model_dump())
                elif seg_type == HL7SegmentType.PR1.value:
                    proc = self._parse_pr1(fields)
                    parse_result.procedures.append(proc.model_dump())
                    segments_dict.setdefault(seg_type, []).append(proc.model_dump())
                elif seg_type == HL7SegmentType.OBX.value:
                    obs = self._parse_obx(fields)
                    parse_result.observations.append(obs.model_dump())
                    segments_dict.setdefault(seg_type, []).append(obs.model_dump())
                elif seg_type == HL7SegmentType.IN1.value:
                    ins = self._parse_in1(fields)
                    parse_result.insurance.append(ins.model_dump())
                    segments_dict.setdefault(seg_type, []).append(ins.model_dump())
                elif seg_type == HL7SegmentType.AL1.value:
                    allergy = self._parse_al1(fields)
                    parse_result.allergies.append(allergy)
                    segments_dict.setdefault(seg_type, []).append(allergy)
                elif seg_type == HL7SegmentType.FT1.value:
                    ft = self._parse_ft1(fields)
                    parse_result.financial.append(ft.model_dump())
                    segments_dict.setdefault(seg_type, []).append(ft.model_dump())
                elif seg_type == HL7SegmentType.NTE.value:
                    note = self._parse_nte(fields)
                    parse_result.notes.append(note)
                    segments_dict.setdefault(seg_type, []).append({"comment": note})
                elif seg_type in {HL7SegmentType.ORC.value, HL7SegmentType.OBR.value}:
                    order = self._parse_order(fields, seg_type)
                    parse_result.orders.append(order)
                    segments_dict.setdefault(seg_type, []).append(order)
                else:
                    segments_dict.setdefault(seg_type, []).append({"raw": raw_seg})
            except Exception as exc:  # pragma: no cover - defensive
                parse_result.errors.append(f"Failed to parse {seg_type}: {exc}")
                self.logger.exception("Error parsing segment %s", seg_type)

        parse_result.segments = segments_dict
        parse_result.patient = patient.model_dump() if patient else None
        parse_result.encounter = visit.model_dump() if visit else None

        # Stats
        msg_type = parse_result.message_type or "UNKNOWN"
        self._parse_stats[msg_type] = self._parse_stats.get(msg_type, 0) + 1
        if parse_result.errors:
            parse_result.success = False
        return parse_result

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _format_datetime(self, iso_datetime: Optional[str] = None) -> str:
        """Format ISO datetime into HL7 timestamp; fall back to current UTC."""
        if iso_datetime:
            try:
                dt = datetime.fromisoformat(iso_datetime)
                return dt.strftime("%Y%m%d%H%M%S")
            except Exception:
                pass
        return datetime.utcnow().strftime("%Y%m%d%H%M%S")

    def _build_msh(self, message_type: str, sending_app: str = "MEDICOMPLY", sending_facility: str = "SYSTEM", receiving_app: str = "HIS", receiving_facility: str = "HOSPITAL") -> str:
        """Build an MSH segment for the given message type."""
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        control_id = uuid.uuid4().hex[:12].upper()
        return self.field_separator.join(
            [
                "MSH",
                "^~\\&",
                sending_app,
                sending_facility,
                receiving_app,
                receiving_facility,
                ts,
                "",
                message_type,
                control_id,
                "P",
                "2.5",
            ]
        )

    def _build_evn(self, event_code: str) -> str:
        """Build an EVN segment for the event code with current timestamp."""
        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        return self.field_separator.join(["EVN", event_code, ts])

    def _build_pid(self, patient: HL7Patient) -> str:
        """Build a PID segment from patient data."""
        name = f"{patient.last_name}^{patient.first_name}"
        if patient.middle_name:
            name += f"^{patient.middle_name}"
        address = f"{patient.address_street or ''}^^{patient.address_city or ''}^{patient.address_state or ''}^{patient.address_zip or ''}"
        return self.field_separator.join(
            [
                "PID",
                "1",
                "",
                f"{patient.mrn}^^^HOSPITAL^MR",
                "",
                name,
                "",
                patient.date_of_birth or "",
                patient.gender or "",
                "",
                patient.race or "",
                address,
                "",
                patient.phone or "",
                "",
                "",
                "",
                "",
                "",
                patient.account_number or "",
            ]
        )

    def _build_pv1(self, visit: HL7Visit) -> str:
        """Build a PV1 segment from visit data."""
        attending = visit.attending_doctor_id or ""
        if visit.attending_doctor_name:
            attending += f"^{visit.attending_doctor_name}"
        location = f"{visit.assigned_location or ''}^{visit.room or ''}^{visit.bed or ''}"
        return self.field_separator.join(
            [
                "PV1",
                "1",
                visit.patient_class or "",
                location,
                visit.admission_type or "",
                "",
                "",
                attending,
                "",
                "",
                visit.hospital_service or "",
                "",
                "",
                visit.admit_source or "",
                "",
                "",
                f"{visit.attending_doctor_id or ''}^{visit.attending_doctor_name or ''}",
                visit.patient_class or "",
                visit.visit_number or "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                visit.discharge_disposition or "",
                "",
                "",
                visit.admit_datetime or "",
                visit.discharge_datetime or "",
            ]
        )

    def _build_dg1(self, diagnosis: HL7Diagnosis) -> str:
        """Build a DG1 segment from diagnosis data."""
        code = f"{diagnosis.code}^{diagnosis.description}^{diagnosis.coding_method}"
        return self.field_separator.join(
            [
                "DG1",
                str(diagnosis.set_id),
                diagnosis.coding_method,
                code,
                "",
                diagnosis.diagnosis_datetime or "",
                diagnosis.diagnosis_type,
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                str(diagnosis.priority or ""),
            ]
        )

    def _build_pr1(self, procedure: HL7Procedure) -> str:
        """Build a PR1 segment from procedure data."""
        code = f"{procedure.code}^{procedure.description}^{procedure.coding_method}"
        practitioner = procedure.practitioner_id or ""
        if procedure.practitioner_name:
            practitioner += f"^{procedure.practitioner_name}"
        return self.field_separator.join(
            [
                "PR1",
                str(procedure.set_id),
                procedure.coding_method,
                code,
                "",
                procedure.procedure_datetime or "",
                procedure.procedure_type or "",
                "",
                practitioner,
            ]
        )

    def _build_obx(self, observation: HL7Observation) -> str:
        """Build an OBX segment from observation data."""
        obs_id = f"{observation.observation_id}^{observation.observation_name}"
        return self.field_separator.join(
            [
                "OBX",
                str(observation.set_id),
                "ST",
                obs_id,
                "",
                observation.value,
                observation.units or "",
                observation.reference_range or "",
                observation.abnormal_flags or "",
                "",
                "",
                observation.result_status,
                "",
                "",
                observation.observation_datetime or "",
            ]
        )

    def _build_ft1(self, transaction: HL7FinancialTransaction) -> str:
        """Build an FT1 segment from a financial transaction model."""
        proc = f"{transaction.procedure_code or ''}^{transaction.procedure_description or ''}"
        return self.field_separator.join(
            [
                "FT1",
                str(transaction.set_id),
                "",
                "",
                transaction.transaction_datetime or "",
                "",
                transaction.transaction_type,
                "",
                "",
                "",
                str(transaction.quantity),
                f"{transaction.transaction_amount or ''}",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                transaction.diagnosis_code or "",
                transaction.insurance_plan_id or "",
                "",
                "",
                "",
                "",
                proc,
            ]
        )

    def build_adt_message(
        self,
        patient: HL7Patient,
        visit: HL7Visit,
        diagnoses: Optional[List[HL7Diagnosis]] = None,
        message_type: str = HL7MessageType.ADT_A01.value,
        sending_app: str = "MEDICOMPLY",
        sending_facility: str = "SYSTEM",
        receiving_app: str = "HIS",
        receiving_facility: str = "HOSPITAL",
    ) -> HL7BuildResult:
        """Build an ADT message (default A01) from patient, visit, and diagnoses."""
        diagnoses = diagnoses or []
        segments: List[str] = []
        errors: List[str] = []
        try:
            segments.append(self._build_msh(message_type, sending_app, sending_facility, receiving_app, receiving_facility))
            evn_code = message_type.split("^")[1] if "^" in message_type else message_type
            segments.append(self._build_evn(evn_code))
            segments.append(self._build_pid(patient))
            segments.append(self._build_pv1(visit))
            for dx in diagnoses:
                segments.append(self._build_dg1(dx))
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(str(exc))
            self.logger.exception("Failed to build ADT message")
        message = "\r".join(segments)
        self._build_stats[message_type] = self._build_stats.get(message_type, 0) + 1
        return HL7BuildResult(success=len(errors) == 0, message=message, message_type=message_type, segment_count=len(segments), errors=errors)

    def build_ack_message(self, original_msh: HL7MessageHeader, ack_code: str = "AA", error_message: str = "") -> HL7BuildResult:
        """Build an ACK response swapping sender/receiver from the original MSH."""
        message_type = HL7MessageType.ACK.value
        try:
            msh = self.field_separator.join(
                [
                    "MSH",
                    "^~\\&",
                    original_msh.receiving_application,
                    original_msh.receiving_facility,
                    original_msh.sending_application,
                    original_msh.sending_facility,
                    datetime.utcnow().strftime("%Y%m%d%H%M%S"),
                    "",
                    message_type,
                    uuid.uuid4().hex[:12].upper(),
                    original_msh.processing_id or "P",
                    original_msh.version_id or "2.5",
                ]
            )
            msa = self.field_separator.join(["MSA", ack_code, original_msh.message_control_id, error_message])
            message = "\r".join([msh, msa])
            self._build_stats[message_type] = self._build_stats.get(message_type, 0) + 1
            return HL7BuildResult(success=True, message=message, message_type=message_type, segment_count=2)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.exception("Failed to build ACK message")
            return HL7BuildResult(success=False, message_type=message_type, errors=[str(exc)])

    def build_oru_message(
        self,
        patient: HL7Patient,
        observations: List[HL7Observation],
        order_info: Optional[Dict[str, Any]] = None,
    ) -> HL7BuildResult:
        """Build an ORU^R01 message with optional OBR order info."""
        message_type = HL7MessageType.ORU_R01.value
        segments: List[str] = []
        errors: List[str] = []
        try:
            segments.append(self._build_msh(message_type))
            segments.append(self._build_pid(patient))
            if order_info:
                service_code = order_info.get("service_code", "")
                service_desc = order_info.get("service_desc", "")
                obr = self.field_separator.join(
                    [
                        "OBR",
                        "1",
                        order_info.get("placer_order_number", ""),
                        order_info.get("filler_order_number", ""),
                        f"{service_code}^{service_desc}",
                    ]
                )
                segments.append(obr)
            for obs in observations:
                segments.append(self._build_obx(obs))
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(str(exc))
            self.logger.exception("Failed to build ORU message")
        message = "\r".join(segments)
        self._build_stats[message_type] = self._build_stats.get(message_type, 0) + 1
        return HL7BuildResult(success=len(errors) == 0, message=message, message_type=message_type, segment_count=len(segments), errors=errors)

    def build_dft_message(
        self,
        patient: HL7Patient,
        visit: HL7Visit,
        transactions: List[HL7FinancialTransaction],
        diagnoses: Optional[List[HL7Diagnosis]] = None,
    ) -> HL7BuildResult:
        """Build a DFT^P03 charge message with transactions and optional diagnoses."""
        message_type = HL7MessageType.DFT_P03.value
        diagnoses = diagnoses or []
        segments: List[str] = []
        errors: List[str] = []
        try:
            segments.append(self._build_msh(message_type))
            evn_code = message_type.split("^")[1] if "^" in message_type else message_type
            segments.append(self._build_evn(evn_code))
            segments.append(self._build_pid(patient))
            segments.append(self._build_pv1(visit))
            for ft in transactions:
                segments.append(self._build_ft1(ft))
            for dx in diagnoses:
                segments.append(self._build_dg1(dx))
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(str(exc))
            self.logger.exception("Failed to build DFT message")
        message = "\r".join(segments)
        self._build_stats[message_type] = self._build_stats.get(message_type, 0) + 1
        return HL7BuildResult(success=len(errors) == 0, message=message, message_type=message_type, segment_count=len(segments), errors=errors)

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def to_fhir_patient(self, hl7_patient: HL7Patient) -> Dict[str, Any]:
        """Convert an HL7 patient model into a FHIR Patient resource dict."""
        return {
            "resourceType": "Patient",
            "identifier": [
                {
                    "system": "urn:oid:1.2.840.114350",
                    "value": hl7_patient.mrn,
                }
            ],
            "name": [
                {
                    "family": hl7_patient.last_name,
                    "given": [hl7_patient.first_name] + ([hl7_patient.middle_name] if hl7_patient.middle_name else []),
                }
            ],
            "gender": {"M": "male", "F": "female"}.get((hl7_patient.gender or "").upper(), "unknown"),
            "birthDate": f"{hl7_patient.date_of_birth[:4]}-{hl7_patient.date_of_birth[4:6]}-{hl7_patient.date_of_birth[6:]}" if hl7_patient.date_of_birth else None,
            "address": [
                {
                    "line": [hl7_patient.address_street] if hl7_patient.address_street else [],
                    "city": hl7_patient.address_city,
                    "state": hl7_patient.address_state,
                    "postalCode": hl7_patient.address_zip,
                }
            ],
            "telecom": [
                {
                    "system": "phone",
                    "value": hl7_patient.phone,
                }
            ]
            if hl7_patient.phone
            else [],
        }

    def to_fhir_encounter(self, hl7_visit: HL7Visit, patient_id: str) -> Dict[str, Any]:
        """Convert an HL7 visit model into a FHIR Encounter resource dict."""
        class_map = {
            "I": "IMP",
            "O": "AMB",
            "E": "EMER",
            "B": "OBSENC",
        }
        return {
            "resourceType": "Encounter",
            "status": "in-progress",
            "class": {"code": class_map.get(hl7_visit.patient_class, "AMB")},
            "subject": {"reference": f"Patient/{patient_id}"},
            "id": hl7_visit.visit_number or hl7_visit.visit_id or str(uuid.uuid4()),
            "period": {
                "start": self._parse_datetime(hl7_visit.admit_datetime),
                "end": self._parse_datetime(hl7_visit.discharge_datetime),
            },
        }

    def to_fhir_condition(self, hl7_diagnosis: HL7Diagnosis, patient_id: str) -> Dict[str, Any]:
        """Convert an HL7 diagnosis into a FHIR Condition resource dict."""
        return {
            "resourceType": "Condition",
            "subject": {"reference": f"Patient/{patient_id}"},
            "code": {
                "coding": [
                    {
                        "system": "http://hl7.org/fhir/sid/icd-10",
                        "code": hl7_diagnosis.code,
                        "display": hl7_diagnosis.description,
                    }
                ]
            },
            "recordedDate": self._parse_datetime(hl7_diagnosis.diagnosis_datetime),
            "category": [{"text": hl7_diagnosis.diagnosis_type}],
        }

    def hl7_to_internal(self, parse_result: HL7ParseResult) -> Dict[str, Any]:
        """Convert a parse result into MEDI-COMPLY internal structure."""
        internal: Dict[str, Any] = {}
        patient = parse_result.patient or {}
        encounter = parse_result.encounter or {}
        internal["patient_id"] = patient.get("mrn") or patient.get("patient_id")
        internal["patient_name"] = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
        internal["date_of_birth"] = patient.get("date_of_birth")
        internal["gender"] = patient.get("gender")
        internal["age"] = patient.get("age")
        internal["encounter_type"] = encounter.get("patient_class")
        internal["encounter_id"] = encounter.get("visit_id") or encounter.get("visit_number")
        internal["diagnoses"] = [
            {
                "code": dx.get("code"),
                "description": dx.get("description"),
                "type": dx.get("diagnosis_type"),
            }
            for dx in parse_result.diagnoses
        ]
        internal["procedures"] = [
            {
                "code": proc.get("code"),
                "description": proc.get("description"),
            }
            for proc in parse_result.procedures
        ]
        internal["observations"] = [
            {
                "name": obs.get("observation_name"),
                "value": obs.get("value"),
                "units": obs.get("units"),
                "status": obs.get("result_status"),
            }
            for obs in parse_result.observations
        ]
        internal["insurance"] = [
            {
                "plan_id": ins.get("plan_id"),
                "company": ins.get("company_name"),
                "subscriber_id": ins.get("subscriber_id"),
            }
            for ins in parse_result.insurance
        ]
        return internal

    # ------------------------------------------------------------------
    # Validation & stats
    # ------------------------------------------------------------------

    def validate_message(self, raw_message: str) -> Dict[str, Any]:
        """Lightweight validation of HL7 shape and presence of key fields."""
        errors: List[str] = []
        warnings: List[str] = []
        if not raw_message or not raw_message.strip():
            errors.append("Empty message")
        elif not raw_message.startswith("MSH"):
            errors.append("Message must start with MSH")
        if "|" not in raw_message:
            errors.append("Missing field separators '|'")
        segment_count = len([ln for ln in re.split(r"\r\n|\n|\r", raw_message) if ln.strip()])
        msg_type = None
        if raw_message.startswith("MSH"):
            try:
                first = raw_message.split("\r")[0]
                msg_type = first.split("|")[8]
            except Exception:
                warnings.append("Unable to determine message type")
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings, "segment_count": segment_count, "message_type": msg_type}

    def get_parse_stats(self) -> Dict[str, int]:
        """Return accumulated parse counts per message type."""
        return dict(self._parse_stats)

    def get_build_stats(self) -> Dict[str, int]:
        """Return accumulated build counts per message type."""
        return dict(self._build_stats)


# ---------------------------------------------------------------------------
# Main demonstration
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    adapter = HL7Adapter()

    sample_adt = (
        "MSH|^~\\&|HIS|HOSPITAL|MEDICOMPLY|SYSTEM|20240115103000||ADT^A01|MSG001|P|2.5\r"
        "EVN|A01|20240115103000\r"
        "PID|1||MRN12345^^^HOSPITAL^MR||DOE^JANE^MARIE||19600322|F||W|456 OAK AVE^^CHICAGO^IL^60601||5559876543|||||||ACC001\r"
        "PV1|1|I|ICU^101^A|E|||DR001^SMITH^JOHN|||CAR||||ADM|||DR001^SMITH^JOHN|IP|V001|||||||||||||||||||||||||20240115080000\r"
        "DG1|1|I10|I21.4^Non-ST elevation myocardial infarction^I10||20240115|A\r"
        "DG1|2|I10|E11.22^Type 2 DM with diabetic CKD^I10||20240115|A\r"
        "DG1|3|I10|N18.31^CKD stage 3a^I10||20240115|A\r"
        "IN1|1|BCBS001|BCBS|Blue Cross Blue Shield|||||||||||DOE^JANE|01|20240101|20241231\r"
        "AL1|1|DA|PCN^Penicillin|SV|Anaphylaxis\r"
        "OBX|1|NM|2160-0^Creatinine^LN||1.8|mg/dL|0.6-1.2|H|||F||20240115\r"
        "OBX|2|NM|33762-6^GFR^LN||38|mL/min/1.73m2|>60|L|||F||20240115\r"
        "OBX|3|NM|49563-0^Troponin^LN||0.8|ng/mL|<0.04|C|||F||20240115\r"
    )

    result = adapter.parse_message(sample_adt)
    print(f"Parse success: {result.success}")
    print(f"Message type: {result.message_type}")
    print(f"Segments parsed: {result.raw_segment_count}")

    if result.patient:
        print(f"\nPatient: {result.patient}")

    if result.encounter:
        print(f"\nEncounter: {result.encounter}")

    print(f"\nDiagnoses ({len(result.diagnoses)}):")
    for dx in result.diagnoses:
        print(f"  {dx}")

    print(f"\nObservations ({len(result.observations)}):")
    for obs in result.observations:
        print(f"  {obs}")

    print(f"\nInsurance ({len(result.insurance)}):")
    for ins in result.insurance:
        print(f"  {ins}")

    print(f"\nAllergies ({len(result.allergies)}):")
    for al in result.allergies:
        print(f"  {al}")

    patient_model = HL7Patient(
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
        phone="5551234567",
    )
    visit_model = HL7Visit(
        visit_id="V001",
        patient_class="I",
        assigned_location="ICU",
        room="201",
        bed="A",
        attending_doctor_id="DR001",
        attending_doctor_name="SMITH^JOHN",
        hospital_service="CAR",
        admit_datetime="20240115080000",
    )
    dx_models = [
        HL7Diagnosis(set_id=1, code="I21.4", description="Acute NSTEMI", diagnosis_type="A"),
        HL7Diagnosis(set_id=2, code="E11.22", description="T2DM with diabetic CKD", diagnosis_type="A"),
    ]

    build_result = adapter.build_adt_message(patient_model, visit_model, dx_models)
    print("\n--- Built ADT Message ---")
    print(f"Success: {build_result.success}")
    print(f"Segments: {build_result.segment_count}")
    print(f"\n{build_result.message}")

    reparse = adapter.parse_message(build_result.message)
    print("\n--- Round-trip Parse ---")
    print(f"Success: {reparse.success}")
    print(f"Patient: {reparse.patient}")
    print(f"Diagnoses: {len(reparse.diagnoses)}")

    internal = adapter.hl7_to_internal(result)
    print("\n--- Internal Format ---")
    print(f"Patient ID: {internal.get('patient_id')}")
    print(f"Encounter type: {internal.get('encounter_type')}")
    print(f"Diagnoses: {len(internal.get('diagnoses', []))}")
    print(f"Observations: {len(internal.get('observations', []))}")

    print(f"\nParse stats: {adapter.get_parse_stats()}")
    print(f"Build stats: {adapter.get_build_stats()}")
