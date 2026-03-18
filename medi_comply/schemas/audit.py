"""
MEDI-COMPLY — Audit, reasoning chain, and risk-scoring schemas.

Provides fine-grained audit models that capture every decision, its
reasoning chain, evidence links, and an overall risk score.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from medi_comply.schemas.common import BaseTimestampedModel, RiskLevel


# ---------------------------------------------------------------------------
# Risk score
# ---------------------------------------------------------------------------


class RiskScore(BaseModel):
    """Quantified risk assessment.

    Attributes
    ----------
    score : float
        Continuous risk value between 0 (no risk) and 1 (critical).
    level : RiskLevel
        Discretised tier (LOW / MEDIUM / HIGH / CRITICAL).
    factors : list[str]
        Human-readable factors that contributed to the score.
    """

    score: float = Field(ge=0.0, le=1.0, description="Continuous risk score")
    level: RiskLevel = Field(description="Discretised risk tier")
    factors: list[str] = Field(
        default_factory=list,
        description="Contributing risk factors",
    )

    @field_validator("level", mode="before")
    @classmethod
    def _infer_level(cls, v: Any, info: Any) -> Any:
        """Allow ``level`` to be auto-inferred from ``score`` when set to None."""
        return v


# ---------------------------------------------------------------------------
# Evidence & reasoning
# ---------------------------------------------------------------------------


class EvidenceLink(BaseModel):
    """Link from an audit entry to a specific piece of supporting evidence.

    The ``source_id`` may reference a :class:`SourceEvidence`, code
    assignment, compliance check, or any other traceable artefact.
    """

    source_id: str = Field(description="ID or locator of the evidence artefact")
    source_type: str = Field(description="Type of evidence (e.g. 'clinical_text', 'guideline')")
    description: str = Field(default="", description="Human-readable summary of the evidence")
    relevance_score: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="How relevant this evidence is to the decision",
    )


class ReasoningChain(BaseModel):
    """Ordered chain of reasoning steps leading to a decision.

    Used inside :class:`AuditEntry` to provide a fully transparent
    rationale.
    """

    steps: list[str] = Field(description="Ordered reasoning steps")
    evidence_links: list[EvidenceLink] = Field(
        default_factory=list,
        description="Evidence supporting each reasoning step",
    )
    conclusion: str = Field(description="Final conclusion drawn from the reasoning chain")
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Aggregated confidence in the chain",
    )


# ---------------------------------------------------------------------------
# Audit entry & record
# ---------------------------------------------------------------------------


class AuditEntry(BaseTimestampedModel):
    """A single auditable event in the system.

    Every decision, state transition, or significant action produces an
    :class:`AuditEntry` so the full history can be reconstructed.
    """

    agent_id: str = Field(description="ID of the agent that produced this entry")
    agent_name: str = Field(description="Human-readable agent name")
    action: str = Field(description="Action tag (e.g. 'code_assignment')")
    detail: str = Field(description="Human-readable description")
    trace_id: str = Field(description="Workflow correlation ID")
    reasoning: Optional[ReasoningChain] = Field(
        default=None,
        description="Full reasoning chain (when applicable)",
    )
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    risk_score: Optional[RiskScore] = Field(
        default=None,
        description="Risk assessment for this particular action",
    )
    input_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of inputs at decision time",
    )
    output_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="Snapshot of outputs produced",
    )


class AuditRecord(BaseTimestampedModel):
    """Aggregate audit record for an entire workflow invocation.

    Groups all :class:`AuditEntry` objects produced during one trace.
    """

    trace_id: str = Field(description="Workflow correlation ID")
    workflow_type: str = Field(description="Type of workflow (e.g. 'CLINICAL_CODING')")
    entries: list[AuditEntry] = Field(default_factory=list)
    overall_risk: Optional[RiskScore] = Field(
        default=None,
        description="Aggregate risk score across all entries",
    )
    outcome: str = Field(
        default="PENDING",
        description="Final workflow outcome (e.g. 'COMPLETED', 'ESCALATED')",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary workflow-level metadata",
    )
