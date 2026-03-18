"""
MEDI-COMPLY — Agent state machine with enforced transitions.

Defines all valid lifecycle states for an agent and the legal transitions
between them.  The ``StateMachine`` class tracks the current state, enforces
transition rules, and records full transition history with timestamps.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from medi_comply.schemas.common import AgentState


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class InvalidTransitionError(Exception):
    """Raised when an agent attempts a disallowed state transition."""

    def __init__(self, current: AgentState, requested: AgentState) -> None:
        self.current = current
        self.requested = requested
        super().__init__(
            f"Invalid state transition: {current.value} -> {requested.value}"
        )


# ---------------------------------------------------------------------------
# Transition rules
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[AgentState, list[AgentState]] = {
    AgentState.IDLE: [AgentState.THINKING, AgentState.ERROR],
    AgentState.THINKING: [AgentState.PROPOSING, AgentState.UNCERTAIN, AgentState.ERROR],
    AgentState.UNCERTAIN: [AgentState.ESCALATED],
    AgentState.PROPOSING: [AgentState.VALIDATING],
    AgentState.VALIDATING: [AgentState.APPROVED, AgentState.RETRY, AgentState.ESCALATED],
    AgentState.RETRY: [AgentState.THINKING],
    AgentState.APPROVED: [AgentState.COMPLETED],
    AgentState.ERROR: [AgentState.ESCALATED],
    AgentState.ESCALATED: [],
    AgentState.COMPLETED: [],
}


# ---------------------------------------------------------------------------
# Transition history record
# ---------------------------------------------------------------------------


class TransitionRecord(BaseModel):
    """Immutable record of a single state transition."""

    from_state: AgentState
    to_state: AgentState
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Optional[dict] = None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class StateMachine:
    """Enforces the agent lifecycle and tracks all transitions.

    Parameters
    ----------
    initial_state:
        The starting state — defaults to ``IDLE``.
    max_retries:
        Maximum number of RETRY transitions before forcing escalation.
    """

    def __init__(
        self,
        initial_state: AgentState = AgentState.IDLE,
        max_retries: int = 3,
    ) -> None:
        self._state: AgentState = initial_state
        self._max_retries: int = max_retries
        self._retry_count: int = 0
        self._history: list[TransitionRecord] = []

    # -- Properties --------------------------------------------------------

    @property
    def current_state(self) -> AgentState:
        """Return the current state."""
        return self._state

    @property
    def history(self) -> list[TransitionRecord]:
        """Return the full transition history (read-only copy)."""
        return list(self._history)

    @property
    def retry_count(self) -> int:
        """Return the number of RETRY transitions so far."""
        return self._retry_count

    # -- Core API ----------------------------------------------------------

    def transition(
        self,
        new_state: AgentState,
        metadata: Optional[dict] = None,
    ) -> AgentState:
        """Attempt to move from the current state to *new_state*.

        Parameters
        ----------
        new_state:
            Target state.
        metadata:
            Optional context attached to the transition record.

        Returns
        -------
        AgentState
            The new current state after a successful transition.

        Raises
        ------
        InvalidTransitionError
            If the requested transition is not in ``VALID_TRANSITIONS``.
        """
        allowed = VALID_TRANSITIONS.get(self._state, [])
        if new_state not in allowed:
            raise InvalidTransitionError(self._state, new_state)

        # Retry handling
        if new_state == AgentState.RETRY:
            self._retry_count += 1
            if self._retry_count > self._max_retries:
                new_state = AgentState.ESCALATED

        record = TransitionRecord(
            from_state=self._state,
            to_state=new_state,
            metadata=metadata,
        )
        self._history.append(record)
        self._state = new_state
        return self._state

    def can_transition(self, new_state: AgentState) -> bool:
        """Check whether a transition to *new_state* is legal."""
        return new_state in VALID_TRANSITIONS.get(self._state, [])

    def reset(self) -> None:
        """Reset the machine to ``IDLE`` and clear history."""
        self._state = AgentState.IDLE
        self._retry_count = 0
        self._history.clear()

    # -- Dunder ------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"StateMachine(state={self._state.value}, "
            f"retries={self._retry_count}/{self._max_retries}, "
            f"transitions={len(self._history)})"
        )
