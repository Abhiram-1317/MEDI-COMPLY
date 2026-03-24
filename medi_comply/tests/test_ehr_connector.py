from datetime import datetime, timedelta

import pytest

from medi_comply.integrations.ehr_connector import (
    AuditEvent,
    AuthTokens,
    ConnectorState,
    CoverageSummary,
    DataRequestType,
    DocumentReferenceModel,
    EHRConnector,
    EHRVendor,
    PatientSummary,
    RateLimitStatus,
    VENDOR_CONFIGS,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def connector():
    """Return a fresh connector for EPIC vendor."""
    return EHRConnector(EHRVendor.EPIC)


@pytest.fixture
def generic_connector():
    """Return a connector for the Generic vendor (no refresh support)."""
    return EHRConnector(EHRVendor.GENERIC)


@pytest.fixture
def auth_connector(connector):
    """Authenticate the connector using client credentials."""
    connector.client_credentials()
    return connector


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestConnectorInit:
    def test_create_epic_connector(self, connector):
        """Connector for EPIC initializes with correct vendor and status."""
        assert connector.vendor is EHRVendor.EPIC
        assert connector.status == connector.status.DISCONNECTED
        assert connector.config.name == "Epic Sandbox"

    def test_create_generic_connector(self, generic_connector):
        """Generic connector sets refresh support flag to False."""
        assert generic_connector.vendor is EHRVendor.GENERIC
        assert generic_connector.config.supports_refresh is False

    def test_vendor_configs_available(self):
        """All vendors defined in VENDOR_CONFIGS."""
        assert set(VENDOR_CONFIGS.keys()) == {
            EHRVendor.EPIC,
            EHRVendor.CERNER,
            EHRVendor.ALLSCRIPTS,
            EHRVendor.ATHENA,
            EHRVendor.GENERIC,
        }

    def test_supported_resources_list(self, connector):
        """Supported resources align with simulated dataset keys."""
        expected = {
            "patients",
            "encounters",
            "conditions",
            "procedures",
            "observations",
            "medications",
            "allergies",
            "documents",
            "coverage",
            "immunizations",
        }
        assert set(connector.list_supported_resources()) == expected

    def test_rate_limit_configured_from_vendor(self, connector):
        """Rate limiter respects vendor-specific quota."""
        assert connector.rate_limiter.limit == connector.config.rate_limit_per_minute


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuthentication:
    def test_client_credentials_auth_sets_tokens(self, connector):
        """client_credentials returns tokens and sets status to CONNECTED."""
        tokens = connector.client_credentials()
        assert isinstance(tokens, AuthTokens)
        assert tokens.access_token.startswith("access-")
        assert connector.status == connector.status.CONNECTED

    def test_authorization_url_contains_params(self, connector):
        """get_authorization_url contains redirect_uri, state, and scope."""
        url = connector.get_authorization_url("https://callback")
        assert "redirect_uri=https://callback" in url
        assert "scope=" in url
        assert "client_id=demo" in url

    def test_exchange_code_sets_tokens(self, connector):
        """exchange_code returns tokens using the provided code."""
        tokens = connector.exchange_code("abc123", "https://callback")
        assert tokens.access_token == "access-abc123"
        assert tokens.refresh_token == "refresh-abc123"
        assert connector.status == connector.status.CONNECTED

    def test_refresh_token_supported_vendor(self, auth_connector):
        """Supported vendors refresh tokens and keep status connected."""
        first_access = auth_connector.tokens.access_token
        refreshed = auth_connector.refresh_token()
        assert refreshed.access_token != first_access
        assert refreshed.refresh_token == auth_connector.tokens.refresh_token
        assert auth_connector.status == auth_connector.status.CONNECTED

    def test_refresh_token_unsupported_vendor(self, generic_connector):
        """Generic vendor refresh raises RuntimeError."""
        generic_connector.client_credentials()
        with pytest.raises(RuntimeError):
            generic_connector.refresh_token()

    def test_revoke_tokens_clears_state(self, auth_connector):
        """revoke_tokens clears tokens and sets DISCONNECTED status."""
        auth_connector.revoke_tokens()
        assert auth_connector.tokens is None
        assert auth_connector.status == auth_connector.status.DISCONNECTED

    def test_token_expiry_sets_status_expired(self, auth_connector):
        """Expired tokens trigger EXPIRED status on ensure_connection."""
        auth_connector.tokens.expires_at = datetime.utcnow() - timedelta(seconds=1)
        with pytest.raises(RuntimeError):
            auth_connector.fetch_patient()
        assert auth_connector.status == auth_connector.status.EXPIRED


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_rate_limiter_allows_initial_calls(self):
        """Rate limiter allows up to its limit within the window."""
        limiter = EHRConnector.SimpleRateLimiter(limit=2, window_seconds=1)
        assert limiter.allow() is True
        assert limiter.allow() is True

    def test_rate_limiter_blocks_after_limit(self):
        """Rate limiter blocks when limit is exceeded."""
        limiter = EHRConnector.SimpleRateLimiter(limit=1, window_seconds=10)
        assert limiter.allow() is True
        assert limiter.allow() is False

    def test_get_rate_limit_status(self, connector):
        """Connector reports rate limit diagnostics."""
        status = connector.get_rate_limit_status()
        assert isinstance(status, RateLimitStatus)
        assert status.limit == connector.rate_limiter.limit
        assert status.window_seconds == connector.rate_limiter.window


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------


class TestDataFetch:
    def test_fetch_requires_auth(self, connector):
        """Fetching without authentication raises."""
        with pytest.raises(RuntimeError):
            connector.fetch_patient()

    def test_fetch_patient_success(self, auth_connector):
        """fetch_patient returns a PatientSummary with demographics."""
        patient = auth_connector.fetch_patient()
        assert isinstance(patient, PatientSummary)
        assert patient.patient_id == auth_connector.config.sandbox_patient
        assert patient.first_name == "Test"
        assert patient.gender == "F"

    def test_fetch_encounters(self, auth_connector):
        """fetch_encounters returns encounter list with matching patient."""
        encs = auth_connector.fetch_encounters(auth_connector.config.sandbox_patient)
        assert len(encs) == 1
        assert encs[0].patient_id == auth_connector.config.sandbox_patient
        assert encs[0].encounter_type == "outpatient"

    def test_fetch_conditions(self, auth_connector):
        """fetch_conditions returns conditions with ICD-10 codes."""
        conds = auth_connector.fetch_conditions(auth_connector.config.sandbox_patient)
        assert len(conds) == 1
        assert conds[0].icd10_code == "E11.9"

    def test_fetch_procedures(self, auth_connector):
        """fetch_procedures returns CPT-coded procedures."""
        procs = auth_connector.fetch_procedures(auth_connector.config.sandbox_patient)
        assert len(procs) == 1
        assert procs[0].cpt_code == "99213"

    def test_fetch_observations(self, auth_connector):
        """fetch_observations returns lab-like observations."""
        obs = auth_connector.fetch_observations(auth_connector.config.sandbox_patient)
        assert len(obs) == 1
        assert obs[0].code == "718-7"
        assert obs[0].numeric_value == 13.2

    def test_fetch_medications(self, auth_connector):
        """fetch_medications returns active medications."""
        meds = auth_connector.fetch_medications(auth_connector.config.sandbox_patient)
        assert len(meds) == 1
        assert meds[0].medication_name == "Metformin 500mg"
        assert meds[0].status == "active"

    def test_fetch_allergies(self, auth_connector):
        """fetch_allergies returns allergy entries."""
        allergies = auth_connector.fetch_allergies(auth_connector.config.sandbox_patient)
        assert len(allergies) == 1
        assert allergies[0].substance == "Penicillin"

    def test_fetch_documents(self, auth_connector):
        """fetch_documents returns a document reference."""
        docs = auth_connector.fetch_documents(auth_connector.config.sandbox_patient)
        assert len(docs) == 1
        assert isinstance(docs[0], DocumentReferenceModel)
        assert docs[0].url.endswith(".pdf")

    def test_fetch_coverage(self, auth_connector):
        """fetch_coverage returns coverage details with member id."""
        cov = auth_connector.fetch_coverage(auth_connector.config.sandbox_patient)
        assert len(cov) == 1
        assert cov[0].member_id.startswith("MBR-")

    def test_fetch_immunizations(self, auth_connector):
        """fetch_immunizations returns immunization records."""
        imms = auth_connector.fetch_immunizations(auth_connector.config.sandbox_patient)
        assert len(imms) == 1
        assert imms[0].display_name == "COVID-19 vaccine"


# ---------------------------------------------------------------------------
# Summary and bundles
# ---------------------------------------------------------------------------


class TestSummaryAndBundle:
    def test_full_summary_compiles(self, auth_connector):
        """fetch_full_summary returns all major sections."""
        summary = auth_connector.fetch_full_summary()
        assert "patient" in summary and isinstance(summary["patient"], PatientSummary)
        assert summary["conditions"] and summary["procedures"]
        assert summary["observations"] and summary["medications"]

    def test_to_fhir_bundle_contains_entries(self, auth_connector):
        """to_fhir_bundle returns a Bundle with entries."""
        summary = auth_connector.fetch_full_summary()
        bundle = auth_connector.to_fhir_bundle(summary)
        assert bundle.get("resourceType") == "Bundle"
        assert len(bundle.get("entry", [])) >= 3

    def test_simulate_resource_overrides_ids(self, connector):
        """simulate_resource clones template and rewrites IDs for patient."""
        resource = connector.simulate_resource("conditions", "custom-id")
        assert resource["patient_id"] == "custom-id"
        assert resource["condition_id"].startswith("cond-")


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


class TestWriteOperations:
    def test_write_resource_success(self, auth_connector):
        """write_resource returns an accepted request with matching type."""
        req = auth_connector.write_resource("Observation", {"code": "123"})
        assert req.status == "accepted"
        assert req.resource_type == "Observation"
        assert req.request_type is DataRequestType.WRITE

    def test_update_resource_includes_message(self, auth_connector):
        """update_resource echoes updated resource id in message."""
        req = auth_connector.update_resource("Procedure", "proc-1", {})
        assert "proc-1" in req.message

    def test_post_claim_generates_claim_id(self, auth_connector):
        """post_claim returns a submission with a claim_id."""
        submission = auth_connector.post_claim({"patient_id": "p1"})
        assert submission.claim_id
        assert submission.patient_id == "p1"

    def test_post_coding_result_accepts_payload(self, auth_connector):
        """post_coding_result accepts an object with model_dump_json."""

        class DummyCoding:
            def __init__(self):
                self.scr_id = "scr-1"
                self.context_id = "enc-1"

            def model_dump_json(self) -> str:
                return "{}"

        submission = auth_connector.post_coding_result(DummyCoding())
        assert submission.patient_id == "scr-1"
        assert submission.encounter_id == "enc-1"
        assert submission.coding_result == {}


# ---------------------------------------------------------------------------
# Diagnostics and audit
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_connection_state(self, auth_connector):
        """get_connection_state reports current adapter and scope."""
        state = auth_connector.get_connection_state()
        assert isinstance(state, ConnectorState)
        assert state.status == auth_connector.status
        assert state.vendor is EHRVendor.EPIC
        assert state.scope == auth_connector.tokens.scope

    def test_record_audit_appends_event(self, connector):
        """record_audit adds an event to the audit log."""
        event = connector.record_audit("TEST", "detail")
        assert isinstance(event, AuditEvent)
        assert len(connector.audit_log) == 1
        assert connector.audit_log[0].event_type == "TEST"

    def test_simulated_rate_limit_exceeded_raises(self, auth_connector):
        """Overrunning the rate limit raises a RuntimeError."""
        auth_connector.rate_limiter = EHRConnector.SimpleRateLimiter(limit=1, window_seconds=60)
        auth_connector.fetch_patient()
        with pytest.raises(RuntimeError):
            auth_connector.fetch_patient()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_exchange_code_rate_limit_enforced(self, connector):
        """Rate limit enforcement triggers errors when exceeded."""
        connector.rate_limiter = EHRConnector.SimpleRateLimiter(limit=1, window_seconds=60)
        connector.exchange_code("one", "https://callback")
        with pytest.raises(RuntimeError):
            connector.exchange_code("two", "https://callback")

    def test_simulate_unknown_resource_raises(self, connector):
        """simulate_resource raises for unsupported types."""
        with pytest.raises(KeyError):
            connector.simulate_resource("unknown", "p1")

    def test_to_fhir_bundle_requires_connection(self, connector):
        """to_fhir_bundle without tokens raises for disconnected state."""
        summary = {
            "patient": PatientSummary(
                patient_id="p1",
                first_name="A",
                last_name="B",
                date_of_birth="2000-01-01",
                gender="M",
                mrn="MRN-p1",
            ),
            "encounters": [],
            "conditions": [],
            "procedures": [],
        }
        with pytest.raises(RuntimeError):
            connector.to_fhir_bundle(summary)
