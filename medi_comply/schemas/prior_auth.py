"""
MEDI-COMPLY — Prior-authorization schemas.

Models for authorization requests, clinical criteria, and authorization
decisions issued by a payer or utilization-review agent.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

from medi_comply.schemas.common import BaseTimestampedModel, ConfidenceLevel


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AuthStatus(str, enum.Enum):
    """Lifecycle status of a prior-authorization request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    PENDED_FOR_REVIEW = "PENDED_FOR_REVIEW"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class UrgencyLevel(str, enum.Enum):
    """Urgency classification for prior-auth requests."""

    ROUTINE = "ROUTINE"
    URGENT = "URGENT"
    EMERGENT = "EMERGENT"


# ---------------------------------------------------------------------------
# Clinical criteria
# ---------------------------------------------------------------------------


class ClinicalCriteria(BaseModel):
    """A single clinical criterion evaluated during prior-authorization.

    Each criterion captures a guideline requirement and whether the
    submitted clinical documentation satisfies it.
    """

    criterion_name: str = Field(description="Name of the clinical criterion")
    description: str = Field(description="What the criterion evaluates")
    guideline_source: str = Field(
        default="",
        description="Source guideline (e.g. 'InterQual', 'MCG', 'Custom')",
    )
    is_met: bool = Field(default=False, description="Whether the criterion was satisfied")
    evidence_summary: str = Field(
        default="",
        description="Summary of evidence supporting or refuting the criterion",
    )
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Confidence that the criterion assessment is correct",
    )


# ---------------------------------------------------------------------------
# Authorization request
# ---------------------------------------------------------------------------


class AuthRequest(BaseTimestampedModel):
    """A prior-authorization request submitted for review.

    Aggregates the clinical context, requested services, and criteria
    evaluated.
    """

    request_number: str = Field(description="Unique prior-auth request number")
    patient_id: str = Field(description="De-identified patient reference")
    provider_npi: str = Field(description="Requesting provider NPI")
    payer_id: str = Field(description="Payer / insurance identifier")
    urgency: UrgencyLevel = Field(
        default=UrgencyLevel.ROUTINE, description="Request urgency"
    )

    # Requested services
    procedure_codes: list[str] = Field(
        default_factory=list, description="CPT codes for requested procedures"
    )
    diagnosis_codes: list[str] = Field(
        default_factory=list, description="ICD-10 codes supporting medical necessity"
    )

    # Clinical context
    clinical_summary: str = Field(
        default="",
        description="Narrative clinical summary supporting the request",
    )
    criteria: list[ClinicalCriteria] = Field(
        default_factory=list,
        description="Clinical criteria evaluated",
    )

    status: AuthStatus = Field(
        default=AuthStatus.PENDING, description="Current request status"
    )
    submitted_date: Optional[date] = Field(
        default=None, description="Date submitted to the payer"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional request metadata"
    )


# ---------------------------------------------------------------------------
# Authorization decision
# ---------------------------------------------------------------------------


class AuthDecision(BaseTimestampedModel):
    """Decision rendered for a prior-authorization request.

    Contains the verdict, rationale, any conditions of approval, and
    confidence scoring.
    """

    request_id: uuid.UUID = Field(description="ID of the originating AuthRequest")
    decision: AuthStatus = Field(description="Authorization decision")
    rationale: list[str] = Field(
        default_factory=list,
        description="Step-by-step reasoning for the decision",
    )
    conditions: list[str] = Field(
        default_factory=list,
        description="Conditions attached to an approval",
    )
    denial_reasons: list[str] = Field(
        default_factory=list,
        description="Reasons for denial (if applicable)",
    )
    approved_units: Optional[int] = Field(
        default=None, ge=0,
        description="Number of approved units / visits",
    )
    valid_from: Optional[date] = Field(
        default=None, description="Start date of authorization"
    )
    valid_to: Optional[date] = Field(
        default=None, description="End date of authorization"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the authorization decision",
    )
    reviewer_id: Optional[str] = Field(
        default=None, description="Human reviewer ID (if escalated)"
    )

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier."""
        return ConfidenceLevel.from_score(self.confidence)
