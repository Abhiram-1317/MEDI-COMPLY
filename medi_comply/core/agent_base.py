"""
MEDI-COMPLY — Abstract base agent.

Every concrete agent in the system inherits from :class:`BaseAgent`, which
provides identity (UUID, name, type), state-machine integration, structured
logging, confidence scoring, and timeout handling.
"""

from __future__ import annotations

import abc
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from medi_comply.core.logger import get_logger, log_action
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.core.state_machine import StateMachine
from medi_comply.schemas.common import AgentState, AgentType, ConfidenceLevel, ResponseStatus


class AgentTimeoutError(Exception):
    """Raised when an agent's processing exceeds its timeout."""

    def __init__(self, agent_name: str, timeout_seconds: float) -> None:
        self.agent_name = agent_name
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Agent '{agent_name}' timed out after {timeout_seconds}s"
        )


class BaseAgent(abc.ABC):
    """Abstract base class for all MEDI-COMPLY agents.

    Subclasses must implement :meth:`process` to handle incoming messages.

    Parameters
    ----------
    agent_name:
        Human-readable name for the agent.
    agent_type:
        The functional role of the agent (see :class:`AgentType`).
    max_retries:
        Number of retries the state machine allows before escalation.
    timeout_seconds:
        Maximum time (in seconds) a single ``process`` call may take.
    """

    def __init__(
        self,
        agent_name: str,
        agent_type: AgentType,
        max_retries: int = 3,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.agent_id: str = str(uuid.uuid4())
        self.agent_name: str = agent_name
        self.agent_type: AgentType = agent_type
        self.timeout_seconds: float = timeout_seconds

        self._state_machine: StateMachine = StateMachine(
            initial_state=AgentState.IDLE,
            max_retries=max_retries,
        )
        self._confidence: float = 0.0
        self._action_log: list[dict[str, Any]] = []
        self._logger = get_logger(agent_name=self.agent_name)

    # -- Properties --------------------------------------------------------

    @property
    def state(self) -> AgentState:
        """Current lifecycle state of this agent."""
        return self._state_machine.current_state

    @property
    def confidence(self) -> float:
        """Latest self-assessed confidence score (0.0 – 1.0)."""
        return self._confidence

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier derived from the raw score."""
        return ConfidenceLevel.from_score(self._confidence)

    @property
    def action_log(self) -> list[dict[str, Any]]:
        """Return a copy of the structured action audit log."""
        return list(self._action_log)

    @property
    def transition_history(self) -> list:
        """Proxy to the underlying state-machine's transition records."""
        return self._state_machine.history

    # -- State management --------------------------------------------------

    def transition_state(self, new_state: AgentState, metadata: Optional[dict] = None) -> AgentState:
        """Attempt a state transition.

        Delegates to the internal :class:`StateMachine` and emits a
        structured log entry.

        Parameters
        ----------
        new_state:
            Target state.
        metadata:
            Optional context data attached to the transition record.

        Returns
        -------
        AgentState
            The resulting state after the transition.
        """
        previous = self.state
        result = self._state_machine.transition(new_state, metadata=metadata)
        log_action(
            self._logger,
            action="state_transition",
            message=f"{previous.value} -> {result.value}",
            extra_data={"agent_id": self.agent_id, "metadata": metadata},
        )
        return result

    # -- Confidence --------------------------------------------------------

    def set_confidence(self, score: float) -> None:
        """Set the agent's current confidence score.

        Parameters
        ----------
        score:
            A float between 0.0 and 1.0.

        Raises
        ------
        ValueError
            If *score* is outside the [0, 1] range.
        """
        if not 0.0 <= score <= 1.0:
            raise ValueError(f"Confidence score must be in [0, 1], got {score}")
        self._confidence = score

    # -- Audit logging -----------------------------------------------------

    def log_agent_action(
        self,
        action: str,
        detail: str,
        evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        """Record an action in the agent's audit trail.

        Parameters
        ----------
        action:
            Short action tag (e.g. ``"code_lookup"``).
        detail:
            Human-readable description of what happened.
        evidence:
            Optional supporting evidence attached to the record.
        """
        entry: dict[str, Any] = {
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "action": action,
            "detail": detail,
            "evidence": evidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": self.state.value,
        }
        self._action_log.append(entry)
        log_action(
            self._logger,
            action=action,
            message=detail,
            extra_data=evidence,
        )

    # -- Processing with timeout -------------------------------------------

    async def handle(self, message: AgentMessage) -> AgentResponse:
        """Public entry point — wraps :meth:`process` with timeout handling.

        If the agent's processing exceeds ``timeout_seconds``, the agent
        transitions to ``ERROR`` and returns a failure response.

        Parameters
        ----------
        message:
            Incoming :class:`AgentMessage`.

        Returns
        -------
        AgentResponse
            The agent's structured response.
        """
        try:
            response = await asyncio.wait_for(
                self.process(message),
                timeout=self.timeout_seconds,
            )
            return response
        except asyncio.TimeoutError:
            self._state_machine.transition(AgentState.ERROR)
            log_action(
                self._logger,
                action="timeout",
                message=f"Processing timed out after {self.timeout_seconds}s",
                trace_id=message.trace_id,
            )
            return AgentResponse(
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status=ResponseStatus.FAILURE,
                payload={},
                confidence_score=0.0,
                reasoning=[],
                errors=[f"Timeout after {self.timeout_seconds}s"],
                trace_id=message.trace_id,
            )
        except Exception as exc:
            self._state_machine.transition(AgentState.ERROR)
            log_action(
                self._logger,
                action="processing_error",
                message=str(exc),
                trace_id=message.trace_id,
            )
            return AgentResponse(
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status=ResponseStatus.FAILURE,
                payload={},
                confidence_score=0.0,
                reasoning=[],
                errors=[str(exc)],
                trace_id=message.trace_id,
            )

    # -- Abstract method ---------------------------------------------------

    @abc.abstractmethod
    async def process(self, message: AgentMessage) -> AgentResponse:
        """Process an incoming message and return a response.

        Concrete subclasses **must** implement this method.

        Parameters
        ----------
        message:
            The :class:`AgentMessage` to process.

        Returns
        -------
        AgentResponse
            The structured response from the agent.
        """
        ...

    # -- Dunder ------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"id={self.agent_id!r}, "
            f"name={self.agent_name!r}, "
            f"type={self.agent_type.value}, "
            f"state={self.state.value}, "
            f"confidence={self._confidence:.2f})"
        )
