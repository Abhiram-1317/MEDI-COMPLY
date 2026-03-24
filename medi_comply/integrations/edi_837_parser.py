"""
EDI 837 (Professional and Institutional) parser and builder for MEDI-COMPLY.

Parses X12 837 transactions into structured claim data without external EDI
libraries. Provides helpers to validate, convert to internal structures, and
build outbound 837 payloads. Designed to mirror adapter patterns used in FHIR
and HL7 adapters.
"""
from __future__ import annotations

import hashlib
import logging
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class EDI837Type(str, Enum):
    PROFESSIONAL = "837P"
    INSTITUTIONAL = "837I"


class ClaimFrequencyCode(str, Enum):
    ORIGINAL = "1"
    CORRECTED = "7"
    VOID = "8"


class ClaimFilingIndicator(str, Enum):
    COMMERCIAL = "CI"
    MEDICARE_A = "MA"
    MEDICARE_B = "MB"
    MEDICAID = "MC"
    CHAMPUS = "CH"
    BLUE_CROSS = "BL"
    HMO = "HM"
    PPO = "12"
    WORKERS_COMP = "WC"
    OTHER = "ZZ"


class PlaceOfService(str, Enum):
    OFFICE = "11"
    HOME = "12"
    INPATIENT = "21"
    OUTPATIENT = "22"
    EMERGENCY = "23"
    ASC = "24"
    SKILLED_NURSING = "31"
    TELEHEALTH = "02"
    LAB = "81"


class DiagnosisQualifier(str, Enum):
    ABK = "ABK"
    ABF = "ABF"
    BK = "BK"
    BF = "BF"
    ABJ = "ABJ"
    APR = "APR"


class EDISegment(BaseModel):
    segment_id: str
    elements: List[str] = Field(default_factory=list)
    raw_text: str = ""


class EDIEnvelope(BaseModel):
    sender_id: str = ""
    receiver_id: str = ""
    interchange_control_number: str = ""
    interchange_date: str = ""
    interchange_time: str = ""
    group_control_number: str = ""
    version: str = ""
    transaction_type: str = ""


class EDISubmitter(BaseModel):
    entity_type: str = ""
    name: str = ""
    identifier: str = ""
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None


class EDIProvider(BaseModel):
    entity_type: str = ""
    last_name: str = ""
    first_name: Optional[str] = None
    npi: str = ""
    tax_id: Optional[str] = None
    taxonomy_code: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    provider_type: str = ""


class EDISubscriber(BaseModel):
    payer_name: str = ""
    payer_id: str = ""
    subscriber_id: str = ""
    subscriber_last_name: str = ""
    subscriber_first_name: Optional[str] = None
    subscriber_dob: Optional[str] = None
    subscriber_gender: Optional[str] = None
    group_number: Optional[str] = None
    relationship_code: str = "18"
    claim_filing_indicator: str = ""
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None


class EDIPatient(BaseModel):
    is_subscriber: bool = True
    patient_last_name: Optional[str] = None
    patient_first_name: Optional[str] = None
    patient_dob: Optional[str] = None
    patient_gender: Optional[str] = None
    patient_address: Optional[str] = None
    patient_city: Optional[str] = None
    patient_state: Optional[str] = None
    patient_zip: Optional[str] = None
    relationship_to_subscriber: str = "18"


class EDIDiagnosis(BaseModel):
    code: str
    qualifier: str = DiagnosisQualifier.ABK.value
    sequence: int = 1
    is_primary: bool = False


class EDIServiceLine(BaseModel):
    line_number: int = 1
    procedure_code: str = ""
    modifiers: List[str] = Field(default_factory=list)
    charge_amount: float = 0.0
    units: float = 1.0
    unit_type: str = "UN"
    place_of_service: str = PlaceOfService.OFFICE.value
    diagnosis_pointers: List[int] = Field(default_factory=list)
    service_date: Optional[str] = None
    service_end_date: Optional[str] = None
    rendering_provider_npi: Optional[str] = None
    revenue_code: Optional[str] = None
    description: Optional[str] = None
    ndc_code: Optional[str] = None
    authorization_number: Optional[str] = None
    line_note: Optional[str] = None


class EDIClaim(BaseModel):
    claim_id: str = ""
    claim_type: EDI837Type = EDI837Type.PROFESSIONAL
    total_charge: float = 0.0
    place_of_service: str = PlaceOfService.OFFICE.value
    facility_code: Optional[str] = None
    frequency_code: str = ClaimFrequencyCode.ORIGINAL.value
    provider_signature: bool = True
    assignment_of_benefits: bool = True
    release_of_info: bool = True
    patient_paid_amount: Optional[float] = None
    prior_authorization_number: Optional[str] = None
    referral_number: Optional[str] = None
    onset_date: Optional[str] = None
    admission_date: Optional[str] = None
    discharge_date: Optional[str] = None
    admission_type: Optional[str] = None
    admission_source: Optional[str] = None
    patient_status: Optional[str] = None
    drg_code: Optional[str] = None
    submitter: Optional[EDISubmitter] = None
    billing_provider: Optional[EDIProvider] = None
    rendering_provider: Optional[EDIProvider] = None
    referring_provider: Optional[EDIProvider] = None
    subscriber: Optional[EDISubscriber] = None
    patient: Optional[EDIPatient] = None
    diagnoses: List[EDIDiagnosis] = Field(default_factory=list)
    service_lines: List[EDIServiceLine] = Field(default_factory=list)


class EDI837ParseResult(BaseModel):
    success: bool
    claim_type: Optional[str] = None
    envelope: Optional[EDIEnvelope] = None
    claims: List[EDIClaim] = Field(default_factory=list)
    segment_count: int = 0
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    raw_segments: List[str] = Field(default_factory=list)


class EDI837BuildResult(BaseModel):
    success: bool
    edi_text: str = ""
    claim_type: str = ""
    segment_count: int = 0
    errors: List[str] = Field(default_factory=list)


class EDI837Parser:
    """Parser and builder for EDI 837 transactions."""

    def __init__(self) -> None:
        """Initialize parser defaults."""
        self.logger = logger
        self.element_separator = "*"
        self.segment_terminator = "~"
        self.component_separator = ":"
        self.repetition_separator = "^"
        self._parse_stats: Dict[str, int] = {EDI837Type.PROFESSIONAL.value: 0, EDI837Type.INSTITUTIONAL.value: 0, "errors": 0}

    # ------------------------------------------------------------------
    # Core parse entrypoint
    # ------------------------------------------------------------------

    def parse(self, raw_edi: str) -> EDI837ParseResult:
        """Parse raw EDI 837 text into structured claims."""
        if not raw_edi or not raw_edi.strip():
            return EDI837ParseResult(success=False, errors=["Empty EDI input"], segment_count=0)

        try:
            self._detect_separators(raw_edi)
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.exception("Failed to detect separators")
            return EDI837ParseResult(success=False, errors=[f"Separator detection failed: {exc}"], segment_count=0)

        segments = self._parse_segments(raw_edi)
        parse_result = EDI837ParseResult(success=True, segment_count=len(segments), raw_segments=[s.raw_text for s in segments])

        envelope = EDIEnvelope()
        claims: List[EDIClaim] = []
        current_claim_type: Optional[str] = None

        pending_billing_provider: Optional[EDIProvider] = None
        pending_subscriber: Optional[EDISubscriber] = None
        pending_payer: Optional[Dict[str, str]] = None
        pending_sbr: Optional[Dict[str, str]] = None

        try:
            for idx, seg in enumerate(segments):
                sid = seg.segment_id
                if sid == "ISA":
                    isa_vals = self._parse_isa(seg)
                    envelope.sender_id = isa_vals.get("sender_id", "")
                    envelope.receiver_id = isa_vals.get("receiver_id", "")
                    envelope.interchange_control_number = isa_vals.get("control_number", "")
                    envelope.interchange_date = isa_vals.get("date", "")
                    envelope.interchange_time = isa_vals.get("time", "")
                elif sid == "GS":
                    gs_vals = self._parse_gs(seg)
                    envelope.group_control_number = gs_vals.get("control_number", "")
                    envelope.version = gs_vals.get("version", "")
                    envelope.transaction_type = gs_vals.get("functional_id", "")
                    if "223" in envelope.version:
                        current_claim_type = EDI837Type.INSTITUTIONAL.value
                    else:
                        current_claim_type = EDI837Type.PROFESSIONAL.value
                elif sid == "ST":
                    current_claim_type = current_claim_type or EDI837Type.PROFESSIONAL.value
                elif sid == "NM1":
                    nm = self._parse_nm1(seg)
                    ent = nm.get("entity_id")
                    if ent == "85":
                        pending_billing_provider = EDIProvider(
                            entity_type=nm.get("entity_type", ""),
                            last_name=nm.get("last_name", ""),
                            first_name=nm.get("first_name"),
                            npi=nm.get("id_code", ""),
                            provider_type="billing",
                        )
                    elif ent == "IL":
                        pending_subscriber = EDISubscriber(
                            subscriber_last_name=nm.get("last_name", ""),
                            subscriber_first_name=nm.get("first_name"),
                            subscriber_id=nm.get("id_code", ""),
                        )
                    elif ent == "PR":
                        pending_payer = {"name": nm.get("last_name", ""), "id": nm.get("id_code", "")}
                elif sid == "SBR":
                    pending_sbr = self._parse_sbr(seg)
                elif sid == "CLM":
                    claim, new_idx = self._parse_claim_block(segments, idx)
                    if pending_billing_provider and not claim.billing_provider:
                        claim.billing_provider = pending_billing_provider
                    if pending_subscriber and not claim.subscriber:
                        claim.subscriber = pending_subscriber
                    if pending_sbr:
                        sub = claim.subscriber or EDISubscriber()
                        sub.relationship_code = pending_sbr.get("relationship", "18")
                        sub.claim_filing_indicator = pending_sbr.get("claim_filing_indicator", "")
                        sub.group_number = pending_sbr.get("group_number")
                        claim.subscriber = sub
                    if pending_payer:
                        sub = claim.subscriber or EDISubscriber()
                        sub.payer_name = pending_payer.get("name", "")
                        sub.payer_id = pending_payer.get("id", "")
                        claim.subscriber = sub
                    claims.append(claim)
                    pending_billing_provider = None
                    pending_subscriber = None
                    pending_payer = None
                    pending_sbr = None
                    if current_claim_type:
                        claim.claim_type = EDI837Type(current_claim_type)
                    continue  # will move index via loop naturally
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.exception("Parse failure")
            parse_result.success = False
            parse_result.errors.append(str(exc))
            self._parse_stats["errors"] += 1

        parse_result.envelope = envelope
        parse_result.claims = claims
        parse_result.claim_type = current_claim_type
        if not claims:
            parse_result.success = False
            parse_result.errors.append("No claims found")
            self._parse_stats["errors"] += 1
        else:
            if current_claim_type:
                self._parse_stats[current_claim_type] = self._parse_stats.get(current_claim_type, 0) + len(claims)
        return parse_result

    # ------------------------------------------------------------------
    # Segment parsing helpers
    # ------------------------------------------------------------------

    def _parse_segments(self, raw_edi: str) -> List[EDISegment]:
        """Split raw EDI text into segments using the detected terminator."""
        parts = re.split(re.escape(self.segment_terminator), raw_edi)
        segments: List[EDISegment] = []
        for part in parts:
            line = part.strip("\r\n")
            if not line:
                continue
            elements = line.split(self.element_separator)
            segment_id = elements[0]
            segments.append(EDISegment(segment_id=segment_id, elements=elements[1:], raw_text=line))
        return segments

    def _detect_separators(self, raw_edi: str) -> Tuple[str, str, str]:
        """Detect separators from the ISA segment if possible.

        The ISA is fixed width, but real-world payloads sometimes trim padding
        which makes fixed index lookups brittle. We fall back to positional
        detection: element separator at index 3, segment terminator as the first
        delimiter after the ISA segment, and component separator immediately
        before that terminator."""

        if raw_edi.startswith("ISA"):
            self.element_separator = raw_edi[3]

            # Find the end of the ISA segment using the first common terminator.
            isa_end = raw_edi.find("~")
            if isa_end == -1:
                isa_end = raw_edi.find("\n")

            if isa_end > 0:
                self.segment_terminator = raw_edi[isa_end]
                # Component separator is the last character in ISA (just before terminator)
                if isa_end - 1 < len(raw_edi):
                    self.component_separator = raw_edi[isa_end - 1]

        return self.element_separator, self.component_separator, self.segment_terminator

    def _parse_isa(self, segment: EDISegment) -> Dict[str, str]:
        """Parse ISA interchange header elements."""
        vals = segment.elements
        return {
            "sender_id": self._get_element(segment, 5).strip(),
            "receiver_id": self._get_element(segment, 7).strip(),
            "date": self._get_element(segment, 8),
            "time": self._get_element(segment, 9),
            "control_number": self._get_element(segment, 12),
            "usage_indicator": self._get_element(segment, 14),
        }

    def _parse_gs(self, segment: EDISegment) -> Dict[str, str]:
        """Parse GS functional group header."""
        vals = segment.elements
        return {
            "functional_id": self._get_element(segment, 0),
            "sender": self._get_element(segment, 1),
            "receiver": self._get_element(segment, 2),
            "date": self._get_element(segment, 3),
            "time": self._get_element(segment, 4),
            "control_number": self._get_element(segment, 5),
            "agency": self._get_element(segment, 6),
            "version": self._get_element(segment, 7),
        }

    def _parse_bht(self, segment: EDISegment) -> Dict[str, str]:
        """Parse BHT beginning of hierarchical transaction."""
        return {
            "structure_code": self._get_element(segment, 0),
            "purpose": self._get_element(segment, 1),
            "reference_id": self._get_element(segment, 2),
            "date": self._get_element(segment, 3),
            "time": self._get_element(segment, 4),
            "type_code": self._get_element(segment, 5),
        }

    def _parse_nm1(self, segment: EDISegment) -> Dict[str, str]:
        """Parse NM1 entity name segment."""
        return {
            "entity_id": self._get_element(segment, 0),
            "entity_type": self._get_element(segment, 1),
            "last_name": self._get_element(segment, 2),
            "first_name": self._get_element(segment, 3),
            "id_qualifier": self._get_element(segment, 7),
            "id_code": self._get_element(segment, 8),
        }

    def _parse_clm(self, segment: EDISegment) -> Dict[str, Any]:
        """Parse CLM claim header segment."""
        result: Dict[str, Any] = {}
        result["claim_id"] = self._get_element(segment, 0)
        charge_raw = self._get_element(segment, 1)
        result["total_charge"] = float(charge_raw or 0)
        pos_comp = self._get_element(segment, 4)
        parts = pos_comp.split(self.component_separator) if pos_comp else []
        result["place_of_service"] = parts[0] if parts else ""
        if len(parts) > 2:
            result["frequency_code"] = parts[2]
        result["provider_signature"] = self._get_element(segment, 5, "Y") == "Y"
        result["assignment_of_benefits"] = self._get_element(segment, 6, "A") in {"A", "Y"}
        result["release_of_info"] = self._get_element(segment, 8, "Y") in {"Y", "I"}
        return result

    def _parse_hi(self, segment: EDISegment) -> List[EDIDiagnosis]:
        """Parse HI diagnoses segment."""
        diagnoses: List[EDIDiagnosis] = []
        seq = 1
        for element in segment.elements:
            if not element:
                continue
            qual = self._get_component(element, 0)
            code_raw = self._get_component(element, 1)
            code = self._format_icd10_code(code_raw)
            diagnoses.append(EDIDiagnosis(code=code, qualifier=qual, sequence=seq, is_primary=(seq == 1)))
            seq += 1
        return diagnoses

    def _parse_sv1(self, segment: EDISegment) -> EDIServiceLine:
        """Parse SV1 professional service line."""
        comp = self._get_element(segment, 0)
        comps = comp.split(self.component_separator) if comp else []
        qualifiers = comps[0] if comps else ""
        code = comps[1] if len(comps) > 1 else ""
        modifiers = [c for c in comps[2:] if c]
        charge = float(self._get_element(segment, 1) or 0)
        unit_type = self._get_element(segment, 2, "UN")
        units_raw = self._get_element(segment, 3) or "1"
        try:
            units = float(units_raw)
        except ValueError:
            units = 1.0
        pos = self._get_element(segment, 4, PlaceOfService.OFFICE.value)
        pointers_raw = self._get_element(segment, 6)
        pointers: List[int] = []
        if pointers_raw:
            for p in pointers_raw.split(self.component_separator):
                try:
                    pointers.append(int(p))
                except ValueError:
                    continue
        line = EDIServiceLine(
            procedure_code=code,
            modifiers=modifiers,
            charge_amount=charge,
            units=units,
            unit_type=unit_type,
            place_of_service=pos,
            diagnosis_pointers=pointers,
        )
        return line

    def _parse_sv2(self, segment: EDISegment) -> EDIServiceLine:
        """Parse SV2 institutional service line."""
        revenue_code = self._get_element(segment, 0)
        proc_comp = self._get_element(segment, 1)
        proc_parts = proc_comp.split(self.component_separator) if proc_comp else []
        code = proc_parts[1] if len(proc_parts) > 1 else ""
        modifiers = proc_parts[2:]
        charge = float(self._get_element(segment, 2) or 0)
        unit_type = self._get_element(segment, 3, "UN")
        units_raw = self._get_element(segment, 4) or "1"
        try:
            units = float(units_raw)
        except ValueError:
            units = 1.0
        line = EDIServiceLine(
            procedure_code=code,
            modifiers=[m for m in modifiers if m],
            charge_amount=charge,
            units=units,
            unit_type=unit_type,
            revenue_code=revenue_code,
        )
        return line

    def _parse_dtp(self, segment: EDISegment) -> Dict[str, str]:
        """Parse DTP date/time reference."""
        qualifier = self._get_element(segment, 0)
        fmt = self._get_element(segment, 1)
        value = self._get_element(segment, 2)
        return {"qualifier": qualifier, "format": fmt, "value": value}

    def _parse_ref(self, segment: EDISegment) -> Dict[str, str]:
        """Parse REF reference segment."""
        return {"qualifier": self._get_element(segment, 0), "value": self._get_element(segment, 1)}

    def _parse_sbr(self, segment: EDISegment) -> Dict[str, str]:
        """Parse SBR subscriber info."""
        return {
            "sequence": self._get_element(segment, 0),
            "relationship": self._get_element(segment, 1, "18"),
            "group_number": self._get_element(segment, 2),
            "group_name": self._get_element(segment, 3),
            "claim_filing_indicator": self._get_element(segment, 8),
        }

    def _parse_dmg(self, segment: EDISegment) -> Dict[str, str]:
        """Parse DMG demographics."""
        return {"dob": self._get_element(segment, 1), "gender": self._get_element(segment, 2)}

    def _parse_amt(self, segment: EDISegment) -> Dict[str, Any]:
        """Parse AMT monetary amount."""
        qualifier = self._get_element(segment, 0)
        amt_raw = self._get_element(segment, 1)
        amt_val: Optional[float] = None
        try:
            amt_val = float(amt_raw)
        except Exception:
            amt_val = None
        return {"qualifier": qualifier, "amount": amt_val}

    def _parse_claim_block(self, segments: List[EDISegment], start_index: int) -> Tuple[EDIClaim, int]:
        """Parse a claim starting at the given CLM segment index."""
        claim = EDIClaim()
        idx = start_index
        seq_service_line = 1
        while idx < len(segments):
            seg = segments[idx]
            sid = seg.segment_id
            if idx > start_index and sid == "CLM":
                break
            if sid == "CLM":
                clm_vals = self._parse_clm(seg)
                claim.claim_id = clm_vals.get("claim_id", "")
                claim.total_charge = clm_vals.get("total_charge", 0.0)
                claim.place_of_service = clm_vals.get("place_of_service", PlaceOfService.OFFICE.value)
                claim.frequency_code = clm_vals.get("frequency_code", ClaimFrequencyCode.ORIGINAL.value)
                claim.provider_signature = clm_vals.get("provider_signature", True)
                claim.assignment_of_benefits = clm_vals.get("assignment_of_benefits", True)
                claim.release_of_info = clm_vals.get("release_of_info", True)
            elif sid == "HI":
                claim.diagnoses.extend(self._parse_hi(seg))
            elif sid == "DTP":
                dtp = self._parse_dtp(seg)
                if dtp["qualifier"] == "472":
                    claim.onset_date = self._format_date(dtp["value"]) if dtp["format"] == "D8" else None
                elif dtp["qualifier"] == "431":
                    claim.onset_date = self._format_date(dtp["value"])
                elif dtp["qualifier"] == "435":
                    claim.admission_date = self._format_date(dtp["value"])
                elif dtp["qualifier"] == "096":
                    claim.discharge_date = self._format_date(dtp["value"])
            elif sid == "REF":
                ref = self._parse_ref(seg)
                if ref["qualifier"] == "G1":
                    claim.prior_authorization_number = ref["value"]
                elif ref["qualifier"] == "9F":
                    claim.referral_number = ref["value"]
            elif sid == "NM1":
                nm = self._parse_nm1(seg)
                ent = nm.get("entity_id")
                if ent == "85":
                    claim.billing_provider = EDIProvider(
                        entity_type=nm.get("entity_type", ""),
                        last_name=nm.get("last_name", ""),
                        first_name=nm.get("first_name"),
                        npi=nm.get("id_code", ""),
                        provider_type="billing",
                    )
                elif ent == "82":
                    claim.rendering_provider = EDIProvider(
                        entity_type=nm.get("entity_type", ""),
                        last_name=nm.get("last_name", ""),
                        first_name=nm.get("first_name"),
                        npi=nm.get("id_code", ""),
                        provider_type="rendering",
                    )
                elif ent == "DN":
                    claim.referring_provider = EDIProvider(
                        entity_type=nm.get("entity_type", ""),
                        last_name=nm.get("last_name", ""),
                        first_name=nm.get("first_name"),
                        npi=nm.get("id_code", ""),
                        provider_type="referring",
                    )
                elif ent == "IL":
                    claim.subscriber = claim.subscriber or EDISubscriber(
                        subscriber_last_name=nm.get("last_name", ""),
                        subscriber_first_name=nm.get("first_name"),
                        subscriber_id=nm.get("id_code", ""),
                    )
                elif ent == "QC":
                    claim.patient = claim.patient or EDIPatient(
                        is_subscriber=False,
                        patient_last_name=nm.get("last_name"),
                        patient_first_name=nm.get("first_name"),
                    )
                elif ent == "PR":
                    # payer
                    sub = claim.subscriber or EDISubscriber()
                    sub.payer_name = nm.get("last_name", "")
                    sub.payer_id = nm.get("id_code", "")
                    claim.subscriber = sub
            elif sid == "SBR":
                sbr = self._parse_sbr(seg)
                sub = claim.subscriber or EDISubscriber()
                sub.relationship_code = sbr.get("relationship", "18")
                sub.claim_filing_indicator = sbr.get("claim_filing_indicator", "")
                claim.subscriber = sub
            elif sid == "DMG":
                dmg = self._parse_dmg(seg)
                if claim.subscriber:
                    claim.subscriber.subscriber_dob = dmg.get("dob")
                    claim.subscriber.subscriber_gender = dmg.get("gender")
            elif sid == "AMT":
                amt = self._parse_amt(seg)
                if amt.get("qualifier") == "F5":
                    claim.patient_paid_amount = amt.get("amount")
            elif sid == "SV1":
                line = self._parse_sv1(seg)
                line.line_number = seq_service_line
                seq_service_line += 1
                # look ahead for next DTP with qualifier 472 for service date
                if idx + 1 < len(segments) and segments[idx + 1].segment_id == "DTP":
                    dtp = self._parse_dtp(segments[idx + 1])
                    if dtp.get("qualifier") == "472":
                        line.service_date = self._format_date(dtp.get("value"))
                        idx += 1
                claim.service_lines.append(line)
            elif sid == "SV2":
                line = self._parse_sv2(seg)
                line.line_number = seq_service_line
                seq_service_line += 1
                if idx + 1 < len(segments) and segments[idx + 1].segment_id == "DTP":
                    dtp = self._parse_dtp(segments[idx + 1])
                    if dtp.get("qualifier") == "472":
                        line.service_date = self._format_date(dtp.get("value"))
                        idx += 1
                claim.service_lines.append(line)
            elif sid == "SE":
                break
            idx += 1
        return claim, idx

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_icd10_code(self, raw_code: str) -> str:
        """Reinsert a dot into stripped ICD-10 code."""
        if not raw_code:
            return ""
        if "." in raw_code:
            return raw_code
        if len(raw_code) <= 3:
            return raw_code
        return f"{raw_code[:3]}.{raw_code[3:]}"

    def _strip_icd10_dot(self, code: str) -> str:
        """Remove dot from ICD-10 code for EDI format."""
        return code.replace(".", "") if code else ""

    def _format_date(self, edi_date: str) -> Optional[str]:
        """Convert CCYYMMDD to ISO date string."""
        if not edi_date:
            return None
        try:
            dt = datetime.strptime(edi_date, "%Y%m%d").date()
            return dt.isoformat()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Builders
    # ------------------------------------------------------------------

    def _build_isa(self, sender_id: str = "MEDICOMPLY", receiver_id: str = "PAYER") -> str:
        """Build ISA segment with padded IDs."""
        now = datetime.utcnow()
        control = f"{now.strftime('%H%M%S')}{now.microsecond:06d}"[-9:]
        sender_padded = sender_id.ljust(15)
        receiver_padded = receiver_id.ljust(15)
        return self.element_separator.join(
            [
                "ISA",
                "00",
                "".ljust(10),
                "00",
                "".ljust(10),
                "ZZ",
                sender_padded,
                "ZZ",
                receiver_padded,
                now.strftime("%y%m%d"),
                now.strftime("%H%M"),
                self.repetition_separator,
                "00501",
                control,
                "0",
                "P",
                self.component_separator,
            ]
        ) + self.segment_terminator

    def _build_gs(self, sender_id: str, receiver_id: str, version: str) -> str:
        """Build GS functional group header."""
        now = datetime.utcnow()
        control = now.strftime("%H%M%S")
        return self.element_separator.join(
            [
                "GS",
                "HC",
                sender_id,
                receiver_id,
                now.strftime("%Y%m%d"),
                now.strftime("%H%M"),
                control,
                "X",
                version,
            ]
        ) + self.segment_terminator

    def _build_clm(self, claim: EDIClaim) -> str:
        """Build CLM segment from claim."""
        clm05 = f"{claim.place_of_service}{self.component_separator}B{self.component_separator}{claim.frequency_code}"
        return self.element_separator.join(
            [
                "CLM",
                claim.claim_id,
                f"{claim.total_charge:.2f}",
                "",
                "",
                clm05,
                "Y" if claim.provider_signature else "N",
                "A" if claim.assignment_of_benefits else "B",
                "Y" if claim.assignment_of_benefits else "N",
                "Y" if claim.release_of_info else "I",
            ]
        ) + self.segment_terminator

    def _build_hi(self, diagnoses: List[EDIDiagnosis]) -> str:
        """Build HI segment from diagnoses."""
        if not diagnoses:
            return ""
        elements: List[str] = ["HI"]
        for idx, dx in enumerate(diagnoses):
            qual = DiagnosisQualifier.ABK.value if idx == 0 else DiagnosisQualifier.ABF.value
            code = self._strip_icd10_dot(dx.code)
            elements.append(f"{qual}{self.component_separator}{code}")
        return self.element_separator.join(elements) + self.segment_terminator

    def _build_sv1(self, line: EDIServiceLine) -> str:
        """Build SV1 segment for professional service line."""
        mods = self.component_separator.join(line.modifiers) if line.modifiers else ""
        proc_comp = f"HC{self.component_separator}{line.procedure_code}"
        if mods:
            proc_comp = f"{proc_comp}{self.component_separator}{mods}"
        pointers = self.component_separator.join(str(p) for p in line.diagnosis_pointers) if line.diagnosis_pointers else ""
        return self.element_separator.join(
            [
                "SV1",
                proc_comp,
                f"{line.charge_amount:.2f}",
                line.unit_type,
                f"{line.units:.2f}",
                line.place_of_service,
                "",
                pointers,
            ]
        ) + self.segment_terminator

    def _build_sv2(self, line: EDIServiceLine) -> str:
        """Build SV2 segment for institutional service line."""
        proc_comp = f"HC{self.component_separator}{line.procedure_code}"
        if line.modifiers:
            proc_comp = f"{proc_comp}{self.component_separator}{self.component_separator.join(line.modifiers)}"
        return self.element_separator.join(
            [
                "SV2",
                line.revenue_code or "",
                proc_comp,
                f"{line.charge_amount:.2f}",
                line.unit_type,
                f"{line.units:.2f}",
                "",
                "",
            ]
        ) + self.segment_terminator

    def _build_isa_to_iea(self, claim: EDIClaim, version: str) -> List[str]:
        """Build envelope (ISA/GS/ST/.../GE/IEA) for a single claim."""
        segments: List[str] = []
        segments.append(self._build_isa())
        segments.append(self._build_gs("MEDICOMPLY", "PAYER", version))
        segments.append(self.element_separator.join(["ST", "837", "0001", version]) + self.segment_terminator)
        segments.append(self.element_separator.join(["BHT", "0019", "00", claim.claim_id or "BATCH", datetime.utcnow().strftime("%Y%m%d"), datetime.utcnow().strftime("%H%M"), "CH"]) + self.segment_terminator)
        return segments

    def _build_common_loops(self, claim: EDIClaim) -> List[str]:
        """Build common loops for billing provider, subscriber, and payer."""
        segments: List[str] = []
        if claim.billing_provider:
            bp = claim.billing_provider
            segments.append(self.element_separator.join(["NM1", "85", bp.entity_type or "2", bp.last_name, bp.first_name or "", "", "", "", "XX", bp.npi]) + self.segment_terminator)
        if claim.subscriber:
            sub = claim.subscriber
            segments.append(self.element_separator.join(["SBR", "P", sub.relationship_code or "18", sub.group_number or "", sub.payer_name or "", "", "", "", sub.claim_filing_indicator or ""] + []) + self.segment_terminator)
            segments.append(self.element_separator.join(["NM1", "IL", "1", sub.subscriber_last_name, sub.subscriber_first_name or "", "", "", "", "MI", sub.subscriber_id]) + self.segment_terminator)
            if sub.address_line1:
                segments.append(self.element_separator.join(["N3", sub.address_line1]) + self.segment_terminator)
            city_state_zip = [sub.city or "", sub.state or "", sub.zip_code or ""]
            if any(city_state_zip):
                segments.append(self.element_separator.join(["N4"] + city_state_zip) + self.segment_terminator)
            if sub.subscriber_dob or sub.subscriber_gender:
                segments.append(self.element_separator.join(["DMG", "D8", sub.subscriber_dob or "", sub.subscriber_gender or ""]) + self.segment_terminator)
        if claim.rendering_provider:
            rp = claim.rendering_provider
            segments.append(self.element_separator.join(["NM1", "82", rp.entity_type or "1", rp.last_name, rp.first_name or "", "", "", "", "XX", rp.npi]) + self.segment_terminator)
        return segments

    def build_837p(self, claim: EDIClaim) -> EDI837BuildResult:
        """Build an 837P transaction for a single claim."""
        segments = self._build_isa_to_iea(claim, "005010X222A1")
        segments.extend(self._build_common_loops(claim))
        segments.append(self._build_clm(claim))
        # dates
        if claim.onset_date:
            segments.append(self.element_separator.join(["DTP", "431", "D8", claim.onset_date.replace("-", "")]) + self.segment_terminator)
        if claim.admission_date:
            segments.append(self.element_separator.join(["DTP", "435", "D8", claim.admission_date.replace("-", "")]) + self.segment_terminator)
        if claim.discharge_date:
            segments.append(self.element_separator.join(["DTP", "096", "D8", claim.discharge_date.replace("-", "")]) + self.segment_terminator)
        if claim.prior_authorization_number:
            segments.append(self.element_separator.join(["REF", "G1", claim.prior_authorization_number]) + self.segment_terminator)
        if claim.referral_number:
            segments.append(self.element_separator.join(["REF", "9F", claim.referral_number]) + self.segment_terminator)
        hi_seg = self._build_hi(claim.diagnoses)
        if hi_seg:
            segments.append(hi_seg)
        for line in claim.service_lines:
            segments.append(self._build_sv1(line))
            if line.service_date:
                segments.append(self.element_separator.join(["DTP", "472", "D8", line.service_date.replace("-", "")]) + self.segment_terminator)
        segments.append(self.element_separator.join(["SE", str(len(segments) - 2), "0001"]) + self.segment_terminator)
        segments.append(self.element_separator.join(["GE", "1", "1"]) + self.segment_terminator)
        segments.append(self.element_separator.join(["IEA", "1", "000000001"]) + self.segment_terminator)
        edi_text = "\n".join(segments)
        return EDI837BuildResult(success=True, edi_text=edi_text, claim_type=EDI837Type.PROFESSIONAL.value, segment_count=len(segments))

    def build_837i(self, claim: EDIClaim) -> EDI837BuildResult:
        """Build an 837I transaction for a single claim using SV2 lines."""
        claim.claim_type = EDI837Type.INSTITUTIONAL
        segments = self._build_isa_to_iea(claim, "005010X223A3")
        segments.extend(self._build_common_loops(claim))
        segments.append(self._build_clm(claim))
        hi_seg = self._build_hi(claim.diagnoses)
        if hi_seg:
            segments.append(hi_seg)
        for line in claim.service_lines:
            segments.append(self._build_sv2(line))
            if line.service_date:
                segments.append(self.element_separator.join(["DTP", "472", "D8", line.service_date.replace("-", "")]) + self.segment_terminator)
        segments.append(self.element_separator.join(["SE", str(len(segments) - 2), "0001"]) + self.segment_terminator)
        segments.append(self.element_separator.join(["GE", "1", "1"]) + self.segment_terminator)
        segments.append(self.element_separator.join(["IEA", "1", "000000001"]) + self.segment_terminator)
        edi_text = "\n".join(segments)
        return EDI837BuildResult(success=True, edi_text=edi_text, claim_type=EDI837Type.INSTITUTIONAL.value, segment_count=len(segments))

    # ------------------------------------------------------------------
    # Conversions
    # ------------------------------------------------------------------

    def to_internal_claim(self, edi_claim: EDIClaim) -> Dict[str, Any]:
        """Convert EDIClaim to internal MEDI-COMPLY structure."""
        internal: Dict[str, Any] = {}
        internal["claim_id"] = edi_claim.claim_id
        internal["patient_id"] = edi_claim.subscriber.subscriber_id if edi_claim.subscriber else None
        internal["provider_id"] = (edi_claim.rendering_provider or edi_claim.billing_provider).npi if (edi_claim.rendering_provider or edi_claim.billing_provider) else None
        internal["payer_id"] = edi_claim.subscriber.payer_id if edi_claim.subscriber else None
        internal["claim_type"] = edi_claim.claim_type.value
        internal["total_charge"] = edi_claim.total_charge
        internal["diagnosis_codes"] = [
            {"code": dx.code, "sequence": dx.sequence, "is_primary": dx.is_primary}
            for dx in edi_claim.diagnoses
        ]
        internal["line_items"] = [
            {
                "cpt_code": line.procedure_code,
                "modifiers": line.modifiers,
                "charge": line.charge_amount,
                "units": line.units,
                "place_of_service": line.place_of_service,
                "diagnosis_pointers": line.diagnosis_pointers,
                "service_date": line.service_date,
            }
            for line in edi_claim.service_lines
        ]
        internal["prior_auth_number"] = edi_claim.prior_authorization_number
        internal["referral_number"] = edi_claim.referral_number
        internal["admission_date"] = edi_claim.admission_date
        internal["discharge_date"] = edi_claim.discharge_date
        internal["onset_date"] = edi_claim.onset_date
        if edi_claim.subscriber:
            internal["subscriber"] = {
                "id": edi_claim.subscriber.subscriber_id,
                "name": f"{edi_claim.subscriber.subscriber_first_name or ''} {edi_claim.subscriber.subscriber_last_name}".strip(),
                "payer_id": edi_claim.subscriber.payer_id,
                "payer_name": edi_claim.subscriber.payer_name,
            }
        return internal

    def from_internal_claim(self, internal_claim: Dict[str, Any]) -> EDIClaim:
        """Convert internal claim dict to EDIClaim model."""
        claim = EDIClaim(
            claim_id=internal_claim.get("claim_id", ""),
            total_charge=float(internal_claim.get("total_charge", 0.0)),
            claim_type=EDI837Type(internal_claim.get("claim_type", EDI837Type.PROFESSIONAL.value)),
        )
        diagnoses = internal_claim.get("diagnosis_codes", [])
        for idx, dx in enumerate(diagnoses, start=1):
            claim.diagnoses.append(
                EDIDiagnosis(
                    code=dx.get("code", ""),
                    qualifier=DiagnosisQualifier.ABK.value if idx == 1 else DiagnosisQualifier.ABF.value,
                    sequence=idx,
                    is_primary=bool(dx.get("is_primary", idx == 1)),
                )
            )
        for line in internal_claim.get("line_items", []):
            claim.service_lines.append(
                EDIServiceLine(
                    procedure_code=line.get("cpt_code", ""),
                    modifiers=line.get("modifiers", []),
                    charge_amount=float(line.get("charge", 0.0)),
                    units=float(line.get("units", 1.0)),
                    place_of_service=line.get("place_of_service", PlaceOfService.OFFICE.value),
                    diagnosis_pointers=line.get("diagnosis_pointers", []),
                    service_date=line.get("service_date"),
                )
            )
        return claim

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_claim(self, claim: EDIClaim) -> Dict[str, Any]:
        """Validate basic completeness of an EDI claim."""
        errors: List[str] = []
        warnings: List[str] = []
        if not claim.claim_id:
            errors.append("Missing claim ID")
        if not claim.diagnoses:
            errors.append("At least one diagnosis required")
        if not claim.service_lines:
            errors.append("At least one service line required")
        if claim.total_charge <= 0:
            errors.append("Total charge must be positive")
        total_lines = sum(line.charge_amount for line in claim.service_lines)
        if claim.total_charge and abs(total_lines - claim.total_charge) > 0.01:
            warnings.append("Total charge does not match sum of service lines")
        for line in claim.service_lines:
            if not line.procedure_code or len(line.procedure_code) < 3:
                errors.append(f"Invalid procedure code on line {line.line_number}")
            for ptr in line.diagnosis_pointers:
                if ptr < 1 or ptr > len(claim.diagnoses):
                    errors.append(f"Invalid diagnosis pointer {ptr} on line {line.line_number}")
        for dx in claim.diagnoses:
            if not re.match(r"^[A-Z][0-9A-Z]{2,5}(\.[0-9A-Z]{1,4})?$", dx.code):
                warnings.append(f"Diagnosis code format suspicious: {dx.code}")
        if not (claim.rendering_provider or claim.billing_provider):
            warnings.append("Missing provider NPI")
        if claim.subscriber and not claim.subscriber.subscriber_id:
            warnings.append("Missing subscriber ID")
        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def get_parse_stats(self) -> Dict[str, int]:
        """Return parse statistics."""
        return dict(self._parse_stats)

    def _get_element(self, segment: EDISegment, index: int, default: str = "") -> str:
        """Safely get element by index from a segment."""
        try:
            return segment.elements[index]
        except IndexError:
            return default

    def _get_component(self, element: str, index: int, default: str = "") -> str:
        """Safely split a component element and return part."""
        if not element:
            return default
        parts = element.split(self.component_separator)
        try:
            return parts[index]
        except IndexError:
            return default


if __name__ == "__main__":
    parser = EDI837Parser()

    sample_837p = (
        "ISA*00*          *00*          *ZZ*MEDICOMPLY      *ZZ*BCBS           *240115*1030*^*00501*000000001*0*P*:~\n"
        "GS*HC*MEDICOMPLY*BCBS*20240115*1030*1*X*005010X222A1~\n"
        "ST*837*0001*005010X222A1~\n"
        "BHT*0019*00*BATCH001*20240115*1030*CH~\n"
        "NM1*85*2*SPRINGFIELD MEDICAL*****XX*1234567890~\n"
        "N3*123 MAIN ST~\n"
        "N4*SPRINGFIELD*IL*62701~\n"
        "NM1*87*2*SPRINGFIELD MEDICAL~\n"
        "N3*123 MAIN ST~\n"
        "N4*SPRINGFIELD*IL*62701~\n"
        "SBR*P*18*GRP001****CI~\n"
        "NM1*IL*1*DOE*JANE****MI*SUB12345~\n"
        "N3*456 OAK AVE~\n"
        "N4*CHICAGO*IL*60601~\n"
        "DMG*D8*19600322*F~\n"
        "NM1*PR*2*BLUE CROSS BLUE SHIELD*****PI*BCBS001~\n"
        "CLM*CLAIM001*1500***11:B:1*Y*A*Y*Y~\n"
        "DTP*431*D8*20240108~\n"
        "DTP*472*D8*20240115~\n"
        "REF*G1*AUTH12345~\n"
        "HI*ABK:I214*ABF:E1122*ABF:N1831*ABF:I10~\n"
        "NM1*82*1*SMITH*JOHN****XX*9876543210~\n"
        "SV1*HC:99223:25*500*UN*1***1~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:93000*150*UN*1***1:2~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:71046*200*UN*1***1~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:80053*100*UN*1***1:2:3~\n"
        "DTP*472*D8*20240115~\n"
        "SV1*HC:84484*75*UN*1***1~\n"
        "DTP*472*D8*20240115~\n"
        "SE*28*0001~\n"
        "GE*1*1~\n"
        "IEA*1*000000001~\n"
    )

    result = parser.parse(sample_837p)
    print(f"Parse success: {result.success}")
    print(f"Claim type: {result.claim_type}")
    print(f"Segments parsed: {result.segment_count}")
    print(f"Claims found: {len(result.claims)}")

    if result.claims:
        claim = result.claims[0]
        print(f"\n--- Claim Details ---")
        print(f"Claim ID: {claim.claim_id}")
        print(f"Total charge: ${claim.total_charge}")
        print(f"Place of service: {claim.place_of_service}")

        print(f"\nDiagnoses ({len(claim.diagnoses)}):")
        for dx in claim.diagnoses:
            print(f"  {dx.sequence}. [{dx.qualifier}] {dx.code} (primary={dx.is_primary})")

        print(f"\nService Lines ({len(claim.service_lines)}):")
        for line in claim.service_lines:
            mods = ",".join(line.modifiers) if line.modifiers else "none"
            ptrs = ",".join(str(p) for p in line.diagnosis_pointers)
            print(f"  Line {line.line_number}: {line.procedure_code} (mods={mods}) ${line.charge_amount} x{line.units} dx_ptrs=[{ptrs}]")

        if claim.billing_provider:
            print(f"\nBilling Provider: {claim.billing_provider.last_name} NPI={claim.billing_provider.npi}")
        if claim.rendering_provider:
            print(f"Rendering Provider: {claim.rendering_provider.last_name}, {claim.rendering_provider.first_name} NPI={claim.rendering_provider.npi}")
        if claim.subscriber:
            print(f"Subscriber: {claim.subscriber.subscriber_last_name}, {claim.subscriber.subscriber_first_name} ID={claim.subscriber.subscriber_id}")
            print(f"Payer: {claim.subscriber.payer_name} ({claim.subscriber.payer_id})")

        validation = parser.validate_claim(claim)
        print(f"\nValidation: valid={validation['valid']}")
        if validation['errors']:
            print(f"Errors: {validation['errors']}")
        if validation['warnings']:
            print(f"Warnings: {validation['warnings']}")

        internal = parser.to_internal_claim(claim)
        print(f"\n--- Internal Format ---")
        print(f"Patient ID: {internal.get('patient_id')}")
        print(f"Provider NPI: {internal.get('provider_id')}")
        print(f"Payer: {internal.get('payer_id')}")
        print(f"Diagnosis codes: {[d['code'] for d in internal.get('diagnosis_codes', [])]}")
        print(f"Line items: {len(internal.get('line_items', []))}")

        print(f"\n--- Building 837P from parsed claim ---")
        build_result = parser.build_837p(claim)
        print(f"Build success: {build_result.success}")
        print(f"Segments: {build_result.segment_count}")
        print(f"\n{build_result.edi_text[:500]}...")

    if result.errors:
        print(f"\nErrors: {result.errors}")
    if result.warnings:
        print(f"Warnings: {result.warnings}")

    print(f"\nParse stats: {parser.get_parse_stats()}")
