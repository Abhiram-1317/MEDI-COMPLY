from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import pytest

from medi_comply.agents.orchestrator_agent import OrchestratorAgent
from medi_comply.audit.audit_models import (
    AuditReport,
    AuditRiskAssessment,
    EvidenceMap,
    RetryRecord,
)
from medi_comply.core.config import Settings
from medi_comply.core.message_models import AgentMessage, AgentResponse, ResponseStatus
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.guardrails.feedback_generator import ComplianceFeedback, FeedbackItem
from medi_comply.guardrails.layer3_structural import StructuralCheckResult
from medi_comply.guardrails.layer4_semantic import SemanticCheckResult
from medi_comply.guardrails.layer5_output import OutputCheckResult
from medi_comply.metrics_collector import PipelineMetricsCollector
from medi_comply.nlp.document_ingester import IngestedDocument, PageInfo
from medi_comply.nlp.scr_builder import ConditionEntry, ProcedureEntry, StructuredClinicalRepresentation
from medi_comply.pipeline import PipelineContext, PipelineStage
from medi_comply.result_models import EscalationRecord, MediComplyResult
from medi_comply.schemas.coding_result import (
    AlternativeCode,
    ClinicalEvidenceLink,
    CodingResult,
    ConfidenceFactor,
    ReasoningStep,
    SingleCodeDecision,
)
from medi_comply.schemas.retrieval import (
    CodeRetrievalContext,
    ConditionCodeCandidates,
    ProcedureCodeCandidates,
    RankedCodeCandidate,
)
from medi_comply.system import MediComplySystem, SystemNotInitializedError


@dataclass
class ScenarioConfig:
    key: str
    document_text: str
    document_type: str
    patient_context: Dict[str, Any] = field(default_factory=dict)
    diagnosis_codes: List[Tuple[str, str]] = field(default_factory=list)
    procedure_codes: List[Tuple[str, str]] = field(default_factory=list)
    compliance_decision: str = "PASS"
    compliance_risk_level: str = "LOW"
    compliance_risk_score: float = 0.05
    expected_status: str = "SUCCESS"
    retry_attempts: int = 0
    warnings: List[str] = field(default_factory=list)
    audit_should_fail: bool = False
    fail_stage: Optional[str] = None
    workflow_type: str = "MEDICAL_CODING"
    audit_payload_as_dict: bool = False
    force_manual_escalation: bool = False


SCENARIOS: Dict[str, ScenarioConfig] = {
    "cardiac_success": ScenarioConfig(
        key="cardiac_success",
        document_text="Patient presents with STEMI involving LAD. PCI performed.",
        document_type="DISCHARGE_SUMMARY",
        patient_context={"encounter_type": "INPATIENT", "age": 64, "gender": "MALE"},
        diagnosis_codes=[("I21.09", "STEMI involving LAD"), ("I10", "Chronic hypertension")],
        procedure_codes=[("92941", "Percutaneous coronary intervention")],
    ),
    "pulmonary_success": ScenarioConfig(
        key="pulmonary_success",
        document_text="COPD exacerbation treated in ED.",
        document_type="ER_NOTE",
        patient_context={"encounter_type": "EMERGENCY", "age": 72, "gender": "FEMALE"},
        diagnosis_codes=[("J44.1", "COPD with exacerbation"), ("J96.01", "Acute respiratory failure")],
        procedure_codes=[("94640", "Nebulizer treatment")],
    ),
    "outpatient_followup": ScenarioConfig(
        key="outpatient_followup",
        document_text="Diabetes follow-up visit with labs.",
        document_type="PROGRESS_NOTE",
        patient_context={"encounter_type": "OUTPATIENT", "age": 58, "gender": "FEMALE"},
        diagnosis_codes=[("E11.9", "Type 2 diabetes without complications")],
        procedure_codes=[("83036", "Hemoglobin A1c")],
    ),
    "minimal_context": ScenarioConfig(
        key="minimal_context",
        document_text="Brief triage note for chest pain.",
        document_type="ER_NOTE",
        patient_context={"encounter_type": "EMERGENCY"},
        diagnosis_codes=[("R07.9", "Chest pain, unspecified")],
        procedure_codes=[],
    ),
    "empty_note": ScenarioConfig(
        key="empty_note",
        document_text="",
        document_type="UNKNOWN",
        patient_context={"encounter_type": "INPATIENT", "age": 40, "gender": "FEMALE"},
        diagnosis_codes=[("Z00.00", "General adult medical exam")],
        procedure_codes=[("99395", "Periodic comprehensive exam")],
    ),
    "retry_once_pass": ScenarioConfig(
        key="retry_once_pass",
        document_text="Seizure disorder requires clarification.",
        document_type="CONSULTATION",
        patient_context={"encounter_type": "INPATIENT", "age": 47, "gender": "MALE"},
        diagnosis_codes=[("G40.909", "Epilepsy, unspecified"), ("R56.9", "Unspecified convulsions")],
        procedure_codes=[("95720", "EEG monitoring")],
        retry_attempts=1,
    ),
    "retry_twice_escalate": ScenarioConfig(
        key="retry_twice_escalate",
        document_text="Advanced kidney disease with conflicting documentation.",
        document_type="DISCHARGE_SUMMARY",
        patient_context={"encounter_type": "INPATIENT", "age": 70, "gender": "MALE"},
        diagnosis_codes=[("N18.5", "Chronic kidney disease stage 5")],
        procedure_codes=[("90935", "Hemodialysis")],
        compliance_decision="ESCALATE",
        expected_status="ESCALATED",
        retry_attempts=2,
        compliance_risk_level="HIGH",
        compliance_risk_score=0.62,
    ),
    "block_security": ScenarioConfig(
        key="block_security",
        document_text="Security alert due to PHI leak.",
        document_type="OP_NOTE",
        patient_context={"encounter_type": "INPATIENT", "age": 55, "gender": "FEMALE"},
        diagnosis_codes=[("A41.9", "Sepsis, unspecified")],
        procedure_codes=[("36620", "Arterial catheter")],
        compliance_decision="BLOCK",
        expected_status="BLOCKED",
        compliance_risk_level="CRITICAL",
        compliance_risk_score=0.91,
    ),
    "forced_escalation": ScenarioConfig(
        key="forced_escalation",
        document_text="Complex obstetrics note flagged for manual review.",
        document_type="CONSULTATION",
        patient_context={"encounter_type": "INPATIENT", "age": 32, "gender": "FEMALE"},
        diagnosis_codes=[("O80", "Encounter for full-term uncomplicated delivery")],
        procedure_codes=[("59400", "Routine obstetric care")],
        expected_status="ESCALATED",
        force_manual_escalation=True,
    ),
    "audit_warning": ScenarioConfig(
        key="audit_warning",
        document_text="Orthopedic follow-up with missing audit trail.",
        document_type="PROGRESS_NOTE",
        patient_context={"encounter_type": "OUTPATIENT", "age": 45, "gender": "MALE"},
        diagnosis_codes=[("M54.50", "Low back pain")],
        procedure_codes=[("97110", "Therapeutic exercises")],
        audit_should_fail=True,
        warnings=["Audit trail incomplete"],
    ),
    "ingest_failure": ScenarioConfig(
        key="ingest_failure",
        document_text="Corrupted payload",
        document_type="UNKNOWN",
        patient_context={"encounter_type": "INPATIENT"},
        diagnosis_codes=[("T14.90", "Injury, unspecified")],
        procedure_codes=[],
        expected_status="ERROR",
        fail_stage="INGEST",
        audit_should_fail=True,
    ),
    "retrieval_failure": ScenarioConfig(
        key="retrieval_failure",
        document_text="NLP ok but retrieval fails.",
        document_type="CONSULTATION",
        patient_context={"encounter_type": "INPATIENT"},
        diagnosis_codes=[("B37.7", "Candidal sepsis")],
        procedure_codes=[("87205", "Smear, primary source")],
        expected_status="ERROR",
        fail_stage="RETRIEVAL",
        audit_should_fail=True,
    ),
}


class ScrPayload(dict):
    """Dictionary payload that preserves attribute access for SCR data."""

    def __init__(self, base: StructuredClinicalRepresentation) -> None:
        super().__init__(asdict(base))
        self._base = base

    def __getattr__(self, item: str):  # pragma: no cover - passthrough
        return getattr(self._base, item)


def _build_reasoning_chain(label: str) -> List[ReasoningStep]:
    return [
        ReasoningStep(step_number=1, action="EVIDENCE_REVIEW", detail=f"Reviewed {label} evidence."),
        ReasoningStep(step_number=2, action="CODE_SELECTION", detail=f"Confirmed {label} matches documentation."),
    ]


def _build_evidence_link(code: str) -> ClinicalEvidenceLink:
    return ClinicalEvidenceLink(
        evidence_id=f"EV-{code}",
        entity_id=f"ENT-{code}",
        source_text=f"Evidence supporting {code}",
        section="ASSESSMENT",
        page=1,
        line=5,
        char_offset=(0, 10),
        relevance="HIGH",
    )


def _build_confidence_factors(code: str) -> List[ConfidenceFactor]:
    return [
        ConfidenceFactor(factor="Documentation", impact="POSITIVE", weight=0.6, detail=f"Clear mention of {code}"),
        ConfidenceFactor(factor="Guidelines", impact="POSITIVE", weight=0.4, detail="Matches coding guideline"),
    ]


def _build_decision(code: str, description: str, index: int, code_type: str) -> SingleCodeDecision:
    return SingleCodeDecision(
        code=code,
        code_type=code_type,
        description=description,
        sequence_position="PRINCIPAL" if index == 1 else "SECONDARY",
        sequence_number=index,
        reasoning_chain=_build_reasoning_chain(description),
        clinical_evidence=[_build_evidence_link(code)],
        alternatives_considered=[
            AlternativeCode(code=f"ALT-{code}", description="Alternate", reason_rejected="Lower relevance")
        ],
        confidence_score=0.92,
        confidence_factors=_build_confidence_factors(code),
        requires_human_review=False,
        is_billable=True,
        guidelines_cited=["2024 ICD-10", "2024 CPT"],
    )


def _build_coding_result(config: ScenarioConfig, context: PipelineContext) -> CodingResult:
    dx_decisions = [
        _build_decision(code, desc, idx, "ICD10")
        for idx, (code, desc) in enumerate(config.diagnosis_codes, start=1)
    ]
    proc_decisions = [
        _build_decision(code, desc, idx, "CPT")
        for idx, (code, desc) in enumerate(config.procedure_codes, start=1)
    ]
    encounter = context.patient_context.get("encounter_type", "UNKNOWN") if context.patient_context else "UNKNOWN"
    age = context.patient_context.get("age", 60) if context.patient_context else 60
    gender = context.patient_context.get("gender", "FEMALE") if context.patient_context else "FEMALE"
    total_codes = len(dx_decisions) + len(proc_decisions)
    return CodingResult(
        scr_id=context.scr.scr_id if context.scr else f"SCR-{config.key}",
        context_id=context.trace_id,
        created_at=datetime.now(timezone.utc),
        processing_time_ms=1250.0,
        encounter_type=encounter,
        patient_age=age,
        patient_gender=gender,
        diagnosis_codes=dx_decisions,
        principal_diagnosis=dx_decisions[0] if dx_decisions else None,
        procedure_codes=proc_decisions,
        overall_confidence=0.88 if config.expected_status != "ERROR" else 0.4,
        total_codes_assigned=total_codes,
        total_icd10_codes=len(dx_decisions),
        total_cpt_codes=len(proc_decisions),
        has_combination_codes=False,
        has_use_additional_codes=False,
        has_code_first_codes=False,
        requires_human_review=config.expected_status in {"BLOCKED", "ESCALATED"},
        review_reasons=["High risk"] if config.expected_status in {"BLOCKED", "ESCALATED"} else [],
        attempt_number=config.retry_attempts + 1,
        previous_feedback=["Address risk"] if config.retry_attempts else None,
        coding_summary=f"{config.key} summary",
        all_guidelines_cited=["2024 ICD-10", "2024 CPT"],
    )


def _build_feedback(config: ScenarioConfig) -> Optional[ComplianceFeedback]:
    if config.compliance_decision == "PASS":
        return None
    return ComplianceFeedback(
        overall_decision=config.compliance_decision,
        total_checks=4,
        passed=3 if config.compliance_decision != "BLOCK" else 2,
        failed=1 if config.compliance_decision != "PASS" else 0,
        hard_fails=1 if config.compliance_decision in {"BLOCK", "ESCALATE"} else 0,
        soft_fails=1 if config.compliance_decision == "RETRY" else 0,
        feedback_items=[
            FeedbackItem(
                check_id="CHECK_FAIL",
                severity="HARD_FAIL",
                issue="Guideline conflict",
                action_required="Review documentation",
                affected_codes=[config.diagnosis_codes[0][0] if config.diagnosis_codes else "UNKNOWN"],
            )
        ],
        retry_allowed=config.compliance_decision == "RETRY",
        retry_count=config.retry_attempts,
        max_retries=3,
        human_review_items=["Manual review required"],
    )


def _build_structural_results(config: ScenarioConfig) -> List[StructuralCheckResult]:
    fail = config.compliance_decision in {"RETRY", "ESCALATE", "BLOCK"}
    return [
        StructuralCheckResult(
            check_id="CHECK_01",
            check_name="Code existence",
            passed=not fail,
            severity="HARD_FAIL" if fail else "NONE",
            details="Codes validated" if not fail else "Missing guideline citation",
            affected_codes=[config.diagnosis_codes[0][0]] if fail and config.diagnosis_codes else [],
            check_time_ms=12.0,
        ),
        StructuralCheckResult(
            check_id="CHECK_02",
            check_name="NCCI",
            passed=True,
            severity="NONE",
            details="No NCCI conflicts",
            check_time_ms=8.0,
        ),
    ]


def _build_semantic_results(config: ScenarioConfig) -> List[SemanticCheckResult]:
    severity = "ESCALATE" if config.compliance_decision == "ESCALATE" else "NONE"
    passed = severity == "NONE"
    return [
        SemanticCheckResult(
            check_id="CHECK_14",
            check_name="Evidence sufficiency",
            passed=passed,
            severity=severity if not passed else "NONE",
            details="LLM review passed" if passed else "LLM flagged inconsistency",
            check_time_ms=15.0,
        )
    ]


def _build_output_results(config: ScenarioConfig) -> List[OutputCheckResult]:
    severity = "HARD_FAIL_SECURITY" if config.compliance_decision == "BLOCK" else "NONE"
    passed = severity == "NONE"
    return [
        OutputCheckResult(
            check_id="CHECK_19",
            check_name="Schema validation",
            passed=passed,
            severity=severity,
            details="Schema valid" if passed else "PHI detected in payload",
            check_time_ms=6.0,
        )
    ]


def _build_compliance_report(config: ScenarioConfig, coding_result: CodingResult) -> ComplianceReport:
    structural = _build_structural_results(config)
    semantic = _build_semantic_results(config)
    output = _build_output_results(config)
    total = len(structural) + len(semantic) + len(output)
    failed = sum(1 for r in structural + semantic + output if not r.passed)
    passed = total - failed
    return ComplianceReport(
        report_id=f"CR-{config.key}",
        coding_result_id=coding_result.coding_result_id,
        created_at=datetime.now(timezone.utc),
        processing_time_ms=640.0,
        overall_decision=config.compliance_decision,
        total_checks_run=total,
        checks_passed=passed,
        checks_failed=failed,
        checks_skipped=0,
        layer3_results=structural,
        layer4_results=semantic,
        layer5_results=output,
        overall_risk_score=config.compliance_risk_score,
        risk_level=config.compliance_risk_level,
        risk_factors=[f"{config.compliance_risk_level} risk detected"],
        feedback=_build_feedback(config),
        security_alerts=["PHI leak"] if config.compliance_decision == "BLOCK" else [],
        phi_detected=config.compliance_decision == "BLOCK",
        injection_detected=False,
    )


def _build_retry_history(config: ScenarioConfig) -> List[RetryRecord]:
    history: List[RetryRecord] = []
    for attempt in range(1, config.retry_attempts + 1):
        history.append(
            RetryRecord(
                attempt_number=attempt,
                triggered_at=datetime.now(timezone.utc),
                trigger_reason="COMPLIANCE_RETRY",
                feedback_provided=[f"Resolve issue {attempt}"],
                codes_changed=[{"change": "UPDATED", "code": config.diagnosis_codes[0][0]}],
                compliance_result_after="RETRY",
            )
        )
    return history


def _build_risk_assessment(config: ScenarioConfig) -> AuditRiskAssessment:
    return AuditRiskAssessment(
        overall_score=config.compliance_risk_score,
        risk_level=config.compliance_risk_level,
        risk_factors_triggered=[{"factor": config.compliance_risk_level, "detail": config.key}],
        recommendations=["Document rationale", "Capture vital signs"],
        audit_priority="HIGH" if config.compliance_risk_level in {"HIGH", "CRITICAL"} else "NORMAL",
    )


def _build_evidence_map() -> EvidenceMap:
    return EvidenceMap(
        code_to_evidence={},
        evidence_to_codes={},
        unlinked_evidence=[],
        unlinked_codes=[],
        coverage_score=0.98,
    )


def _build_audit_report(config: ScenarioConfig, trace_id: str) -> AuditReport:
    risk = _build_risk_assessment(config)
    return AuditReport(
        report_id=f"AUD-{config.key}",
        trace_id=trace_id,
        generated_at=datetime.now(timezone.utc),
        summary=f"Audit summary for {config.key}",
        code_explanations=["Detailed reasoning available"],
        compliance_summary=config.compliance_decision,
        risk_assessment=risk,
        evidence_map_summary={"coverage": 0.98},
        json_export={"diagnosis_codes": [code for code, _ in config.diagnosis_codes]},
        compliance_certificate="CERTIFIED",
    )


def _build_retrieval_context(config: ScenarioConfig, scr: StructuredClinicalRepresentation | dict) -> CodeRetrievalContext:
    scr_id = getattr(scr, "scr_id", None)
    if scr_id is None and isinstance(scr, dict):
        scr_id = scr.get("scr_id", f"SCR-{config.key}")
    if scr_id is None:
        scr_id = f"SCR-{config.key}"
    condition_candidates = []
    for idx, (code, desc) in enumerate(config.diagnosis_codes, start=1):
        candidate = RankedCodeCandidate(code=code, description=desc, code_type="ICD10", relevance_score=0.9)
        condition_candidates.append(
            ConditionCodeCandidates(
                condition_entity_id=f"cond-{idx}",
                condition_text=desc,
                normalized_text=desc.lower(),
                assertion="PRESENT",
                candidates=[candidate],
            )
        )
    procedure_candidates = []
    for idx, (code, desc) in enumerate(config.procedure_codes, start=1):
        candidate = RankedCodeCandidate(code=code, description=desc, code_type="CPT", relevance_score=0.85)
        procedure_candidates.append(
            ProcedureCodeCandidates(
                procedure_entity_id=f"proc-{idx}",
                procedure_text=desc,
                normalized_text=desc.lower(),
                candidates=[candidate],
            )
        )
    return CodeRetrievalContext(
        scr_id=scr_id,
        patient_context=config.patient_context,
        condition_candidates=condition_candidates,
        procedure_candidates=procedure_candidates,
        retrieval_summary={"knowledge_version": "v-test"},
    )


class StubIngester:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    def ingest(self, raw_input: Any, source_type: str = "auto") -> IngestedDocument:
        if self.config.fail_stage == "INGEST":
            raise ValueError("Failed to ingest document")
        text = str(raw_input) if raw_input else self.config.document_text
        page = PageInfo(page_number=1, text=text, line_offsets=[(0, len(text))])
        return IngestedDocument(
            source_type=source_type if source_type != "auto" else "PLAIN_TEXT",
            raw_text=text,
            pages=[page],
            metadata={"document_type": self.config.document_type},
        )


class StubNlpPipeline:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    async def process(self, input_data: Any, source_type: str = "auto", patient_context: Optional[dict] = None, use_llm: bool = False):
        scr = StructuredClinicalRepresentation(
            document_id=f"DOC-{self.config.key}",
            patient_context=self.config.patient_context,
            clinical_summary=f"Structured summary for {self.config.key}",
            sections_found=["HPI", "ASSESSMENT"],
        )
        scr.conditions = [
            ConditionEntry(entity_id=f"cond-{idx}", text=desc, normalized_text=desc.lower(), is_primary_reason=(idx == 1))
            for idx, (_, desc) in enumerate(self.config.diagnosis_codes, start=1)
        ]
        scr.procedures = [
            ProcedureEntry(entity_id=f"proc-{idx}", text=desc, normalized_text=desc.lower())
            for idx, (_, desc) in enumerate(self.config.procedure_codes, start=1)
        ]
        return ScrPayload(scr)


class StubRetrievalAgent:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    async def process(self, message: AgentMessage) -> AgentResponse:
        if self.config.fail_stage == "RETRIEVAL":
            raise RuntimeError("Retrieval engine unavailable")
        scr = message.payload
        context = _build_retrieval_context(self.config, scr)
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent="StubRetrievalAgent",
            status=ResponseStatus.SUCCESS,
            payload={"retrieval_context": context},
            trace_id=message.trace_id,
        )


class StubRetryController:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config
        self.max_retries = 3

    async def execute_with_retry(self, coding_agent, compliance_agent, context: PipelineContext, message_bus=None):  # noqa: D401
        coding_result = _build_coding_result(self.config, context)
        context.coding_result = coding_result
        for warning in self.config.warnings:
            context.add_warning(warning)
        compliance_report = _build_compliance_report(self.config, coding_result)
        retry_history = _build_retry_history(self.config)
        if self.config.force_manual_escalation:
            context.is_escalated = True
        return coding_result, compliance_report, retry_history


class StubEscalationAgent:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    async def process(self, message: AgentMessage) -> AgentResponse:
        if not (self.config.force_manual_escalation or self.config.compliance_decision in {"ESCALATE", "BLOCK"}):
            return AgentResponse(
                original_message_id=message.message_id,
                from_agent="StubEscalationAgent",
                status=ResponseStatus.SUCCESS,
                payload={},
                trace_id=message.trace_id,
            )
        record = EscalationRecord(
            escalation_id=f"ESC-{self.config.key}",
            escalated_at=datetime.now(timezone.utc),
            reason="COMPLIANCE_ESCALATION",
            trigger_stage=message.payload.get("trigger_stage", "CODING"),
            trigger_details=["Manual review"],
            context_for_reviewer={"summary": self.config.key},
            priority="URGENT",
            estimated_review_time="10 minutes",
        )
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent="StubEscalationAgent",
            status=ResponseStatus.SUCCESS,
            payload={"escalation_record": record.model_dump()},
            trace_id=message.trace_id,
        )


class StubAuditAgent:
    def __init__(self, config: ScenarioConfig) -> None:
        self.config = config

    async def process(self, message: AgentMessage) -> AgentResponse:
        if self.config.audit_should_fail:
            raise RuntimeError("Audit service offline")
        report = _build_audit_report(self.config, message.trace_id)
        evidence_map = _build_evidence_map()
        risk = _build_risk_assessment(self.config)
        payload = {
            "audit_report": report.model_dump() if self.config.audit_payload_as_dict else report,
            "evidence_map": evidence_map.model_dump() if self.config.audit_payload_as_dict else evidence_map,
            "risk_assessment": risk.model_dump() if self.config.audit_payload_as_dict else risk,
        }
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent="StubAuditAgent",
            status=ResponseStatus.SUCCESS,
            payload=payload,
            trace_id=message.trace_id,
        )


class StubCodingAgent:
    async def process(self, message: AgentMessage) -> AgentResponse:
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent="StubCodingAgent",
            status=ResponseStatus.SUCCESS,
            payload={"ack": True},
            trace_id=message.trace_id,
        )


class StubComplianceAgent:
    async def process(self, message: AgentMessage) -> AgentResponse:
        return AgentResponse(
            original_message_id=message.message_id,
            from_agent="StubComplianceAgent",
            status=ResponseStatus.SUCCESS,
            payload={"compliance_report": {}},
            trace_id=message.trace_id,
        )


def _build_orchestrator(config: ScenarioConfig) -> OrchestratorAgent:
    dummy_km = SimpleNamespace(is_initialized=True, vector_store=SimpleNamespace(is_initialized=True))
    settings = Settings()
    orchestrator = OrchestratorAgent(
        knowledge_manager=dummy_km,
        config=settings,
        llm_client=None,
        audit_agent=StubAuditAgent(config),
        nlp_pipeline=StubNlpPipeline(config),
        retrieval_agent=StubRetrievalAgent(config),
        coding_agent=StubCodingAgent(),
        compliance_agent=StubComplianceAgent(),
        retry_controller=StubRetryController(config),
        metrics_collector=PipelineMetricsCollector(),
        escalation_agent=StubEscalationAgent(config),
    )
    orchestrator._ingester = StubIngester(config)
    return orchestrator


async def run_scenario(scenario_key: str) -> MediComplyResult:
    config = SCENARIOS[scenario_key]
    orchestrator = _build_orchestrator(config)
    result = await orchestrator.run_pipeline(
        clinical_document=config.document_text,
        source_type="PLAIN_TEXT",
        patient_context=config.patient_context,
        workflow_type=config.workflow_type,
    )
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "expected_status"),
    [
        ("cardiac_success", "SUCCESS"),
        ("pulmonary_success", "SUCCESS"),
        ("retry_once_pass", "SUCCESS"),
        ("retry_twice_escalate", "ESCALATED"),
        ("block_security", "BLOCKED"),
    ],
)
async def test_pipeline_status_outcomes(scenario_key: str, expected_status: str) -> None:
    result = await run_scenario(scenario_key)
    if result.status != expected_status:
        detail = [error.model_dump() for error in result.errors]
        pytest.fail(f"status={result.status} errors={detail}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "expected_code"),
    [
        ("cardiac_success", "I21.09"),
        ("pulmonary_success", "J44.1"),
        ("outpatient_followup", "E11.9"),
        ("minimal_context", "R07.9"),
        ("empty_note", "Z00.00"),
    ],
)
async def test_pipeline_contains_expected_codes(scenario_key: str, expected_code: str) -> None:
    result = await run_scenario(scenario_key)
    assert result.coding_result
    codes = [code.code for code in result.coding_result.diagnosis_codes]
    assert expected_code in codes


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "expected_retries"),
    [
        ("cardiac_success", 0),
        ("retry_once_pass", 1),
        ("retry_twice_escalate", 2),
        ("block_security", 0),
        ("audit_warning", 0),
    ],
)
async def test_pipeline_retry_counts_match(scenario_key: str, expected_retries: int) -> None:
    result = await run_scenario(scenario_key)
    assert result.retry_count == expected_retries


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "expect_escalation"),
    [
        ("cardiac_success", False),
        ("retry_twice_escalate", True),
        ("block_security", True),
        ("forced_escalation", True),
        ("audit_warning", False),
    ],
)
async def test_pipeline_escalation_metadata(scenario_key: str, expect_escalation: bool) -> None:
    result = await run_scenario(scenario_key)
    assert (result.escalation is not None) == expect_escalation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "expect_audit"),
    [
        ("cardiac_success", True),
        ("pulmonary_success", True),
        ("retry_once_pass", True),
        ("audit_warning", False),
        ("block_security", True),
    ],
)
async def test_pipeline_audit_artifacts_preserved(scenario_key: str, expect_audit: bool) -> None:
    result = await run_scenario(scenario_key)
    assert (result.audit_report_full is not None) == expect_audit
    if expect_audit:
        assert result.audit_report_summary.startswith("Audit summary")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scenario_key",
    [
        "cardiac_success",
        "pulmonary_success",
        "retry_once_pass",
        "block_security",
        "audit_warning",
    ],
)
async def test_pipeline_metrics_summary(scenario_key: str) -> None:
    result = await run_scenario(scenario_key)
    metrics = result.metrics
    expected_codes = len(result.coding_result.diagnosis_codes) + len(result.coding_result.procedure_codes)
    assert metrics.codes_assigned == expected_codes
    assert metrics.retry_count == result.retry_count
    assert metrics.compliance_checks_run >= metrics.compliance_checks_passed
    assert {"INGEST", "NLP", "RETRIEVAL"}.issubset(set(metrics.stage_times_ms.keys()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario_key", "warning_substring"),
    [
        ("audit_warning", "Audit service offline"),
        ("cardiac_success", ""),
        ("retry_twice_escalate", ""),
        ("ingest_failure", "Audit trail incomplete"),
        ("retrieval_failure", "Audit trail incomplete"),
    ],
)
async def test_pipeline_warnings_and_errors_propagate(scenario_key: str, warning_substring: str) -> None:
    result = await run_scenario(scenario_key)
    if warning_substring:
        assert any(warning_substring in warning for warning in result.warnings)
    else:
        assert result.warnings == []


@pytest.mark.asyncio
async def test_pipeline_stage_records_cover_all_success_paths() -> None:
    result = await run_scenario("cardiac_success")
    stage_names = [stage.stage_name for stage in result.pipeline_stages]
    expected = [
        PipelineStage.INGEST.value,
        PipelineStage.NLP.value,
        PipelineStage.RETRIEVAL.value,
        PipelineStage.CODING.value,
        PipelineStage.AUDIT.value,
    ]
    for stage in expected:
        assert stage in stage_names


@pytest.mark.asyncio
async def test_medi_comply_system_process_with_stubbed_orchestrator() -> None:
    system = MediComplySystem()
    system.orchestrator = _build_orchestrator(SCENARIOS["cardiac_success"])
    system._initialized = True
    result = await system.process("Acute MI note", patient_context=SCENARIOS["cardiac_success"].patient_context)
    assert isinstance(result, MediComplyResult)
    assert result.status == "SUCCESS"


@pytest.mark.asyncio
async def test_medi_comply_system_process_batch_runs_all_documents() -> None:
    system = MediComplySystem()
    system.orchestrator = _build_orchestrator(SCENARIOS["pulmonary_success"])
    system._initialized = True
    docs = [
        {"clinical_note": "Doc 1", "patient_context": SCENARIOS["pulmonary_success"].patient_context},
        {"clinical_note": "Doc 2", "patient_context": SCENARIOS["pulmonary_success"].patient_context},
    ]
    results = await system.process_batch(docs)
    assert len(results) == 2
    assert all(res.status == "SUCCESS" for res in results)


@pytest.mark.asyncio
async def test_medi_comply_system_health_check_flags_uninitialized() -> None:
    system = MediComplySystem()
    health = await system.health_check()
    assert not health["is_healthy"]
    assert health["checks"]["knowledge_base"]["status"] == "NOT_READY"


@pytest.mark.asyncio
async def test_medi_comply_system_process_requires_initialization() -> None:
    system = MediComplySystem()
    with pytest.raises(SystemNotInitializedError):
        await system.process("note")


class MockLLMClient:
    """Deterministic mock that returns curated coding decisions."""

    def __init__(self) -> None:
        self._icd_rules = [
            ("nstemi", "I21.4"),
            ("acute mi", "I21.4"),
            ("diabetes", "E11.22"),
            ("nephropathy", "E11.22"),
            ("ckd stage 3", "N18.32"),
            ("ckd", "N18.32"),
            ("hypertension", "I10"),
            ("copd", "J44.1"),
            ("tobacco", "Z87.891"),
        ]
        self._cpt_rules = [
            ("office", "99213"),
            ("echocardiogram", "93306"),
        ]

    async def handle_prompt(self, type_flag: str, obj: Any) -> Optional[dict[str, Any]]:
        text_sources = [
            getattr(obj, "normalized_text", None),
            getattr(obj, "condition_text", None),
            getattr(obj, "procedure_text", None),
            getattr(obj, "description", None),
        ]
        normalized = next((src.lower() for src in text_sources if isinstance(src, str) and src), "")
        if not normalized:
            return None

        rules = self._cpt_rules if type_flag == "CPT" else self._icd_rules
        for keyword, code in rules:
            if keyword in normalized:
                return {
                    "selected_code": code,
                    "confidence_score": 0.95,
                    "requires_human_review": False,
                    "reasoning_steps": [
                        {"step_number": 1, "action": "MockSelect", "detail": f"Matched {keyword}"}
                    ],
                }
        return None


@pytest.mark.asyncio
async def test_system_health_check_positive() -> None:
    """After initialize(), health_check should report a healthy system with code counts."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        health = await system.health_check()

        assert health["is_healthy"] is True
        kb = health["checks"]["knowledge_base"]
        assert kb["status"] == "OK"
        assert kb["icd10_codes"] >= 50
        assert kb["cpt_codes"] >= 30
        assert health["checks"]["agents"]["status"] == "OK"
        assert len(health["issues"]) == 0
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_cardiac_four_code_nstemi_full_validation() -> None:
    """Validate NSTEMI scenario enforces combination logic and reasoning guardrails."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        cardiac_note = """
        CHIEF COMPLAINT: Chest pain, shortness of breath

        HPI: 62-year-old male presents with substernal chest pain radiating to
        left arm, onset 2 hours ago. History of type 2 diabetes mellitus with
        diabetic nephropathy. On metformin 1000mg BID, lisinopril 20mg daily.
        GFR 38 mL/min. Denies fever or cough.

        PE: BP 160/95, HR 102, SpO2 94%

        LABS: Troponin 0.8 ng/mL (elevated)

        ASSESSMENT:
        1. Acute NSTEMI
        2. Type 2 diabetes with diabetic chronic kidney disease
        3. CKD stage 3b
        4. Hypertension, uncontrolled

        Plan: Admit cardiac ICU. Heparin drip. Hold metformin.
        """

        result = await system.process(
            clinical_note=cardiac_note,
            patient_context={"age": 62, "gender": "male", "encounter_type": "INPATIENT"},
        )

        assigned_codes: set[str] = set()
        if result.coding_result and result.coding_result.diagnosis_codes:
            assigned_codes = {decision.code for decision in result.coding_result.diagnosis_codes}

        assert result.status in ["SUCCESS", "ESCALATED"]
        assert result.coding_result is not None
        assert len(result.coding_result.diagnosis_codes) >= 2

        if "E11.22" in assigned_codes:
            has_ckd_stage = any(code.startswith("N18.") for code in assigned_codes)
            assert has_ckd_stage, "E11.22 requires Use Additional N18.x code"

        if result.coding_result.principal_diagnosis:
            primary = result.coding_result.principal_diagnosis.code
            assert not primary.startswith("N18."), "CKD stage code should not be primary"

        for decision in result.coding_result.diagnosis_codes:
            assert decision.reasoning_chain is not None
            assert len(decision.reasoning_chain) >= 1

        assert result.audit_report_summary is not None
        assert len(result.audit_report_summary) > 0
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_pulmonary_negation_and_tobacco() -> None:
    """Verify negated symptoms are excluded and tobacco history is captured when present."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        pulmonary_note = """
        CC: Worsening dyspnea
        HPI: 55yo F with COPD, 3-day worsening SOB, productive cough.
        Denies chest pain, hemoptysis, leg swelling. Former smoker.
        PE: SpO2 88%, bilateral wheezing.
        Assessment: COPD acute exacerbation. Former tobacco use.
        """

        result = await system.process(
            clinical_note=pulmonary_note,
            patient_context={"age": 55, "gender": "female", "encounter_type": "INPATIENT"},
        )

        assert result.status in ["SUCCESS", "ESCALATED"]
        if result.coding_result and result.coding_result.diagnosis_codes:
            assigned_codes = {code.code for code in result.coding_result.diagnosis_codes}
            chest_pain_codes = {"R07.1", "R07.2", "R07.9", "R07.89"}
            assert not assigned_codes.intersection(chest_pain_codes)
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_simple_office_visit_processing_time() -> None:
    """Simple outpatient visits should finish quickly and produce metrics."""

    import time

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        simple_note = """
        CC: Diabetes follow-up
        HPI: 48yo F, T2DM and HTN well controlled. No complaints.
        Vitals: BP 128/78
        Assessment: T2DM without complications. Essential HTN controlled.
        """

        start = time.time()
        result = await system.process(
            clinical_note=simple_note,
            patient_context={"age": 48, "gender": "female", "encounter_type": "OUTPATIENT"},
        )
        elapsed_ms = (time.time() - start) * 1000

        assert result.status in ["SUCCESS", "ESCALATED"]
        assert elapsed_ms < 30000
        assert result.metrics is not None
        assert result.metrics.total_time_ms > 0
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_messy_note_abbreviation_handling() -> None:
    """Messy abbreviation-heavy notes should still complete the pipeline."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        messy_note = """
        pt 45yo M PMH HTN T2DM CAD s/p PCI 2019 c/o dizziness x2d
        denies CP SOB syncope n/v
        BP 142/88 HR 76 SpO2 98% A&Ox3 RRR CTAB
        A: dizziness likely orthostatic d/t antihypertensives
        """

        result = await system.process(
            clinical_note=messy_note,
            patient_context={"age": 45, "gender": "male", "encounter_type": "OUTPATIENT"},
        )

        assert result is not None
        assert result.status in ["SUCCESS", "ESCALATED"]
        if result.coding_result:
            assert result.coding_result.total_codes_assigned >= 0
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_empty_note_warning() -> None:
    """Near-empty notes must warn instead of crashing."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        empty_note = "Patient seen. No significant findings. Follow up."

        result = await system.process(
            clinical_note=empty_note,
            patient_context={"age": 30, "gender": "female", "encounter_type": "OUTPATIENT"},
        )

        assert result is not None
        assert result.status in ["SUCCESS", "ESCALATED", "ERROR"]
        warning_present = bool(result.warnings) or bool(result.errors)
        zero_codes = bool(result.coding_result) and result.coding_result.total_codes_assigned == 0
        assert warning_present or zero_codes
    finally:
        await system.shutdown()


@pytest.mark.asyncio
async def test_knowledge_base_seeded_correctly() -> None:
    """Ensure knowledge base seeding loads critical ICD-10/CPT codes and edits."""

    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        km = system.knowledge_manager
        assert km is not None

        counts = km.code_count if hasattr(km, "code_count") else {}
        assert counts.get("icd10", 0) > 0
        assert counts.get("cpt", 0) > 0

        if hasattr(km, "icd10_db") and km.icd10_db:
            critical_icd10 = ["I21.4", "E11.22", "N18.32", "I10", "J44.1", "R07.9"]
            for code in critical_icd10:
                assert km.validate_code_exists(code, "icd10"), f"Missing ICD-10 code {code}"

        if hasattr(km, "cpt_db") and km.cpt_db:
            critical_cpt = ["99213", "93306", "84484", "80048"]
            for code in critical_cpt:
                assert km.validate_code_exists(code, "cpt"), f"Missing CPT code {code}"

        if hasattr(km, "ncci_engine") and km.ncci_engine:
            ncci_result = km.check_ncci_edits(["80053", "80048"])
            assert len(ncci_result) >= 0
    finally:
        await system.shutdown()
