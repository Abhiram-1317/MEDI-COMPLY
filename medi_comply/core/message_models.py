"""
MEDI-COMPLY — Message schemas for inter-agent communication.

``AgentMessage`` and ``AgentResponse`` are the canonical wire formats flowing
through the :class:`~medi_comply.core.message_bus.AsyncMessageBus`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from medi_comply.schemas.common import ResponseStatus


# ---------------------------------------------------------------------------
# AgentMessage
# ---------------------------------------------------------------------------


class AgentMessage(BaseModel):
    """A message dispatched *to* an agent via the message bus.

    Attributes
    ----------
    message_id : str
        Unique message identifier (auto-generated UUIDv4).
    timestamp : datetime
        UTC creation timestamp.
    from_agent : str
        Name (or ID) of the sending agent.
    to_agent : str
        Name (or ID) of the target agent.
    action : str
        Short verb / tag describing the requested action.
    payload : dict
        Arbitrary structured data for the recipient.
    requires_response : bool
        Whether the sender expects a synchronous reply.
    timeout_ms : int
        If a response is required, how long to wait (milliseconds).
    trace_id : str
        Correlation ID propagated across the entire workflow.
    priority : int
        Higher values ⇒ higher priority (default 0).
    """

    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    from_agent: str
    to_agent: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_response: bool = Field(default=False)
    timeout_ms: int = Field(default=30_000, gt=0)
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    priority: int = Field(default=0, ge=0)

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}


# ---------------------------------------------------------------------------
# AgentResponse
# ---------------------------------------------------------------------------


class AgentResponse(BaseModel):
    """A response returned by an agent after processing a message.

    Attributes
    ----------
    response_id : str
        Unique response identifier (auto-generated UUIDv4).
    original_message_id : str
        ``message_id`` of the message this response answers.
    from_agent : str
        Name (or ID) of the responding agent.
    status : ResponseStatus
        Outcome status of the processing.
    payload : dict
        Arbitrary result data.
    confidence_score : float
        Agent's self-assessed confidence (0.0 – 1.0).
    reasoning : list[str]
        Step-by-step reasoning chain the agent used.
    errors : list[str]
        Error messages, if any.
    trace_id : str
        Propagated workflow correlation ID.
    """

    response_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    original_message_id: str
    from_agent: str
    status: ResponseStatus = ResponseStatus.SUCCESS
    payload: dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    reasoning: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat()}}
