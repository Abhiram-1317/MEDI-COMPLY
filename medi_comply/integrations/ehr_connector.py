"""
Comprehensive, simulation-backed EHR connector.

This module provides a lightweight, deterministic connector that mimics
interactions with common EHR vendors. It exposes OAuth-like flows, fetch and
write operations, rate limiting, in-memory simulation payloads, and optional
FHIR bundle rendering via the FHIR adapter. All functionality is designed to
be side-effect free and safe to run in tests or demos without external
dependencies.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

try:
    from medi_comply.integrations.fhir_adapter import (
        FHIRAdapter,
        InternalCondition,
        InternalEncounter,
        InternalPatient,
        InternalProcedure,
    )
except Exception:  # pragma: no cover - optional dependency
    FHIRAdapter = None
    InternalCondition = None
    InternalEncounter = None
    InternalPatient = None
    InternalProcedure = None

try:
    from medi_comply.schemas.coding_result import CodingResult, SingleCodeDecision
except Exception:  # pragma: no cover - optional dependency
    CodingResult = Any
    SingleCodeDecision = Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EHRVendor(str, Enum):
    EPIC = "epic"
    CERNER = "cerner"
    ALLSCRIPTS = "allscripts"
    ATHENA = "athena"
    GENERIC = "generic"


class ConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    EXPIRED = "expired"


class FHIRResourceScope(str, Enum):
    PATIENT = "patient/*.read"
    ENCOUNTER = "encounter/*.read"
    CLINICAL = "clinical/*.read"
    FINANCIAL = "financial/*.read"
    WRITE = "clinical/*.write"


class DataRequestType(str, Enum):
    READ = "read"
    WRITE = "write"
    BATCH = "batch"
    SUMMARY = "summary"


# ---------------------------------------------------------------------------
# Data models (lightweight for demo use)
# ---------------------------------------------------------------------------


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    scope: List[str]

    def is_expired(self) -> bool:
        return datetime.utcnow() >= self.expires_at


@dataclass
class VendorConfig:
    name: str
    auth_base: str
    api_base: str
    scopes: List[str]
    sandbox_patient: str
    rate_limit_per_minute: int = 60
    supports_refresh: bool = True


@dataclass
class ResourceRequest:
    request_id: str
    resource_type: str
    request_type: DataRequestType
    vendor: EHRVendor
    timestamp: datetime
    status: str = "pending"
    message: Optional[str] = None


@dataclass
class PatientSummary:
    patient_id: str
    first_name: str
    last_name: str
    date_of_birth: str
    gender: str
    mrn: str


@dataclass
class EncounterSummary:
    encounter_id: str
    patient_id: str
    encounter_type: str
    status: str
    start_date: str
    end_date: Optional[str]
    reason_codes: List[str]


@dataclass
class ConditionSummary:
    condition_id: str
    patient_id: str
    encounter_id: Optional[str]
    icd10_code: str
    display_name: str
    category: str


@dataclass
class ProcedureSummary:
    procedure_id: str
    patient_id: str
    encounter_id: Optional[str]
    cpt_code: str
    display_name: str


@dataclass
class ObservationSummary:
    observation_id: str
    patient_id: str
    encounter_id: Optional[str]
    code: str
    display_name: str
    numeric_value: Optional[float]
    unit: Optional[str]
    status: str


@dataclass
class MedicationSummary:
    medication_id: str
    patient_id: str
    encounter_id: Optional[str]
    medication_code: str
    medication_name: str
    status: str


@dataclass
class AllergySummary:
    allergy_id: str
    patient_id: str
    substance: str
    reaction: str
    severity: str


@dataclass
class DocumentReferenceModel:
    document_id: str
    patient_id: str
    doc_type: str
    title: str
    url: str


@dataclass
class CoverageSummary:
    coverage_id: str
    patient_id: str
    payer: str
    member_id: str
    status: str


@dataclass
class ImmunizationSummary:
    immunization_id: str
    patient_id: str
    code: str
    display_name: str
    date: str


@dataclass
class CodingSubmission:
    payload_id: str
    patient_id: str
    encounter_id: Optional[str]
    coding_result: Dict[str, Any]


@dataclass
class ClaimSubmission:
    claim_id: str
    patient_id: str
    payload: Dict[str, Any]


@dataclass
class RateLimitStatus:
    """Snapshot of the current rate limit window."""

    limit: int
    remaining: int
    window_seconds: int
    reset_at: datetime


@dataclass
class ConnectorState:
    """Summary of connection state for diagnostics."""

    vendor: EHRVendor
    status: ConnectionStatus
    has_adapter: bool
    scope: List[str]
    tokens_expires_at: Optional[datetime]


@dataclass
class AuditEvent:
    """Lightweight audit record for simulated operations."""

    event_id: str
    timestamp: datetime
    event_type: str
    detail: str
    success: bool


# ---------------------------------------------------------------------------
# Vendor configurations
# ---------------------------------------------------------------------------


VENDOR_CONFIGS: Dict[EHRVendor, VendorConfig] = {
    EHRVendor.EPIC: VendorConfig(
        name="Epic Sandbox",
        auth_base="https://auth.epic.example.com",
        api_base="https://api.epic.example.com/fhir",
        scopes=[FHIRResourceScope.PATIENT.value, FHIRResourceScope.CLINICAL.value],
        sandbox_patient="epic-patient-001",
        rate_limit_per_minute=120,
    ),
    EHRVendor.CERNER: VendorConfig(
        name="Cerner Sandbox",
        auth_base="https://auth.cerner.example.com",
        api_base="https://api.cerner.example.com/fhir",
        scopes=[FHIRResourceScope.PATIENT.value, FHIRResourceScope.ENCOUNTER.value],
        sandbox_patient="cerner-patient-001",
        rate_limit_per_minute=90,
    ),
    EHRVendor.ALLSCRIPTS: VendorConfig(
        name="Allscripts Playground",
        auth_base="https://auth.allscripts.example.com",
        api_base="https://api.allscripts.example.com/fhir",
        scopes=[FHIRResourceScope.CLINICAL.value, FHIRResourceScope.FINANCIAL.value],
        sandbox_patient="allscripts-patient-001",
        rate_limit_per_minute=60,
    ),
    EHRVendor.ATHENA: VendorConfig(
        name="Athena Test",
        auth_base="https://auth.athena.example.com",
        api_base="https://api.athena.example.com/fhir",
        scopes=[FHIRResourceScope.PATIENT.value, FHIRResourceScope.WRITE.value],
        sandbox_patient="athena-patient-001",
        rate_limit_per_minute=80,
    ),
    EHRVendor.GENERIC: VendorConfig(
        name="Generic FHIR",
        auth_base="https://auth.generic.example.com",
        api_base="https://api.generic.example.com/fhir",
        scopes=[FHIRResourceScope.PATIENT.value, FHIRResourceScope.CLINICAL.value],
        sandbox_patient="generic-patient-001",
        rate_limit_per_minute=40,
        supports_refresh=False,
    ),
}


SIMULATED_DATA: Dict[str, List[Dict[str, Any]]] = {
    "patients": [
        {
            "patient_id": "demo-patient",
            "first_name": "Test",
            "last_name": "Patient",
            "date_of_birth": "1980-01-01",
            "gender": "F",
            "mrn": "MRN-demo-patient",
        }
    ],
    "encounters": [
        {
            "encounter_id": "enc-demo-patient",
            "patient_id": "demo-patient",
            "encounter_type": "outpatient",
            "status": "finished",
            "start_date": "2024-05-01",
            "end_date": "2024-05-01",
            "reason_codes": ["Z00.00"],
        }
    ],
    "conditions": [
        {
            "condition_id": "cond-demo-patient",
            "patient_id": "demo-patient",
            "encounter_id": "enc-demo-patient",
            "icd10_code": "E11.9",
            "display_name": "Type 2 diabetes mellitus without complications",
            "category": "encounter-diagnosis",
        }
    ],
    "procedures": [
        {
            "procedure_id": "proc-demo-patient",
            "patient_id": "demo-patient",
            "encounter_id": "enc-demo-patient",
            "cpt_code": "99213",
            "display_name": "Office/outpatient visit established",
        }
    ],
    "observations": [
        {
            "observation_id": "obs-demo-patient",
            "patient_id": "demo-patient",
            "encounter_id": "enc-demo-patient",
            "code": "718-7",
            "display_name": "Hemoglobin [Mass/volume] in Blood",
            "numeric_value": 13.2,
            "unit": "g/dL",
            "status": "final",
        }
    ],
    "medications": [
        {
            "medication_id": "med-demo-patient",
            "patient_id": "demo-patient",
            "encounter_id": None,
            "medication_code": "12345-6789",
            "medication_name": "Metformin 500mg",
            "status": "active",
        }
    ],
    "allergies": [
        {
            "allergy_id": "alg-demo-patient",
            "patient_id": "demo-patient",
            "substance": "Penicillin",
            "reaction": "Rash",
            "severity": "mild",
        }
    ],
    "documents": [
        {
            "document_id": "doc-demo-patient",
            "patient_id": "demo-patient",
            "doc_type": "Discharge Summary",
            "title": "Discharge Summary",
            "url": "https://example.com/docs/demo-patient.pdf",
        }
    ],
    "coverage": [
        {
            "coverage_id": "cov-demo-patient",
            "patient_id": "demo-patient",
            "payer": "Acme Health",
            "member_id": "MBR-demo-patient",
            "status": "active",
        }
    ],
    "immunizations": [
        {
            "immunization_id": "imm-demo-patient",
            "patient_id": "demo-patient",
            "code": "207",
            "display_name": "COVID-19 vaccine",
            "date": "2024-04-01",
        }
    ],
}


# ---------------------------------------------------------------------------
# EHR connector implementation
# ---------------------------------------------------------------------------


class EHRConnector:
    """In-memory EHR connector with OAuth-like flows and mock data."""

    class SimpleRateLimiter:
        """Sliding window limiter to emulate vendor throttling."""

        def __init__(self, limit: int, window_seconds: int = 60) -> None:
            """Initialize limiter with a max call count per window."""
            self.limit = max(limit, 1)
            self.window = window_seconds
            self.calls: List[float] = []

        def allow(self) -> bool:
            """Return True when within the allowed window budget."""
            now = time.time()
            self.calls = [c for c in self.calls if now - c <= self.window]
            if len(self.calls) >= self.limit:
                return False
            self.calls.append(now)
            return True

    def __init__(self, vendor: EHRVendor = EHRVendor.GENERIC) -> None:
        """Construct the connector for a given vendor."""
        self.vendor = vendor
        self.config = VENDOR_CONFIGS.get(vendor)
        self.tokens: Optional[AuthTokens] = None
        self.status = ConnectionStatus.DISCONNECTED
        self.rate_limiter = self.SimpleRateLimiter(self.config.rate_limit_per_minute if self.config else 60)
        self._adapter = self._get_adapter()
        self.audit_log: List[AuditEvent] = []
        logger.debug("Initialized EHRConnector for %s", vendor.value)

    # ------------------------------------------------------------------
    # Adapter and helper utilities
    # ------------------------------------------------------------------

    def _get_adapter(self) -> Optional[FHIRAdapter]:
        """Initialize a FHIR adapter when available."""
        try:
            return FHIRAdapter() if FHIRAdapter else None
        except Exception:
            logger.exception("Failed to initialize FHIRAdapter; continuing without it")
            return None

    def _build_base_url(self) -> str:
        """Return the vendor API base URL."""
        return self.config.api_base if self.config else "https://api.generic.example.com/fhir"

    def _auth_endpoint(self) -> str:
        """Return the authorization endpoint for the vendor."""
        return f"{self.config.auth_base}/authorize" if self.config else "https://auth.generic.example.com/authorize"

    def _token_endpoint(self) -> str:
        """Return the token endpoint for the vendor."""
        return f"{self.config.auth_base}/token" if self.config else "https://auth.generic.example.com/token"

    def _store_tokens(self, access_token: str, refresh_token: str, expires_in: int, scope: List[str]) -> None:
        """Persist tokens in-memory and update status."""
        expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
        self.tokens = AuthTokens(access_token=access_token, refresh_token=refresh_token, expires_at=expires_at, scope=scope)
        self.status = ConnectionStatus.CONNECTED
        logger.debug("Stored tokens; expires at %s", expires_at.isoformat())

    def get_connection_state(self) -> ConnectorState:
        """Return a diagnostic snapshot of the connector."""

        tokens_expires_at = self.tokens.expires_at if self.tokens else None
        has_adapter = bool(self._adapter)
        scope = self.tokens.scope if self.tokens else (self.config.scopes if self.config else [])
        return ConnectorState(
            vendor=self.vendor,
            status=self.status,
            has_adapter=has_adapter,
            scope=scope,
            tokens_expires_at=tokens_expires_at,
        )

    def get_rate_limit_status(self) -> RateLimitStatus:
        """Report current rate limit status."""

        window_seconds = self.rate_limiter.window
        now = datetime.utcnow()
        reset_at = now + timedelta(seconds=window_seconds)
        remaining = max(self.rate_limiter.limit - len(self.rate_limiter.calls), 0)
        return RateLimitStatus(
            limit=self.rate_limiter.limit,
            remaining=remaining,
            window_seconds=window_seconds,
            reset_at=reset_at,
        )

    def revoke_tokens(self) -> None:
        """Clear stored tokens and mark the connector disconnected."""

        self.tokens = None
        self.status = ConnectionStatus.DISCONNECTED
        logger.debug("Tokens revoked; status reset")

    def list_supported_resources(self) -> List[str]:
        """Return the simulated resource types supported by this connector."""

        return list(SIMULATED_DATA.keys())

    def record_audit(self, event_type: str, detail: str, success: bool = True) -> AuditEvent:
        """Append an audit event for traceability."""

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.utcnow(),
            event_type=event_type,
            detail=detail,
            success=success,
        )
        self.audit_log.append(event)
        return event

    def simulate_resource(self, resource_type: str, patient_id: str) -> Dict[str, Any]:
        """Return a simulated resource dict for the given type."""

        try:
            if resource_type not in SIMULATED_DATA:
                raise KeyError(f"Unsupported resource_type {resource_type}")
            base = SIMULATED_DATA[resource_type][0]
            cloned = {**base}
            if "patient_id" in cloned:
                cloned["patient_id"] = patient_id
            for key in ["encounter_id", "condition_id", "procedure_id", "observation_id", "medication_id", "allergy_id", "document_id", "coverage_id", "immunization_id"]:
                if key in cloned:
                    cloned[key] = f"{cloned[key].split('-')[0]}-{patient_id}"
            return cloned
        except Exception as exc:
            logger.exception("Simulation failed for %s: %s", resource_type, exc)
            raise

    def _ensure_connection(self) -> None:
        """Raise when the connector is not connected or tokens are expired."""
        if self.status == ConnectionStatus.DISCONNECTED:
            raise RuntimeError("Connector not connected")
        if self.tokens and self.tokens.is_expired():
            self.status = ConnectionStatus.EXPIRED
            raise RuntimeError("Access token expired")

    def _check_rate_limit(self) -> None:
        """Enforce the simulated rate limit window."""
        if not self.rate_limiter.allow():
            raise RuntimeError("Rate limit exceeded")

    # ------------------------------------------------------------------
    # OAuth-like flows
    # ------------------------------------------------------------------

    def get_authorization_url(self, redirect_uri: str, state: Optional[str] = None) -> str:
        """Return a simulated authorization URL."""

        try:
            self._check_rate_limit()
            state_param = state or str(uuid.uuid4())
            scope = "+".join(self.config.scopes if self.config else [])
            url = f"{self._auth_endpoint()}?client_id=demo&redirect_uri={redirect_uri}&state={state_param}&scope={scope}"
            logger.debug("Authorization URL generated: %s", url)
            return url
        except Exception as exc:
            logger.exception("Failed to build authorization URL: %s", exc)
            raise

    def exchange_code(self, code: str, redirect_uri: str) -> AuthTokens:
        """Simulate authorization code exchange."""

        try:
            self._check_rate_limit()
            access = f"access-{code}"
            refresh = f"refresh-{code}"
            scope = self.config.scopes if self.config else []
            self._store_tokens(access, refresh, 3600, scope)
            return self.tokens  # type: ignore
        except Exception as exc:
            logger.exception("Code exchange failed: %s", exc)
            raise

    def client_credentials(self) -> AuthTokens:
        """Simulate client credentials grant."""

        try:
            self._check_rate_limit()
            access = f"access-{uuid.uuid4()}"
            refresh = f"refresh-{uuid.uuid4()}"
            scope = self.config.scopes if self.config else []
            self._store_tokens(access, refresh, 1800, scope)
            return self.tokens  # type: ignore
        except Exception as exc:
            logger.exception("Client credentials failed: %s", exc)
            raise

    def refresh_token(self) -> AuthTokens:
        """Refresh the access token using the stored refresh token."""

        try:
            self._check_rate_limit()
            if not self.tokens or not self.tokens.refresh_token:
                raise RuntimeError("No refresh token available")
            if self.config and not self.config.supports_refresh:
                raise RuntimeError("Vendor does not support refresh")
            access = f"access-{uuid.uuid4()}"
            self._store_tokens(access, self.tokens.refresh_token, 1800, self.tokens.scope)
            return self.tokens  # type: ignore
        except Exception as exc:
            logger.exception("Refresh failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Data fetch entrypoints
    # ------------------------------------------------------------------

    def fetch_patient(self, patient_id: Optional[str] = None) -> PatientSummary:
        """Fetch a mock patient."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            pid = patient_id or (self.config.sandbox_patient if self.config else "demo-patient")
            return self._simulate_patient(pid)
        except Exception as exc:
            logger.exception("Failed to fetch patient: %s", exc)
            raise

    def fetch_encounters(self, patient_id: str) -> List[EncounterSummary]:
        """Fetch mock encounters."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_encounter(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch encounters: %s", exc)
            raise

    def fetch_conditions(self, patient_id: str, encounter_id: Optional[str] = None) -> List[ConditionSummary]:
        """Fetch mock conditions."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_condition(patient_id, encounter_id)]
        except Exception as exc:
            logger.exception("Failed to fetch conditions: %s", exc)
            raise

    def fetch_procedures(self, patient_id: str, encounter_id: Optional[str] = None) -> List[ProcedureSummary]:
        """Fetch mock procedures."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_procedure(patient_id, encounter_id)]
        except Exception as exc:
            logger.exception("Failed to fetch procedures: %s", exc)
            raise

    def fetch_observations(self, patient_id: str, encounter_id: Optional[str] = None) -> List[ObservationSummary]:
        """Fetch mock labs/observations."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_observation(patient_id, encounter_id)]
        except Exception as exc:
            logger.exception("Failed to fetch observations: %s", exc)
            raise

    def fetch_medications(self, patient_id: str) -> List[MedicationSummary]:
        """Fetch mock medications."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_medication(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch medications: %s", exc)
            raise

    def fetch_allergies(self, patient_id: str) -> List[AllergySummary]:
        """Fetch mock allergies."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_allergy(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch allergies: %s", exc)
            raise

    def fetch_documents(self, patient_id: str) -> List[DocumentReferenceModel]:
        """Fetch mock document references."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_document(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch documents: %s", exc)
            raise

    def fetch_coverage(self, patient_id: str) -> List[CoverageSummary]:
        """Fetch mock coverage records."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_coverage(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch coverage: %s", exc)
            raise

    def fetch_immunizations(self, patient_id: str) -> List[ImmunizationSummary]:
        """Fetch mock immunizations."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            return [self._simulate_immunization(patient_id)]
        except Exception as exc:
            logger.exception("Failed to fetch immunizations: %s", exc)
            raise

    def fetch_full_summary(self, patient_id: Optional[str] = None) -> Dict[str, Any]:
        """Return a consolidated mock clinical summary."""

        try:
            patient = self.fetch_patient(patient_id)
            encounter = self.fetch_encounters(patient.patient_id)[0]
            return {
                "patient": patient,
                "encounters": [encounter],
                "conditions": self.fetch_conditions(patient.patient_id, encounter.encounter_id),
                "procedures": self.fetch_procedures(patient.patient_id, encounter.encounter_id),
                "observations": self.fetch_observations(patient.patient_id, encounter.encounter_id),
                "medications": self.fetch_medications(patient.patient_id),
                "allergies": self.fetch_allergies(patient.patient_id),
                "documents": self.fetch_documents(patient.patient_id),
                "coverage": self.fetch_coverage(patient.patient_id),
                "immunizations": self.fetch_immunizations(patient.patient_id),
            }
        except Exception as exc:
            logger.exception("Failed to fetch full summary: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Data write entrypoints
    # ------------------------------------------------------------------

    def write_resource(self, resource_type: str, payload: Dict[str, Any]) -> ResourceRequest:
        """Simulate writing a resource to the EHR."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            req = ResourceRequest(
                request_id=str(uuid.uuid4()),
                resource_type=resource_type,
                request_type=DataRequestType.WRITE,
                vendor=self.vendor,
                timestamp=datetime.utcnow(),
                status="accepted",
            )
            logger.debug("Write resource %s accepted", resource_type)
            return req
        except Exception as exc:
            logger.exception("Failed to write resource: %s", exc)
            raise

    def update_resource(self, resource_type: str, resource_id: str, payload: Dict[str, Any]) -> ResourceRequest:
        """Simulate updating a resource."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            req = ResourceRequest(
                request_id=str(uuid.uuid4()),
                resource_type=resource_type,
                request_type=DataRequestType.WRITE,
                vendor=self.vendor,
                timestamp=datetime.utcnow(),
                status="accepted",
                message=f"Updated {resource_type}/{resource_id}",
            )
            logger.debug("Update resource %s/%s accepted", resource_type, resource_id)
            return req
        except Exception as exc:
            logger.exception("Failed to update resource: %s", exc)
            raise

    def post_coding_result(self, coding_result: CodingResult) -> CodingSubmission:
        """Simulate posting coding results back to the EHR."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            payload = json.loads(coding_result.model_dump_json()) if hasattr(coding_result, "model_dump_json") else {}
            submission = CodingSubmission(
                payload_id=str(uuid.uuid4()),
                patient_id=getattr(coding_result, "scr_id", ""),
                encounter_id=getattr(coding_result, "context_id", None),
                coding_result=payload,
            )
            logger.debug("Posted coding result payload %s", submission.payload_id)
            return submission
        except Exception as exc:
            logger.exception("Failed to post coding result: %s", exc)
            raise

    def post_claim(self, claim_payload: Dict[str, Any]) -> ClaimSubmission:
        """Simulate submitting a claim to the payer/EHR."""

        try:
            self._ensure_connection()
            self._check_rate_limit()
            claim_id = claim_payload.get("claim_id") or str(uuid.uuid4())
            submission = ClaimSubmission(claim_id=claim_id, patient_id=claim_payload.get("patient_id", ""), payload=claim_payload)
            logger.debug("Posted claim %s", claim_id)
            return submission
        except Exception as exc:
            logger.exception("Failed to post claim: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Simulation helpers
    # ------------------------------------------------------------------

    def _simulate_patient(self, patient_id: str) -> PatientSummary:
        """Build a synthetic patient record."""
        return PatientSummary(
            patient_id=patient_id,
            first_name="Test",
            last_name="Patient",
            date_of_birth="1980-01-01",
            gender="F",
            mrn=f"MRN-{patient_id}",
        )

    def _simulate_encounter(self, patient_id: str) -> EncounterSummary:
        """Build a synthetic encounter."""
        enc_id = f"enc-{patient_id}"
        return EncounterSummary(
            encounter_id=enc_id,
            patient_id=patient_id,
            encounter_type="outpatient",
            status="finished",
            start_date="2024-05-01",
            end_date="2024-05-01",
            reason_codes=["Z00.00"],
        )

    def _simulate_condition(self, patient_id: str, encounter_id: Optional[str]) -> ConditionSummary:
        """Build a synthetic condition."""
        return ConditionSummary(
            condition_id=f"cond-{patient_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            icd10_code="E11.9",
            display_name="Type 2 diabetes mellitus without complications",
            category="encounter-diagnosis",
        )

    def _simulate_procedure(self, patient_id: str, encounter_id: Optional[str]) -> ProcedureSummary:
        """Build a synthetic procedure."""
        return ProcedureSummary(
            procedure_id=f"proc-{patient_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            cpt_code="99213",
            display_name="Office/outpatient visit established",
        )

    def _simulate_observation(self, patient_id: str, encounter_id: Optional[str]) -> ObservationSummary:
        """Build a synthetic observation."""
        return ObservationSummary(
            observation_id=f"obs-{patient_id}",
            patient_id=patient_id,
            encounter_id=encounter_id,
            code="718-7",
            display_name="Hemoglobin [Mass/volume] in Blood",
            numeric_value=13.2,
            unit="g/dL",
            status="final",
        )

    def _simulate_medication(self, patient_id: str) -> MedicationSummary:
        """Build a synthetic medication."""
        return MedicationSummary(
            medication_id=f"med-{patient_id}",
            patient_id=patient_id,
            encounter_id=None,
            medication_code="12345-6789",
            medication_name="Metformin 500mg",
            status="active",
        )

    def _simulate_allergy(self, patient_id: str) -> AllergySummary:
        """Build a synthetic allergy."""
        return AllergySummary(
            allergy_id=f"alg-{patient_id}",
            patient_id=patient_id,
            substance="Penicillin",
            reaction="Rash",
            severity="mild",
        )

    def _simulate_document(self, patient_id: str) -> DocumentReferenceModel:
        """Build a synthetic document reference."""
        return DocumentReferenceModel(
            document_id=f"doc-{patient_id}",
            patient_id=patient_id,
            doc_type="Discharge Summary",
            title="Discharge Summary",
            url=f"https://example.com/docs/{patient_id}.pdf",
        )

    def _simulate_coverage(self, patient_id: str) -> CoverageSummary:
        """Build a synthetic coverage record."""
        return CoverageSummary(
            coverage_id=f"cov-{patient_id}",
            patient_id=patient_id,
            payer="Acme Health",
            member_id=f"MBR-{patient_id}",
            status="active",
        )

    def _simulate_immunization(self, patient_id: str) -> ImmunizationSummary:
        """Build a synthetic immunization."""
        return ImmunizationSummary(
            immunization_id=f"imm-{patient_id}",
            patient_id=patient_id,
            code="207",
            display_name="COVID-19 vaccine",
            date="2024-04-01",
        )

    # ------------------------------------------------------------------
    # Bundle rendering
    # ------------------------------------------------------------------

    def _map_to_internal(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Convert summaries into internal model instances when available."""
        try:
            patient_internal: Any = summary.get("patient")
            encounters_internal: List[Any] = summary.get("encounters", [])
            conditions_internal: List[Any] = summary.get("conditions", [])
            procedures_internal: List[Any] = summary.get("procedures", [])

            if InternalPatient and isinstance(summary.get("patient"), PatientSummary):
                patient_internal = InternalPatient(**summary["patient"].__dict__)
            if InternalEncounter:
                encounters_internal = [InternalEncounter(**e.__dict__, procedure_codes=[], diagnosis_codes=[]) for e in summary.get("encounters", [])]
            if InternalCondition:
                conditions_internal = [InternalCondition(**c.__dict__, clinical_status="active", verification_status="confirmed", severity=None, recorded_date=None, onset_date=None) for c in summary.get("conditions", [])]
            if InternalProcedure:
                procedures_internal = [InternalProcedure(**p.__dict__, status="completed", performed_date=None, performer_id=None, body_site=None, laterality=None) for p in summary.get("procedures", [])]

            return {
                "patient": patient_internal,
                "encounters": encounters_internal,
                "conditions": conditions_internal,
                "procedures": procedures_internal,
            }
        except Exception as exc:
            logger.exception("Failed to map to internal models: %s", exc)
            raise

    def to_fhir_bundle(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """Render the clinical summary into a FHIR Bundle when adapter is available."""

        try:
            self._ensure_connection()
            mapped = self._map_to_internal(summary)
            adapter = self._adapter
            resources: List[Dict[str, Any]] = []

            patient = mapped.get("patient")
            if adapter and isinstance(patient, InternalPatient):
                resources.append(adapter.to_fhir_patient(patient))
            elif isinstance(patient, PatientSummary):
                resources.append(
                    {
                        "resourceType": "Patient",
                        "id": patient.patient_id,
                        "name": [{"family": patient.last_name, "given": [patient.first_name]}],
                        "gender": {"F": "female", "M": "male"}.get(patient.gender, "unknown"),
                        "birthDate": patient.date_of_birth,
                    }
                )

            for condition in mapped.get("conditions", []):
                if adapter and isinstance(condition, InternalCondition):
                    resources.append(adapter.to_fhir_condition(condition))
                elif isinstance(condition, ConditionSummary):
                    resources.append(
                        {
                            "resourceType": "Condition",
                            "id": condition.condition_id,
                            "code": {"coding": [{"code": condition.icd10_code, "display": condition.display_name}]},
                        }
                    )

            for procedure in mapped.get("procedures", []):
                if adapter and isinstance(procedure, InternalProcedure):
                    resources.append(adapter.to_fhir_procedure(procedure))
                elif isinstance(procedure, ProcedureSummary):
                    resources.append(
                        {
                            "resourceType": "Procedure",
                            "id": procedure.procedure_id,
                            "code": {"coding": [{"code": procedure.cpt_code, "display": procedure.display_name}]},
                        }
                    )

            if adapter:
                return adapter.to_fhir_bundle(resources, bundle_type="collection")

            entries = []
            for res in resources:
                rid = res.get("id", str(uuid.uuid4()))
                entries.append({"fullUrl": f"urn:uuid:{rid}", "resource": res})
            return {"resourceType": "Bundle", "type": "collection", "entry": entries}
        except Exception as exc:
            logger.exception("Failed to render FHIR bundle: %s", exc)
            raise


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def configure_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure module-level logging."""

    level = getattr(logging, log_level.upper(), logging.INFO)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logger.setLevel(level)
    logger.debug("Logging configured to %s", log_level)
    return logger


def _demo_run() -> None:
    """Run a demo flow to validate the connector works end-to-end."""

    configure_logging("INFO")
    connector = EHRConnector(EHRVendor.EPIC)
    connector.client_credentials()
    summary = connector.fetch_full_summary()
    bundle = connector.to_fhir_bundle(summary)
    logger.info("Fetched summary for patient %s", summary["patient"].patient_id)
    logger.info("Bundle contains %d entries", len(bundle.get("entry", [])))


if __name__ == "__main__":
    _demo_run()


__all__ = [
    "EHRConnector",
    "EHRVendor",
    "ConnectionStatus",
    "FHIRResourceScope",
    "DataRequestType",
    "configure_logging",
    "AuthTokens",
    "PatientSummary",
    "EncounterSummary",
    "ConditionSummary",
    "ProcedureSummary",
    "ObservationSummary",
    "MedicationSummary",
    "AllergySummary",
    "DocumentReferenceModel",
    "CoverageSummary",
    "ImmunizationSummary",
    "CodingSubmission",
    "ClaimSubmission",
]
