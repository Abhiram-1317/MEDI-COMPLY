"""
MEDI-COMPLY — Audit Data Models.
Comprehensive, PHI-secure data models for the audit trail system.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel

class InputReference(BaseModel):
    document_id: str
    document_hash: str
    document_type: str
    encounter_id: Optional[str] = None
    encounter_type: str
    document_date: Optional[str] = None
    page_count: int = 1
    character_count: int = 0

class ExtractedEntitySummary(BaseModel):
    entity_id: str
    entity_text: str
    entity_type: str
    assertion: str
    section_found_in: str
    confidence: float
    evidence_page: int
    evidence_line: int

class StateTransitionRecord(BaseModel):
    agent_name: str
    from_state: str
    to_state: str
    timestamp: datetime
    trigger: str

class NLPStageRecord(BaseModel):
    stage_id: str
    agent_name: str
    started_at: datetime
    completed_at: datetime
    processing_time_ms: float
    sections_detected: list[str]
    total_entities_extracted: int
    entities_by_type: dict
    conditions_extracted: list[ExtractedEntitySummary]
    procedures_extracted: list[ExtractedEntitySummary]
    medications_extracted: list[ExtractedEntitySummary]
    negated_findings: list[str]
    uncertain_findings: list[str]
    extraction_methods_used: list[str]
    average_confidence: float
    low_confidence_entities: list[str]
    state_transitions: list[StateTransitionRecord]

class RetrievalDetail(BaseModel):
    entity_id: str
    entity_text: str
    candidates_count: int
    top_3_candidates: list[dict]
    strategies_contributed: list[str]
    retrieval_time_ms: float

class RetrievalStageRecord(BaseModel):
    stage_id: str
    agent_name: str
    started_at: datetime
    completed_at: datetime
    processing_time_ms: float
    conditions_processed: int
    procedures_processed: int
    retrieval_details: list[RetrievalDetail]
    knowledge_base_version: str
    strategies_used: list[str]
    total_candidates_retrieved: int
    guidelines_retrieved: list[str]
    excludes_warnings_found: int
    ncci_warnings_found: int
    state_transitions: list[StateTransitionRecord]

class ReasoningStepRecord(BaseModel):
    step_number: int
    action: str
    detail: str
    evidence_ref: Optional[str] = None
    guideline_ref: Optional[str] = None
    timestamp: datetime

class EvidenceLinkRecord(BaseModel):
    evidence_id: str
    code: str
    source_text: str
    section: str
    page: int
    line: int
    char_offset: tuple[int, int]
    relevance: str
    link_strength: float

class AlternativeRecord(BaseModel):
    code: str
    description: str
    reason_rejected: str
    was_candidate_rank: int

class CodeDecisionRecord(BaseModel):
    decision_id: str
    code: str
    code_type: str
    description: str
    sequence_position: str
    sequence_number: int
    reasoning_chain: list[ReasoningStepRecord]
    evidence_links: list[EvidenceLinkRecord]
    alternatives_considered: list[AlternativeRecord]
    confidence_score: float
    confidence_factors: list[dict]
    is_combination_code: bool
    is_use_additional: bool
    is_code_first: bool
    guidelines_cited: list[str]
    decision_method: str

class SequencingRecord(BaseModel):
    primary_code: str
    primary_rationale: str
    full_sequence: list[dict]
    guidelines_applied: list[str]
    confidence: float

class CombinationRecord(BaseModel):
    combination_type: str
    individual_conditions: list[str]
    combination_code: str
    replaced_codes: list[str]
    additional_codes_added: list[str]
    guideline_ref: str

class LLMInteractionRecord(BaseModel):
    interaction_id: str
    timestamp: datetime
    model_name: str
    model_version: str
    prompt_template: str
    prompt_token_count: int
    response_token_count: int
    total_tokens: int
    latency_ms: float
    response_parsed_successfully: bool
    validation_passed: bool
    validation_issues: list[str]

class CodingStageRecord(BaseModel):
    stage_id: str
    agent_name: str
    started_at: datetime
    completed_at: datetime
    processing_time_ms: float
    attempt_number: int
    code_decisions: list[CodeDecisionRecord]
    sequencing_decision: SequencingRecord
    combination_codes_applied: list[CombinationRecord]
    total_codes_assigned: int
    overall_confidence: float
    codes_flagged_for_review: list[str]
    llm_interactions: list[LLMInteractionRecord]
    state_transitions: list[StateTransitionRecord]

class LayerSummary(BaseModel):
    layer_name: str
    checks_run: int
    checks_passed: int
    checks_failed: int
    check_details: list[dict]

class ComplianceStageRecord(BaseModel):
    stage_id: str
    agent_name: str
    started_at: datetime
    completed_at: datetime
    processing_time_ms: float
    overall_decision: str
    total_checks_run: int
    checks_passed: int
    checks_failed: int
    checks_skipped: int
    layer3_summary: LayerSummary
    layer4_summary: LayerSummary
    layer5_summary: LayerSummary
    risk_score: float
    risk_level: str
    risk_factors: list[str]
    security_alerts: list[str]
    feedback_generated: Optional[str] = None
    state_transitions: list[StateTransitionRecord]

class RetryRecord(BaseModel):
    attempt_number: int
    triggered_at: datetime
    trigger_reason: str
    feedback_provided: list[str]
    codes_changed: list[dict]
    compliance_result_after: str

class FinalOutputRecord(BaseModel):
    output_id: str
    final_diagnosis_codes: list[dict]
    final_procedure_codes: list[dict]
    overall_confidence: float
    total_codes: int
    human_review_required: bool
    review_reasons: list[str]
    was_escalated: bool
    escalation_reason: Optional[str] = None
    coding_summary: str

class SystemMetadata(BaseModel):
    system_version: str
    knowledge_base_version: str
    knowledge_base_last_updated: str
    models_used: dict
    configuration: dict
    deployment_environment: str

class WorkflowTrace(BaseModel):
    trace_id: str
    workflow_type: str
    started_at: datetime
    completed_at: datetime
    total_processing_time_ms: float
    input_reference: InputReference
    nlp_stage: NLPStageRecord
    retrieval_stage: RetrievalStageRecord
    coding_stage: CodingStageRecord
    compliance_stage: ComplianceStageRecord
    retry_history: list[RetryRecord]
    total_attempts: int
    final_output: FinalOutputRecord
    system_metadata: SystemMetadata
    record_hash: str
    previous_record_hash: Optional[str] = None
    digital_signature: str

class AuditSearchResult(BaseModel):
    trace_id: str
    workflow_type: str
    created_at: datetime
    encounter_type: str
    total_codes: int
    overall_confidence: float
    risk_score: float
    risk_level: str
    compliance_decision: str
    was_escalated: bool
    processing_time_ms: float

class AuditQuery(BaseModel):
    date_range: Optional[dict] = None
    workflow_type: Optional[str] = None
    risk_level: Optional[list[str]] = None
    compliance_decision: Optional[list[str]] = None
    code_filter: Optional[str] = None
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    was_escalated: Optional[bool] = None
    sort_by: str = "created_at"
    sort_order: str = "DESC"
    limit: int = 50
    offset: int = 0

class AuditQueryResult(BaseModel):
    total_matching: int
    returned: int
    offset: int
    results: list[AuditSearchResult]

class AuditRiskAssessment(BaseModel):
    overall_score: float
    risk_level: str
    risk_factors_triggered: list[dict]
    recommendations: list[str]
    audit_priority: str

class AuditStatistics(BaseModel):
    total_records: int
    records_by_workflow: dict
    records_by_risk_level: dict
    records_by_compliance: dict
    average_confidence: float
    average_processing_time_ms: float
    escalation_rate: float
    top_10_codes: list[dict]
    chain_integrity: str
    date_range: dict

class ChainVerificationResult(BaseModel):
    is_valid: bool
    records_checked: int
    first_broken_link: Optional[int] = None
    broken_details: Optional[str] = None
    verification_time_ms: float

class EvidenceMap(BaseModel):
    code_to_evidence: dict[str, list[EvidenceLinkRecord]]
    evidence_to_codes: dict[str, dict]
    unlinked_evidence: list[str]
    unlinked_codes: list[str]
    coverage_score: float

class AuditReport(BaseModel):
    report_id: str
    trace_id: str
    generated_at: datetime
    summary: str
    code_explanations: list[str]
    compliance_summary: str
    risk_assessment: AuditRiskAssessment
    evidence_map_summary: dict
    json_export: dict
    compliance_certificate: str
