"""Pipeline primitives used by the Orchestrator agent."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from medi_comply.audit.audit_models import (
    AuditReport,
    AuditRiskAssessment,
    EvidenceMap,
    InputReference,
    LLMInteractionRecord,
    RetryRecord,
    WorkflowTrace,
)
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.nlp.document_ingester import IngestedDocument
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.result_models import EscalationRecord, PipelineError, PipelineStageResult
from medi_comply.schemas.coding_result import CodingResult
from medi_comply.schemas.retrieval import CodeRetrievalContext


class PipelineStage(Enum):
    """Ordered stages for the end-to-end pipeline."""

    INGEST = "INGEST"
    NLP = "NLP"
    RETRIEVAL = "RETRIEVAL"
    CODING = "CODING"
    COMPLIANCE = "COMPLIANCE"
    RETRY = "RETRY"
    AUDIT = "AUDIT"
    ESCALATION = "ESCALATION"
    OUTPUT = "OUTPUT"


class PipelineDefinition:
    """Static definitions for supported workflow types."""

    MEDICAL_CODING_PIPELINE = [
        PipelineStage.INGEST,
        PipelineStage.NLP,
        PipelineStage.RETRIEVAL,
        PipelineStage.CODING,
        PipelineStage.COMPLIANCE,
        PipelineStage.AUDIT,
        PipelineStage.OUTPUT,
    ]

    CLAIMS_ADJUDICATION_PIPELINE = [
        PipelineStage.INGEST,
        PipelineStage.NLP,
        PipelineStage.RETRIEVAL,
        PipelineStage.CODING,
        PipelineStage.COMPLIANCE,
        PipelineStage.AUDIT,
        PipelineStage.OUTPUT,
    ]

    PRIOR_AUTH_PIPELINE = [
        PipelineStage.INGEST,
        PipelineStage.NLP,
        PipelineStage.RETRIEVAL,
        PipelineStage.CODING,
        PipelineStage.COMPLIANCE,
        PipelineStage.AUDIT,
        PipelineStage.OUTPUT,
    ]

    @staticmethod
    def get_pipeline(workflow_type: str) -> list[PipelineStage]:
        mapping = {
            "MEDICAL_CODING": PipelineDefinition.MEDICAL_CODING_PIPELINE,
            "CLAIMS_ADJUDICATION": PipelineDefinition.CLAIMS_ADJUDICATION_PIPELINE,
            "PRIOR_AUTH": PipelineDefinition.PRIOR_AUTH_PIPELINE,
        }
        return mapping.get(workflow_type.upper(), PipelineDefinition.MEDICAL_CODING_PIPELINE)


class PipelineContext:
    """Mutable context shared by all stages."""

    def __init__(self, trace_id: str, workflow_type: str) -> None:
        self.trace_id = trace_id
        self.workflow_type = workflow_type
        self.started_at = datetime.now(timezone.utc)

        self.raw_input: Any = None
        self.input_reference: Optional[InputReference] = None
        self.patient_context: dict[str, Any] = {}

        self.ingested_document: Optional[IngestedDocument] = None
        self.scr: Optional[StructuredClinicalRepresentation] = None
        self.retrieval_context: Optional[CodeRetrievalContext] = None
        self.coding_result: Optional[CodingResult] = None
        self.compliance_report: Optional[ComplianceReport] = None
        self.workflow_trace: Optional[WorkflowTrace] = None
        self.audit_report: Optional[AuditReport] = None
        self.evidence_map: Optional[EvidenceMap] = None
        self.risk_assessment: Optional[AuditRiskAssessment] = None

        self.current_attempt: int = 1
        self.max_retries: int = 3
        self.retry_history: list[RetryRecord] = []
        self.compliance_feedback: Optional[list[str]] = None

        self.stage_results: list[PipelineStageResult] = []
        self.current_stage: Optional[PipelineStage] = None
        self.stage_timings: Dict[str, float] = {}

        self.llm_interactions: list[LLMInteractionRecord] = []

        self.errors: list[PipelineError] = []
        self.warnings: list[str] = []

        self.is_escalated: bool = False
        self.escalation_record: Optional[EscalationRecord] = None

        self.final_status: str = "PENDING"

        self._stage_start_times: dict[PipelineStage, datetime] = {}

    def record_stage_start(self, stage: PipelineStage) -> None:
        self.current_stage = stage
        self._stage_start_times[stage] = datetime.now(timezone.utc)

    def record_stage_complete(
        self,
        stage: PipelineStage,
        status: str,
        details: Optional[dict] = None,
        errors: Optional[list] = None,
    ) -> None:
        end_time = datetime.now(timezone.utc)
        start_time = self._stage_start_times.pop(stage, self.started_at)
        duration = (end_time - start_time).total_seconds() * 1000
        self.stage_timings[stage.value.lower()] = duration
        stage_result = PipelineStageResult(
            stage_name=stage.value,
            status=status,
            started_at=start_time,
            completed_at=end_time,
            processing_time_ms=duration,
            details=details or {},
            errors=[str(e) for e in (errors or [])],
        )
        self.stage_results.append(stage_result)

    def record_error(
        self,
        stage: str,
        error_type: str,
        message: str,
        recoverable: bool,
        recovery: Optional[str] = None,
    ) -> None:
        self.errors.append(
            PipelineError(
                stage=stage,
                error_type=error_type,
                error_message=message,
                is_recoverable=recoverable,
                recovery_action=recovery,
            )
        )

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    def record_llm_interaction(self, interaction: LLMInteractionRecord) -> None:
        self.llm_interactions.append(interaction)

    def get_processing_time_ms(self) -> float:
        return (datetime.now(timezone.utc) - self.started_at).total_seconds() * 1000
