"""
MEDI-COMPLY — Core module test suite.

Covers state machine, message bus, agent base, and schema instantiation /
serialization.  All tests are async-compatible via ``pytest-asyncio``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Core imports
# ---------------------------------------------------------------------------
from medi_comply.core.state_machine import (
    AgentState,
    InvalidTransitionError,
    StateMachine,
    VALID_TRANSITIONS,
)
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.core.message_bus import AsyncMessageBus, MessageDeliveryError
from medi_comply.core.agent_base import BaseAgent, AgentTimeoutError
from medi_comply.schemas.common import (
    AgentType,
    ConfidenceLevel,
    ResponseStatus,
    RiskLevel,
    WorkflowType,
    DecisionType,
    BaseTimestampedModel,
)

# Schema imports
from medi_comply.schemas.clinical import (
    ClinicalDocument,
    ClinicalEntity,
    ExtractedCondition,
    ExtractedMedication,
    ExtractedProcedure,
    SourceEvidence,
)
from medi_comply.schemas.coding import (
    CodeAssignment,
    CodeCandidate,
    CodingResult,
    CPTCode,
    ICD10Code,
    ReasoningStep,
)
from medi_comply.schemas.compliance import (
    ComplianceCheck,
    ComplianceResult,
    GuardrailDecision,
)
from medi_comply.schemas.audit import (
    AuditEntry,
    AuditRecord,
    EvidenceLink,
    ReasoningChain,
    RiskScore,
)
from medi_comply.schemas.claims import (
    AdjudicationResult,
    ClaimData,
    ClaimLine,
    DenialReason,
)
from medi_comply.schemas.prior_auth import (
    AuthDecision,
    AuthRequest,
    ClinicalCriteria,
)


# ===================================================================
# Helpers
# ===================================================================


class EchoAgent(BaseAgent):
    """Minimal concrete agent that echoes the payload back."""

    async def process(self, message: AgentMessage) -> AgentResponse:
        """Return the incoming payload as-is."""
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload=message.payload,
            confidence_score=0.95,
            reasoning=["Echoed input payload"],
            trace_id=message.trace_id,
        )


class SlowAgent(BaseAgent):
    """Agent that deliberately sleeps longer than its timeout."""

    async def process(self, message: AgentMessage) -> AgentResponse:
        """Sleep forever (in practice, longer than any reasonable timeout)."""
        await asyncio.sleep(999)
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status=ResponseStatus.SUCCESS,
            payload={},
            trace_id=message.trace_id,
        )


# ===================================================================
# 1. State Machine tests
# ===================================================================


class TestStateMachine:
    """Tests for AgentState transitions and StateMachine enforcement."""

    def test_valid_transition_idle_to_thinking(self) -> None:
        """IDLE -> THINKING should succeed."""
        sm = StateMachine()
        result = sm.transition(AgentState.THINKING)
        assert result == AgentState.THINKING

    def test_valid_transition_chain(self) -> None:
        """Walk the happy path: IDLE -> THINKING -> PROPOSING -> VALIDATING -> APPROVED -> COMPLETED."""
        sm = StateMachine()
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.PROPOSING)
        sm.transition(AgentState.VALIDATING)
        sm.transition(AgentState.APPROVED)
        result = sm.transition(AgentState.COMPLETED)
        assert result == AgentState.COMPLETED

    def test_invalid_transition_raises(self) -> None:
        """IDLE -> COMPLETED must raise InvalidTransitionError."""
        sm = StateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition(AgentState.COMPLETED)

    def test_invalid_transition_from_thinking_to_approved(self) -> None:
        """THINKING -> APPROVED is not allowed."""
        sm = StateMachine()
        sm.transition(AgentState.THINKING)
        with pytest.raises(InvalidTransitionError):
            sm.transition(AgentState.APPROVED)

    def test_retry_increments_counter(self) -> None:
        """Each RETRY transition should increment retry_count."""
        sm = StateMachine(max_retries=5)
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.PROPOSING)
        sm.transition(AgentState.VALIDATING)
        sm.transition(AgentState.RETRY)
        assert sm.retry_count == 1

    def test_retry_exceeds_max_escalates(self) -> None:
        """Exceeding max_retries forces ESCALATED instead of back to THINKING."""
        sm = StateMachine(max_retries=1)
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.PROPOSING)
        sm.transition(AgentState.VALIDATING)
        # First retry (count goes to 1, which equals max — still allowed)
        sm.transition(AgentState.RETRY)
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.PROPOSING)
        sm.transition(AgentState.VALIDATING)
        # Second retry (count goes to 2 > max_retries=1) → escalated
        result = sm.transition(AgentState.RETRY)
        assert result == AgentState.ESCALATED

    def test_transition_history_tracking(self) -> None:
        """Every transition should be recorded with timestamps."""
        sm = StateMachine()
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.PROPOSING)
        assert len(sm.history) == 2
        assert sm.history[0].from_state == AgentState.IDLE
        assert sm.history[0].to_state == AgentState.THINKING
        assert sm.history[1].from_state == AgentState.THINKING
        assert sm.history[1].to_state == AgentState.PROPOSING

    def test_can_transition(self) -> None:
        """can_transition should reflect the valid-transitions map."""
        sm = StateMachine()
        assert sm.can_transition(AgentState.THINKING) is True
        assert sm.can_transition(AgentState.COMPLETED) is False

    def test_error_to_escalated(self) -> None:
        """ERROR -> ESCALATED should be valid."""
        sm = StateMachine()
        sm.transition(AgentState.THINKING)
        sm.transition(AgentState.ERROR)
        result = sm.transition(AgentState.ESCALATED)
        assert result == AgentState.ESCALATED

    def test_reset(self) -> None:
        """reset() should return machine to IDLE with empty history."""
        sm = StateMachine()
        sm.transition(AgentState.THINKING)
        sm.reset()
        assert sm.current_state == AgentState.IDLE
        assert len(sm.history) == 0


# ===================================================================
# 2. Message bus tests
# ===================================================================


class TestMessageBus:
    """Tests for AsyncMessageBus publish, subscribe, and request/response."""

    @pytest.mark.asyncio
    async def test_publish_and_subscribe(self) -> None:
        """Published messages should be receivable by subscribers."""
        bus = AsyncMessageBus()
        received: list[AgentMessage] = []

        async def handler(msg: AgentMessage) -> AgentResponse:
            received.append(msg)
            return AgentResponse(
                original_message_id=msg.message_id,
                from_agent="test_agent",
                status=ResponseStatus.SUCCESS,
                payload=msg.payload,
                trace_id=msg.trace_id,
            )

        bus.subscribe("test_agent", handler)

        msg = AgentMessage(
            from_agent="sender",
            to_agent="test_agent",
            action="ping",
            payload={"data": 42},
        )
        await bus.publish(msg)

        # Drain the queue through a direct call
        response = await bus.request_response(
            AgentMessage(
                from_agent="sender",
                to_agent="test_agent",
                action="ping2",
                payload={"data": 99},
            ),
            timeout_ms=5000,
        )
        assert response.status == ResponseStatus.SUCCESS
        assert response.payload == {"data": 99}

    @pytest.mark.asyncio
    async def test_request_response_success(self) -> None:
        """request_response should return the subscriber's response."""
        bus = AsyncMessageBus()

        async def handler(msg: AgentMessage) -> AgentResponse:
            return AgentResponse(
                original_message_id=msg.message_id,
                from_agent="responder",
                status=ResponseStatus.SUCCESS,
                payload={"answer": "hello"},
                confidence_score=0.9,
                trace_id=msg.trace_id,
            )

        bus.subscribe("responder", handler)

        msg = AgentMessage(
            from_agent="caller",
            to_agent="responder",
            action="greet",
        )
        response = await bus.request_response(msg, timeout_ms=5000)
        assert response.status == ResponseStatus.SUCCESS
        assert response.payload["answer"] == "hello"
        assert response.confidence_score == 0.9

    @pytest.mark.asyncio
    async def test_request_response_timeout(self) -> None:
        """request_response should raise TimeoutError on slow handlers."""
        bus = AsyncMessageBus()

        async def slow_handler(msg: AgentMessage) -> AgentResponse:
            await asyncio.sleep(10)
            return AgentResponse(
                original_message_id=msg.message_id,
                from_agent="slow",
                status=ResponseStatus.SUCCESS,
                payload={},
                trace_id=msg.trace_id,
            )

        bus.subscribe("slow_agent", slow_handler)

        msg = AgentMessage(
            from_agent="caller",
            to_agent="slow_agent",
            action="wait",
        )
        with pytest.raises(asyncio.TimeoutError):
            await bus.request_response(msg, timeout_ms=100)

    @pytest.mark.asyncio
    async def test_request_response_no_subscriber(self) -> None:
        """request_response to an unknown agent should raise MessageDeliveryError."""
        bus = AsyncMessageBus()
        msg = AgentMessage(
            from_agent="caller",
            to_agent="nonexistent",
            action="ping",
        )
        with pytest.raises(MessageDeliveryError):
            await bus.request_response(msg, timeout_ms=1000)

    @pytest.mark.asyncio
    async def test_dead_letter_on_no_subscriber(self) -> None:
        """Failed delivery should add to the dead-letter queue."""
        bus = AsyncMessageBus()
        msg = AgentMessage(from_agent="a", to_agent="nobody", action="test")
        with pytest.raises(MessageDeliveryError):
            await bus.request_response(msg, timeout_ms=1000)
        assert len(bus.dead_letters) == 1
        assert bus.dead_letters[0]["reason"] == "no_subscriber"

    @pytest.mark.asyncio
    async def test_message_history(self) -> None:
        """All published messages should be in message_history."""
        bus = AsyncMessageBus()

        async def handler(msg: AgentMessage) -> AgentResponse:
            return AgentResponse(
                original_message_id=msg.message_id,
                from_agent="h",
                status=ResponseStatus.SUCCESS,
                payload={},
                trace_id=msg.trace_id,
            )

        bus.subscribe("target", handler)
        msg = AgentMessage(from_agent="s", to_agent="target", action="x")
        await bus.request_response(msg, timeout_ms=5000)
        assert len(bus.message_history) >= 1


# ===================================================================
# 3. Agent base tests
# ===================================================================


class TestBaseAgent:
    """Tests for BaseAgent state transitions, confidence, and timeout."""

    def test_initial_state(self) -> None:
        """A new agent should start in IDLE state."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.DOMAIN_EXPERT)
        assert agent.state == AgentState.IDLE

    def test_transition_state(self) -> None:
        """transition_state should update the agent's state."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.DOMAIN_EXPERT)
        agent.transition_state(AgentState.THINKING)
        assert agent.state == AgentState.THINKING

    def test_invalid_agent_transition(self) -> None:
        """Invalid transition should raise InvalidTransitionError."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.DOMAIN_EXPERT)
        with pytest.raises(InvalidTransitionError):
            agent.transition_state(AgentState.COMPLETED)

    def test_confidence_scoring(self) -> None:
        """set_confidence should update both score and level."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.VALIDATOR)
        agent.set_confidence(0.92)
        assert agent.confidence == 0.92
        assert agent.confidence_level == ConfidenceLevel.VERY_HIGH

    def test_confidence_out_of_range(self) -> None:
        """set_confidence with value > 1 should raise ValueError."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.VALIDATOR)
        with pytest.raises(ValueError):
            agent.set_confidence(1.5)

    @pytest.mark.asyncio
    async def test_handle_returns_response(self) -> None:
        """handle() should invoke process() and return a response."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.DOMAIN_EXPERT)
        msg = AgentMessage(
            from_agent="tester",
            to_agent="echo",
            action="test",
            payload={"key": "value"},
        )
        response = await agent.handle(msg)
        assert response.status == ResponseStatus.SUCCESS
        assert response.payload == {"key": "value"}

    @pytest.mark.asyncio
    async def test_handle_timeout(self) -> None:
        """handle() should return FAILURE on timeout."""
        agent = SlowAgent(
            agent_name="slow",
            agent_type=AgentType.DOMAIN_EXPERT,
            timeout_seconds=0.1,
        )
        msg = AgentMessage(
            from_agent="tester",
            to_agent="slow",
            action="wait",
        )
        response = await agent.handle(msg)
        assert response.status == ResponseStatus.FAILURE
        assert any("Timeout" in e for e in response.errors)

    def test_log_agent_action(self) -> None:
        """log_agent_action should append to the action log."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.OBSERVER)
        agent.log_agent_action("test_action", "did something", {"ref": "123"})
        assert len(agent.action_log) == 1
        assert agent.action_log[0]["action"] == "test_action"

    def test_repr(self) -> None:
        """__repr__ should include key agent attributes."""
        agent = EchoAgent(agent_name="echo", agent_type=AgentType.SUPERVISOR)
        r = repr(agent)
        assert "echo" in r
        assert "SUPERVISOR" in r
        assert "IDLE" in r


# ===================================================================
# 4. Schema instantiation & serialization tests
# ===================================================================


class TestSchemas:
    """Verify all schema models can be instantiated and round-tripped."""

    def test_source_evidence(self) -> None:
        """SourceEvidence with valid data."""
        se = SourceEvidence(
            section="HPI",
            page=1,
            line=10,
            char_offset=(5, 25),
            surrounding_text="Patient reports chest pain",
            confidence=0.95,
        )
        data = se.model_dump()
        assert data["section"] == "HPI"
        assert data["char_offset"] == (5, 25)

    def test_clinical_document(self) -> None:
        """ClinicalDocument with nested entities."""
        doc = ClinicalDocument(
            document_type="Discharge Summary",
            patient_id="P001",
            raw_text="Sample text",
            conditions=[
                ExtractedCondition(
                    text="Type 2 Diabetes",
                    entity_type="CONDITION",
                    icd10_code="E11.65",
                    confidence=0.9,
                )
            ],
        )
        json_str = doc.model_dump_json()
        assert "Type 2 Diabetes" in json_str
        assert "E11.65" in json_str

    def test_coding_result(self) -> None:
        """CodingResult with assignments and reasoning."""
        result = CodingResult(
            document_id=uuid.uuid4(),
            overall_confidence=0.88,
            assignments=[
                CodeAssignment(
                    code="E11.65",
                    code_system="ICD-10-CM",
                    description="Type 2 DM with hyperglycemia",
                    confidence=0.88,
                    entity_text="Type 2 Diabetes",
                    reasoning_steps=[
                        ReasoningStep(
                            step_number=1,
                            action="extract_entity",
                            detail="Found diabetes mention",
                            evidence_ref="ev-001",
                        )
                    ],
                )
            ],
        )
        data = result.model_dump()
        assert len(data["assignments"]) == 1
        assert data["overall_confidence"] == 0.88

    def test_compliance_result(self) -> None:
        """ComplianceResult with guardrail decision."""
        cr = ComplianceResult(
            check_id=uuid.uuid4(),
            decision=GuardrailDecision.SOFT_FAIL,
            findings=["Code specificity insufficient"],
            confidence=0.7,
            risk_level=RiskLevel.MEDIUM,
        )
        assert cr.decision == GuardrailDecision.SOFT_FAIL
        assert cr.confidence_level == ConfidenceLevel.HIGH

    def test_audit_record(self) -> None:
        """AuditRecord with entries and risk score."""
        ar = AuditRecord(
            trace_id="trace-001",
            workflow_type="CLINICAL_CODING",
            entries=[
                AuditEntry(
                    agent_id="a1",
                    agent_name="CodingAgent",
                    action="code_assignment",
                    detail="Assigned E11.65",
                    trace_id="trace-001",
                    risk_score=RiskScore(
                        score=0.3,
                        level=RiskLevel.LOW,
                        factors=["low complexity"],
                    ),
                )
            ],
            overall_risk=RiskScore(
                score=0.3, level=RiskLevel.LOW, factors=["low complexity"]
            ),
        )
        data = ar.model_dump()
        assert len(data["entries"]) == 1
        assert data["overall_risk"]["score"] == 0.3

    def test_claim_data(self) -> None:
        """ClaimData with claim lines."""
        claim = ClaimData(
            claim_number="CLM-001",
            patient_id="P001",
            provider_npi="1234567890",
            payer_id="PAYER-A",
            total_charge=1500.0,
            lines=[
                ClaimLine(
                    line_number=1,
                    procedure_code="99213",
                    diagnosis_codes=["E11.65"],
                    charge_amount=250.0,
                    service_date=date(2026, 3, 15),
                )
            ],
        )
        json_str = claim.model_dump_json()
        assert "CLM-001" in json_str

    def test_auth_request(self) -> None:
        """AuthRequest with clinical criteria."""
        req = AuthRequest(
            request_number="PA-001",
            patient_id="P001",
            provider_npi="1234567890",
            payer_id="PAYER-A",
            procedure_codes=["27447"],
            diagnosis_codes=["M17.11"],
            criteria=[
                ClinicalCriteria(
                    criterion_name="Failed conservative therapy",
                    description="6+ months of non-surgical treatment",
                    is_met=True,
                    confidence=0.92,
                )
            ],
        )
        data = req.model_dump()
        assert len(data["criteria"]) == 1
        assert data["criteria"][0]["is_met"] is True

    def test_icd10_code_normalization(self) -> None:
        """ICD-10 code should be uppercased / trimmed."""
        code = ICD10Code(code=" e11.65 ", description="Type 2 DM with hyperglycemia")
        assert code.code == "E11.65"

    def test_guardrail_decision_values(self) -> None:
        """GuardrailDecision should have all five members."""
        assert set(GuardrailDecision) == {
            GuardrailDecision.PASS,
            GuardrailDecision.SOFT_FAIL,
            GuardrailDecision.HARD_FAIL,
            GuardrailDecision.ESCALATE,
            GuardrailDecision.BLOCK_AND_ALERT,
        }

    def test_confidence_level_from_score(self) -> None:
        """ConfidenceLevel.from_score mapping should be correct."""
        assert ConfidenceLevel.from_score(0.1) == ConfidenceLevel.VERY_LOW
        assert ConfidenceLevel.from_score(0.3) == ConfidenceLevel.LOW
        assert ConfidenceLevel.from_score(0.5) == ConfidenceLevel.MEDIUM
        assert ConfidenceLevel.from_score(0.7) == ConfidenceLevel.HIGH
        assert ConfidenceLevel.from_score(0.95) == ConfidenceLevel.VERY_HIGH

    def test_base_timestamped_model_touch(self) -> None:
        """touch() should update the updated_at field."""
        model = BaseTimestampedModel()
        old_ts = model.updated_at
        model.touch()
        assert model.updated_at >= old_ts
