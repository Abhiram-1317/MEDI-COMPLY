"""
MEDI-COMPLY — Medical coding schemas (ICD-10, CPT, reasoning).

Models for code candidates, assignments, reasoning steps, and the final
coding result produced by the coding agent.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from medi_comply.schemas.common import BaseTimestampedModel, ConfidenceLevel


# ---------------------------------------------------------------------------
# Code primitives
# ---------------------------------------------------------------------------


class ICD10Code(BaseModel):
    """An ICD-10-CM diagnosis code."""

    code: str = Field(description="ICD-10-CM code (e.g. 'E11.65')")
    description: str = Field(description="Official code description")
    chapter: Optional[str] = Field(default=None, description="ICD-10 chapter")
    is_billable: bool = Field(default=True, description="Whether the code is terminal / billable")

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return v.strip().upper()


class CPTCode(BaseModel):
    """A CPT / HCPCS procedure code."""

    code: str = Field(description="CPT / HCPCS code (e.g. '99213')")
    description: str = Field(description="Official code description")
    category: Optional[str] = Field(default=None, description="Code category")
    rvu: Optional[float] = Field(default=None, ge=0.0, description="Relative Value Unit")

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, v: str) -> str:
        return v.strip().upper()


# ---------------------------------------------------------------------------
# Reasoning & candidates
# ---------------------------------------------------------------------------


class ReasoningStep(BaseModel):
    """One step in the agent's reasoning chain during code assignment.

    Attributes
    ----------
    step_number : int
        Ordinal position in the reasoning chain.
    action : str
        What the agent did (e.g. ``"extract_entity"``, ``"lookup_code"``).
    detail : str
        Free-text explanation of this step.
    evidence_ref : str
        Pointer to the source evidence that supports this step.
    guideline_ref : str | None
        Reference to the clinical guideline or coding rule applied.
    """

    step_number: int = Field(ge=1, description="Step ordinal")
    action: str = Field(description="Action tag")
    detail: str = Field(description="Human-readable explanation")
    evidence_ref: str = Field(description="Source-evidence reference ID or label")
    guideline_ref: Optional[str] = Field(
        default=None,
        description="Coding guideline / rule reference (e.g. 'ICD-10-CM Guidelines §I.A.1')",
    )


class CodeCandidate(BaseModel):
    """A candidate code surfaced during code assignment.

    Multiple candidates may be considered before a final assignment is made.
    """

    code: str = Field(description="The candidate code string")
    code_system: str = Field(
        description="Code system ('ICD-10-CM', 'CPT', 'HCPCS')"
    )
    description: str = Field(description="Code description")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for this candidate")
    ranking: int = Field(ge=1, description="Rank among all candidates (1 = best)")
    reasoning: list[ReasoningStep] = Field(
        default_factory=list,
        description="Reasoning chain that led to this candidate",
    )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier."""
        return ConfidenceLevel.from_score(self.confidence)


# ---------------------------------------------------------------------------
# Code assignment & result
# ---------------------------------------------------------------------------


class CodeAssignment(BaseTimestampedModel):
    """Final assignment of a single code to a clinical entity."""

    code: str = Field(description="Assigned code string")
    code_system: str = Field(description="Code system identifier")
    description: str = Field(description="Code description")
    confidence: float = Field(ge=0.0, le=1.0, description="Assignment confidence")
    entity_text: str = Field(description="Original clinical text that was coded")
    reasoning_steps: list[ReasoningStep] = Field(default_factory=list)
    alternatives: list[CodeCandidate] = Field(
        default_factory=list,
        description="Other candidates that were considered",
    )
    is_primary: bool = Field(default=False, description="Whether this is the primary code")


class CodingResult(BaseTimestampedModel):
    """Aggregate output of the coding agent for a single document.

    Contains all code assignments, the full candidate pool, and an overall
    confidence metric.
    """

    document_id: uuid.UUID = Field(description="ID of the source ClinicalDocument")
    assignments: list[CodeAssignment] = Field(default_factory=list)
    all_candidates: list[CodeCandidate] = Field(default_factory=list)
    overall_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Weighted-average confidence across all assignments",
    )
    reasoning_summary: str = Field(
        default="",
        description="Human-readable narrative of the coding rationale",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings produced during coding",
    )
