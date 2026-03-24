"""
Claims adjudication API routes for MEDI-COMPLY.

Exposes endpoints to validate and adjudicate single or batch claims using the
ClaimsAdjudicationAgent and MediComplySystem facade.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from medi_comply.agents.claims_adjudication_agent import (
    ClaimAdjudicationResult,
    ClaimInput,
    ClaimValidator,
)
from medi_comply.core.config import get_settings
from medi_comply.system import MediComplySystem

router = APIRouter(prefix="/api/v1/claims", tags=["claims"])


class BatchAdjudicationRequest(BaseModel):
    claims: List[ClaimInput] = Field(..., description="List of claims to adjudicate")


class BatchStatusResponse(BaseModel):
    batch_id: str
    status: str
    progress: int
    completed: int
    errors: int
    results: List[dict] = Field(default_factory=list)
    error_messages: List[str] = Field(default_factory=list)


def _get_system(request: Request) -> MediComplySystem:
    system = getattr(request.app.state, "mc_system", None)
    if system is None:
        system = MediComplySystem(config=get_settings())
        setattr(request.app.state, "mc_system", system)
    return system


async def _ensure_initialized(system: MediComplySystem) -> None:
    if getattr(system, "_initialized", False):
        return
    await system.initialize()


@router.post(
    "/adjudicate",
    response_model=dict,
    summary="Adjudicate a single claim",
    description="Runs the full claims adjudication pipeline and returns the ClaimAdjudicationResult.",
)
async def adjudicate_claim(request: Request, claim: ClaimInput) -> dict:
    system = _get_system(request)
    await _ensure_initialized(system)
    result = await system.adjudicate_claim(claim.model_dump())
    return result


@router.post(
    "/batch-adjudicate",
    response_model=dict,
    summary="Start batch claims adjudication",
    description="Starts asynchronous batch adjudication. Poll status endpoint for results.",
)
async def adjudicate_claims_batch(request: Request, payload: BatchAdjudicationRequest) -> dict:
    system = _get_system(request)
    await _ensure_initialized(system)

    batch_id = f"batch-{uuid.uuid4().hex[:10]}"
    batches: Dict[str, Dict[str, Any]] = getattr(request.app.state, "claim_batches", {})

    status: Dict[str, Any] = {
        "batch_id": batch_id,
        "status": "processing",
        "progress": 0,
        "completed": 0,
        "errors": 0,
        "results": [],
        "error_messages": [],
    }

    async def _run_batch() -> None:
        for idx, claim in enumerate(payload.claims, start=1):
            try:
                res = await system.adjudicate_claim(claim.model_dump())
                status["results"].append(res)
                status["completed"] += 1
            except Exception as exc:  # pragma: no cover - best-effort logging
                status["errors"] += 1
                status["error_messages"].append(str(exc))
            finally:
                status["progress"] = idx
        status["status"] = "completed"

    task = asyncio.create_task(_run_batch())
    status["task"] = task  # stored for lifecycle; not serialized
    batches[batch_id] = status
    setattr(request.app.state, "claim_batches", batches)

    return {"batch_id": batch_id, "status": "processing"}


@router.get(
    "/batch/{batch_id}/status",
    response_model=BatchStatusResponse,
    summary="Check batch adjudication status",
)
async def batch_status(request: Request, batch_id: str) -> BatchStatusResponse:
    batches: Dict[str, Dict[str, Any]] = getattr(request.app.state, "claim_batches", {})
    status = batches.get(batch_id)
    if not status:
        raise HTTPException(status_code=404, detail="Batch ID not found")

    # Ensure task completion errors bubble into status
    task = status.get("task")
    if task and task.done() and "status" in status and status["status"] != "completed":
        status["status"] = "completed"
        if task.exception():
            status["errors"] += 1
            status["error_messages"].append(str(task.exception()))

    return BatchStatusResponse(
        batch_id=batch_id,
        status=status.get("status", "processing"),
        progress=status.get("progress", 0),
        completed=status.get("completed", 0),
        errors=status.get("errors", 0),
        results=status.get("results", []),
        error_messages=status.get("error_messages", []),
    )


@router.post(
    "/validate",
    response_model=dict,
    summary="Validate claim structure without adjudication",
)
async def validate_claim(claim: ClaimInput) -> dict:
    validator = ClaimValidator()
    result = validator.validate(claim)
    return result


# OpenAPI examples
ClaimInput.model_config["json_schema_extra"] = {
    "examples": [
        {
            "claim_id": "CLM-1001",
            "claim_type": "PROFESSIONAL",
            "submission_date": "2026-03-01",
            "member_id": "MEM-123",
            "member_name": "Jane Doe",
            "member_dob": "1980-01-01",
            "member_gender": "F",
            "payer_id": "PYR-1",
            "plan_id": "PLAN-A",
            "provider_id": "PRV-1",
            "provider_name": "Dr. Smith",
            "provider_npi": "1111111111",
            "provider_taxonomy": "207Q00000X",
            "facility_id": "FAC-1",
            "place_of_service": "11",
            "date_of_service_from": "2026-02-25",
            "primary_diagnosis": "R07.9",
            "secondary_diagnoses": ["Z00.00"],
            "line_items": [
                {
                    "line_number": 1,
                    "cpt_code": "99213",
                    "modifiers": ["25"],
                    "diagnosis_pointers": [1],
                    "units": 1,
                    "charge_amount": 150.0,
                    "description": "Office visit",
                },
                {
                    "line_number": 2,
                    "cpt_code": "93000",
                    "modifiers": [],
                    "diagnosis_pointers": [1],
                    "units": 1,
                    "charge_amount": 75.0,
                    "description": "ECG",
                },
            ],
            "total_charges": 225.0,
            "attachments": [],
            "notes": "Routine visit",
        }
    ]
}

ClaimAdjudicationResult.model_config["json_schema_extra"] = {
    "examples": [
        {
            "claim_id": "CLM-1001",
            "claim_status": "APPROVED",
            "processing_date": "2026-03-24",
            "processing_time_ms": 120.0,
            "payer_id": "PYR-1",
            "payer_name": "MEDI-COMPLY Payer",
            "member_id": "MEM-123",
            "provider_id": "PRV-1",
            "date_of_service": "2026-02-25",
            "line_results": [],
            "total_charges": 225.0,
            "total_allowed": 190.0,
            "total_paid": 150.0,
            "total_member_responsibility": 40.0,
            "total_adjustment": 35.0,
            "claim_level_denial_reasons": [],
            "timely_filing_check": True,
            "duplicate_check": True,
            "eligibility_check": True,
            "provider_check": True,
            "parity_check_result": None,
            "compliance_checks_passed": 1,
            "compliance_checks_total": 1,
            "appeal_guidance": None,
            "eob_summary": "...",
            "audit_trail_id": "AUD-xyz",
            "reasoning_summary": "Claim status: APPROVED",
            "warnings": [],
        }
    ]
}
