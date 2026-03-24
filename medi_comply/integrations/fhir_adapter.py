"""
FHIR R4 adapter for MEDI-COMPLY.

This module provides bidirectional conversion between FHIR R4 resources and
MEDI-COMPLY internal models to enable interoperability with EHR systems such as
Epic and Cerner. All conversions are rules-based, deterministic, and avoid
runtime dependencies on external FHIR SDKs to keep the adapter lightweight and
hackathon-friendly.

Constraints:
- Pure Python + Pydantic
- Defensive parsing with detailed debug logging
- Graceful handling of missing or malformed data
- Methods carry docstrings and type hints
- Conversion stats are tracked per resource type
"""

from __future__ import annotations

import json
import logging
import re
import uuid
import hashlib
from datetime import datetime, date
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# FHIR system constants
# ---------------------------------------------------------------------------

FHIR_ICD10_SYSTEM = "http://hl7.org/fhir/sid/icd-10-cm"
FHIR_CPT_SYSTEM = "http://www.ama-assn.org/go/cpt"
FHIR_HCPCS_SYSTEM = "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"
FHIR_LOINC_SYSTEM = "http://loinc.org"
FHIR_RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
FHIR_SNOMED_SYSTEM = "http://snomed.info/sct"
FHIR_NDC_SYSTEM = "http://hl7.org/fhir/sid/ndc"
FHIR_CLAIM_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/claim-type"
FHIR_ENCOUNTER_CLASS_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

# ---------------------------------------------------------------------------
# Enumerations and models
# ---------------------------------------------------------------------------


class FHIRResourceType(str, Enum):
    """FHIR resource types supported by the adapter."""

    PATIENT = "Patient"
    ENCOUNTER = "Encounter"
    CONDITION = "Condition"
    PROCEDURE = "Procedure"
    CLAIM = "Claim"
    CLAIM_RESPONSE = "ClaimResponse"
    COVERAGE = "Coverage"
    MEDICATION_REQUEST = "MedicationRequest"
    OBSERVATION = "Observation"
    DOCUMENT_REFERENCE = "DocumentReference"
    BUNDLE = "Bundle"


class FHIRValidationResult(BaseModel):
    """Validation response for inbound FHIR resources."""

    is_valid: bool
    resource_type: Optional[str] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class InternalPatient(BaseModel):
    """Internal representation of a patient."""

    patient_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    address: Optional[Dict[str, str]] = None
    phone: Optional[str] = None
    insurance_ids: List[str] = Field(default_factory=list)
    mrn: Optional[str] = None


class InternalEncounter(BaseModel):
    """Internal representation of an encounter."""

    encounter_id: str
    patient_id: str
    encounter_type: str
    status: str
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    facility_id: Optional[str] = None
    provider_id: Optional[str] = None
    reason_codes: List[str] = Field(default_factory=list)
    diagnosis_codes: List[str] = Field(default_factory=list)
    procedure_codes: List[str] = Field(default_factory=list)


class InternalCondition(BaseModel):
    """Internal representation of a diagnosis condition."""

    condition_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    icd10_code: str
    display_name: str
    clinical_status: str
    verification_status: str
    category: str
    onset_date: Optional[str] = None
    recorded_date: Optional[str] = None
    severity: Optional[str] = None


class InternalProcedure(BaseModel):
    """Internal representation of a procedure."""

    procedure_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    cpt_code: str
    display_name: str
    status: str
    performed_date: Optional[str] = None
    performer_id: Optional[str] = None
    body_site: Optional[str] = None
    laterality: Optional[str] = None


class InternalClaim(BaseModel):
    """Internal representation of an insurance claim."""

    claim_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    payer_id: str
    provider_id: str
    claim_type: str
    status: str
    created_date: str
    billable_period_start: Optional[str] = None
    billable_period_end: Optional[str] = None
    diagnosis_codes: List[Dict[str, str]] = Field(default_factory=list)
    line_items: List[Dict[str, Any]] = Field(default_factory=list)
    total_amount: float = 0.0


class InternalObservation(BaseModel):
    """Internal representation of an observation."""

    observation_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    code: str
    display_name: str
    value: Optional[str] = None
    unit: Optional[str] = None
    numeric_value: Optional[float] = None
    reference_range: Optional[str] = None
    status: str
    effective_date: Optional[str] = None
    interpretation: Optional[str] = None


class InternalMedicationRequest(BaseModel):
    """Internal representation of a medication request."""

    prescription_id: str
    patient_id: str
    encounter_id: Optional[str] = None
    medication_code: str
    medication_name: str
    dosage: Optional[str] = None
    frequency: Optional[str] = None
    route: Optional[str] = None
    status: str
    prescriber_id: Optional[str] = None
    authored_date: Optional[str] = None


class FHIRConversionResult(BaseModel):
    """Result wrapper for conversions to or from FHIR."""

    success: bool
    resource_type: str
    internal_data: Optional[Any] = None
    fhir_data: Optional[Dict[str, Any]] = None
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# FHIR Adapter
# ---------------------------------------------------------------------------


class FHIRAdapter:
    """Adapter that converts between FHIR R4 resources and internal models."""

    def __init__(self) -> None:
        """Initialize the adapter with supported types and counters."""

        self.logger = logger
        self.supported_resources: List[str] = [r.value for r in FHIRResourceType]
        self._conversion_stats: Dict[str, int] = {r: 0 for r in self.supported_resources}

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_fhir_resource(self, fhir_data: Dict[str, Any]) -> FHIRValidationResult:
        """Validate a FHIR resource for basic structural requirements."""

        errors: List[str] = []
        warnings: List[str] = []
        resource_type = fhir_data.get("resourceType") if isinstance(fhir_data, dict) else None
        if not resource_type:
            errors.append("Missing resourceType")
        elif resource_type not in self.supported_resources:
            errors.append(f"Unsupported resourceType: {resource_type}")
        if errors:
            return FHIRValidationResult(is_valid=False, resource_type=resource_type, errors=errors, warnings=warnings)

        def _require(field: str) -> None:
            if field not in fhir_data:
                errors.append(f"Missing required field: {field}")

        if resource_type == FHIRResourceType.PATIENT.value:
            _require("id")
        elif resource_type == FHIRResourceType.ENCOUNTER.value:
            _require("id")
            _require("status")
            _require("class")
        elif resource_type == FHIRResourceType.CONDITION.value:
            _require("id")
            _require("code")
        elif resource_type == FHIRResourceType.PROCEDURE.value:
            _require("id")
            _require("code")
            _require("status")
        elif resource_type == FHIRResourceType.CLAIM.value:
            _require("id")
            _require("status")
            _require("type")
            _require("provider")
            _require("patient")
        elif resource_type == FHIRResourceType.BUNDLE.value:
            _require("entry")
        if errors:
            self.logger.debug("Validation failed: %s", errors)
            return FHIRValidationResult(is_valid=False, resource_type=resource_type, errors=errors, warnings=warnings)
        return FHIRValidationResult(is_valid=True, resource_type=resource_type, errors=errors, warnings=warnings)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _extract_reference_id(self, reference: Optional[str]) -> Optional[str]:
        """Extract the identifier from a FHIR reference like 'Patient/123'."""

        if not reference:
            return None
        try:
            return reference.split("/")[-1] if "/" in reference else reference
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.debug("Failed to parse reference '%s': %s", reference, exc)
            return None

    def _build_reference(self, resource_type: str, resource_id: str) -> str:
        """Construct a FHIR reference string."""

        return f"{resource_type}/{resource_id}"

    def _parse_date(self, date_str: Optional[str]) -> Optional[str]:
        """Normalize date strings to ISO format when possible."""

        if not date_str:
            return None
        try:
            if len(date_str) == 10 and re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                return date_str
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.date().isoformat()
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.debug("Date parse failed for '%s': %s", date_str, exc)
            return None

    def _calculate_age(self, birth_date_str: Optional[str]) -> Optional[int]:
        """Compute age in years from a birth date string."""

        if not birth_date_str:
            return None
        try:
            bdate = datetime.fromisoformat(birth_date_str).date()
            today = date.today()
            return today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
        except Exception as exc:  # pragma: no cover - defensive
            self.logger.debug("Age calculation failed for '%s': %s", birth_date_str, exc)
            return None

    def _map_gender(self, fhir_gender: Optional[str]) -> Optional[str]:
        """Map FHIR gender values to internal representation."""

        if not fhir_gender:
            return "U"
        low = fhir_gender.lower()
        if low == "male":
            return "M"
        if low == "female":
            return "F"
        return "U"

    def _get_coding_by_system(self, codings: List[Dict[str, Any]], system_keyword: str) -> Optional[Dict[str, Any]]:
        """Return coding dict whose system contains the keyword."""

        for coding in codings or []:
            system = coding.get("system", "")
            if system_keyword.lower() in system.lower():
                return coding
        return None

    def _infer_laterality(self, text: Optional[str]) -> Optional[str]:
        """Infer laterality from free text when possible."""

        if not text:
            return None
        low = text.lower()
        if "left" in low:
            return "left"
        if "right" in low:
            return "right"
        if "bilateral" in low:
            return "bilateral"
        return None

    def _increment_stat(self, resource_type: str) -> None:
        """Increment conversion counter for the given resource type."""

        try:
            self._conversion_stats[resource_type] = self._conversion_stats.get(resource_type, 0) + 1
        except Exception:  # pragma: no cover - defensive
            self._conversion_stats[resource_type] = 1

    # ------------------------------------------------------------------
    # FHIR -> Internal parsers
    # ------------------------------------------------------------------

    def parse_patient(self, fhir_data: Dict[str, Any]) -> InternalPatient:
        """Parse FHIR Patient into InternalPatient."""

        try:
            patient_id = fhir_data.get("id", str(uuid.uuid4()))
            name = (fhir_data.get("name") or [{}])[0]
            family = name.get("family")
            given_list = name.get("given") or []
            given = given_list[0] if given_list else None
            birth_date = fhir_data.get("birthDate")
            gender = self._map_gender(fhir_data.get("gender"))
            address_raw = (fhir_data.get("address") or [{}])[0]
            address = None
            if address_raw:
                address = {
                    "line": ", ".join(address_raw.get("line", [])) if address_raw.get("line") else None,
                    "city": address_raw.get("city"),
                    "state": address_raw.get("state"),
                    "postalCode": address_raw.get("postalCode"),
                }
            telecom = fhir_data.get("telecom") or []
            phone = None
            for t in telecom:
                if t.get("system") == "phone":
                    phone = t.get("value")
                    break
            mrn = None
            for identifier in fhir_data.get("identifier", []):
                coding_list = identifier.get("type", {}).get("coding", [])
                for coding in coding_list:
                    if coding.get("code") == "MR":
                        mrn = identifier.get("value")
                        break
            age = self._calculate_age(birth_date)
            insurance_ids = []
            if "link" in fhir_data:
                for link in fhir_data.get("link", []):
                    ref = link.get("other", {}).get("reference")
                    if ref:
                        insurance_ids.append(ref)
            internal = InternalPatient(
                patient_id=patient_id,
                first_name=given,
                last_name=family,
                date_of_birth=birth_date,
                gender=gender,
                age=age,
                address=address,
                phone=phone,
                insurance_ids=insurance_ids,
                mrn=mrn,
            )
            self._increment_stat(FHIRResourceType.PATIENT.value)
            self.logger.debug("Parsed Patient %s", patient_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Patient: %s", exc)
            raise

    def parse_encounter(self, fhir_data: Dict[str, Any]) -> InternalEncounter:
        """Parse FHIR Encounter into InternalEncounter."""

        try:
            encounter_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("subject", {}).get("reference")) or ""
            raw_class = fhir_data.get("class", {}).get("code")
            encounter_type = {
                "IMP": "inpatient",
                "ACUTE": "inpatient",
                "NONAC": "inpatient",
                "AMB": "outpatient",
                "EMER": "emergency",
                "OBSENC": "observation",
            }.get(raw_class, "outpatient")
            status = fhir_data.get("status", "unknown")
            period = fhir_data.get("period", {})
            start_date = self._parse_date(period.get("start"))
            end_date = self._parse_date(period.get("end"))
            facility_id = self._extract_reference_id(fhir_data.get("serviceProvider", {}).get("reference"))
            provider_id = None
            participants = fhir_data.get("participant") or []
            if participants:
                provider_id = self._extract_reference_id(participants[0].get("individual", {}).get("reference"))
            reason_codes: List[str] = []
            for rc in fhir_data.get("reasonCode", []):
                for coding in rc.get("coding", []):
                    if "code" in coding:
                        reason_codes.append(coding.get("code"))
            diagnosis_refs: List[str] = []
            for diag in fhir_data.get("diagnosis", []):
                ref = self._extract_reference_id(diag.get("condition", {}).get("reference"))
                if ref:
                    diagnosis_refs.append(ref)
            internal = InternalEncounter(
                encounter_id=encounter_id,
                patient_id=patient_id,
                encounter_type=encounter_type,
                status=status,
                start_date=start_date,
                end_date=end_date,
                facility_id=facility_id,
                provider_id=provider_id,
                reason_codes=reason_codes,
                diagnosis_codes=diagnosis_refs,
                procedure_codes=[],
            )
            self._increment_stat(FHIRResourceType.ENCOUNTER.value)
            self.logger.debug("Parsed Encounter %s", encounter_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Encounter: %s", exc)
            raise

    def parse_condition(self, fhir_data: Dict[str, Any]) -> InternalCondition:
        """Parse FHIR Condition into InternalCondition."""

        try:
            condition_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("subject", {}).get("reference")) or ""
            encounter_id = self._extract_reference_id(fhir_data.get("encounter", {}).get("reference"))
            codings = fhir_data.get("code", {}).get("coding", []) or []
            icd10_coding = self._get_coding_by_system(codings, "icd-10")
            if icd10_coding:
                icd10_code = icd10_coding.get("code", "UNKNOWN")
                display = icd10_coding.get("display", "") or ""
            else:
                any_coding = codings[0] if codings else {}
                icd10_code = any_coding.get("code", "UNKNOWN")
                display = any_coding.get("display", "") or ""
                self.logger.warning("No ICD-10 coding found for condition, using fallback: %s", icd10_code)
            clinical_status = (fhir_data.get("clinicalStatus", {}).get("coding", [{}])[0].get("code")) or "unknown"
            verification_status = (fhir_data.get("verificationStatus", {}).get("coding", [{}])[0].get("code")) or "unconfirmed"
            category = (fhir_data.get("category", [{}])[0].get("coding", [{}])[0].get("code")) or "encounter-diagnosis"
            onset_date = self._parse_date(fhir_data.get("onsetDateTime") or fhir_data.get("onsetPeriod", {}).get("start"))
            recorded_date = self._parse_date(fhir_data.get("recordedDate"))
            severity = (fhir_data.get("severity", {}).get("coding", [{}])[0].get("display"))
            internal = InternalCondition(
                condition_id=condition_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                icd10_code=icd10_code,
                display_name=display,
                clinical_status=clinical_status,
                verification_status=verification_status,
                category=category,
                onset_date=onset_date,
                recorded_date=recorded_date,
                severity=severity,
            )
            self._increment_stat(FHIRResourceType.CONDITION.value)
            self.logger.debug("Parsed Condition %s", condition_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Condition: %s", exc)
            raise

    def parse_procedure(self, fhir_data: Dict[str, Any]) -> InternalProcedure:
        """Parse FHIR Procedure into InternalProcedure."""

        try:
            procedure_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("subject", {}).get("reference")) or ""
            encounter_id = self._extract_reference_id(fhir_data.get("encounter", {}).get("reference"))
            coding = None
            for c in fhir_data.get("code", {}).get("coding", []):
                system = c.get("system", "").lower()
                if "cpt" in system or "hcpcs" in system or FHIR_CPT_SYSTEM in system:
                    coding = c
                    break
            if not coding and fhir_data.get("code", {}).get("coding"):
                coding = fhir_data.get("code", {}).get("coding", [])[0]
            cpt_code = coding.get("code") if coding else ""
            display = coding.get("display") if coding else ""
            status = fhir_data.get("status", "unknown")
            performed_date = self._parse_date(
                fhir_data.get("performedDateTime") or fhir_data.get("performedPeriod", {}).get("start")
            )
            performer_id = None
            if fhir_data.get("performer"):
                performer_id = self._extract_reference_id(fhir_data.get("performer", [{}])[0].get("actor", {}).get("reference"))
            body_site = None
            laterality = None
            if fhir_data.get("bodySite"):
                bs_coding = fhir_data.get("bodySite", [{}])[0].get("coding", [{}])[0]
                body_site = bs_coding.get("display") or fhir_data.get("bodySite", [{}])[0].get("text")
                laterality = self._infer_laterality(body_site)
            internal = InternalProcedure(
                procedure_id=procedure_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                cpt_code=cpt_code,
                display_name=display,
                status=status,
                performed_date=performed_date,
                performer_id=performer_id,
                body_site=body_site,
                laterality=laterality,
            )
            self._increment_stat(FHIRResourceType.PROCEDURE.value)
            self.logger.debug("Parsed Procedure %s", procedure_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Procedure: %s", exc)
            raise

    def parse_claim(self, fhir_data: Dict[str, Any]) -> InternalClaim:
        """Parse FHIR Claim into InternalClaim."""

        try:
            claim_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("patient", {}).get("reference")) or ""
            provider_id = self._extract_reference_id(fhir_data.get("provider", {}).get("reference")) or ""
            payer_id = self._extract_reference_id(fhir_data.get("insurer", {}).get("reference")) or ""
            claim_type = (fhir_data.get("type", {}).get("coding", [{}])[0].get("code")) or "professional"
            status = fhir_data.get("status", "draft")
            created_date = self._parse_date(fhir_data.get("created")) or datetime.utcnow().date().isoformat()
            billable_period = fhir_data.get("billablePeriod", {})
            billable_start = self._parse_date(billable_period.get("start"))
            billable_end = self._parse_date(billable_period.get("end"))
            diagnoses: List[Dict[str, str]] = []
            for diag in fhir_data.get("diagnosis", []) or []:
                diag_code = (diag.get("diagnosisCodeableConcept", {}).get("coding", [{}])[0].get("code"))
                diag_type = (diag.get("type", [{}])[0].get("coding", [{}])[0].get("code"))
                sequence = diag.get("sequence")
                diagnoses.append({"code": diag_code or "", "type": diag_type or "", "sequence": str(sequence) if sequence else ""})
            line_items: List[Dict[str, Any]] = []
            total = 0.0
            for item in fhir_data.get("item", []) or []:
                coding = (item.get("productOrService", {}).get("coding", [{}])[0])
                cpt_code = coding.get("code") if coding else ""
                quantity = (item.get("quantity", {}) or {}).get("value")
                unit_price = (item.get("unitPrice", {}) or {}).get("value")
                line_total = 0.0
                try:
                    if quantity and unit_price:
                        line_total = float(quantity) * float(unit_price)
                except Exception:
                    line_total = 0.0
                total += line_total
                line_items.append(
                    {
                        "cpt_code": cpt_code,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "line_total": line_total,
                    }
                )
            if fhir_data.get("total"):
                total = fhir_data.get("total", {}).get("value", total)
            internal = InternalClaim(
                claim_id=claim_id,
                patient_id=patient_id,
                encounter_id=self._extract_reference_id(fhir_data.get("encounter", {}).get("reference")),
                payer_id=payer_id,
                provider_id=provider_id,
                claim_type=claim_type,
                status=status,
                created_date=created_date,
                billable_period_start=billable_start,
                billable_period_end=billable_end,
                diagnosis_codes=diagnoses,
                line_items=line_items,
                total_amount=float(total or 0.0),
            )
            self._increment_stat(FHIRResourceType.CLAIM.value)
            self.logger.debug("Parsed Claim %s", claim_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Claim: %s", exc)
            raise

    def parse_observation(self, fhir_data: Dict[str, Any]) -> InternalObservation:
        """Parse FHIR Observation into InternalObservation."""

        try:
            obs_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("subject", {}).get("reference")) or ""
            encounter_id = self._extract_reference_id(fhir_data.get("encounter", {}).get("reference"))
            code_coding = (fhir_data.get("code", {}).get("coding", [{}])[0])
            code = code_coding.get("code", "")
            display = code_coding.get("display", "")
            value = None
            numeric_value = None
            unit = None
            if "valueQuantity" in fhir_data:
                value_qty = fhir_data.get("valueQuantity", {})
                numeric_value = value_qty.get("value")
                unit = value_qty.get("unit")
            elif "valueString" in fhir_data:
                value = fhir_data.get("valueString")
            elif "valueCodeableConcept" in fhir_data:
                vcoding = fhir_data.get("valueCodeableConcept", {}).get("coding", [{}])[0]
                value = vcoding.get("display") or vcoding.get("code")
            reference_range = None
            if fhir_data.get("referenceRange"):
                reference_range = fhir_data.get("referenceRange", [{}])[0].get("text")
            status = fhir_data.get("status", "unknown")
            effective = self._parse_date(fhir_data.get("effectiveDateTime"))
            interpretation_code = None
            if fhir_data.get("interpretation"):
                interpretation_code = fhir_data.get("interpretation", [{}])[0].get("coding", [{}])[0].get("code")
            interpretation = None
            if interpretation_code:
                low = interpretation_code.lower()
                if low in {"a", "abnormal"}:
                    interpretation = "abnormal"
                elif low in {"h", "high"}:
                    interpretation = "high"
                elif low in {"l", "low"}:
                    interpretation = "low"
                elif low in {"aa", "critical"}:
                    interpretation = "critical"
                else:
                    interpretation = "normal"
            internal = InternalObservation(
                observation_id=obs_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                code=code,
                display_name=display,
                value=value,
                unit=unit,
                numeric_value=numeric_value,
                reference_range=reference_range,
                status=status,
                effective_date=effective,
                interpretation=interpretation,
            )
            self._increment_stat(FHIRResourceType.OBSERVATION.value)
            self.logger.debug("Parsed Observation %s", obs_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse Observation: %s", exc)
            raise

    def parse_medication_request(self, fhir_data: Dict[str, Any]) -> InternalMedicationRequest:
        """Parse FHIR MedicationRequest into InternalMedicationRequest."""

        try:
            prescription_id = fhir_data.get("id", str(uuid.uuid4()))
            patient_id = self._extract_reference_id(fhir_data.get("subject", {}).get("reference")) or ""
            encounter_id = self._extract_reference_id(fhir_data.get("encounter", {}).get("reference"))
            med_coding = (fhir_data.get("medicationCodeableConcept", {}).get("coding", [{}])[0])
            med_code = med_coding.get("code", "")
            med_display = med_coding.get("display", "")
            dosage = None
            frequency = None
            route = None
            if fhir_data.get("dosageInstruction"):
                di = fhir_data.get("dosageInstruction", [{}])[0]
                dosage = di.get("text")
                frequency = di.get("timing", {}).get("code", {}).get("text")
                route = di.get("route", {}).get("coding", [{}])[0].get("display")
            status = fhir_data.get("status", "active")
            requester = fhir_data.get("requester", {})
            prescriber_id = self._extract_reference_id(requester.get("reference"))
            authored_on = self._parse_date(fhir_data.get("authoredOn"))
            internal = InternalMedicationRequest(
                prescription_id=prescription_id,
                patient_id=patient_id,
                encounter_id=encounter_id,
                medication_code=med_code,
                medication_name=med_display,
                dosage=dosage,
                frequency=frequency,
                route=route,
                status=status,
                prescriber_id=prescriber_id,
                authored_date=authored_on,
            )
            self._increment_stat(FHIRResourceType.MEDICATION_REQUEST.value)
            self.logger.debug("Parsed MedicationRequest %s", prescription_id)
            return internal
        except Exception as exc:
            self.logger.exception("Failed to parse MedicationRequest: %s", exc)
            raise

    def parse_bundle(self, fhir_data: Dict[str, Any]) -> List[FHIRConversionResult]:
        """Parse FHIR Bundle and return conversion results for entries."""

        results: List[FHIRConversionResult] = []
        try:
            entries = fhir_data.get("entry", []) if isinstance(fhir_data, dict) else []
            for entry in entries:
                resource = entry.get("resource") if isinstance(entry, dict) else None
                if not resource:
                    results.append(
                        FHIRConversionResult(
                            success=False,
                            resource_type="Unknown",
                            internal_data=None,
                            fhir_data=None,
                            errors=["Missing resource in bundle entry"],
                            warnings=[],
                        )
                    )
                    continue
                results.append(self.parse_resource(resource))
            return results
        except Exception as exc:
            self.logger.exception("Failed to parse Bundle: %s", exc)
            results.append(
                FHIRConversionResult(
                    success=False,
                    resource_type=FHIRResourceType.BUNDLE.value,
                    internal_data=None,
                    fhir_data=None,
                    errors=[str(exc)],
                    warnings=[],
                )
            )
            return results

    def parse_resource(self, fhir_data: Dict[str, Any]) -> FHIRConversionResult:
        """Universal entry point for FHIR -> internal conversion."""

        try:
            validation = self.validate_fhir_resource(fhir_data)
            if not validation.is_valid:
                # Align error message with parser expectation
                resource_type = validation.resource_type or fhir_data.get("resourceType", "Unknown")
                errors = validation.errors
                if resource_type not in self.supported_resources:
                    errors = [f"No parser available for resourceType: {resource_type}"]
                return FHIRConversionResult(
                    success=False,
                    resource_type=resource_type,
                    internal_data=None,
                    fhir_data=fhir_data,
                    errors=errors,
                    warnings=validation.warnings,
                )
            rtype = validation.resource_type
            parser_map = {
                FHIRResourceType.PATIENT.value: self.parse_patient,
                FHIRResourceType.ENCOUNTER.value: self.parse_encounter,
                FHIRResourceType.CONDITION.value: self.parse_condition,
                FHIRResourceType.PROCEDURE.value: self.parse_procedure,
                FHIRResourceType.CLAIM.value: self.parse_claim,
                FHIRResourceType.MEDICATION_REQUEST.value: self.parse_medication_request,
                FHIRResourceType.OBSERVATION.value: self.parse_observation,
                FHIRResourceType.BUNDLE.value: self.parse_bundle,
            }
            if rtype not in parser_map:
                return FHIRConversionResult(
                    success=False,
                    resource_type=rtype or "Unknown",
                    internal_data=None,
                    fhir_data=fhir_data,
                    errors=[f"No parser for {rtype}"],
                    warnings=[],
                )
            parsed = parser_map[rtype](fhir_data)
            return FHIRConversionResult(
                success=True,
                resource_type=rtype,
                internal_data=parsed,
                fhir_data=None,
                errors=[],
                warnings=[],
            )
        except Exception as exc:
            self.logger.exception("Conversion failed for resource: %s", exc)
            return FHIRConversionResult(
                success=False,
                resource_type=fhir_data.get("resourceType", "Unknown") if isinstance(fhir_data, dict) else "Unknown",
                internal_data=None,
                fhir_data=fhir_data,
                errors=[str(exc)],
                warnings=[],
            )

    # ------------------------------------------------------------------
    # Internal -> FHIR converters
    # ------------------------------------------------------------------

    def to_fhir_patient(self, patient: InternalPatient) -> Dict[str, Any]:
        """Convert InternalPatient to FHIR Patient JSON."""

        try:
            name = []
            if patient.first_name or patient.last_name:
                name.append({"family": patient.last_name, "given": [g for g in [patient.first_name] if g]})
            address = []
            if patient.address:
                address.append(
                    {
                        "line": [patient.address.get("line")] if patient.address.get("line") else [],
                        "city": patient.address.get("city"),
                        "state": patient.address.get("state"),
                        "postalCode": patient.address.get("postalCode"),
                    }
                )
            telecom = []
            if patient.phone:
                telecom.append({"system": "phone", "value": patient.phone})
            identifiers = []
            if patient.mrn:
                identifiers.append({
                    "type": {"coding": [{"code": "MR"}]},
                    "value": patient.mrn,
                })
            resource = {
                "resourceType": FHIRResourceType.PATIENT.value,
                "id": patient.patient_id,
                "name": name,
                "birthDate": patient.date_of_birth,
                "gender": {"M": "male", "F": "female"}.get(patient.gender or "", "unknown"),
                "address": address,
                "telecom": telecom,
                "identifier": identifiers,
            }
            return resource
        except Exception as exc:
            self.logger.exception("Failed to build FHIR Patient: %s", exc)
            return {"resourceType": FHIRResourceType.PATIENT.value, "id": patient.patient_id, "error": str(exc)}

    def to_fhir_condition(self, condition: InternalCondition) -> Dict[str, Any]:
        """Convert InternalCondition to FHIR Condition JSON."""

        try:
            resource = {
                "resourceType": FHIRResourceType.CONDITION.value,
                "id": condition.condition_id,
                "subject": {"reference": self._build_reference(FHIRResourceType.PATIENT.value, condition.patient_id)},
                "code": {
                    "coding": [
                        {
                            "system": FHIR_ICD10_SYSTEM,
                            "code": condition.icd10_code,
                            "display": condition.display_name,
                        }
                    ]
                },
                "clinicalStatus": {"coding": [{"code": condition.clinical_status}]},
                "verificationStatus": {"coding": [{"code": condition.verification_status}]},
                "category": [{"coding": [{"code": condition.category}]}],
                "onsetDateTime": condition.onset_date,
                "recordedDate": condition.recorded_date,
            }
            if condition.encounter_id:
                resource["encounter"] = {"reference": self._build_reference(FHIRResourceType.ENCOUNTER.value, condition.encounter_id)}
            if condition.severity:
                resource["severity"] = {"coding": [{"display": condition.severity}]}
            return resource
        except Exception as exc:
            self.logger.exception("Failed to build FHIR Condition: %s", exc)
            return {"resourceType": FHIRResourceType.CONDITION.value, "id": condition.condition_id, "error": str(exc)}

    def to_fhir_procedure(self, procedure: InternalProcedure) -> Dict[str, Any]:
        """Convert InternalProcedure to FHIR Procedure JSON."""

        try:
            resource = {
                "resourceType": FHIRResourceType.PROCEDURE.value,
                "id": procedure.procedure_id,
                "subject": {"reference": self._build_reference(FHIRResourceType.PATIENT.value, procedure.patient_id)},
                "code": {
                    "coding": [
                        {
                            "system": FHIR_CPT_SYSTEM,
                            "code": procedure.cpt_code,
                            "display": procedure.display_name,
                        }
                    ]
                },
                "status": procedure.status,
                "performedDateTime": procedure.performed_date,
            }
            if procedure.encounter_id:
                resource["encounter"] = {"reference": self._build_reference(FHIRResourceType.ENCOUNTER.value, procedure.encounter_id)}
            if procedure.body_site:
                resource["bodySite"] = [{"coding": [{"display": procedure.body_site}]}]
            return resource
        except Exception as exc:
            self.logger.exception("Failed to build FHIR Procedure: %s", exc)
            return {"resourceType": FHIRResourceType.PROCEDURE.value, "id": procedure.procedure_id, "error": str(exc)}

    def to_fhir_claim(self, claim: InternalClaim) -> Dict[str, Any]:
        """Convert InternalClaim to FHIR Claim JSON."""

        try:
            diagnosis_entries = []
            for diag in claim.diagnosis_codes:
                diagnosis_entries.append(
                    {
                        "sequence": int(diag.get("sequence")) if diag.get("sequence") else None,
                        "diagnosisCodeableConcept": {
                            "coding": [
                                {
                                    "system": FHIR_ICD10_SYSTEM,
                                    "code": diag.get("code"),
                                }
                            ]
                        },
                        "type": [{"coding": [{"code": diag.get("type") or "principal"}]}],
                    }
                )
            items = []
            for idx, line in enumerate(claim.line_items, start=1):
                items.append(
                    {
                        "sequence": idx,
                        "productOrService": {
                            "coding": [
                                {
                                    "system": FHIR_CPT_SYSTEM,
                                    "code": line.get("cpt_code"),
                                }
                            ]
                        },
                        "quantity": {"value": line.get("quantity")},
                        "unitPrice": {"value": line.get("unit_price")},
                    }
                )
            total_amount = claim.total_amount or 0.0
            resource = {
                "resourceType": FHIRResourceType.CLAIM.value,
                "id": claim.claim_id,
                "status": claim.status,
                "type": {"coding": [{"system": FHIR_CLAIM_TYPE_SYSTEM, "code": claim.claim_type}]},
                "patient": {"reference": self._build_reference(FHIRResourceType.PATIENT.value, claim.patient_id)},
                "provider": {"reference": self._build_reference("Organization", claim.provider_id)},
                "insurer": {"reference": self._build_reference("Organization", claim.payer_id)},
                "created": claim.created_date,
                "billablePeriod": {"start": claim.billable_period_start, "end": claim.billable_period_end},
                "diagnosis": diagnosis_entries,
                "item": items,
                "total": {"value": total_amount},
            }
            if claim.encounter_id:
                resource["encounter"] = {"reference": self._build_reference(FHIRResourceType.ENCOUNTER.value, claim.encounter_id)}
            return resource
        except Exception as exc:
            self.logger.exception("Failed to build FHIR Claim: %s", exc)
            return {"resourceType": FHIRResourceType.CLAIM.value, "id": claim.claim_id, "error": str(exc)}

    def to_fhir_claim_response(self, adjudication_result: Dict[str, Any]) -> Dict[str, Any]:
        """Build FHIR ClaimResponse from adjudication output."""

        try:
            claim_id = adjudication_result.get("claim_id") or str(uuid.uuid4())
            patient_id = adjudication_result.get("patient_id", "")
            status = adjudication_result.get("status", "active")
            outcome = adjudication_result.get("outcome", "complete")
            disposition = adjudication_result.get("disposition", "Processed")
            items = []
            for idx, item in enumerate(adjudication_result.get("items", []), start=1):
                adjudications = []
                for adj in item.get("adjudication", []):
                    adjudications.append(
                        {
                            "category": {"coding": [{"code": adj.get("category", "submitted")}]},
                            "amount": {"value": adj.get("amount")},
                        }
                    )
                items.append(
                    {
                        "itemSequence": idx,
                        "adjudication": adjudications,
                    }
                )
            payment_amount = (adjudication_result.get("payment") or {}).get("amount")
            payment_date = (adjudication_result.get("payment") or {}).get("date")
            resource = {
                "resourceType": FHIRResourceType.CLAIM_RESPONSE.value,
                "id": f"cr-{claim_id}",
                "status": status,
                "type": {"coding": [{"code": adjudication_result.get("claim_type", "professional")}]} ,
                "patient": {"reference": self._build_reference(FHIRResourceType.PATIENT.value, patient_id)} if patient_id else None,
                "outcome": outcome,
                "disposition": disposition,
                "item": items,
                "payment": {"amount": {"value": payment_amount}, "date": payment_date},
            }
            return resource
        except Exception as exc:
            self.logger.exception("Failed to build FHIR ClaimResponse: %s", exc)
            return {"resourceType": FHIRResourceType.CLAIM_RESPONSE.value, "id": f"cr-{uuid.uuid4()}", "error": str(exc)}

    def to_fhir_bundle(self, resources: List[Dict[str, Any]], bundle_type: str = "collection") -> Dict[str, Any]:
        """Wrap resources into a FHIR Bundle."""

        try:
            entries = []
            for res in resources:
                rid = res.get("id", str(uuid.uuid4()))
                full_url = f"urn:uuid:{rid}"
                entries.append({"fullUrl": full_url, "resource": res})
            return {"resourceType": FHIRResourceType.BUNDLE.value, "type": bundle_type, "entry": entries}
        except Exception as exc:
            self.logger.exception("Failed to build FHIR Bundle: %s", exc)
            return {"resourceType": FHIRResourceType.BUNDLE.value, "type": bundle_type, "entry": [], "error": str(exc)}

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_conversion_stats(self) -> Dict[str, int]:
        """Return a copy of conversion statistics."""

        return dict(self._conversion_stats)


# ---------------------------------------------------------------------------
# __main__ demonstration
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    adapter = FHIRAdapter()

    sample_patient_fhir = {
        "resourceType": "Patient",
        "id": "patient-123",
        "name": [{"family": "Smith", "given": ["John"]}],
        "birthDate": "1962-05-15",
        "gender": "male",
        "address": [
            {
                "line": ["123 Main St"],
                "city": "Springfield",
                "state": "IL",
                "postalCode": "62701",
            }
        ],
        "telecom": [{"system": "phone", "value": "555-123-4567"}],
        "identifier": [{"type": {"coding": [{"code": "MR"}]}, "value": "MRN-001"}],
    }

    result = adapter.parse_resource(sample_patient_fhir)
    print(f"Patient parse success: {result.success}")
    print(f"Patient: {result.internal_data}")

    if result.success and result.internal_data:
        fhir_out = adapter.to_fhir_patient(result.internal_data)
        print(f"\nFHIR output: {json.dumps(fhir_out, indent=2)}")

    sample_condition_fhir = {
        "resourceType": "Condition",
        "id": "condition-456",
        "subject": {"reference": "Patient/patient-123"},
        "encounter": {"reference": "Encounter/enc-789"},
        "code": {
            "coding": [
                {
                    "system": FHIR_ICD10_SYSTEM,
                    "code": "E11.22",
                    "display": "Type 2 diabetes mellitus with diabetic chronic kidney disease",
                }
            ]
        },
        "clinicalStatus": {"coding": [{"code": "active"}]},
        "verificationStatus": {"coding": [{"code": "confirmed"}]},
        "category": [{"coding": [{"code": "encounter-diagnosis"}]}],
        "onsetDateTime": "2023-06-15",
        "recordedDate": "2024-01-15",
    }

    result2 = adapter.parse_resource(sample_condition_fhir)
    print(f"\nCondition parse success: {result2.success}")
    print(f"Condition: {result2.internal_data}")

    sample_bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"fullUrl": "urn:uuid:1", "resource": sample_patient_fhir},
            {"fullUrl": "urn:uuid:2", "resource": sample_condition_fhir},
        ],
    }

    bundle_results = adapter.parse_bundle(sample_bundle)
    print(f"\nBundle parsed: {len(bundle_results)} resources")
    for r in bundle_results:
        print(f"  {r.resource_type}: success={r.success}")

    print(f"\nConversion stats: {adapter.get_conversion_stats()}")
