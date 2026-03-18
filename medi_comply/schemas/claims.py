"""
MEDI-COMPLY — Claims and adjudication schemas.

Models for insurance claims, individual claim lines, adjudication results,
and denial reasons.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from medi_comply.schemas.common import BaseTimestampedModel, ConfidenceLevel


# ---------------------------------------------------------------------------
# Supporting models
# ---------------------------------------------------------------------------


class ClaimStatus(str, enum.Enum):
    """Lifecycle status of a claim."""

    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    IN_REVIEW = "IN_REVIEW"
    ADJUDICATED = "ADJUDICATED"
    PAID = "PAID"
    DENIED = "DENIED"
    APPEALED = "APPEALED"


class DenialReason(BaseModel):
    """Structured representation of a claim denial or line-level denial.

    Attributes
    ----------
    code : str
        Payer-specific denial reason code (e.g. ``"CO-4"``).
    description : str
        Human-readable explanation.
    category : str
        Broad category (e.g. ``"Medical Necessity"``, ``"Coding Error"``).
    remediation : str
        Suggested corrective action.
    """

    code: str = Field(description="Denial reason code")
    description: str = Field(description="Human-readable denial explanation")
    category: str = Field(default="", description="Denial category")
    remediation: str = Field(default="", description="Suggested corrective action")


# ---------------------------------------------------------------------------
# Claim line
# ---------------------------------------------------------------------------


class ClaimLine(BaseTimestampedModel):
    """A single service line on a claim.

    Each line represents one procedure / service billed to the payer.
    """

    line_number: int = Field(ge=1, description="Sequential line ordinal")
    procedure_code: str = Field(description="CPT / HCPCS code")
    procedure_description: str = Field(default="", description="Code description")
    diagnosis_codes: list[str] = Field(
        default_factory=list,
        description="ICD-10-CM codes linked to this line",
    )
    modifier_codes: list[str] = Field(
        default_factory=list,
        description="Modifier codes (e.g. '-25', '-59')",
    )
    units: int = Field(default=1, ge=1, description="Number of units")
    charge_amount: float = Field(ge=0.0, description="Billed amount (USD)")
    service_date: date = Field(description="Date of service")
    place_of_service: str = Field(default="11", description="Place-of-service code")
    rendering_provider_npi: Optional[str] = Field(
        default=None, description="Rendering provider NPI"
    )


# ---------------------------------------------------------------------------
# Claim data
# ---------------------------------------------------------------------------


class ClaimData(BaseTimestampedModel):
    """Full claim payload submitted by a provider to a payer.

    Aggregates patient / provider / payer identifiers with service lines.
    """

    claim_number: str = Field(description="Unique claim reference number")
    patient_id: str = Field(description="De-identified patient reference")
    provider_npi: str = Field(description="Billing provider NPI")
    payer_id: str = Field(description="Payer / insurance identifier")
    status: ClaimStatus = Field(default=ClaimStatus.DRAFT, description="Current claim status")
    total_charge: float = Field(ge=0.0, description="Total billed amount (USD)")
    lines: list[ClaimLine] = Field(default_factory=list, description="Service lines")
    submission_date: Optional[date] = Field(default=None, description="Date submitted to payer")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Extra claim metadata")

    @field_validator("total_charge", mode="before")
    @classmethod
    def _default_total(cls, v: Any, info: Any) -> float:
        """Accept ``0`` as a valid default value."""
        return float(v)


# ---------------------------------------------------------------------------
# Adjudication result
# ---------------------------------------------------------------------------


class AdjudicationResult(BaseTimestampedModel):
    """Outcome of adjudicating a single claim.

    Captures allowed amounts, denial reasons (if any), and an overall
    confidence assessment.
    """

    claim_id: uuid.UUID = Field(description="ID of the evaluated ClaimData")
    decision: str = Field(
        description="Adjudication decision (e.g. 'PAID', 'DENIED', 'PARTIAL')"
    )
    allowed_amount: float = Field(ge=0.0, description="Total allowed amount (USD)")
    paid_amount: float = Field(ge=0.0, description="Total paid amount (USD)")
    patient_responsibility: float = Field(
        ge=0.0, description="Amount owed by the patient (USD)"
    )
    denial_reasons: list[DenialReason] = Field(
        default_factory=list,
        description="Denial or adjustment reasons (if any)",
    )
    line_results: dict[int, str] = Field(
        default_factory=dict,
        description="Per-line adjudication outcomes (line_number -> status)",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score for the adjudication decision",
    )
    notes: list[str] = Field(default_factory=list, description="Adjudicator notes")

    @property
    def confidence_level(self) -> ConfidenceLevel:
        """Discretised confidence tier."""
        return ConfidenceLevel.from_score(self.confidence)
