"""
MEDI-COMPLY — Output schemas for final coding decisions.

These models define the rich, auditable structure produced by the
Medical Coding Agent, capturing the selected codes, step-by-step
reasoning, sequencing, and confidence scores.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class ReasoningStep(BaseModel):
    """Single step in the LLM's reasoning chain."""
    step_number: int
    action: str
    detail: str
    evidence_ref: Optional[str] = None
    guideline_ref: Optional[str] = None
    sub_decision: Optional[str] = None
    confidence_impact: Optional[str] = None


class ClinicalEvidenceLink(BaseModel):
    """Links a code decision back to the source clinical documentation."""
    evidence_id: str
    entity_id: str
    source_text: str
    section: str
    page: int
    line: int
    char_offset: tuple[int, int]
    relevance: str


class AlternativeCode(BaseModel):
    """A code from the candidate list that was considered but rejected."""
    code: str
    description: str
    reason_rejected: str
    would_be_correct_if: Optional[str] = None


class ConfidenceFactor(BaseModel):
    """Component factor contributing to the overall confidence score."""
    factor: str
    impact: str
    weight: float
    detail: str


class SingleCodeDecision(BaseModel):
    """Decision and audit trail for a single assigned medical code."""

    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    
    # The chosen code
    code: str
    code_type: str
    description: str
    
    # Sequencing
    sequence_position: str
    sequence_number: int
    
    # Audit & Reasoning
    reasoning_chain: list[ReasoningStep]
    clinical_evidence: list[ClinicalEvidenceLink]
    alternatives_considered: list[AlternativeCode]
    
    # Confidence metrics
    confidence_score: float = Field(ge=0.0, le=1.0)
    confidence_factors: list[ConfidenceFactor]
    
    # Special instructions applied
    use_additional_applied: list[str] = Field(default_factory=list)
    code_first_applied: list[str] = Field(default_factory=list)
    combination_code_note: Optional[str] = None
    
    # Flags and Citations
    requires_human_review: bool
    review_reason: Optional[str] = None
    is_billable: bool = True
    guidelines_cited: list[str] = Field(default_factory=list)


class CodingResult(BaseModel):
    """Complete, final coding output package for an encounter."""

    coding_result_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    scr_id: str
    context_id: str
    created_at: datetime
    processing_time_ms: float
    
    # Patient context snapshot
    encounter_type: str
    patient_age: int
    patient_gender: str
    
    # Code Decisions
    diagnosis_codes: list[SingleCodeDecision]
    principal_diagnosis: Optional[SingleCodeDecision] = None
    procedure_codes: list[SingleCodeDecision]
    
    # Overall metrics
    overall_confidence: float
    total_codes_assigned: int
    total_icd10_codes: int
    total_cpt_codes: int
    
    # Summary flags
    has_combination_codes: bool = False
    has_use_additional_codes: bool = False
    has_code_first_codes: bool = False
    requires_human_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    
    # Retry mechanism state
    attempt_number: int = 1
    previous_feedback: Optional[list[str]] = None
    
    # Rollups
    coding_summary: str
    all_guidelines_cited: list[str] = Field(default_factory=list)
