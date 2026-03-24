import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from medi_comply.agents.escalation_agent import (
    EscalationAgent,
    EscalationCase,
    EscalationPriority,
    EscalationQueue,
    EscalationStatus,
    EscalationTrigger,
    should_escalate,
)


class DummyBus:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, topic: str, payload):
        self.published.append((topic, payload))


def build_case(priority: EscalationPriority, status: EscalationStatus = EscalationStatus.PENDING) -> EscalationCase:
    now = datetime.now(timezone.utc)
    return EscalationCase(
        case_id="case-1",
        created_at=now,
        trigger=EscalationTrigger.AGENT_ERROR,
        priority=priority,
        status=status,
        source_agent="Tester",
        workflow_type="MEDICAL_CODING",
        patient_context={},
        clinical_summary="",
        original_input="",
        attempted_output=None,
        reasoning_history=[],
        compliance_failures=[],
        confidence_scores={},
        suggested_action="",
        sla_deadline=now + timedelta(hours=1),
    )


# Priority determination ----------------------------------------------------

def test_priority_determination_critical():
    agent = EscalationAgent()
    assert agent.determine_priority(EscalationTrigger.PROMPT_INJECTION_DETECTED) == EscalationPriority.CRITICAL
    assert agent.determine_priority(EscalationTrigger.UPCODING_SUSPECTED) == EscalationPriority.CRITICAL


def test_priority_determination_high():
    agent = EscalationAgent()
    assert agent.determine_priority(EscalationTrigger.COMPLIANCE_HARD_FAIL) == EscalationPriority.HIGH
    assert agent.determine_priority(EscalationTrigger.CONFLICTING_INFORMATION) == EscalationPriority.HIGH
    assert agent.determine_priority(EscalationTrigger.MAX_RETRIES_EXCEEDED) == EscalationPriority.HIGH


def test_priority_determination_medium():
    agent = EscalationAgent()
    assert agent.determine_priority(EscalationTrigger.LOW_CONFIDENCE) == EscalationPriority.MEDIUM
    assert agent.determine_priority(EscalationTrigger.EDGE_CASE_UNRESOLVED) == EscalationPriority.MEDIUM
    assert agent.determine_priority(EscalationTrigger.MISSING_CRITICAL_DATA) == EscalationPriority.MEDIUM


def test_priority_determination_low():
    agent = EscalationAgent()
    assert agent.determine_priority(EscalationTrigger.KNOWLEDGE_STALENESS) == EscalationPriority.LOW
    assert agent.determine_priority(EscalationTrigger.MULTI_PAYER_CONFLICT) == EscalationPriority.LOW


# SLA calculation -----------------------------------------------------------

def test_sla_critical_1_hour():
    agent = EscalationAgent()
    deadline = agent.calculate_sla_deadline(EscalationPriority.CRITICAL)
    delta = deadline - datetime.now(timezone.utc)
    assert abs(delta.total_seconds() - 3600) < 120


def test_sla_high_4_hours():
    agent = EscalationAgent()
    deadline = agent.calculate_sla_deadline(EscalationPriority.HIGH)
    delta = deadline - datetime.now(timezone.utc)
    assert abs(delta.total_seconds() - 4 * 3600) < 120


def test_sla_medium_24_hours():
    agent = EscalationAgent()
    deadline = agent.calculate_sla_deadline(EscalationPriority.MEDIUM)
    delta = deadline - datetime.now(timezone.utc)
    assert abs(delta.total_seconds() - 24 * 3600) < 300


def test_sla_low_72_hours():
    agent = EscalationAgent()
    deadline = agent.calculate_sla_deadline(EscalationPriority.LOW)
    delta = deadline - datetime.now(timezone.utc)
    assert abs(delta.total_seconds() - 72 * 3600) < 300


# EscalationQueue -----------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_and_dequeue():
    queue = EscalationQueue()
    case = build_case(EscalationPriority.MEDIUM)
    await queue.enqueue(case)
    dequeued = await queue.dequeue()
    assert dequeued is not None
    assert dequeued.case_id == case.case_id


@pytest.mark.asyncio
async def test_priority_ordering():
    queue = EscalationQueue()
    low = build_case(EscalationPriority.LOW)
    high = build_case(EscalationPriority.CRITICAL)
    await queue.enqueue(low)
    await queue.enqueue(high)
    first = await queue.dequeue()
    assert first is not None
    assert first.priority == EscalationPriority.CRITICAL


@pytest.mark.asyncio
async def test_get_case_by_id():
    queue = EscalationQueue()
    case = build_case(EscalationPriority.MEDIUM)
    await queue.enqueue(case)
    fetched = await queue.get_case(case.case_id)
    assert fetched is not None
    assert fetched.case_id == case.case_id


@pytest.mark.asyncio
async def test_update_status():
    queue = EscalationQueue()
    case = build_case(EscalationPriority.MEDIUM)
    await queue.enqueue(case)
    await queue.update_status(case.case_id, EscalationStatus.ASSIGNED)
    updated = await queue.get_case(case.case_id)
    assert updated is not None
    assert updated.status == EscalationStatus.ASSIGNED
    await queue.update_status(case.case_id, EscalationStatus.IN_REVIEW)
    updated = await queue.get_case(case.case_id)
    assert updated is not None
    assert updated.status == EscalationStatus.IN_REVIEW


@pytest.mark.asyncio
async def test_resolve_case():
    queue = EscalationQueue()
    case = build_case(EscalationPriority.MEDIUM)
    await queue.enqueue(case)
    await queue.resolve_case(case.case_id, {"note": "fixed"}, resolved_by="tester")
    resolved = await queue.get_case(case.case_id)
    assert resolved is not None
    assert resolved.status == EscalationStatus.RESOLVED
    assert resolved.resolution is not None
    assert resolved.resolution["resolved_by"] == "tester"


@pytest.mark.asyncio
async def test_get_pending_count():
    queue = EscalationQueue()
    await queue.enqueue(build_case(EscalationPriority.LOW))
    await queue.enqueue(build_case(EscalationPriority.HIGH))
    counts = await queue.get_pending_count()
    assert counts[EscalationPriority.LOW] == 1
    assert counts[EscalationPriority.HIGH] == 1


@pytest.mark.asyncio
async def test_get_overdue_cases():
    queue = EscalationQueue()
    past_case = build_case(EscalationPriority.MEDIUM)
    past_case.sla_deadline = datetime.now(timezone.utc) - timedelta(hours=1)
    await queue.enqueue(past_case)
    overdue = await queue.get_overdue_cases()
    assert len(overdue) == 1
    assert overdue[0].case_id == past_case.case_id


@pytest.mark.asyncio
async def test_dequeue_by_priority():
    queue = EscalationQueue()
    low = build_case(EscalationPriority.LOW)
    high = build_case(EscalationPriority.HIGH)
    await queue.enqueue(low)
    await queue.enqueue(high)
    only_high = await queue.dequeue(priority=EscalationPriority.HIGH)
    assert only_high is not None
    assert only_high.priority == EscalationPriority.HIGH
    remaining = await queue.dequeue()
    assert remaining is not None
    assert remaining.priority == EscalationPriority.LOW


# EscalationAgent -----------------------------------------------------------

@pytest.mark.asyncio
async def test_escalate_low_confidence():
    bus = DummyBus()
    agent = EscalationAgent(message_bus=bus)
    case = await agent.escalate(
        trigger=EscalationTrigger.LOW_CONFIDENCE,
        source_agent="MedicalCodingAgent",
        context={"workflow_type": "MEDICAL_CODING", "confidence": 0.5, "clinical_summary": ""},
        attempted_output={"codes": []},
        compliance_failures=[{"check_id": "RISK"}],
        confidence_scores={"overall_confidence": 0.5},
    )
    assert case.priority == EscalationPriority.MEDIUM
    assert case.trigger == EscalationTrigger.LOW_CONFIDENCE
    assert bus.published  # notification was sent


@pytest.mark.asyncio
async def test_escalate_compliance_failure():
    agent = EscalationAgent()
    case = await agent.escalate(
        trigger=EscalationTrigger.COMPLIANCE_HARD_FAIL,
        source_agent="ComplianceGuardAgent",
        context={"workflow_type": "MEDICAL_CODING"},
        attempted_output=None,
        compliance_failures=[{"check_id": "HARD"}],
        confidence_scores={},
    )
    assert case.priority == EscalationPriority.HIGH
    assert case.compliance_failures[0]["check_id"] == "HARD"


@pytest.mark.asyncio
async def test_escalate_conflicting_info():
    agent = EscalationAgent()
    case = await agent.escalate(
        trigger=EscalationTrigger.CONFLICTING_INFORMATION,
        source_agent="ComplianceGuardAgent",
        context={"condition": "asthma", "detail": "notes conflict"},
        attempted_output=None,
        compliance_failures=[],
        confidence_scores={},
    )
    assert "conflicting" in case.suggested_action.lower()


@pytest.mark.asyncio
async def test_escalate_prompt_injection():
    agent = EscalationAgent()
    case = await agent.escalate(
        trigger=EscalationTrigger.PROMPT_INJECTION_DETECTED,
        source_agent="ComplianceGuardAgent",
        context={},
        attempted_output=None,
        compliance_failures=[],
        confidence_scores={},
    )
    assert case.priority == EscalationPriority.CRITICAL


def test_suggested_action_low_confidence():
    agent = EscalationAgent()
    action = agent.build_suggested_action(EscalationTrigger.LOW_CONFIDENCE, {"condition": "COPD", "confidence": 0.6})
    assert "confidence" in action.lower()
    assert "copd" in action.lower()


def test_suggested_action_missing_laterality():
    agent = EscalationAgent()
    action = agent.build_suggested_action(EscalationTrigger.MISSING_CRITICAL_DATA, {"condition": "fx arm"})
    assert "laterality" in action.lower()


@pytest.mark.asyncio
async def test_escalation_summary_readable():
    agent = EscalationAgent()
    case = await agent.escalate(
        trigger=EscalationTrigger.LOW_CONFIDENCE,
        source_agent="MedicalCodingAgent",
        context={},
        attempted_output={},
        compliance_failures=[],
        confidence_scores={},
    )
    summary = agent.build_escalation_summary(case)
    assert str(case.case_id) in summary
    assert case.trigger.value in summary


# should_escalate -----------------------------------------------------------

def test_should_escalate_low_confidence():
    flag, trigger = should_escalate(0.5, retry_count=0, compliance_result={}, max_retries=3, confidence_threshold=0.7)
    assert flag is True
    assert trigger == EscalationTrigger.LOW_CONFIDENCE


def test_should_not_escalate_high_confidence():
    flag, trigger = should_escalate(0.95, retry_count=0, compliance_result={}, max_retries=3, confidence_threshold=0.7)
    assert flag is False
    assert trigger is None


def test_should_escalate_max_retries():
    flag, trigger = should_escalate(0.9, retry_count=3, compliance_result={}, max_retries=3, confidence_threshold=0.7)
    assert flag is True
    assert trigger == EscalationTrigger.MAX_RETRIES_EXCEEDED


def test_should_escalate_hard_fail():
    compliance = {"hard_fails": 1}
    flag, trigger = should_escalate(0.9, retry_count=0, compliance_result=compliance, max_retries=3, confidence_threshold=0.7)
    assert flag is True
    assert trigger == EscalationTrigger.COMPLIANCE_HARD_FAIL


def test_should_not_escalate_all_good():
    compliance = {"hard_fails": 0}
    flag, trigger = should_escalate(0.9, retry_count=0, compliance_result=compliance, max_retries=3, confidence_threshold=0.7)
    assert flag is False
    assert trigger is None
