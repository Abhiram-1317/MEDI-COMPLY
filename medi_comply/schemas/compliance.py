"""
MEDI-COMPLY — Compliance and guardrail schemas.

Models for compliance checks, results, and the ``GuardrailDecision`` enum
used across the safety layer.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import Field

from medi_comply.schemas.common import BaseTimestampedModel, ConfidenceLevel, RiskLevel


# ---------------------------------------------------------------------------
# GuardrailDecision enum
# ---------------------------------------------------------------------------


class GuardrailDecision(str, enum.Enum):
    """Outcome of a guardrail evaluation."""

    PASS = "PASS"
    SOFT_FAIL = "SOFT_FAIL"
    HARD_FAIL = "HARD_FAIL"
    ESCALATE = "ESCALATE"
    BLOCK_AND_ALERT = "BLOCK_AND_ALERT"


# ---------------------------------------------------------------------------
# Compliance models
# ---------------------------------------------------------------------------


class ComplianceCheck(BaseTimestampedModel):
    """A single compliance check executed against a coding result or claim.

    Attributes
    ----------
    check_name : str
        Machine-readable check identifier (e.g. ``"code_specificity"``).
    check_description : str
        Human-readable explanation of what the check validates.
    rule_reference : str
        Regulatory or internal rule reference (e.g. ``"CMS-1500 §4.2"``).
    target_entity_id : uuid.UUID
        ID of the entity being checked (CodeAssignment, ClaimLine, etc.).
    """

    check_name: str = Field(description="Machine-readable check name")
    check_description: str = Field(description="What the check validates")
    rule_reference: str = Field(default="", description="Regulatory rule reference")
    target_entity_id: uuid.UUID = Field(description="ID of the entity under evaluation")
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of inputs used during evaluation",
    )


class ComplianceResult(BaseTimestampedModel):
    """Outcome of a compliance check.

    Captures the guardrail decision, any findings, remediation suggestions,
    and the confidence of the evaluation.
    """

    check_id: uuid.UUID = Field(description="ID of the originating ComplianceCheck")
    decision: GuardrailDecision = Field(description="Guardrail decision outcome")
    findings: list[str] = Field(
        default_factory=list,
        description="Specific findings / violations detected",
    )
    remediation: list[str] = Field(
        default_factory=list,
        description="Suggested corrective actions",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score for the compliance evaluation",
    )
    risk_level: RiskLevel = Field(
        default=RiskLevel.LOW,
        description="Risk severity of the finding",
    )
    evidence_references: list[str] = Field(
        default_factory=list,
        description="Pointers to supporting evidence",
    )
    requires_human_review: bool = Field(
        default=False,
        description="Whether a human must review before proceeding",
    )
    reviewer_notes: Optional[str] = Field(
        default=None,
        description="Notes added by a human reviewer",
    )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier."""
        return ConfidenceLevel.from_score(self.confidence)
