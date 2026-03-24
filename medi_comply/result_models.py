"""High-level result schemas for the MEDI-COMPLY orchestrator."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from medi_comply.audit.audit_models import AuditReport, AuditRiskAssessment, EvidenceMap
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.schemas.coding_result import CodingResult


class PipelineError(BaseModel):
    """Error captured during pipeline execution."""

    stage: str
    error_type: str
    error_message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    is_recoverable: bool = False
    recovery_action: Optional[str] = None


class PipelineStageResult(BaseModel):
    """Per-stage summary including timing and status."""

    stage_name: str
    status: str
    started_at: datetime
    completed_at: datetime
    processing_time_ms: float
    details: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)


class EscalationRecord(BaseModel):
    """Escalation metadata handed to human reviewers."""

    escalation_id: str
    escalated_at: datetime
    reason: str
    trigger_stage: str
    trigger_details: List[str]
    context_for_reviewer: Dict[str, Any]
    priority: str
    estimated_review_time: str


class PipelineMetrics(BaseModel):
    """Operational metrics for a completed pipeline run."""

    total_time_ms: float
    stage_times_ms: Dict[str, float]
    llm_calls_count: int
    llm_total_tokens: int
    llm_total_latency_ms: float
    retrieval_candidates_count: int
    codes_assigned: int
    compliance_checks_run: int
    compliance_checks_passed: int
    retry_count: int
    knowledge_base_version: str
    model_versions: Dict[str, str]


class PipelineStageResultCollection(BaseModel):
    """Helper container for stage lists (used in validations)."""

    stages: List[PipelineStageResult] = Field(default_factory=list)


class PipelineMetricsCollection(BaseModel):
    metrics: PipelineMetrics


class PipelineErrorCollection(BaseModel):
    errors: List[PipelineError] = Field(default_factory=list)


class MediComplyResult(BaseModel):
    """Top-level output returned to end users / API clients."""

    result_id: str
    trace_id: str
    status: str

    started_at: datetime
    completed_at: datetime
    total_processing_time_ms: float

    encounter_type: str
    document_type: str

    coding_result: Optional[CodingResult] = None
    compliance_report: Optional[ComplianceReport] = None

    audit_report_summary: str
    audit_report_full: Optional[AuditReport] = None
    evidence_map: Optional[EvidenceMap] = None
    risk_assessment: Optional[AuditRiskAssessment] = None

    pipeline_stages: List[PipelineStageResult] = Field(default_factory=list)
    retry_count: int = 0

    escalation: Optional[EscalationRecord] = None

    errors: List[PipelineError] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    metrics: PipelineMetrics

    # Regulatory + version metadata
    knowledge_base_version: str = "UNKNOWN"
    code_set_versions: Dict[str, str] = Field(default_factory=dict)
    regulatory_validation: Optional[Dict[str, Any]] = None
