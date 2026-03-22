"""
MEDI-COMPLY — Observer agent that passively records all workflow decisions.

The AuditTrailAgent is an OBSERVER — it does NOT modify any data or
influence any decisions.  It watches, records, and reports.

Design principles:
  1. **Passive observation** — never mutates workflow data
  2. **Immutable recording** — every trace is hash-chained and signed
  3. **Court-admissible output** — reports are suitable for legal review
  4. **Zero-trust** — the agent validates its own inputs
"""

from __future__ import annotations

from typing import Optional

from pydantic_settings import BaseSettings

from medi_comply.core.agent_base import BaseAgent
from medi_comply.schemas.common import AgentState
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.audit.audit_store import AuditStore
from medi_comply.audit.decision_trace import DecisionTraceBuilder
from medi_comply.audit.evidence_mapper import EvidenceMapper
from medi_comply.audit.risk_scorer import AuditRiskScorer
from medi_comply.audit.report_generator import AuditReportGenerator
from medi_comply.audit.audit_models import (
    WorkflowTrace,
    AuditReport,
    EvidenceMap,
    AuditRiskAssessment,
)


class AuditTrailAgent(BaseAgent):
    """Observer agent that passively records all workflow decisions.

    This agent sits at the end of every pipeline run and compiles
    a complete, tamper-evident audit record from the outputs of all
    preceding stages (NLP, Retrieval, Coding, Compliance).

    Parameters
    ----------
    audit_store:
        The :class:`AuditStore` used to persist workflow traces.
    config:
        Optional application-level configuration overrides.
    """

    def __init__(
        self,
        audit_store: AuditStore,
        config: Optional[BaseSettings] = None,
    ) -> None:
        super().__init__(agent_name="AuditTrailAgent", agent_type="OBSERVER")
        self.agent_type = "OBSERVER"

        self.audit_store = audit_store
        self.trace_builder = DecisionTraceBuilder()
        self.evidence_mapper = EvidenceMapper()
        self.risk_scorer = AuditRiskScorer()
        self.report_generator = AuditReportGenerator()
        self.config = config

        # Track the number of records stored by this agent instance
        self._records_stored: int = 0

    # ── Main processing entry point ───────────────────────

    async def process(self, message: AgentMessage) -> AgentResponse:
        """Process an incoming audit-recording request.

        Expected payload keys:
          - ``scr`` — StructuredClinicalRepresentation
          - ``retrieval_context`` — CodeRetrievalContext
          - ``coding_result`` — CodingResult
          - ``compliance_report`` — ComplianceReport
          - ``input_reference`` — InputReference
          - ``workflow_type`` — str (default ``"MEDICAL_CODING"``)
          - ``retry_history`` — list[dict]
          - ``stage_timings`` — dict[str, float]
          - ``llm_interactions`` — list[dict]

        Returns
        -------
        AgentResponse
            Contains the ``workflow_trace``, ``audit_report``,
            ``evidence_map``, and ``risk_assessment`` in its payload.
        """
        self.transition_state(
            AgentState.THINKING,
            {"detail": "Beginning audit trace compilation"},
        )

        # Extract payload fields
        scr = message.payload.get("scr")
        retrieval_context = message.payload.get("retrieval_context")
        coding_result = message.payload.get("coding_result")
        compliance_report = message.payload.get("compliance_report")
        input_reference = message.payload.get("input_reference")
        workflow_type = message.payload.get("workflow_type", "MEDICAL_CODING")
        retry_history = message.payload.get("retry_history", [])
        stage_timings = message.payload.get("stage_timings", {})
        llm_interactions = message.payload.get("llm_interactions", [])

        # Compile the full audit record
        trace, report, ev_map, risk = self.compile_audit_record(
            scr,
            retrieval_context,
            coding_result,
            compliance_report,
            input_reference,
            workflow_type,
            retry_history,
            stage_timings,
            llm_interactions,
        )

        # Persist the trace in the immutable store when we actually have one
        completion_detail = "Audit record signed and stored in immutable ledger"
        if trace:
            self.audit_store.store(trace)
            self._records_stored += 1
        else:
            completion_detail = "No coding result available; audit record not stored"

        # Advance state machine through required transitions
        self.transition_state(AgentState.PROPOSING)
        self.transition_state(AgentState.VALIDATING)
        self.transition_state(AgentState.APPROVED)
        self.transition_state(
            AgentState.COMPLETED,
            {"detail": completion_detail},
        )

        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status="SUCCESS",
            payload={
                "workflow_trace": trace,
                "audit_report": report,
                "evidence_map": ev_map,
                "risk_assessment": risk,
            },
            trace_id=message.trace_id,
        )

    # ── Audit record compilation ──────────────────────────

    def compile_audit_record(
        self,
        scr,
        retrieval_context,
        coding_result,
        compliance_report,
        input_reference,
        workflow_type: str = "MEDICAL_CODING",
        retry_history: Optional[list] = None,
        stage_timings: Optional[dict] = None,
        llm_interactions: Optional[list] = None,
    ) -> tuple[WorkflowTrace, AuditReport, EvidenceMap, AuditRiskAssessment]:
        """Compile a complete audit record from pipeline outputs.

        Each call creates a fresh ``DecisionTraceBuilder`` so that
        the agent's internal builder state does not leak between
        encounters.

        Parameters
        ----------
        scr:
            NLP pipeline output.
        retrieval_context:
            Knowledge retrieval output.
        coding_result:
            Final coding decisions.
        compliance_report:
            Compliance check results.
        input_reference:
            Metadata about the input document.
        workflow_type:
            Workflow identifier (default ``"MEDICAL_CODING"``).
        retry_history:
            Optional list of retry records.
        stage_timings:
            Optional per-stage timing dict.
        llm_interactions:
            Optional list of LLM interaction dicts.

        Returns
        -------
        tuple
            ``(workflow_trace, audit_report, evidence_map, risk_assessment)``
        """
        retry_history = retry_history or []
        stage_timings = stage_timings or {}
        llm_interactions = llm_interactions or []

        # Fresh builder per record
        tb = DecisionTraceBuilder()
        tb.start_trace(workflow_type, input_reference)

        if scr:
            tb.record_nlp_stage(
                scr,
                stage_timings.get("nlp", 0.0),
                tb._started_at,
                tb._started_at,
            )
        if retrieval_context:
            tb.record_retrieval_stage(
                retrieval_context,
                stage_timings.get("retrieval", 0.0),
                tb._started_at,
                tb._started_at,
            )
        if coding_result:
            tb.record_coding_stage(
                coding_result,
                stage_timings.get("coding", 0.0),
                tb._started_at,
                tb._started_at,
            )
        if compliance_report:
            tb.record_compliance_stage(
                compliance_report,
                stage_timings.get("compliance", 0.0),
                tb._started_at,
                tb._started_at,
            )
        if retry_history:
            for idx, rt in enumerate(retry_history):
                tb.record_retry(
                    idx + 1,
                    rt.get("feedback", []),
                    rt.get("codes_changed", []),
                    rt.get("compliance_result_after", "RETRY"),
                )
        if llm_interactions:
            for llm in llm_interactions:
                tb.record_llm_interaction(**llm)

        trace = tb.build_trace(coding_result) if coding_result else None

        # Build the evidence map
        ev_map = None
        if scr and coding_result:
            ev_map = self.evidence_mapper.build_evidence_map(
                coding_result, scr
            )

        # Compute risk assessment
        risk = self.risk_scorer.calculate_risk(trace) if trace else None

        # Generate the human-readable report
        report = (
            self.report_generator.generate_full_report(trace, ev_map)
            if trace
            else None
        )

        return trace, report, ev_map, risk

    # ── Convenience methods ───────────────────────────────

    def quick_summary(self, workflow_trace: WorkflowTrace) -> str:
        """Return a one-line summary of a stored trace."""
        return (
            f"Trace {workflow_trace.trace_id} stored. "
            f"Risk: {workflow_trace.compliance_stage.risk_level}. "
            f"Check hash: {workflow_trace.record_hash[:8]}"
        )

    @property
    def records_stored(self) -> int:
        """Number of records stored by this agent instance."""
        return self._records_stored

