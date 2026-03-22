"""Supervisor agent orchestrating the full MEDI-COMPLY pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Union
from uuid import uuid4

from medi_comply.agents.audit_trail_agent import AuditTrailAgent
from medi_comply.agents.compliance_guard_agent import ComplianceGuardAgent
from medi_comply.agents.escalation_agent import EscalationAgent
from medi_comply.agents.knowledge_retrieval_agent import KnowledgeRetrievalAgent
from medi_comply.agents.medical_coding_agent import MedicalCodingAgent
from medi_comply.core.agent_base import AgentType, BaseAgent
from medi_comply.core.config import Settings
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.core.message_models import ResponseStatus
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.metrics_collector import PipelineMetricsCollector
from medi_comply.nlp.clinical_nlp_pipeline import ClinicalNLPPipeline
from medi_comply.nlp.document_ingester import DocumentIngester, IngestedDocument
from medi_comply.pipeline import PipelineContext, PipelineDefinition, PipelineStage
from medi_comply.result_models import EscalationRecord, MediComplyResult, PipelineError, PipelineMetrics
from medi_comply.retry_controller import RetryController
from medi_comply.audit.audit_models import AuditReport, AuditRiskAssessment, EvidenceMap, InputReference, WorkflowTrace
from medi_comply.audit.audit_store import AuditStore
from medi_comply.schemas.retrieval import CodeRetrievalContext


class OrchestratorAgent(BaseAgent):
    """Master orchestrator coordinating every agent in the system."""

    def __init__(
        self,
        knowledge_manager: KnowledgeManager,
        config: Settings,
        llm_client: Any = None,
        audit_agent: Optional[AuditTrailAgent] = None,
        nlp_pipeline: Optional[ClinicalNLPPipeline] = None,
        retrieval_agent: Optional[KnowledgeRetrievalAgent] = None,
        coding_agent: Optional[MedicalCodingAgent] = None,
        compliance_agent: Optional[ComplianceGuardAgent] = None,
        retry_controller: Optional[RetryController] = None,
        metrics_collector: Optional[PipelineMetricsCollector] = None,
        escalation_agent: Optional[EscalationAgent] = None,
    ) -> None:
        super().__init__(agent_name="OrchestratorAgent", agent_type=AgentType.SUPERVISOR)
        self.config = config
        self.km = knowledge_manager
        self.llm_client = llm_client

        self.nlp_pipeline = nlp_pipeline or ClinicalNLPPipeline()
        self.retrieval_agent = retrieval_agent or KnowledgeRetrievalAgent(knowledge_manager, config)
        self.coding_agent = coding_agent or MedicalCodingAgent(knowledge_manager, config, llm_client)
        self.compliance_agent = compliance_agent or ComplianceGuardAgent(knowledge_manager, config, llm_client)
        self.audit_agent = audit_agent or AuditTrailAgent(audit_store=AuditStore(), config=config)
        self.escalation_agent = escalation_agent or EscalationAgent(config)

        self.retry_controller = retry_controller or RetryController(max_retries=getattr(config.guardrail, "max_retries", 3))
        self.metrics_collector = metrics_collector or PipelineMetricsCollector()
        self._ingester = DocumentIngester()

    async def process(self, message: AgentMessage) -> AgentResponse:
        payload = message.payload or {}
        result = await self.run_pipeline(
            clinical_document=payload.get("clinical_document"),
            source_type=payload.get("source_type", "auto"),
            patient_context=payload.get("patient_context"),
            workflow_type=payload.get("workflow_type", "MEDICAL_CODING"),
        )
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload=result.model_dump(),
            trace_id=result.trace_id,
        )

    async def run_pipeline(
        self,
        clinical_document: Union[str, bytes, dict, None],
        source_type: str = "auto",
        patient_context: Optional[dict] = None,
        workflow_type: str = "MEDICAL_CODING",
    ) -> MediComplyResult:
        trace_id = str(uuid4())
        workflow = workflow_type or self._classify_workflow(clinical_document)
        context = PipelineContext(trace_id=trace_id, workflow_type=workflow)
        context.raw_input = clinical_document
        context.patient_context = patient_context or {}
        context.max_retries = self.retry_controller.max_retries

        failed = False
        try:
            await self._run_ingest_stage(context, source_type)
        except Exception:
            failed = True

        if not failed:
            try:
                await self._run_nlp_stage(context)
            except Exception:
                failed = True

        if not failed:
            try:
                await self._run_retrieval_stage(context)
            except Exception:
                failed = True

        if not failed:
            try:
                await self._run_coding_compliance_stage(context)
            except Exception:
                failed = True

        if not failed:
            await self._run_escalation_stage(context)

        try:
            await self._run_audit_stage(context)
        except Exception:
            # Audit errors are recorded internally; continue
            pass

        status_preview = self._determine_status(context)
        self.metrics_collector.record_pipeline_complete(status_preview, len(context.retry_history))
        metrics = self.metrics_collector.build_metrics(context)
        result = self._build_final_result(context, metrics, status_hint=status_preview)
        return result

    async def _run_ingest_stage(self, context: PipelineContext, source_type: str) -> None:
        context.record_stage_start(PipelineStage.INGEST)
        try:
            document = self._ingester.ingest(context.raw_input or "", source_type=source_type)
            context.ingested_document = document
            context.input_reference = self._build_input_reference(document, context.patient_context)
            context.record_stage_complete(
                PipelineStage.INGEST,
                "SUCCESS",
                details={
                    "document_type": document.metadata.get("document_type", "UNKNOWN"),
                    "char_count": len(document.raw_text),
                },
            )
        except Exception as exc:
            context.record_stage_complete(PipelineStage.INGEST, "FAILED", errors=[str(exc)])
            context.record_error("INGEST", type(exc).__name__, str(exc), recoverable=False)
            raise

    async def _run_nlp_stage(self, context: PipelineContext) -> None:
        context.record_stage_start(PipelineStage.NLP)
        try:
            scr = await self.nlp_pipeline.process(
                input_data=context.raw_input,
                source_type=context.ingested_document.source_type if context.ingested_document else "auto",
                patient_context=context.patient_context,
                use_llm=self.llm_client is not None,
            )
            context.scr = scr
            conditions_count = len(scr.conditions)
            if conditions_count == 0 and not scr.procedures:
                context.add_warning("No clinical entities extracted during NLP stage")
            context.record_stage_complete(
                PipelineStage.NLP,
                "SUCCESS",
                details={
                    "conditions_extracted": conditions_count,
                    "procedures_extracted": len(scr.procedures),
                    "medications_found": len(scr.medications),
                    "sections_detected": scr.sections_found,
                },
            )
        except Exception as exc:
            context.record_stage_complete(PipelineStage.NLP, "FAILED", errors=[str(exc)])
            context.record_error("NLP", type(exc).__name__, str(exc), recoverable=False)
            raise

    async def _run_retrieval_stage(self, context: PipelineContext) -> None:
        context.record_stage_start(PipelineStage.RETRIEVAL)
        try:
            scr_payload = self._serialize_scr_payload(context.scr)
            retrieval_message = AgentMessage(
                from_agent=self.agent_name,
                to_agent="KnowledgeRetrievalAgent",
                action="RETRIEVE",
                payload=scr_payload,
                trace_id=context.trace_id,
            )
            response = await self.retrieval_agent.process(retrieval_message)
            payload = response.payload or {}
            retrieval_context_data = payload.get("retrieval_context")
            if retrieval_context_data is None and payload:
                # Allow agents that return the context directly instead of nesting under "retrieval_context"
                if "condition_candidates" in payload or "procedure_candidates" in payload:
                    retrieval_context_data = payload
            if retrieval_context_data is None:
                raise RuntimeError("Retrieval agent returned no context")
            if hasattr(retrieval_context_data, "condition_candidates"):
                context.retrieval_context = retrieval_context_data
            else:
                context.retrieval_context = CodeRetrievalContext.model_validate(retrieval_context_data)
            context.record_stage_complete(
                PipelineStage.RETRIEVAL,
                "SUCCESS",
                details={
                    "condition_candidates": len(context.retrieval_context.condition_candidates),
                    "procedure_candidates": len(context.retrieval_context.procedure_candidates),
                },
            )
        except Exception as exc:
            context.record_stage_complete(PipelineStage.RETRIEVAL, "FAILED", errors=[str(exc)])
            context.record_error("RETRIEVAL", type(exc).__name__, str(exc), recoverable=False)
            raise

    async def _run_coding_compliance_stage(self, context: PipelineContext) -> None:
        context.record_stage_start(PipelineStage.CODING)
        try:
            coding_result, compliance_report, retry_history = await self.retry_controller.execute_with_retry(
                coding_agent=self.coding_agent,
                compliance_agent=self.compliance_agent,
                context=context,
                message_bus=None,
            )
            context.coding_result = coding_result
            context.compliance_report = compliance_report
            context.retry_history = retry_history
            context.record_stage_complete(
                PipelineStage.CODING,
                "SUCCESS",
                details={
                    "codes_assigned": coding_result.total_codes_assigned,
                    "overall_confidence": coding_result.overall_confidence,
                    "compliance_decision": compliance_report.overall_decision,
                    "retries": len(retry_history),
                },
            )
        except Exception as exc:
            context.record_stage_complete(PipelineStage.CODING, "FAILED", errors=[str(exc)])
            context.record_error(
                "CODING",
                type(exc).__name__,
                str(exc),
                recoverable=True,
                recovery="Escalating to human review",
            )
            context.is_escalated = True
            raise

    async def _run_escalation_stage(self, context: PipelineContext) -> None:
        if context.compliance_report and context.compliance_report.overall_decision in {"ESCALATE", "BLOCK"}:
            context.is_escalated = True
        if not context.is_escalated:
            return
        context.record_stage_start(PipelineStage.ESCALATION)
        message = AgentMessage(
            from_agent=self.agent_name,
            to_agent="EscalationAgent",
            action="ESCALATE",
            payload={
                "coding_result": context.coding_result,
                "compliance_report": context.compliance_report,
                "scr": context.scr,
                "retrieval_context": context.retrieval_context,
                "retry_history": context.retry_history,
                "escalation_reason": self._determine_escalation_reason(context),
                "trigger_stage": context.current_stage.value if context.current_stage else "CODING",
            },
            trace_id=context.trace_id,
        )
        response = await self.escalation_agent.process(message)
        record = response.payload.get("escalation_record")
        if record:
            from medi_comply.result_models import EscalationRecord

            context.escalation_record = EscalationRecord.model_validate(record)
        context.record_stage_complete(
            PipelineStage.ESCALATION,
            "SUCCESS",
            details={"priority": context.escalation_record.priority if context.escalation_record else "UNKNOWN"},
        )

    async def _run_audit_stage(self, context: PipelineContext) -> None:
        context.record_stage_start(PipelineStage.AUDIT)
        try:
            audit_message = AgentMessage(
                from_agent=self.agent_name,
                to_agent="AuditTrailAgent",
                action="COMPILE_AUDIT",
                payload={
                    "scr": context.scr,
                    "retrieval_context": context.retrieval_context,
                    "coding_result": context.coding_result,
                    "compliance_report": context.compliance_report,
                    "input_reference": context.input_reference,
                    "workflow_type": context.workflow_type,
                    "retry_history": [r.model_dump() for r in context.retry_history],
                    "stage_timings": context.stage_timings,
                    "llm_interactions": [i.model_dump() for i in context.llm_interactions],
                },
                trace_id=context.trace_id,
            )
            response = await self.audit_agent.process(audit_message)
            payload = response.payload or {}
            context.workflow_trace = self._coerce_audit_model(payload.get("workflow_trace"), WorkflowTrace)
            context.audit_report = self._coerce_audit_model(payload.get("audit_report"), AuditReport)
            context.evidence_map = self._coerce_audit_model(payload.get("evidence_map"), EvidenceMap)
            context.risk_assessment = self._coerce_audit_model(payload.get("risk_assessment"), AuditRiskAssessment)
            context.record_stage_complete(
                PipelineStage.AUDIT,
                "SUCCESS",
                details={
                    "audit_trace_id": context.trace_id,
                    "risk_score": context.risk_assessment.overall_score if context.risk_assessment else 0.0,
                },
            )
        except Exception as exc:
            context.record_stage_complete(PipelineStage.AUDIT, "FAILED", errors=[str(exc)])
            context.record_error(
                "AUDIT",
                type(exc).__name__,
                str(exc),
                recoverable=True,
                recovery="Output released without complete audit",
            )
            context.add_warning(f"Audit trail incomplete: {exc}")
            raise

    def _determine_status(self, context: PipelineContext) -> str:
        if context.errors and not any(error.is_recoverable for error in context.errors):
            return "ERROR"
        if context.compliance_report and context.compliance_report.overall_decision == "BLOCK":
            return "BLOCKED"
        if context.is_escalated:
            return "ESCALATED"
        if context.compliance_report and context.compliance_report.overall_decision != "PASS":
            return context.compliance_report.overall_decision
        return "SUCCESS"

    def _build_final_result(
        self,
        context: PipelineContext,
        metrics: PipelineMetrics,
        status_hint: Optional[str] = None,
    ) -> MediComplyResult:
        status = status_hint or self._determine_status(context)
        context.final_status = status
        audit_summary = (
            context.audit_report.summary
            if isinstance(context.audit_report, AuditReport)
            else "Audit report unavailable"
        )
        total_time_ms = max(context.get_processing_time_ms(), 1.0)

        return MediComplyResult(
            result_id=str(uuid4()),
            trace_id=context.trace_id,
            status=status,
            started_at=context.started_at,
            completed_at=datetime.now(timezone.utc),
            total_processing_time_ms=total_time_ms,
            encounter_type=context.patient_context.get("encounter_type", "UNKNOWN") if context.patient_context else "UNKNOWN",
            document_type=context.input_reference.document_type if context.input_reference else "UNKNOWN",
            coding_result=context.coding_result,
            compliance_report=context.compliance_report,
            audit_report_summary=audit_summary,
            audit_report_full=context.audit_report if isinstance(context.audit_report, AuditReport) else None,
            evidence_map=context.evidence_map if isinstance(context.evidence_map, EvidenceMap) else None,
            risk_assessment=context.risk_assessment if isinstance(context.risk_assessment, AuditRiskAssessment) else None,
            pipeline_stages=context.stage_results,
            retry_count=len(context.retry_history),
            escalation=context.escalation_record,
            errors=context.errors,
            warnings=context.warnings,
            metrics=metrics,
        )

    def _determine_escalation_reason(self, context: PipelineContext) -> str:
        if context.compliance_report:
            return f"COMPLIANCE_{context.compliance_report.overall_decision}"
        if any(isinstance(err, PipelineError) for err in context.errors):
            return "PIPELINE_ERROR"
        return "MANUAL_REVIEW"

    def _classify_workflow(self, clinical_document: Any) -> str:
        return "MEDICAL_CODING"

    def _build_input_reference(self, document: IngestedDocument, patient_context: dict) -> InputReference:
        raw_bytes = document.raw_text.encode("utf-8", errors="ignore")
        digest = hashlib.sha256(raw_bytes).hexdigest()
        return InputReference(
            document_id=document.document_id,
            document_hash=digest,
            document_type=document.metadata.get("document_type", document.source_type),
            encounter_id=patient_context.get("encounter_id") if patient_context else None,
            encounter_type=patient_context.get("encounter_type", "UNKNOWN") if patient_context else "UNKNOWN",
            page_count=len(document.pages) or 1,
            character_count=len(document.raw_text),
        )

    @staticmethod
    def _coerce_audit_model(value, model_cls):
        if value is None:
            return None
        if isinstance(value, model_cls):
            return value
        if hasattr(model_cls, "model_validate"):
            return model_cls.model_validate(value)
        return model_cls(**value)

    @staticmethod
    def _serialize_scr_payload(scr: Any) -> dict[str, Any]:
        if scr is None:
            raise RuntimeError("Structured clinical representation missing prior to retrieval")
        if isinstance(scr, dict):
            return scr
        if is_dataclass(scr):
            return asdict(scr)
        if hasattr(scr, "model_dump"):
            return scr.model_dump()
        if hasattr(scr, "__dict__"):
            return dict(scr.__dict__)
        raise TypeError(f"Unsupported SCR payload type: {type(scr)!r}")
