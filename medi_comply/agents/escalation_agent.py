"""Escalation agent responsible for handing off to human reviewers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from medi_comply.core.agent_base import AgentState, AgentType, BaseAgent
from medi_comply.core.config import Settings
from medi_comply.core.message_models import AgentMessage, AgentResponse, ResponseStatus
from medi_comply.result_models import EscalationRecord
from medi_comply.schemas.coding_result import CodingResult
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation


class EscalationAgent(BaseAgent):
    """Packages all context for human review when automation cannot continue."""

    def __init__(self, config: Optional[Settings] = None) -> None:
        super().__init__(agent_name="EscalationAgent", agent_type=AgentType.SAFETY_NET)
        self.config = config or Settings()
        self._escalation_queue: list[EscalationRecord] = []

    async def process(self, message: AgentMessage) -> AgentResponse:
        self.transition_state(AgentState.THINKING)
        payload = message.payload or {}
        record = self.create_escalation(
            reason=payload.get("escalation_reason", "UNSPECIFIED"),
            trigger_stage=payload.get("trigger_stage", "COMPLIANCE"),
            trigger_details=payload.get("trigger_details", []),
            coding_result=payload.get("coding_result"),
            compliance_report=payload.get("compliance_report"),
            scr=payload.get("scr"),
            retry_history=payload.get("retry_history", []),
        )
        self._escalation_queue.append(record)
        self.transition_state(AgentState.COMPLETED)
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload={"escalation_record": record.model_dump()},
            trace_id=message.trace_id,
        )

    def create_escalation(
        self,
        reason: str,
        trigger_stage: str,
        trigger_details: List[str],
        coding_result: Optional[CodingResult],
        compliance_report: Optional[ComplianceReport],
        scr: Optional[StructuredClinicalRepresentation],
        retry_history: Optional[list] = None,
    ) -> EscalationRecord:
        guidance = self._generate_reviewer_guidance(reason, compliance_report, coding_result)
        context = self._build_context_snapshot(coding_result, compliance_report, scr, retry_history or [])
        priority = self._determine_priority(reason, compliance_report)
        record = EscalationRecord(
            escalation_id=str(uuid4()),
            escalated_at=datetime.now(timezone.utc),
            reason=reason,
            trigger_stage=trigger_stage,
            trigger_details=trigger_details or guidance,
            context_for_reviewer=context,
            priority=priority,
            estimated_review_time=self._estimate_review_time(priority),
        )
        return record

    def _build_context_snapshot(
        self,
        coding_result: Optional[CodingResult],
        compliance_report: Optional[ComplianceReport],
        scr: Optional[StructuredClinicalRepresentation],
        retry_history: list,
    ) -> Dict[str, Any]:
        snapshot: Dict[str, Any] = {
            "retry_history": [r if isinstance(r, dict) else r.model_dump() for r in retry_history],
            "guidance": [],
        }
        if coding_result:
            snapshot["coding_summary"] = coding_result.coding_summary
            snapshot["codes"] = {
                "diagnosis": [f"{c.code_type}:{c.code}" for c in coding_result.diagnosis_codes],
                "procedures": [f"{c.code_type}:{c.code}" for c in coding_result.procedure_codes],
            }
            snapshot["overall_confidence"] = coding_result.overall_confidence
        if compliance_report:
            snapshot["compliance_decision"] = compliance_report.overall_decision
            snapshot["risk_level"] = compliance_report.risk_level
            snapshot["risk_factors"] = compliance_report.risk_factors
        if scr:
            snapshot["encounter_type"] = scr.patient_context.get("encounter_type", "UNKNOWN") if scr.patient_context else "UNKNOWN"
            snapshot["primary_condition"] = scr.conditions[0].text if scr.conditions else "UNKNOWN"
        return snapshot

    def _determine_priority(self, reason: str, compliance_report: Optional[ComplianceReport]) -> str:
        reason_lower = reason.upper()
        if "SECURITY" in reason_lower:
            return "IMMEDIATE"
        if "PRIMARY" in reason_lower or (compliance_report and compliance_report.risk_level == "CRITICAL"):
            return "URGENT"
        if compliance_report and compliance_report.risk_level in {"HIGH", "CRITICAL"}:
            return "ELEVATED"
        return "ROUTINE"

    def _estimate_review_time(self, priority: str) -> str:
        mapping = {
            "IMMEDIATE": "5 minutes",
            "URGENT": "10 minutes",
            "ELEVATED": "15 minutes",
            "ROUTINE": "20 minutes",
        }
        return mapping.get(priority, "15 minutes")

    def _generate_reviewer_guidance(
        self,
        reason: str,
        compliance_report: Optional[ComplianceReport],
        coding_result: Optional[CodingResult],
    ) -> List[str]:
        guidance: List[str] = []
        if coding_result and coding_result.review_reasons:
            guidance.extend(coding_result.review_reasons)
        if compliance_report and compliance_report.feedback:
            for item in compliance_report.feedback.feedback_items:
                guidance.append(f"Check {item.check_id}: {item.issue}")
        if not guidance:
            guidance.append(f"Manual review required due to {reason}.")
        return guidance

    def get_pending_escalations(self) -> List[EscalationRecord]:
        return list(self._escalation_queue)

    def get_escalation_stats(self) -> Dict[str, Any]:
        total = len(self._escalation_queue)
        by_priority: Dict[str, int] = {}
        for record in self._escalation_queue:
            by_priority[record.priority] = by_priority.get(record.priority, 0) + 1
        return {
            "total": total,
            "by_priority": by_priority,
        }
