"""Escalation Agent — Safety net for MEDI-COMPLY."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from medi_comply.core.agent_base import BaseAgent
from medi_comply.core.config import Settings, get_settings
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.schemas.common import AgentState, AgentType, ResponseStatus

_settings: Settings = get_settings()
GUARDRAIL_MAX_RETRIES = _settings.guardrail.max_retries
GUARDRAIL_ESCALATION_THRESHOLD = _settings.guardrail.escalation_threshold


class EscalationTrigger(str, Enum):
    """Reasons that require human review."""

    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    COMPLIANCE_HARD_FAIL = "COMPLIANCE_HARD_FAIL"
    CONFLICTING_INFORMATION = "CONFLICTING_INFORMATION"
    MISSING_CRITICAL_DATA = "MISSING_CRITICAL_DATA"
    EDGE_CASE_UNRESOLVED = "EDGE_CASE_UNRESOLVED"
    MAX_RETRIES_EXCEEDED = "MAX_RETRIES_EXCEEDED"
    UPCODING_SUSPECTED = "UPCODING_SUSPECTED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    KNOWLEDGE_STALENESS = "KNOWLEDGE_STALENESS"
    MULTI_PAYER_CONFLICT = "MULTI_PAYER_CONFLICT"
    AGENT_ERROR = "AGENT_ERROR"
    MANUAL_REVIEW_REQUESTED = "MANUAL_REVIEW_REQUESTED"


class EscalationPriority(str, Enum):
    """Escalation urgency mapped to SLA windows."""

    CRITICAL = "CRITICAL"  # response within 1 hour
    HIGH = "HIGH"          # response within 4 hours
    MEDIUM = "MEDIUM"      # response within 24 hours
    LOW = "LOW"            # response within 72 hours


class EscalationStatus(str, Enum):
    """Lifecycle of an escalation case."""

    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    IN_REVIEW = "IN_REVIEW"
    RESOLVED = "RESOLVED"
    EXPIRED = "EXPIRED"


class EscalationCase(BaseModel):
    """Full payload for a human-review case."""

    case_id: str
    created_at: datetime
    trigger: EscalationTrigger
    priority: EscalationPriority
    status: EscalationStatus
    source_agent: str
    workflow_type: str
    patient_context: Dict[str, Any] = Field(default_factory=dict)
    clinical_summary: str
    original_input: str
    attempted_output: Optional[Dict[str, Any]] = None
    reasoning_history: List[Dict[str, Any]] = Field(default_factory=list)
    compliance_failures: List[Dict[str, Any]] = Field(default_factory=list)
    confidence_scores: Dict[str, float] = Field(default_factory=dict)
    suggested_action: str = ""
    assigned_to: Optional[str] = None
    resolution: Optional[Dict[str, Any]] = None
    resolved_at: Optional[datetime] = None
    sla_deadline: datetime
    audit_trail_id: Optional[str] = None
    fraud_result: Optional[Dict[str, Any]] = None
    requires_compliance_officer: bool = False


class EscalationNotification(BaseModel):
    """Lightweight payload for notifying downstream systems."""

    case_id: str
    priority: EscalationPriority
    trigger: EscalationTrigger
    summary: str
    sla_deadline: datetime
    action_required: str
    requires_compliance_officer: bool = False


class EscalationQueue:
    """In-memory priority queue (thread-safe via asyncio)."""

    def __init__(self) -> None:
        self._cases: Dict[str, EscalationCase] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, case: EscalationCase) -> str:
        async with self._lock:
            if case.case_id in self._cases:
                case = case.model_copy(update={"case_id": str(uuid.uuid4())})
            self._cases[case.case_id] = case
            return case.case_id

    async def dequeue(self, priority: Optional[EscalationPriority] = None) -> Optional[EscalationCase]:
        async with self._lock:
            candidates = [c for c in self._cases.values() if c.status == EscalationStatus.PENDING]
            if priority:
                candidates = [c for c in candidates if c.priority == priority]
            if not candidates:
                return None
            candidates.sort(key=lambda c: (self._priority_rank(c.priority), c.sla_deadline))
            case = candidates[0]
            case.status = EscalationStatus.ASSIGNED
            return case

    async def get_case(self, case_id: str) -> Optional[EscalationCase]:
        async with self._lock:
            return self._cases.get(case_id)

    async def update_status(self, case_id: str, status: EscalationStatus, assigned_to: Optional[str] = None) -> None:
        async with self._lock:
            case = self._cases.get(case_id)
            if case:
                case.status = status
                if assigned_to:
                    case.assigned_to = assigned_to

    async def resolve_case(self, case_id: str, resolution: Dict[str, Any], resolved_by: str) -> None:
        async with self._lock:
            case = self._cases.get(case_id)
            if case:
                case.status = EscalationStatus.RESOLVED
                case.resolution = resolution | {"resolved_by": resolved_by}
                case.resolved_at = datetime.now(timezone.utc)

    async def get_pending_count(self) -> Dict[EscalationPriority, int]:
        async with self._lock:
            counts: Dict[EscalationPriority, int] = {p: 0 for p in EscalationPriority}
            for c in self._cases.values():
                if c.status == EscalationStatus.PENDING:
                    counts[c.priority] += 1
            return counts

    async def get_overdue_cases(self) -> List[EscalationCase]:
        async with self._lock:
            now = datetime.now(timezone.utc)
            return [
                c
                for c in self._cases.values()
                if c.status in {EscalationStatus.PENDING, EscalationStatus.IN_REVIEW} and c.sla_deadline < now
            ]

    async def get_cases_by_status(self, status: EscalationStatus) -> List[EscalationCase]:
        async with self._lock:
            return [c for c in self._cases.values() if c.status == status]

    def _priority_rank(self, priority: EscalationPriority) -> int:
        order = {
            EscalationPriority.CRITICAL: 0,
            EscalationPriority.HIGH: 1,
            EscalationPriority.MEDIUM: 2,
            EscalationPriority.LOW: 3,
        }
        return order[priority]


class EscalationAgent(BaseAgent):
    """Safety net that orchestrates human handoff when automation cannot proceed."""

    def __init__(self, message_bus: Any = None) -> None:
        super().__init__(agent_name="EscalationAgent", agent_type=AgentType.SAFETY_NET)
        self.queue = EscalationQueue()
        self.message_bus = message_bus

    async def process(self, message: AgentMessage) -> AgentResponse:
        self.transition_state(AgentState.THINKING)
        payload = message.payload or {}
        trigger_raw = payload.get("trigger", EscalationTrigger.AGENT_ERROR)
        trigger = trigger_raw if isinstance(trigger_raw, EscalationTrigger) else EscalationTrigger(trigger_raw)

        case = await self.escalate(
            trigger=trigger,
            source_agent=payload.get("source_agent", "unknown"),
            context=payload.get("context", {}),
            attempted_output=payload.get("attempted_output"),
            compliance_failures=payload.get("compliance_failures", []),
            confidence_scores=payload.get("confidence_scores", {}),
        )

        self.transition_state(AgentState.ESCALATED)

        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.ESCALATE,
            payload=case.model_dump(),
            trace_id=message.trace_id,
        )

    async def escalate(
        self,
        trigger: EscalationTrigger,
        source_agent: str,
        context: Dict[str, Any],
        attempted_output: Optional[Dict[str, Any]],
        compliance_failures: List[Dict[str, Any]],
        confidence_scores: Dict[str, float],
    ) -> EscalationCase:
        priority = self.determine_priority(trigger)
        sla_deadline = self.calculate_sla_deadline(priority)
        suggested_action = self.build_suggested_action(trigger, context)

        requires_compliance_officer = False
        fraud_result = context.get("fraud_result") if isinstance(context, dict) else None
        fraud_alerts = context.get("fraud_alerts") if isinstance(context, dict) else None
        if trigger == EscalationTrigger.UPCODING_SUSPECTED:
            priority = EscalationPriority.CRITICAL
            requires_compliance_officer = True

        case = EscalationCase(
            case_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            trigger=trigger,
            priority=priority,
            status=EscalationStatus.PENDING,
            source_agent=source_agent,
            workflow_type=context.get("workflow_type", "MEDICAL_CODING"),
            patient_context=context.get("patient_context", {}),
            clinical_summary=context.get("clinical_summary", ""),
            original_input=context.get("original_input", ""),
            attempted_output=attempted_output,
            reasoning_history=context.get("reasoning_history", []),
            compliance_failures=compliance_failures,
            confidence_scores=confidence_scores,
            suggested_action=suggested_action,
            sla_deadline=sla_deadline,
            audit_trail_id=context.get("audit_trail_id"),
            fraud_result=fraud_result,
            requires_compliance_officer=requires_compliance_officer,
        )

        await self.queue.enqueue(case)

        if self.message_bus:
            notification = EscalationNotification(
                case_id=case.case_id,
                priority=case.priority,
                trigger=case.trigger,
                summary=self.build_escalation_summary(case),
                sla_deadline=case.sla_deadline,
                action_required=suggested_action or "Human review required",
                requires_compliance_officer=case.requires_compliance_officer,
            )
            await self.message_bus.publish("escalations", notification.model_dump())
        log_extra = {"case_id": case.case_id, "trigger": trigger.value, "priority": priority.value}
        if trigger == EscalationTrigger.UPCODING_SUSPECTED:
            log_extra["event_type"] = "fraud_escalation"
            if fraud_alerts:
                log_extra["fraud_alerts"] = fraud_alerts
        self._logger.info("Escalation created", extra=log_extra)
        if trigger == EscalationTrigger.UPCODING_SUSPECTED:
            self._logger.info(
                "Compliance event logged: fraud escalation",
                extra={
                    "case_id": case.case_id,
                    "event_type": "compliance_event",
                    "trigger": trigger.value,
                    "priority": priority.value,
                },
            )
        return case

    def determine_priority(self, trigger: EscalationTrigger) -> EscalationPriority:
        if trigger in {EscalationTrigger.PROMPT_INJECTION_DETECTED, EscalationTrigger.UPCODING_SUSPECTED}:
            return EscalationPriority.CRITICAL
        if trigger in {
            EscalationTrigger.COMPLIANCE_HARD_FAIL,
            EscalationTrigger.CONFLICTING_INFORMATION,
            EscalationTrigger.MAX_RETRIES_EXCEEDED,
        }:
            return EscalationPriority.HIGH
        if trigger in {
            EscalationTrigger.LOW_CONFIDENCE,
            EscalationTrigger.EDGE_CASE_UNRESOLVED,
            EscalationTrigger.MISSING_CRITICAL_DATA,
            EscalationTrigger.AGENT_ERROR,
        }:
            return EscalationPriority.MEDIUM
        return EscalationPriority.LOW

    def calculate_sla_deadline(self, priority: EscalationPriority) -> datetime:
        now = datetime.now(timezone.utc)
        if priority == EscalationPriority.CRITICAL:
            return now + timedelta(hours=1)
        if priority == EscalationPriority.HIGH:
            return now + timedelta(hours=4)
        if priority == EscalationPriority.MEDIUM:
            return now + timedelta(hours=24)
        return now + timedelta(hours=72)

    def build_suggested_action(self, trigger: EscalationTrigger, context: Dict[str, Any]) -> str:
        condition = context.get("condition", "the condition")
        detail = context.get("detail", "")
        fraud_alerts = context.get("fraud_alerts") if isinstance(context, dict) else None
        fraud_result = context.get("fraud_result") if isinstance(context, dict) else None
        first_alert = (fraud_alerts or [None])[0] if fraud_alerts else None
        expected = None
        evidence_gap = None
        financial = None
        code_flagged = None
        if first_alert:
            expected = first_alert.get("expected_code")
            evidence_gap = first_alert.get("evidence_gap") or first_alert.get("documentation_reference")
            financial = first_alert.get("financial_impact")
            code_flagged = first_alert.get("code_involved")
        if trigger == EscalationTrigger.LOW_CONFIDENCE:
            conf = context.get("confidence", "<unknown>")
            return (
                f"Review coding decision for {condition}. AI confidence was {conf}. "
                "Verify code selection against clinical documentation."
            )
        if trigger == EscalationTrigger.CONFLICTING_INFORMATION:
            return (
                f"Documentation contains conflicting statements about {condition}. "
                f"{detail or 'Please clarify with provider.'}"
            )
        if trigger == EscalationTrigger.MISSING_CRITICAL_DATA:
            return f"Laterality or specificity missing for {condition}. Query provider for clarification."
        if trigger == EscalationTrigger.EDGE_CASE_UNRESOLVED:
            return f"Edge case unresolved for {condition}. Provide human judgment."
        if trigger == EscalationTrigger.UPCODING_SUSPECTED:
            parts = [f"Potential upcoding detected for {code_flagged or condition}."]
            if expected:
                parts.append(f"Likely correct code: {expected}.")
            if evidence_gap:
                parts.append(f"Evidence gap: {evidence_gap}.")
            if financial is not None:
                parts.append(f"Estimated financial impact: ${financial:.2f}.")
            if fraud_result and isinstance(fraud_result, dict):
                risk = fraud_result.get("risk_level") or fraud_result.get("overall_risk_score")
                if risk:
                    parts.append(f"Risk: {risk}.")
            parts.append("Perform audit against clinical documentation and payer policy.")
            return " ".join(parts)
        if trigger == EscalationTrigger.PROMPT_INJECTION_DETECTED:
            return "Prompt injection or security threat detected. Do not proceed; sanitize input."
        if trigger == EscalationTrigger.KNOWLEDGE_STALENESS:
            return "Knowledge base may be outdated for DOS. Verify guidelines and payer policies."
        if trigger == EscalationTrigger.MULTI_PAYER_CONFLICT:
            return "Coordination of benefits conflict. Resolve payer sequencing before coding."
        if trigger == EscalationTrigger.MAX_RETRIES_EXCEEDED:
            return "Max retries exceeded without compliant output. Human review required."
        if trigger == EscalationTrigger.COMPLIANCE_HARD_FAIL:
            return "Guardrail hard-fail after retries. Human override needed."
        if trigger == EscalationTrigger.AGENT_ERROR:
            return "Agent error/timeout occurred. Investigate logs and retry manually."
        return "Manual review requested."

    def build_escalation_summary(self, case: EscalationCase) -> str:
        attempted = "yes" if case.attempted_output else "no"
        failures = (
            ", ".join(f.get("check_id", "unknown") for f in case.compliance_failures)
            if case.compliance_failures
            else "none"
        )
        if case.trigger == EscalationTrigger.UPCODING_SUSPECTED and case.fraud_result:
            alerts = case.fraud_result.get("alerts", []) if isinstance(case.fraud_result, dict) else []
            first = alerts[0] if alerts else {}
            fraud_type = first.get("fraud_type", "UPCODING")
            code = first.get("code_involved", "UNKNOWN")
            evidence = first.get("documentation_reference") or first.get("evidence_gap") or "Not provided"
            expected = first.get("expected_code", "Unknown")
            financial = first.get("financial_impact")
            fin_str = f"${financial:.2f}" if isinstance(financial, (int, float)) else "$Unknown"
            return (
                f"FRAUD ALERT: Potential {fraud_type} detected for code {code} | "
                f"Documentation states: {evidence} | Expected code: {expected} | "
                f"Estimated financial impact: {fin_str} | Suggested: {case.suggested_action}"
            )
        return (
            f"Escalation {case.case_id} | Trigger: {case.trigger.value} | Priority: {case.priority.value} | "
            f"Attempted output: {attempted} | Failures: {failures} | Suggested: {case.suggested_action}"
        )

    async def check_sla_compliance(self) -> List[EscalationCase]:
        return await self.queue.get_overdue_cases()

    async def get_queue_stats(self) -> Dict[str, Any]:
        pending = await self.queue.get_pending_count()
        overdue = await self.queue.get_overdue_cases()
        resolved = await self.queue.get_cases_by_status(EscalationStatus.RESOLVED)
        avg_resolution = None
        if resolved:
            durations = [
                (c.resolved_at - c.created_at).total_seconds()
                for c in resolved
                if c.resolved_at is not None
            ]
            if durations:
                avg_resolution = sum(durations) / len(durations)
        return {
            "pending": {k.value: v for k, v in pending.items()},
            "overdue_count": len(overdue),
            "avg_resolution_seconds": avg_resolution,
        }


def should_escalate(
    confidence_score: float,
    retry_count: int,
    compliance_result: Optional[Dict[str, Any]],
    max_retries: int = GUARDRAIL_MAX_RETRIES,
    confidence_threshold: float = GUARDRAIL_ESCALATION_THRESHOLD,
) -> Tuple[bool, Optional[EscalationTrigger]]:
    """Utility for other agents to determine escalation need."""

    if confidence_score < confidence_threshold:
        return True, EscalationTrigger.LOW_CONFIDENCE
    if retry_count >= max_retries:
        return True, EscalationTrigger.MAX_RETRIES_EXCEEDED
    hard_fails = False
    if compliance_result is not None:
        hard_fails = compliance_result.get("hard_fails", 0) or compliance_result.get("has_hard_fail", False)
    if bool(hard_fails):
        return True, EscalationTrigger.COMPLIANCE_HARD_FAIL
    return False, None


__all__ = [
    "EscalationTrigger",
    "EscalationPriority",
    "EscalationStatus",
    "EscalationCase",
    "EscalationNotification",
    "EscalationQueue",
    "EscalationAgent",
    "should_escalate",
]
