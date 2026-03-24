"""
MEDI-COMPLY — Decision Trace Builder.
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from medi_comply.audit.audit_models import (
    WorkflowTrace, InputReference, NLPStageRecord, RetrievalStageRecord,
    CodingStageRecord, ComplianceStageRecord, CodeDecisionRecord,
    EvidenceLinkRecord, ReasoningStepRecord, AlternativeRecord,
    SequencingRecord, CombinationRecord, LLMInteractionRecord,
    RetryRecord, FinalOutputRecord, SystemMetadata,
    StateTransitionRecord, ExtractedEntitySummary, RetrievalDetail, LayerSummary
)
from medi_comply.schemas.coding_result import CodingResult, SingleCodeDecision
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.agents.knowledge_retrieval_agent import CodeRetrievalContext
from medi_comply.guardrails.guardrail_chain import ComplianceReport
from medi_comply.core.utils import (
    safe_get_confidence,
    safe_get_evidence,
    safe_get_section,
    safe_get_text,
)


def _get_evidence_attr(evidence: list[Any], attr: str, default: int | float = 0) -> int | float:
    if not evidence:
        return default
    first = evidence[0]
    if isinstance(first, dict):
        return first.get(attr, default)
    return getattr(first, attr, default)


class DecisionTraceBuilder:
    """
    Builds a complete, traceable decision record from all pipeline stages.
    Takes outputs from every stage and combines them into WorkflowTrace.
    """
    
    def __init__(self):
        self._trace_id: Optional[str] = None
        self._started_at: Optional[datetime] = None
        self._stage_records: dict = {}
        self._state_transitions: list[StateTransitionRecord] = []
        self._llm_interactions: list[LLMInteractionRecord] = []
        self._retry_records: list[RetryRecord] = []
        self._input_ref: Optional[InputReference] = None
        self._workflow_type: Optional[str] = None
    
    def start_trace(self, workflow_type: str, input_reference: InputReference) -> str:
        self._trace_id = f"AUD-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8]}"
        self._started_at = datetime.now(timezone.utc)
        self._workflow_type = workflow_type
        self._input_ref = input_reference
        return self._trace_id
    
    def record_nlp_stage(
        self, scr: StructuredClinicalRepresentation, processing_time_ms: float,
        started_at: datetime, completed_at: datetime
    ) -> None:
        
        ents_c: list[ExtractedEntitySummary] = []
        for c in scr.conditions:
            evidence = safe_get_evidence(c)
            ents_c.append(
                ExtractedEntitySummary(
                    entity_id=c.entity_id,
                    entity_text=safe_get_text(c),
                    entity_type="CONDITION",
                    assertion=c.assertion,
                    section_found_in=safe_get_section(c),
                    confidence=safe_get_confidence(c, getattr(c, "confidence", 0.0)),
                    evidence_page=_get_evidence_attr(evidence, "page", 0),
                    evidence_line=_get_evidence_attr(evidence, "line", 0),
                )
            )

        ents_m: list[ExtractedEntitySummary] = []
        for m in scr.medications:
            evidence = safe_get_evidence(m)
            ents_m.append(
                ExtractedEntitySummary(
                    entity_id=m.entity_id,
                    entity_text=safe_get_text(m),
                    entity_type="MEDICATION",
                    assertion=m.status,
                    section_found_in=safe_get_section(m),
                    confidence=safe_get_confidence(m, getattr(m, "confidence", 0.0)),
                    evidence_page=_get_evidence_attr(evidence, "page", 0),
                    evidence_line=_get_evidence_attr(evidence, "line", 0),
                )
            )

        ents_p: list[ExtractedEntitySummary] = []
        for p in scr.procedures:
            evidence = safe_get_evidence(p)
            ents_p.append(
                ExtractedEntitySummary(
                    entity_id=p.entity_id,
                    entity_text=safe_get_text(p),
                    entity_type="PROCEDURE",
                    assertion=p.status,
                    section_found_in=safe_get_section(p),
                    confidence=safe_get_confidence(p, getattr(p, "confidence", 0.0)),
                    evidence_page=_get_evidence_attr(evidence, "page", 0),
                    evidence_line=_get_evidence_attr(evidence, "line", 0),
                )
            )
        
        self._stage_records["nlp"] = NLPStageRecord(
            stage_id=f"STG-NLP-{uuid.uuid4().hex[:6]}", agent_name="ClinicalNLPAgent",
            started_at=started_at, completed_at=completed_at, processing_time_ms=processing_time_ms,
            sections_detected=scr.detect_sections() if hasattr(scr, "detect_sections") else ["ASSESSMENT"],
            total_entities_extracted=len(ents_c) + len(ents_m) + len(ents_p),
            entities_by_type={"CONDITION": len(ents_c), "MEDICATION": len(ents_m), "PROCEDURE": len(ents_p)},
            conditions_extracted=ents_c, procedures_extracted=ents_p, medications_extracted=ents_m,
            negated_findings=scr.negated_findings if hasattr(scr, "negated_findings") else [],
            uncertain_findings=scr.uncertain_findings if hasattr(scr, "uncertain_findings") else [],
            extraction_methods_used=scr.methods_used if hasattr(scr, "methods_used") else [],
            average_confidence=scr.average_confidence if hasattr(scr, "average_confidence") else 0.95,
            low_confidence_entities=scr.low_confidence_flags if hasattr(scr, "low_confidence_flags") else [],
            state_transitions=[s for s in self._state_transitions if s.agent_name == "ClinicalNLPAgent"]
        )
    
    def record_retrieval_stage(
        self, retrieval_context: CodeRetrievalContext, processing_time_ms: float,
        started_at: datetime, completed_at: datetime
    ) -> None:
        
        details = []
        for r_res in retrieval_context.condition_candidates:
            details.append(RetrievalDetail(
                entity_id=r_res.condition_entity_id, entity_text=r_res.condition_text,
                candidates_count=len(r_res.candidates),
                top_3_candidates=[{"code": c.code, "description": c.description, "score": c.relevance_score} for c in r_res.candidates[:3]],
                strategies_contributed=r_res.retrieval_metadata.strategies_used,
                retrieval_time_ms=r_res.retrieval_metadata.retrieval_time_ms
            ))
            
        for r_res in retrieval_context.procedure_candidates:
            details.append(RetrievalDetail(
                entity_id=r_res.procedure_entity_id, entity_text=r_res.procedure_text,
                candidates_count=len(r_res.candidates),
                top_3_candidates=[{"code": c.code, "description": c.description, "score": c.relevance_score} for c in r_res.candidates[:3]],
                strategies_contributed=r_res.retrieval_metadata.strategies_used,
                retrieval_time_ms=r_res.retrieval_metadata.retrieval_time_ms
            ))
            
        self._stage_records["retrieval"] = RetrievalStageRecord(
             stage_id=f"STG-RET-{uuid.uuid4().hex[:6]}", agent_name="KnowledgeRetrievalAgent",
             started_at=started_at, completed_at=completed_at, processing_time_ms=processing_time_ms,
             conditions_processed=len(retrieval_context.condition_candidates),
             procedures_processed=len(retrieval_context.procedure_candidates),
             retrieval_details=details, knowledge_base_version=retrieval_context.retrieval_summary.get("knowledge_version", "1.0"),
             strategies_used=retrieval_context.retrieval_summary.get("fusion_metrics", {}).get("strategies_combined", []),
             total_candidates_retrieved=sum(len(c.candidates) for c in retrieval_context.condition_candidates) + sum(len(p.candidates) for p in retrieval_context.procedure_candidates),
             guidelines_retrieved=[g.guideline_id for g in retrieval_context.cross_entity_guidelines],
             excludes_warnings_found=len(retrieval_context.overall_excludes_matrix),
             ncci_warnings_found=len(retrieval_context.overall_ncci_matrix),
             state_transitions=[s for s in self._state_transitions if s.agent_name == "KnowledgeRetrievalAgent"]
        )
    
    def _build_code_decision_records(self, coding_result: CodingResult) -> list[CodeDecisionRecord]:
        records = []
        for code in coding_result.diagnosis_codes + coding_result.procedure_codes:
            steps = []
            for i, step in enumerate(code.reasoning_chain):
                 steps.append(ReasoningStepRecord(
                      step_number=i+1, action=step.action, detail=step.detail, 
                      evidence_ref=step.evidence_ref, guideline_ref=step.guideline_ref,
                      timestamp=datetime.now(timezone.utc)
                 ))
                 
            alts = []
            for j, alt in enumerate(code.alternatives_considered):
                 alts.append(AlternativeRecord(
                     code=alt.code, description=alt.description, reason_rejected=alt.reason_rejected, was_candidate_rank=j+1
                 ))
                 
            factors = []
            for f in code.confidence_factors:
                 factors.append({
                     "factor": f.factor,
                     "impact": f.impact,
                     "weight": f.weight,
                     "detail": f.detail
                 })
            
            records.append(CodeDecisionRecord(
                 decision_id=code.decision_id,
                 code=code.code, code_type=code.code_type,
                 description=code.description, sequence_position=code.sequence_position,
                 sequence_number=code.sequence_number,
                 reasoning_chain=steps,
                 evidence_links=[], # Handled by mapper
                 alternatives_considered=alts,
                 confidence_score=code.confidence_score,
                 confidence_factors=factors,
                 is_combination_code=bool(code.combination_code_note),
                 is_use_additional=len(code.use_additional_applied) > 0,
                 is_code_first=len(code.code_first_applied) > 0,
                 guidelines_cited=code.guidelines_cited,
                 decision_method="LLM_REASONED"
            ))
        return records
    
    def record_coding_stage(
        self, coding_result: CodingResult, processing_time_ms: float,
        started_at: datetime, completed_at: datetime, attempt_number: int = 1
    ) -> None:
        decisions = self._build_code_decision_records(coding_result)
        total = len(coding_result.diagnosis_codes) + len(coding_result.procedure_codes)
        
        self._stage_records["coding"] = CodingStageRecord(
             stage_id=f"STG-COD-{uuid.uuid4().hex[:6]}", agent_name="MedicalCodingAgent",
             started_at=started_at, completed_at=completed_at, processing_time_ms=processing_time_ms,
             attempt_number=attempt_number, code_decisions=decisions,
             sequencing_decision=SequencingRecord(primary_code=coding_result.diagnosis_codes[0].code if coding_result.diagnosis_codes else "", primary_rationale="", full_sequence=[], guidelines_applied=[], confidence=1.0),
             combination_codes_applied=[], total_codes_assigned=total, overall_confidence=coding_result.overall_confidence,
             codes_flagged_for_review=[], llm_interactions=self._llm_interactions,
             state_transitions=[s for s in self._state_transitions if s.agent_name == "MedicalCodingAgent"]
        )
    
    def record_compliance_stage(
        self, compliance_report: ComplianceReport, processing_time_ms: float,
        started_at: datetime, completed_at: datetime
    ) -> None:
        
        def _build_layer(name, checks):
             passed = sum(1 for c in checks if c.passed)
             failed = len(checks) - passed
             return LayerSummary(
                 layer_name=name, checks_run=len(checks), checks_passed=passed, checks_failed=failed,
                 check_details=[{"check_id": c.check_id, "check_name": c.check_name, "passed": c.passed, "severity": c.severity, "details": c.details} for c in checks]
             )
             
        self._stage_records["compliance"] = ComplianceStageRecord(
             stage_id=f"STG-CMP-{uuid.uuid4().hex[:6]}", agent_name="ComplianceGuardAgent",
             started_at=started_at, completed_at=completed_at, processing_time_ms=processing_time_ms,
             overall_decision=compliance_report.overall_decision,
             total_checks_run=compliance_report.total_checks_run,
             checks_passed=compliance_report.checks_passed,
             checks_failed=compliance_report.checks_failed,
             checks_skipped=compliance_report.checks_skipped,
             layer3_summary=_build_layer("Layer 3", compliance_report.layer3_results),
             layer4_summary=_build_layer("Layer 4", compliance_report.layer4_results),
             layer5_summary=_build_layer("Layer 5", compliance_report.layer5_results),
             risk_score=compliance_report.overall_risk_score, risk_level=compliance_report.risk_level,
             risk_factors=compliance_report.risk_factors, security_alerts=compliance_report.security_alerts,
             feedback_generated=str(compliance_report.feedback) if compliance_report.feedback else None,
             state_transitions=[s for s in self._state_transitions if s.agent_name == "ComplianceGuardAgent"]
        )
    
    def record_retry(
        self, attempt_number: int, feedback: list[str], codes_changed: list[dict], new_compliance_decision: str
    ) -> None:
        self._retry_records.append(RetryRecord(
             attempt_number=attempt_number, triggered_at=datetime.now(timezone.utc),
             trigger_reason="COMPLIANCE_FAILED", feedback_provided=feedback,
             codes_changed=codes_changed, compliance_result_after=new_compliance_decision
        ))
    
    def record_state_transition(
        self, agent_name: str, from_state: str, to_state: str, trigger: str
    ) -> None:
        self._state_transitions.append(StateTransitionRecord(
             agent_name=agent_name, from_state=from_state, to_state=to_state,
             timestamp=datetime.now(timezone.utc), trigger=trigger
        ))
    
    def record_llm_interaction(
        self, model_name: str, model_version: str, prompt_template: str,
        prompt_tokens: int, response_tokens: int, latency_ms: float,
        parsed_successfully: bool, validation_passed: bool, validation_issues: list[str] = None
    ) -> None:
        self._llm_interactions.append(LLMInteractionRecord(
             interaction_id=f"LLM-{uuid.uuid4().hex[:8]}", timestamp=datetime.now(timezone.utc),
             model_name=model_name, model_version=model_version, prompt_template=prompt_template,
             prompt_token_count=prompt_tokens, response_token_count=response_tokens, 
             total_tokens=prompt_tokens + response_tokens, latency_ms=latency_ms,
             response_parsed_successfully=parsed_successfully, validation_passed=validation_passed,
             validation_issues=validation_issues or []
        ))
    
    def _build_final_output(self, coding_result: CodingResult) -> FinalOutputRecord:
        return FinalOutputRecord(
             output_id=f"OUT-{uuid.uuid4().hex[:8]}",
             final_diagnosis_codes=[{"code": c.code, "description": c.description, "position": getattr(c, "sequence_position", "UNKNOWN")} for c in coding_result.diagnosis_codes],
             final_procedure_codes=[{"code": c.code, "description": c.description, "position": getattr(c, "sequence_position", "UNKNOWN")} for c in coding_result.procedure_codes],
             overall_confidence=coding_result.overall_confidence,
             total_codes=len(coding_result.diagnosis_codes) + len(coding_result.procedure_codes),
             human_review_required=coding_result.requires_human_review,
             review_reasons=coding_result.review_reasons,
             was_escalated=getattr(coding_result, "was_escalated", False) or coding_result.requires_human_review,  # Approximation
             coding_summary=coding_result.coding_summary
        )

    def build_trace(
        self, final_coding_result: CodingResult, system_version: str = "1.0.0",
        knowledge_base_version: str = "KB-2025-Q1", models_used: dict = None, config: dict = None
    ) -> WorkflowTrace:
        
        now = datetime.now(timezone.utc)
        processing_time = (now - self._started_at).total_seconds() * 1000 if self._started_at else 0.0
        
        kb_version = knowledge_base_version or "KB-2025-Q1"

        return WorkflowTrace(
             trace_id=self._trace_id or f"AUD-{now.strftime('%Y-%m-%d')}-{uuid.uuid4().hex[:8]}",
             workflow_type=self._workflow_type or "MEDICAL_CODING",
             started_at=self._started_at or now,
             completed_at=now,
             total_processing_time_ms=processing_time,
             input_reference=self._input_ref or InputReference(document_id="N/A", document_hash="", document_type="", encounter_type="UNKNOWN"),
             nlp_stage=self._stage_records.get("nlp", NLPStageRecord(stage_id="", agent_name="", started_at=now, completed_at=now, processing_time_ms=0, sections_detected=[], total_entities_extracted=0, entities_by_type={}, conditions_extracted=[], procedures_extracted=[], medications_extracted=[], negated_findings=[], uncertain_findings=[], extraction_methods_used=[], average_confidence=0, low_confidence_entities=[], state_transitions=[])),
             retrieval_stage=self._stage_records.get("retrieval", RetrievalStageRecord(stage_id="", agent_name="", started_at=now, completed_at=now, processing_time_ms=0, conditions_processed=0, procedures_processed=0, retrieval_details=[], knowledge_base_version="", strategies_used=[], total_candidates_retrieved=0, guidelines_retrieved=[], excludes_warnings_found=0, ncci_warnings_found=0, state_transitions=[])),
             coding_stage=self._stage_records.get("coding", CodingStageRecord(stage_id="", agent_name="", started_at=now, completed_at=now, processing_time_ms=0, attempt_number=1, code_decisions=[], sequencing_decision=SequencingRecord(primary_code="", primary_rationale="", full_sequence=[], guidelines_applied=[], confidence=1), combination_codes_applied=[], total_codes_assigned=0, overall_confidence=0, codes_flagged_for_review=[], llm_interactions=self._llm_interactions, state_transitions=[])),
             compliance_stage=self._stage_records.get("compliance", ComplianceStageRecord(stage_id="", agent_name="", started_at=now, completed_at=now, processing_time_ms=0, overall_decision="PASS", total_checks_run=0, checks_passed=0, checks_failed=0, checks_skipped=0, layer3_summary=LayerSummary(layer_name="", checks_run=0, checks_passed=0, checks_failed=0, check_details=[]), layer4_summary=LayerSummary(layer_name="", checks_run=0, checks_passed=0, checks_failed=0, check_details=[]), layer5_summary=LayerSummary(layer_name="", checks_run=0, checks_passed=0, checks_failed=0, check_details=[]), risk_score=0, risk_level="LOW", risk_factors=[], security_alerts=[], state_transitions=[])),
             retry_history=self._retry_records,
             total_attempts=len(self._retry_records) + 1,
             final_output=self._build_final_output(final_coding_result),
             system_metadata=SystemMetadata(system_version=system_version, knowledge_base_version=kb_version, knowledge_base_last_updated=now.strftime("%Y-%m-%d"), models_used=models_used or {}, configuration=config or {}, deployment_environment="production"),
             record_hash="",          # Populated by AuditStore at insertion
             previous_record_hash="", # Populated by AuditStore at insertion
             digital_signature="SYSTEM_KEY_V1"
        )
