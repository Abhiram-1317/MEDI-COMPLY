"""
MEDI-COMPLY — Clinical document and entity schemas.

Models for representing clinical documents, extracted entities (conditions,
procedures, medications), and source-level evidence with exact positioning.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from medi_comply.schemas.common import BaseTimestampedModel, ConfidenceLevel


# ---------------------------------------------------------------------------
# Source evidence
# ---------------------------------------------------------------------------


class SourceEvidence(BaseModel):
    """Pointer to the exact location in a clinical document where an entity
    was extracted from.

    Attributes
    ----------
    section : str
        Document section heading (e.g. ``"History of Present Illness"``).
    page : int
        1-based page number.
    line : int
        1-based line number within the page.
    char_offset : tuple[int, int]
        Start and end character offsets within the line.
    surrounding_text : str
        Verbatim text window around the extracted span.
    confidence : float
        Extraction-model confidence for this evidence span.
    """

    section: str = Field(description="Document section heading")
    page: int = Field(ge=1, description="1-based page number")
    line: int = Field(ge=1, description="1-based line number")
    char_offset: tuple[int, int] = Field(description="(start, end) character offsets")
    surrounding_text: str = Field(description="Context window around the extracted span")
    confidence: float = Field(ge=0.0, le=1.0, description="Extraction confidence score")

    @field_validator("char_offset")
    @classmethod
    def _validate_char_offset(cls, v: tuple[int, int]) -> tuple[int, int]:
        if v[0] < 0 or v[1] < v[0]:
            raise ValueError(
                f"char_offset must be a (start, end) pair with 0 <= start <= end, got {v}"
            )
        return v


# ---------------------------------------------------------------------------
# Clinical entities
# ---------------------------------------------------------------------------


class ClinicalEntity(BaseTimestampedModel):
    """Base model for any entity extracted from a clinical document."""

    text: str = Field(description="Original text span as found in the document")
    normalized_text: str = Field(default="", description="Normalised / canonical form")
    entity_type: str = Field(description="NER label (e.g. CONDITION, PROCEDURE, MEDICATION)")
    evidence: list[SourceEvidence] = Field(
        default_factory=list,
        description="Source evidence records linking back to the document",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Aggregated extraction confidence",
    )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier."""
        return ConfidenceLevel.from_score(self.confidence)


class ExtractedCondition(ClinicalEntity):
    """A medical condition / diagnosis extracted from clinical text."""

    entity_type: str = Field(default="CONDITION", description="Entity type label")
    icd10_code: Optional[str] = Field(default=None, description="Candidate ICD-10 code")
    acuity: Optional[str] = Field(default=None, description="Acute / chronic / unspecified")
    is_active: bool = Field(default=True, description="Whether the condition is currently active")
    onset_date: Optional[date] = Field(default=None, description="Approximate onset date")


class ExtractedProcedure(ClinicalEntity):
    """A procedure extracted from clinical text."""

    entity_type: str = Field(default="PROCEDURE", description="Entity type label")
    cpt_code: Optional[str] = Field(default=None, description="Candidate CPT code")
    body_site: Optional[str] = Field(default=None, description="Anatomical site")
    laterality: Optional[str] = Field(default=None, description="Left / Right / Bilateral")
    procedure_date: Optional[date] = Field(default=None, description="Date performed or planned")


class ExtractedMedication(ClinicalEntity):
    """A medication reference extracted from clinical text."""

    entity_type: str = Field(default="MEDICATION", description="Entity type label")
    ndc_code: Optional[str] = Field(default=None, description="National Drug Code")
    dosage: Optional[str] = Field(default=None, description="Dosage string (e.g. '500 mg')")
    route: Optional[str] = Field(default=None, description="Route (oral, IV, topical …)")
    frequency: Optional[str] = Field(default=None, description="Dosing frequency (e.g. 'BID')")
    is_current: bool = Field(default=True, description="Whether the medication is currently active")


# ---------------------------------------------------------------------------
# Clinical document
# ---------------------------------------------------------------------------


class ClinicalDocument(BaseTimestampedModel):
    """A clinical document and its extracted entities.

    Represents a single ingested document together with all conditions,
    procedures, and medications that were identified during NLP extraction.
    """

    document_type: str = Field(description="Document category (e.g. 'Discharge Summary')")
    patient_id: str = Field(description="De-identified patient reference")
    encounter_id: Optional[str] = Field(default=None, description="Encounter / visit reference")
    raw_text: str = Field(default="", description="Full document text (may be PHI-stripped)")
    source_file: Optional[str] = Field(default=None, description="Original filename or URI")

    conditions: list[ExtractedCondition] = Field(default_factory=list)
    procedures: list[ExtractedProcedure] = Field(default_factory=list)
    medications: list[ExtractedMedication] = Field(default_factory=list)

    metadata: dict = Field(
        default_factory=dict,
        description="Additional document-level metadata",
    )
