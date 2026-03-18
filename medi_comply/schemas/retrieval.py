"""
MEDI-COMPLY — Retrieval Data Models.

Defines the structures used by the Knowledge Retrieval Agent (RAG)
to hold candidate codes, warnings, guidelines, and context matrices.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Individual Candidate & Strategy Models
# ---------------------------------------------------------------------------

class RankedCodeCandidate(BaseModel):
    """A single candidate code with ranking info."""
    code: str
    description: str
    long_description: Optional[str] = None
    code_type: str  # "ICD10" or "CPT"
    
    relevance_score: float = 0.0
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None
    graph_score: Optional[float] = None
    direct_map_score: Optional[float] = None
    
    is_billable: bool = True
    specificity_level: int = 3
    has_more_specific_children: bool = False
    children_codes: list[str] = Field(default_factory=list)
    
    parent_code: Optional[str] = None
    
    requires_additional_codes: list[str] = Field(default_factory=list)
    code_first_references: list[str] = Field(default_factory=list)
    excludes1: list[str] = Field(default_factory=list)
    excludes2: list[str] = Field(default_factory=list)
    
    is_manifestation: bool = False
    valid_for_age: bool = True
    valid_for_gender: bool = True
    
    retrieval_source: str = "VECTOR"


class RetrievalMetadata(BaseModel):
    """Metadata about the retrieval process."""
    strategies_used: list[str] = Field(default_factory=list)
    total_candidates_before_filter: int = 0
    total_candidates_after_filter: int = 0
    retrieval_time_ms: float = 0.0
    fusion_method: str = "RECIPROCAL_RANK_FUSION"


# ---------------------------------------------------------------------------
# Warning & Info Sub-Models
# ---------------------------------------------------------------------------

class ExcludesWarning(BaseModel):
    """Warning about Excludes1/Excludes2 conflict between codes."""
    code1: str
    code2: str
    excludes_type: str  # "EXCLUDES1" or "EXCLUDES2"
    description: str
    resolution: str


class NCCIEditWarning(BaseModel):
    """Warning about NCCI edit between two CPT codes."""
    code1: str
    code2: str
    edit_type: str  # "BUNDLED" or "MUTUALLY_EXCLUSIVE"
    modifier_allowed: bool
    rationale: str
    recommendation: str


class MedNecessityInfo(BaseModel):
    """Medical necessity coverage info for a procedure."""
    procedure_code: str
    lcd_id: Optional[str] = None
    lcd_title: Optional[str] = None
    is_covered: bool
    covered_by_diagnoses: list[str] = Field(default_factory=list)
    uncovered_diagnoses: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    frequency_limits: Optional[str] = None


class ModifierSuggestion(BaseModel):
    """Suggested modifier for a CPT code."""
    modifier: str
    description: str
    reason: str
    confidence: float


class GuidelineReference(BaseModel):
    """Reference to a relevant coding guideline."""
    guideline_id: str
    title: str
    section: str
    relevance_score: float
    key_rule: str
    full_text: str
    applies_to_codes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Condition & Procedure Packagers
# ---------------------------------------------------------------------------

class ConditionCodeCandidates(BaseModel):
    """Candidate codes for a single extracted condition."""
    condition_entity_id: str
    condition_text: str
    normalized_text: str
    assertion: str
    acuity: Optional[str] = None
    
    candidates: list[RankedCodeCandidate] = Field(default_factory=list)
    
    relevant_guidelines: list[GuidelineReference] = Field(default_factory=list)
    excludes_warnings: list[ExcludesWarning] = Field(default_factory=list)
    use_additional_instructions: list[str] = Field(default_factory=list)
    code_first_instructions: list[str] = Field(default_factory=list)
    
    retrieval_metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)


class ProcedureCodeCandidates(BaseModel):
    """Candidate CPT codes for a single extracted procedure."""
    procedure_entity_id: str
    procedure_text: str
    normalized_text: str
    
    candidates: list[RankedCodeCandidate] = Field(default_factory=list)
    
    ncci_edits: list[NCCIEditWarning] = Field(default_factory=list)
    medical_necessity: list[MedNecessityInfo] = Field(default_factory=list)
    modifier_suggestions: list[ModifierSuggestion] = Field(default_factory=list)
    
    retrieval_metadata: RetrievalMetadata = Field(default_factory=RetrievalMetadata)


# ---------------------------------------------------------------------------
# Global Retrieval Context (Payload for Coding Agent)
# ---------------------------------------------------------------------------

class CodeRetrievalContext(BaseModel):
    """Complete retrieval context — everything the coding agent needs."""
    scr_id: str
    context_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    
    patient_context: dict = Field(default_factory=dict)
    
    condition_candidates: list[ConditionCodeCandidates] = Field(default_factory=list)
    procedure_candidates: list[ProcedureCodeCandidates] = Field(default_factory=list)
    
    cross_entity_guidelines: list[GuidelineReference] = Field(default_factory=list)
    
    overall_excludes_matrix: list[ExcludesWarning] = Field(default_factory=list)
    overall_ncci_matrix: list[NCCIEditWarning] = Field(default_factory=list)
    
    encounter_type_guidelines: list[GuidelineReference] = Field(default_factory=list)
    
    retrieval_summary: dict = Field(default_factory=dict)
