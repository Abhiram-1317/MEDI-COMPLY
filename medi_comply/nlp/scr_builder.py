"""
MEDI-COMPLY — Structured Clinical Representation (SCR) builder.

Assembles all NLP extraction results into a single structured output
that downstream coding agents consume.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from medi_comply.nlp.document_ingester import IngestedDocument
from medi_comply.nlp.section_parser import ClinicalSection
from medi_comply.nlp.clinical_ner import ClinicalEntity
from medi_comply.nlp.evidence_tracker import SourceEvidence


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class VitalsData:
    """Extracted vital signs."""
    blood_pressure_systolic: Optional[int] = None
    blood_pressure_diastolic: Optional[int] = None
    heart_rate: Optional[int] = None
    respiratory_rate: Optional[int] = None
    spo2: Optional[int] = None
    temperature: Optional[float] = None
    weight: Optional[float] = None
    evidence: list[SourceEvidence] = field(default_factory=list)


@dataclass
class LabResult:
    """A single lab result entry."""
    test_name: str = ""
    value: float = 0.0
    unit: str = ""
    reference_range: str = ""
    is_abnormal: bool = False
    evidence: Optional[SourceEvidence] = None


@dataclass
class ConditionEntry:
    """A structured condition for the SCR."""
    entity_id: str = ""
    text: str = ""
    normalized_text: str = ""
    assertion: str = "PRESENT"
    acuity: str = "unspecified"
    severity: Optional[str] = None
    laterality: Optional[str] = None
    body_site: str = "unspecified"
    is_primary_reason: bool = False
    evidence: list[SourceEvidence] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)
    confidence: float = 0.90


@dataclass
class ProcedureEntry:
    """A structured procedure for the SCR."""
    entity_id: str = ""
    text: str = ""
    normalized_text: str = ""
    laterality: Optional[str] = None
    body_site: str = "unspecified"
    status: str = "PLANNED"
    evidence: list[SourceEvidence] = field(default_factory=list)
    confidence: float = 0.90


@dataclass
class MedicationEntry:
    """A structured medication for the SCR."""
    entity_id: str = ""
    drug_name: str = ""
    dose: str = ""
    unit: str = ""
    route: str = ""
    frequency: str = ""
    status: str = "ACTIVE"
    evidence: Optional[SourceEvidence] = None
    confidence: float = 0.95


@dataclass
class ExtractionMetadata:
    """Pipeline metadata about the extraction process."""
    total_entities_extracted: int = 0
    conditions_count: int = 0
    procedures_count: int = 0
    medications_count: int = 0
    lab_results_count: int = 0
    negated_findings: list[str] = field(default_factory=list)
    uncertain_findings: list[str] = field(default_factory=list)
    methods_used: list[str] = field(default_factory=list)
    average_confidence: float = 0.0
    low_confidence_flags: list[str] = field(default_factory=list)
    step_timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class StructuredClinicalRepresentation:
    """The final structured output for downstream agents."""
    document_id: str = ""
    scr_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    processing_time_ms: float = 0.0

    patient_context: dict[str, Any] = field(default_factory=dict)
    sections_found: list[str] = field(default_factory=list)

    conditions: list[ConditionEntry] = field(default_factory=list)
    procedures: list[ProcedureEntry] = field(default_factory=list)
    medications: list[MedicationEntry] = field(default_factory=list)
    vitals: VitalsData = field(default_factory=VitalsData)
    lab_results: list[LabResult] = field(default_factory=list)

    clinical_summary: str = ""
    extraction_metadata: ExtractionMetadata = field(default_factory=ExtractionMetadata)


# ---------------------------------------------------------------------------
# SCR Builder
# ---------------------------------------------------------------------------

class SCRBuilder:
    """Assembles NLP extraction results into a :class:`StructuredClinicalRepresentation`.

    The SCR is the single structured output consumed by coding agents,
    providing conditions, procedures, medications, vitals, and lab results
    with full evidence tracking.
    """

    def build(
        self,
        document: IngestedDocument,
        sections: list[ClinicalSection],
        entities: list[ClinicalEntity],
        patient_context: Optional[dict[str, Any]] = None,
        processing_time_ms: float = 0.0,
    ) -> StructuredClinicalRepresentation:
        """Build the SCR from extraction results.

        Parameters
        ----------
        document:
            Ingested source document.
        sections:
            Parsed clinical sections.
        entities:
            Extracted and linked entities.
        patient_context:
            Optional patient info (age, gender, encounter_type).
        processing_time_ms:
            Total pipeline time.

        Returns
        -------
        StructuredClinicalRepresentation
        """
        scr = StructuredClinicalRepresentation(
            document_id=document.document_id,
            processing_time_ms=processing_time_ms,
            patient_context=patient_context or {},
            sections_found=[s.section_type for s in sections],
        )

        # Classify entities by type
        conditions: list[ClinicalEntity] = []
        procedures: list[ClinicalEntity] = []
        medications: list[ClinicalEntity] = []
        vitals: list[ClinicalEntity] = []
        labs: list[ClinicalEntity] = []

        for e in entities:
            if e.entity_type == "CONDITION":
                conditions.append(e)
            elif e.entity_type == "PROCEDURE":
                procedures.append(e)
            elif e.entity_type == "MEDICATION":
                medications.append(e)
            elif e.entity_type == "VITAL_SIGN":
                vitals.append(e)
            elif e.entity_type == "LAB_VALUE":
                labs.append(e)

        # Build condition entries
        scr.conditions = self._build_conditions(conditions)
        scr.conditions = self._classify_primary_reason(scr.conditions, sections)

        # Build procedure entries
        scr.procedures = self._build_procedures(procedures)

        # Build medication entries
        scr.medications = self._build_medications(medications)

        # Build vitals
        scr.vitals = self._build_vitals(vitals)

        # Build lab results
        scr.lab_results = self._build_labs(labs)

        # Generate clinical summary
        scr.clinical_summary = self._generate_clinical_summary(scr)

        # Metadata
        scr.extraction_metadata = self._calculate_metadata(entities, conditions, procedures, medications, labs)

        return scr

    # -- Conditions --------------------------------------------------------

    def _build_conditions(self, entities: list[ClinicalEntity]) -> list[ConditionEntry]:
        """Convert condition entities to SCR entries.

        Parameters
        ----------
        entities:
            Condition entities.

        Returns
        -------
        list[ConditionEntry]
        """
        entries: list[ConditionEntry] = []
        for e in entities:
            if e.assertion == "ABSENT":
                continue  # Skip negated conditions from active list
            entries.append(ConditionEntry(
                entity_id=e.entity_id,
                text=e.text,
                normalized_text=e.normalized_text,
                assertion=e.assertion,
                acuity=e.attributes.get("acuity", "unspecified"),
                severity=e.attributes.get("severity"),
                laterality=e.attributes.get("laterality"),
                body_site=e.attributes.get("body_site", "unspecified"),
                evidence=[e.source_evidence] if e.source_evidence else [],
                related_entities=e.related_entities,
                confidence=e.confidence,
            ))
        return entries

    def _classify_primary_reason(
        self,
        conditions: list[ConditionEntry],
        sections: list[ClinicalSection],
    ) -> list[ConditionEntry]:
        """Mark the condition most likely to be the chief complaint.

        The first condition found in a CHIEF_COMPLAINT or ASSESSMENT section
        is marked as the primary reason for the encounter.

        Parameters
        ----------
        conditions:
            Condition entries.
        sections:
            Parsed sections.

        Returns
        -------
        list[ConditionEntry]
        """
        cc_section = None
        for s in sections:
            if s.section_type in ("CHIEF_COMPLAINT", "ASSESSMENT"):
                cc_section = s
                break

        if cc_section and conditions:
            # Mark the first present condition as primary
            for c in conditions:
                if c.assertion == "PRESENT":
                    c.is_primary_reason = True
                    break
        return conditions

    # -- Procedures --------------------------------------------------------

    def _build_procedures(self, entities: list[ClinicalEntity]) -> list[ProcedureEntry]:
        """Convert procedure entities to SCR entries.

        Parameters
        ----------
        entities:
            Procedure entities.

        Returns
        -------
        list[ProcedureEntry]
        """
        return [ProcedureEntry(
            entity_id=e.entity_id,
            text=e.text,
            normalized_text=e.normalized_text,
            body_site=e.attributes.get("body_site", "unspecified"),
            status=e.attributes.get("status", "PLANNED"),
            evidence=[e.source_evidence] if e.source_evidence else [],
            confidence=e.confidence,
        ) for e in entities]

    # -- Medications -------------------------------------------------------

    def _build_medications(self, entities: list[ClinicalEntity]) -> list[MedicationEntry]:
        """Convert medication entities to SCR entries.

        Parameters
        ----------
        entities:
            Medication entities.

        Returns
        -------
        list[MedicationEntry]
        """
        return [MedicationEntry(
            entity_id=e.entity_id,
            drug_name=e.attributes.get("drug_name", e.text),
            dose=e.attributes.get("dose", ""),
            unit=e.attributes.get("unit", ""),
            route=e.attributes.get("route", ""),
            frequency=e.attributes.get("frequency", ""),
            status=e.attributes.get("status", "ACTIVE"),
            evidence=e.source_evidence,
            confidence=e.confidence,
        ) for e in entities]

    # -- Vitals ------------------------------------------------------------

    def _build_vitals(self, entities: list[ClinicalEntity]) -> VitalsData:
        """Populate vitals from vital sign entities.

        Parameters
        ----------
        entities:
            Vital sign entities.

        Returns
        -------
        VitalsData
        """
        vd = VitalsData()
        for e in entities:
            attrs = e.attributes
            norm = e.normalized_text.lower()
            if e.source_evidence:
                vd.evidence.append(e.source_evidence)

            if "blood pressure" in norm or "systolic" in attrs:
                vd.blood_pressure_systolic = attrs.get("systolic")
                vd.blood_pressure_diastolic = attrs.get("diastolic")
            elif "heart rate" in norm:
                vd.heart_rate = attrs.get("value")
            elif "spo2" in norm:
                vd.spo2 = attrs.get("value")
            elif "respiratory" in norm:
                vd.respiratory_rate = attrs.get("value")
            elif "temperature" in norm:
                vd.temperature = attrs.get("value")
            elif "weight" in norm:
                vd.weight = attrs.get("value")
        return vd

    # -- Labs --------------------------------------------------------------

    def _build_labs(self, entities: list[ClinicalEntity]) -> list[LabResult]:
        """Convert lab value entities to LabResult entries.

        Parameters
        ----------
        entities:
            Lab value entities.

        Returns
        -------
        list[LabResult]
        """
        return [LabResult(
            test_name=e.attributes.get("test_name", e.text),
            value=e.attributes.get("value", 0.0),
            unit=e.attributes.get("unit", ""),
            reference_range=e.attributes.get("reference_range", ""),
            is_abnormal=e.attributes.get("is_abnormal", False),
            evidence=e.source_evidence,
        ) for e in entities]

    # -- Summary -----------------------------------------------------------

    def _generate_clinical_summary(self, scr: StructuredClinicalRepresentation) -> str:
        """Generate a one-line clinical summary from the SCR.

        Parameters
        ----------
        scr:
            The SCR being built.

        Returns
        -------
        str
        """
        parts: list[str] = []

        age = scr.patient_context.get("age", "")
        gender = scr.patient_context.get("gender", "")
        if age and gender:
            parts.append(f"{age}-year-old {gender}")

        # Primary condition
        primary = [c for c in scr.conditions if c.is_primary_reason]
        if primary:
            parts.append(f"presenting with {primary[0].normalized_text}")

        # Other active conditions
        other = [c for c in scr.conditions if not c.is_primary_reason and c.assertion == "PRESENT"]
        if other:
            cond_list = ", ".join(c.normalized_text for c in other[:3])
            parts.append(f"with {cond_list}")

        # Key abnormal labs
        abnormal = [l for l in scr.lab_results if l.is_abnormal]
        if abnormal:
            lab_list = ", ".join(f"{l.test_name} {l.value}" for l in abnormal[:2])
            parts.append(f"labs: {lab_list}")

        return ". ".join(parts) + "." if parts else "Clinical note processed."

    # -- Metadata ----------------------------------------------------------

    def _calculate_metadata(
        self,
        all_entities: list[ClinicalEntity],
        conditions: list[ClinicalEntity],
        procedures: list[ClinicalEntity],
        medications: list[ClinicalEntity],
        labs: list[ClinicalEntity],
    ) -> ExtractionMetadata:
        """Calculate extraction metadata.

        Parameters
        ----------
        all_entities:
            All extracted entities.
        conditions:
            Condition entities.
        procedures:
            Procedure entities.
        medications:
            Medication entities.
        labs:
            Lab entities.

        Returns
        -------
        ExtractionMetadata
        """
        negated = [e.text for e in conditions if e.assertion == "ABSENT"]
        uncertain = [e.text for e in conditions if e.assertion == "POSSIBLE"]
        methods = list({e.extraction_method for e in all_entities})
        confidences = [e.confidence for e in all_entities]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        low_conf = [e.text for e in all_entities if e.confidence < 0.7]

        return ExtractionMetadata(
            total_entities_extracted=len(all_entities),
            conditions_count=len([c for c in conditions if c.assertion != "ABSENT"]),
            procedures_count=len(procedures),
            medications_count=len(medications),
            lab_results_count=len(labs),
            negated_findings=negated,
            uncertain_findings=uncertain,
            methods_used=methods,
            average_confidence=round(avg_conf, 3),
            low_confidence_flags=low_conf,
        )

    def _flag_low_confidence(
        self, entities: list[ClinicalEntity], threshold: float = 0.7,
    ) -> list[str]:
        """Flag entities below confidence threshold.

        Parameters
        ----------
        entities:
            All entities.
        threshold:
            Confidence cutoff.

        Returns
        -------
        list[str]
        """
        return [e.text for e in entities if e.confidence < threshold]
