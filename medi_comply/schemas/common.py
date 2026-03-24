"""
MEDI-COMPLY — Shared enumerations and base timestamped model.

Provides the foundation types that every other schema and core module builds on.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AgentState(str, enum.Enum):
    """Lifecycle states an agent may occupy."""

    IDLE = "IDLE"
    THINKING = "THINKING"
    PROPOSING = "PROPOSING"
    UNCERTAIN = "UNCERTAIN"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    RETRY = "RETRY"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class WorkflowType(str, enum.Enum):
    """High-level workflow categories supported by the system."""

    CLINICAL_CODING = "CLINICAL_CODING"
    PRIOR_AUTHORIZATION = "PRIOR_AUTHORIZATION"
    CLAIMS_ADJUDICATION = "CLAIMS_ADJUDICATION"
    COMPLIANCE_REVIEW = "COMPLIANCE_REVIEW"
    AUDIT = "AUDIT"
    APPEAL = "APPEAL"


class ConfidenceLevel(str, enum.Enum):
    """Discretised confidence tiers mapped from continuous scores."""

    VERY_LOW = "VERY_LOW"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"

    @classmethod
    def from_score(cls, score: float) -> "ConfidenceLevel":
        """Map a 0-1 float to the appropriate tier."""
        if score < 0.2:
            return cls.VERY_LOW
        if score < 0.4:
            return cls.LOW
        if score < 0.6:
            return cls.MEDIUM
        if score < 0.8:
            return cls.HIGH
        return cls.VERY_HIGH


class DecisionType(str, enum.Enum):
    """The kind of decision an agent can make."""

    APPROVE = "APPROVE"
    DENY = "DENY"
    PEND = "PEND"
    ESCALATE = "ESCALATE"
    REQUEST_INFO = "REQUEST_INFO"


class AgentType(str, enum.Enum):
    """Roles an agent can fulfil inside the multi-agent system."""

    SUPERVISOR = "SUPERVISOR"
    DOMAIN_EXPERT = "DOMAIN_EXPERT"
    VALIDATOR = "VALIDATOR"
    OBSERVER = "OBSERVER"
    SAFETY_NET = "SAFETY_NET"
    RAG_SPECIALIST = "RAG_SPECIALIST"
    PROCESSOR = "PROCESSOR"


class ResponseStatus(str, enum.Enum):
    """Outcome status carried by every AgentResponse."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    PARTIAL = "PARTIAL"
    ESCALATE = "ESCALATE"


class RiskLevel(str, enum.Enum):
    """Risk severity tiers used across compliance and audit."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Base timestamped model
# ---------------------------------------------------------------------------


class BaseTimestampedModel(BaseModel):
    """Base model that every domain schema inherits from.

    Provides a UUID primary key and automatic ``created_at`` /
    ``updated_at`` timestamps.
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, description="Unique identifier")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Record creation timestamp (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Last-update timestamp (UTC)",
    )

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat()},
        "populate_by_name": True,
    }

    def touch(self) -> None:
        """Refresh ``updated_at`` to the current UTC time."""
        self.updated_at = datetime.now(timezone.utc)
