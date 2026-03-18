"""
Tests for MEDI-COMPLY Audit Trail System
"""

import pytest
import os
import sqlite3
import tempfile
import asyncio
from datetime import datetime, timezone

from medi_comply.audit.audit_models import (
    InputReference, WorkflowTrace, AuditQuery, ExtractedEntitySummary
)
from medi_comply.audit.hash_chain import HashChain
from medi_comply.audit.audit_store import AuditStore, DuplicateAuditRecordError
from medi_comply.audit.decision_trace import DecisionTraceBuilder
from medi_comply.audit.evidence_mapper import EvidenceMapper
from medi_comply.audit.risk_scorer import AuditRiskScorer
from medi_comply.audit.report_generator import AuditReportGenerator
from medi_comply.agents.audit_trail_agent import AuditTrailAgent
from medi_comply.core.message_models import AgentMessage

from medi_comply.schemas.coding_result import CodingResult, SingleCodeDecision, ReasoningStep
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation, ConditionEntry
from medi_comply.nlp.evidence_tracker import SourceEvidence
from medi_comply.schemas.retrieval import CodeRetrievalContext, ConditionCodeCandidates, RankedCodeCandidate
from medi_comply.guardrails.guardrail_chain import ComplianceReport
from medi_comply.guardrails.layer3_structural import StructuralCheckResult

# ==================== FIXTURES ====================

@pytest.fixture
def temp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    os.remove(path)

@pytest.fixture
def audit_store(temp_db_path):
    return AuditStore(db_path=temp_db_path)

@pytest.fixture
def sample_scr():
    doc_id = "DOC1"
    scr_id = "SCR1"
    ev = SourceEvidence(exact_text="chest pain", page=1, line=1, char_offset=(0, 10))
    c1 = ConditionEntry(entity_id="1", text="chest pain", assertion="PRESENT", evidence=[ev])
    
    # Needs to match what EvidenceMapper expects (which we drafted expecting specific entity fields from older interfaces)
    # We will mock it exactly as the SCR builder output
    c1.entity = "chest pain"
    c1.section = "HPI"
    
    scr = StructuredClinicalRepresentation(document_id=doc_id)
    scr.conditions = [c1]
    scr.medications = []
    scr.procedures = []
    return scr

@pytest.fixture
def sample_retrieval():
    cc = RankedCodeCandidate(code="R07.9", description="Chest pain, unspecified", code_type="ICD10", relevance_score=0.95)
    res = ConditionCodeCandidates(
        condition_entity_id="1", condition_text="chest pain", normalized_text="chest pain",
        assertion="PRESENT", candidates=[cc]
    )
    # mock strategies inside retrieval_summary instead of a nonexistent field
    return CodeRetrievalContext(
        scr_id="SCR1",
        condition_candidates=[res], 
        procedure_candidates=[],
        retrieval_summary={"fusion_metrics": {"strategies_combined": ["exact"]}},
        total_candidates=1
    )

@pytest.fixture
def sample_coding():
    rs = ReasoningStep(step_number=1, action="ADD", detail="Linked to chest pain")
    return CodingResult(
        scr_id="SCR1", context_id="CTX1", created_at=datetime.now(timezone.utc), processing_time_ms=10.0,
        encounter_type="OUTPATIENT", patient_age=62, patient_gender="M", total_codes_assigned=1, total_icd10_codes=1, total_cpt_codes=0,
        diagnosis_codes=[SingleCodeDecision(code="R07.9", description="Chest pain, unspecified", code_type="ICD10", sequence_position="PRIMARY", sequence_number=1, reasoning_chain=[rs], clinical_evidence=[], alternatives_considered=[], confidence_score=0.95, confidence_factors=[], requires_human_review=False)], procedure_codes=[], overall_confidence=0.95, coding_summary="Done"
    )

@pytest.fixture
def sample_compliance():
    c3 = StructuralCheckResult(check_id="C1", check_name="C1", passed=True, severity="NONE", details="", check_time_ms=0.0)
    return ComplianceReport(
        report_id="RPT1", coding_result_id="CR1", created_at=datetime.now(timezone.utc), processing_time_ms=10.0,
        overall_decision="PASS", total_checks_run=1, checks_passed=1, checks_failed=0, checks_skipped=0,
        overall_risk_score=0.0, risk_level="ROUTINE", risk_factors=[], security_alerts=[], phi_detected=False, injection_detected=False,
        layer3_results=[c3], layer4_results=[], layer5_results=[]
    )

@pytest.fixture
def input_ref():
    return InputReference(document_id="DOC1", document_hash="hash1", document_type="CLINICAL_NOTE", encounter_type="OUTPATIENT", page_count=1, character_count=100)

@pytest.fixture
def full_trace(sample_scr, sample_retrieval, sample_coding, sample_compliance, input_ref):
    tb = DecisionTraceBuilder()
    tb.start_trace("MEDICAL_CODING", input_ref)
    tb.record_nlp_stage(sample_scr, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_retrieval_stage(sample_retrieval, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_coding_stage(sample_coding, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_compliance_stage(sample_compliance, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    return tb.build_trace(sample_coding)

# ==================== HASH CHAIN TESTS ====================

def test_hash_chain_compute():
    hc = HashChain()
    data = {"a": 1, "b": "test"}
    h1 = hc.compute_record_hash(data)
    h2 = hc.compute_record_hash({"b": "test", "a": 1})
    assert h1 == h2  # deterministic canonical output

def test_hash_chain_different_data():
    hc = HashChain()
    h1 = hc.compute_record_hash({"a": 1})
    h2 = hc.compute_record_hash({"a": 2})
    assert h1 != h2

def test_hash_chain_linking():
    hc = HashChain()
    r1, p1 = hc.create_chain_link({"id": 1})
    assert p1 is None
    r2, p2 = hc.create_chain_link({"id": 2})
    assert p2 == r1
    r3, p3 = hc.create_chain_link({"id": 3})
    assert p3 == r2
    assert hc.get_chain_length() == 3

def test_hash_chain_verify_valid():
    hc = HashChain()
    records = []
    for i in range(3):
        data = {"id": i}
        r_hash, p_hash = hc.create_chain_link(data)
        data["record_hash"] = r_hash
        data["previous_record_hash"] = p_hash
        records.append(data)
    
    vr = hc.verify_chain(records)
    assert vr.is_valid
    assert vr.records_checked == 3

def test_hash_chain_verify_tampered():
    hc = HashChain()
    records = []
    for i in range(3):
        data = {"id": i}
        r_hash, p_hash = hc.create_chain_link(data)
        data["record_hash"] = r_hash
        data["previous_record_hash"] = p_hash
        records.append(data)
        
    # Tamper with middle record
    records[1]["id"] = 99
    
    vr = hc.verify_chain(records)
    assert not vr.is_valid
    assert vr.first_broken_link == 1

def test_hash_chain_canonical_serialization():
    hc = HashChain()
    import pydantic
    class Mock(pydantic.BaseModel):
         x: int
    data = {"obj": Mock(x=42), "time": datetime(2025, 1, 1, 12, 0)}
    s = hc._canonical_serialize(data)
    assert "2025-01-01T12:00:00" in s
    assert "42" in s

# ==================== AUDIT STORE TESTS ====================

def test_store_and_retrieve(audit_store, full_trace):
    tid = audit_store.store(full_trace)
    assert tid == full_trace.trace_id
    
    retrieved = audit_store.retrieve(tid)
    assert retrieved is not None
    assert retrieved.trace_id == tid
    assert retrieved.record_hash == full_trace.record_hash

def test_immutability_no_update(audit_store, full_trace):
    tid = audit_store.store(full_trace)
    with pytest.raises(sqlite3.IntegrityError, match="Audit records are immutable and cannot be updated."):
         with audit_store._get_connection() as conn:
              conn.execute("UPDATE audit_records SET risk_score = 99.0 WHERE trace_id = ?", (tid,))

def test_immutability_no_delete(audit_store, full_trace):
    tid = audit_store.store(full_trace)
    with pytest.raises(sqlite3.IntegrityError, match="Audit records are immutable and cannot be deleted."):
         with audit_store._get_connection() as conn:
              conn.execute("DELETE FROM audit_records WHERE trace_id = ?", (tid,))

def test_duplicate_trace_id(audit_store, full_trace):
    audit_store.store(full_trace)
    with pytest.raises(DuplicateAuditRecordError):
         audit_store.store(full_trace)

def test_search_by_risk(audit_store, full_trace):
    full_trace.compliance_stage.risk_level = "CRITICAL"
    audit_store.store(full_trace)
    
    res = audit_store.retrieve_by_risk_level("CRITICAL")
    assert len(res) == 1
    assert res[0].risk_level == "CRITICAL"
    
def test_search_by_code(audit_store, full_trace):
    audit_store.store(full_trace)
    res = audit_store.retrieve_by_code("R07.9")
    assert len(res) == 1
    assert res[0].trace_id == full_trace.trace_id

def test_search_escalated(audit_store, full_trace):
    full_trace.final_output.was_escalated = True
    audit_store.store(full_trace)
    res = audit_store.retrieve_escalated()
    assert len(res) == 1
    
def test_statistics(audit_store, full_trace):
    audit_store.store(full_trace)
    stats = audit_store.get_statistics()
    assert stats.total_records == 1
    assert stats.chain_integrity == "VALID"
    assert len(stats.top_10_codes) == 1
    
def test_chain_integrity(audit_store, full_trace):
    audit_store.store(full_trace)
    
    # second record
    ft2 = full_trace.model_copy(deep=True)
    ft2.trace_id = "AUD-TEST-002"
    audit_store.store(ft2)
    
    vr = audit_store.verify_chain_integrity()
    assert vr.is_valid
    assert vr.records_checked == 2

def test_export_json(audit_store, full_trace):
    audit_store.store(full_trace)
    ex = audit_store.export_records([full_trace.trace_id], "json")
    import json
    parsed = json.loads(ex)
    assert len(parsed) == 1
    assert parsed[0]["trace_id"] == full_trace.trace_id

# ==================== DECISION TRACE TESTS ====================

def test_trace_builder_start(input_ref):
    tb = DecisionTraceBuilder()
    tid = tb.start_trace("MEDICAL_CODING", input_ref)
    assert tid.startswith("AUD-")
    assert tb._input_ref == input_ref

def test_trace_builder_all_stages(sample_scr, sample_retrieval, sample_coding, sample_compliance, input_ref):
    tb = DecisionTraceBuilder()
    tb.start_trace("MEDICAL_CODING", input_ref)
    tb.record_nlp_stage(sample_scr, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_retrieval_stage(sample_retrieval, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_coding_stage(sample_coding, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    tb.record_compliance_stage(sample_compliance, 10.0, datetime.now(timezone.utc), datetime.now(timezone.utc))
    
    trace = tb.build_trace(sample_coding)
    assert trace.nlp_stage.agent_name == "ClinicalNLPAgent"
    assert trace.retrieval_stage.total_candidates_retrieved == 1
    assert len(trace.coding_stage.code_decisions) == 1
    assert trace.compliance_stage.overall_decision == "PASS"

def test_trace_builder_retries(full_trace, sample_coding):
    tb = DecisionTraceBuilder()
    tb.start_trace("MEDICAL_CODING", InputReference(document_id="D", document_hash="H", document_type="T", encounter_type="OUTPATIENT", page_count=1, character_count=100))
    tb.record_retry(1, ["Needs more specific code"], [], "RETRY")
    trace = tb.build_trace(sample_coding)
    assert len(trace.retry_history) == 1
    assert trace.total_attempts == 2

def test_trace_builder_llm_interactions(sample_coding):
    tb = DecisionTraceBuilder()
    tb.start_trace("MEDICAL_CODING", InputReference(document_id="D", document_hash="H", document_type="T", encounter_type="OUTPATIENT", page_count=1, character_count=100))
    tb.record_llm_interaction("gpt-4", "v1", "coding_template", 100, 50, 200.0, True, True)
    trace = tb.build_trace(sample_coding)
    assert len(trace.coding_stage.llm_interactions) == 1
    assert trace.coding_stage.llm_interactions[0].prompt_token_count == 100

def test_trace_builder_state_transitions(sample_coding):
    tb = DecisionTraceBuilder()
    tb.start_trace("MEDICAL_CODING", InputReference(document_id="D", document_hash="H", document_type="T", encounter_type="OUTPATIENT", page_count=1, character_count=100))
    tb.record_state_transition("AuditTrailAgent", "IDLE", "THINKING", "Start")
    trace = tb.build_trace(sample_coding)
    assert len(trace.nlp_stage.state_transitions) == 0

# ==================== EVIDENCE MAPPER TESTS ====================

def test_evidence_map_code_to_evidence(sample_scr, sample_coding):
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    assert "R07.9" in em.code_to_evidence
    assert len(em.code_to_evidence["R07.9"]) == 1
    assert em.code_to_evidence["R07.9"][0].source_text == "chest pain"

def test_evidence_map_bidirectional(sample_scr, sample_coding):
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    keys = list(em.evidence_to_codes.keys())
    assert len(keys) == 1
    assert em.evidence_to_codes[keys[0]]["codes_supported"][0] == "R07.9"

def test_evidence_coverage_score(sample_scr, sample_coding):
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    assert em.coverage_score == 1.0

def test_evidence_gap_detection(sample_scr, sample_coding):
    # Empty coding result with 1 condition in SCR -> gap
    m = EvidenceMapper()
    empty_coding = sample_coding.model_copy()
    empty_coding.diagnosis_codes = []
    em = m.build_evidence_map(empty_coding, sample_scr)
    assert em.coverage_score < 1.0
    gaps = m.find_evidence_gaps(em)
    assert len(gaps[1]["items"]) == 1 # 1 unlinked evidence

# ==================== RISK SCORER TESTS ====================

def test_risk_low(full_trace):
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    assert res.risk_level == "ROUTINE"

def test_risk_high(full_trace):
    full_trace.final_output.overall_confidence = 0.50
    full_trace.total_attempts = 3
    full_trace.compliance_stage.checks_failed = 1
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    assert res.risk_level in ["ELEVATED", "URGENT"]
    assert len(res.risk_factors_triggered) >= 3

# ==================== REPORT GENERATOR TESTS ====================

def test_summary_report_format(full_trace):
    gen = AuditReportGenerator()
    sm = gen.generate_summary_report(full_trace)
    assert "MEDI-COMPLY AUDIT REPORT" in sm
    assert "ASSIGNED CODES" in sm
    assert "R07.9" in sm
    assert "COMPLIANCE" in sm

def test_code_explanation_card(full_trace):
    gen = AuditReportGenerator()
    code_decision = full_trace.coding_stage.code_decisions[0]
    card = gen.generate_code_explanation_card(code_decision)
    assert "REASONING CHAIN" in card
    assert "ALTERNATIVES CONSIDERED" in card

def test_compliance_certificate(full_trace):
    gen = AuditReportGenerator()
    cert = gen.generate_compliance_certificate(full_trace)
    assert "Hash chain integrity: VERIFIED." in cert
    assert str(full_trace.trace_id) in cert

# ==================== AGENT TESTS ====================

@pytest.mark.asyncio
async def test_agent_process(audit_store, sample_scr, sample_retrieval, sample_coding, sample_compliance, input_ref):
    agent = AuditTrailAgent(audit_store)
    msg = AgentMessage(from_agent="Manager", to_agent="Audit", action="RECORD_TRACE", payload={
        "scr": sample_scr, "retrieval_context": sample_retrieval,
        "coding_result": sample_coding, "compliance_report": sample_compliance, 
        "input_reference": input_ref
    })
    
    resp = await agent.process(msg)
    assert resp.status == "SUCCESS"
    
    data = resp.payload
    assert "workflow_trace" in data
    assert "audit_report" in data
    
    # Verify it hit the DB
    assert audit_store.get_record_count() == 1

def test_agent_observer_type(audit_store):
    agent = AuditTrailAgent(audit_store)
    assert agent.agent_type == "OBSERVER"

def test_agent_records_stored_counter(audit_store, sample_scr, sample_retrieval, sample_coding, sample_compliance, input_ref):
    """Records stored counter increments on each process call."""
    agent = AuditTrailAgent(audit_store)
    assert agent.records_stored == 0
    msg = AgentMessage(
        from_agent="Manager", to_agent="Audit", action="RECORD_TRACE",
        payload={
            "scr": sample_scr, "retrieval_context": sample_retrieval,
            "coding_result": sample_coding, "compliance_report": sample_compliance,
            "input_reference": input_ref,
        },
    )
    import asyncio
    asyncio.get_event_loop().run_until_complete(agent.process(msg))
    assert agent.records_stored == 1


# ==================== ADDITIONAL HASH CHAIN TESTS ====================

def test_hash_chain_empty_data():
    """Hashing an empty dict produces a consistent result."""
    hc = HashChain()
    h1 = hc.compute_record_hash({})
    h2 = hc.compute_record_hash({})
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex length

def test_hash_chain_nested_objects():
    """Nested dicts and lists hash deterministically."""
    hc = HashChain()
    data = {"outer": {"inner": [1, 2, 3], "key": "value"}}
    h1 = hc.compute_record_hash(data)
    h2 = hc.compute_record_hash({"outer": {"key": "value", "inner": [1, 2, 3]}})
    assert h1 == h2

def test_hash_chain_single_link():
    """A chain with one link has no previous hash."""
    hc = HashChain()
    r, p = hc.create_chain_link({"id": "only"})
    assert p is None
    assert hc.get_chain_length() == 1

def test_hash_chain_verify_empty():
    """Verifying an empty chain succeeds trivially."""
    hc = HashChain()
    vr = hc.verify_chain([])
    assert vr.is_valid
    assert vr.records_checked == 0


# ==================== ADDITIONAL AUDIT STORE TESTS ====================

def test_store_get_record_count(audit_store, full_trace):
    """get_record_count reflects stored records."""
    assert audit_store.get_record_count() == 0
    audit_store.store(full_trace)
    assert audit_store.get_record_count() == 1

def test_store_retrieve_nonexistent(audit_store):
    """Retrieving a nonexistent trace_id returns None."""
    result = audit_store.retrieve("AUD-DOES-NOT-EXIST")
    assert result is None

def test_store_export_csv(audit_store, full_trace):
    """Export as CSV returns a non-empty string with trace data."""
    audit_store.store(full_trace)
    csv_str = audit_store.export_records([full_trace.trace_id], "csv")
    assert len(csv_str) > 0
    assert full_trace.trace_id in csv_str


# ==================== ADDITIONAL DECISION TRACE TESTS ====================

def test_trace_builder_hash_populated(audit_store, full_trace):
    """Record hash is populated after storing in AuditStore."""
    # build_trace() sets record_hash="" initially; store() fills it
    audit_store.store(full_trace)
    stored = audit_store.retrieve(full_trace.trace_id)
    assert stored.record_hash is not None
    assert len(stored.record_hash) == 64

def test_trace_builder_processing_time(full_trace):
    """Processing time is non-negative."""
    assert full_trace.total_processing_time_ms >= 0.0

def test_trace_builder_final_output(full_trace):
    """final_output fields are populated from coding_result."""
    fo = full_trace.final_output
    assert fo.total_codes == 1
    assert fo.overall_confidence == 0.95
    assert fo.was_escalated is False


# ==================== ADDITIONAL EVIDENCE MAPPER TESTS ====================

def test_evidence_mapper_get_evidence_for_code(sample_scr, sample_coding):
    """get_evidence_for_code returns links for a known code."""
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    links = m.get_evidence_for_code(em, "R07.9")
    assert len(links) == 1
    assert links[0].code == "R07.9"
    assert links[0].source_text == "chest pain"

def test_evidence_mapper_get_evidence_for_unknown_code(sample_scr, sample_coding):
    """get_evidence_for_code returns empty for an unknown code."""
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    links = m.get_evidence_for_code(em, "Z99.999")
    assert links == []

def test_evidence_mapper_get_codes_for_evidence(sample_scr, sample_coding):
    """get_codes_for_evidence returns code info for known evidence."""
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    results = m.get_codes_for_evidence(em, "HPI", 1, 1)
    assert len(results) == 1
    assert "R07.9" in results[0]["codes_supported"]

def test_evidence_mapper_weak_links(sample_scr, sample_coding):
    """get_weak_links returns empty when all links are strong."""
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    weak = m.get_weak_links(em)
    assert weak == []

def test_evidence_gap_always_two_entries(sample_scr, sample_coding):
    """find_evidence_gaps always returns exactly 2 entries (code, evidence)."""
    m = EvidenceMapper()
    em = m.build_evidence_map(sample_coding, sample_scr)
    gaps = m.find_evidence_gaps(em)
    assert len(gaps) == 2
    assert gaps[0]["type"] == "UNLINKED_CODE"
    assert gaps[1]["type"] == "UNLINKED_EVIDENCE"


# ==================== ADDITIONAL RISK SCORER TESTS ====================

def test_risk_routine_threshold(full_trace):
    """Overall confidence above 0.85 and few flags → low risk."""
    full_trace.final_output.overall_confidence = 0.95
    full_trace.total_attempts = 1
    full_trace.compliance_stage.checks_failed = 0
    full_trace.compliance_stage.checks_skipped = 0
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    # May still trigger unspecified_code or rule_based_fallback
    assert res.risk_level in ["ROUTINE", "ELEVATED"]

def test_risk_unspecified_code_flag(full_trace):
    """A code ending in '9' triggers unspecified_code_used risk factor."""
    # R07.9 ends in 9, so it should trigger unspecified code factor
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    factor_names = [f["factor"] for f in res.risk_factors_triggered]
    assert "unspecified_code_used" in factor_names

def test_risk_multiple_retries_flag(full_trace):
    """Multiple attempts triggers the multiple_retries factor."""
    full_trace.total_attempts = 4
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    factor_names = [f["factor"] for f in res.risk_factors_triggered]
    assert "multiple_retries" in factor_names

def test_risk_compliance_soft_fail_flag(full_trace):
    """Failed compliance checks trigger the soft_fails factor."""
    full_trace.compliance_stage.checks_failed = 2
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    factor_names = [f["factor"] for f in res.risk_factors_triggered]
    assert "compliance_soft_fails" in factor_names

def test_risk_capped_at_one(full_trace):
    """Score is capped at 1.0 even with many triggered factors."""
    full_trace.final_output.overall_confidence = 0.30
    full_trace.total_attempts = 5
    full_trace.compliance_stage.checks_failed = 3
    full_trace.compliance_stage.checks_skipped = 2
    full_trace.final_output.total_codes = 20
    sc = AuditRiskScorer()
    res = sc.calculate_risk(full_trace)
    assert res.overall_score <= 1.0
    # With multiple risk factors triggered, should be at least URGENT
    assert res.risk_level in ["URGENT", "IMMEDIATE"]


# ==================== ADDITIONAL REPORT GENERATOR TESTS ====================

def test_full_report_generation(full_trace):
    """generate_full_report returns a populated AuditReport."""
    gen = AuditReportGenerator()
    report = gen.generate_full_report(full_trace)
    assert report is not None
    assert report.trace_id == full_trace.trace_id
    assert len(report.summary) > 0

def test_json_export_structure(full_trace):
    """generate_json_export returns a dict with expected keys."""
    gen = AuditReportGenerator()
    data = gen.generate_json_export(full_trace)
    assert isinstance(data, dict)
    assert "trace_id" in data
    assert "_export_metadata" in data
    assert data["trace_id"] == full_trace.trace_id

def test_summary_report_contains_codes(full_trace):
    """Summary report mentions all assigned codes."""
    gen = AuditReportGenerator()
    summary = gen.generate_summary_report(full_trace)
    assert "R07.9" in summary

def test_compliance_cert_mentions_trace_id(full_trace):
    """Compliance certificate includes the trace identifier."""
    gen = AuditReportGenerator()
    cert = gen.generate_compliance_certificate(full_trace)
    assert full_trace.trace_id in cert


# ==================== QUERY ENGINE TESTS ====================

def test_query_engine_search_empty(audit_store):
    """Searching an empty store returns zero results."""
    from medi_comply.audit.query_engine import AuditQueryEngine
    qe = AuditQueryEngine(audit_store)
    query = AuditQuery()
    result = qe.search(query)
    assert result.total_matching == 0
    assert result.returned == 0

def test_query_engine_search_with_data(audit_store, full_trace):
    """Searching a populated store returns the stored record."""
    from medi_comply.audit.query_engine import AuditQueryEngine
    audit_store.store(full_trace)
    qe = AuditQueryEngine(audit_store)
    query = AuditQuery()
    result = qe.search(query)
    assert result.total_matching == 1
    assert result.returned == 1

def test_query_engine_get_full_trace(audit_store, full_trace):
    """get_full_trace retrieves a trace by ID."""
    from medi_comply.audit.query_engine import AuditQueryEngine
    audit_store.store(full_trace)
    qe = AuditQueryEngine(audit_store)
    trace = qe.get_full_trace(full_trace.trace_id)
    assert trace is not None
    assert trace.trace_id == full_trace.trace_id

def test_query_engine_get_accuracy_metrics(audit_store, full_trace):
    """get_accuracy_metrics returns aggregate statistics."""
    from medi_comply.audit.query_engine import AuditQueryEngine
    audit_store.store(full_trace)
    qe = AuditQueryEngine(audit_store)
    metrics = qe.get_accuracy_metrics(days=365)
    assert metrics["total_encounters"] == 1
    assert metrics["auto_completed"] >= 0

