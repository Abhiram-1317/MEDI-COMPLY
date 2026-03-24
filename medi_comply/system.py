"""Top-level entry point for MEDI-COMPLY."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple, Union

from medi_comply.agents.audit_trail_agent import AuditTrailAgent
from medi_comply.agents.claims_adjudication_agent import (
    ClaimInput,
    ClaimsAdjudicationAgent,
)
from medi_comply.agents.escalation_agent import EscalationAgent, EscalationCase, EscalationTrigger, should_escalate
from medi_comply.agents.orchestrator_agent import OrchestratorAgent
from medi_comply.audit.audit_models import AuditQuery, AuditQueryResult, WorkflowTrace
from medi_comply.audit.audit_store import AuditStore
from medi_comply.audit.query_engine import AuditQueryEngine
from medi_comply.compliance.regulatory_calendar import RegulatoryCalendar, StalenessDetector, seed_regulatory_events
from medi_comply.core.config import Settings
from medi_comply.core.message_bus import AsyncMessageBus
from medi_comply.core.message_models import AgentMessage
from medi_comply.guardrails.guardrail_chain import GuardrailChain
from medi_comply.guardrails.layer1_model import UseCase
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.result_models import EscalationRecord, MediComplyResult


class _EscalationBusAdapter:
    """Adapts the AsyncMessageBus to the EscalationAgent's publish signature."""

    def __init__(self, bus: AsyncMessageBus) -> None:
        self._bus = bus

    async def publish(self, topic: str, payload: Dict[str, Any]) -> None:
        message = AgentMessage(
            from_agent="EscalationAgent",
            to_agent=topic,
            action="NOTIFY",
            payload=payload,
        )
        await self._bus.publish(message)


class MediComplySystem:
    """High-level facade exposed to end users / API handlers."""

    def __init__(
        self,
        config: Optional[Settings] = None,
        llm_client: Any = None,
        db_path: str = "medi_comply_audit.db",
    ) -> None:
        self.config = config or Settings()
        self.llm_client = llm_client
        self.db_path = db_path

        self.knowledge_manager: Optional[KnowledgeManager] = None
        self.audit_store: Optional[AuditStore] = None
        self.audit_engine: Optional[AuditQueryEngine] = None
        self.orchestrator: Optional[OrchestratorAgent] = None
        self.claims_agent: Optional[ClaimsAdjudicationAgent] = None
        self.escalation_agent: Optional[EscalationAgent] = None
        self.message_bus: Optional[AsyncMessageBus] = None
        self.regulatory_calendar: RegulatoryCalendar = RegulatoryCalendar()
        self.audit_agent: Optional[AuditTrailAgent] = None
        self._initialized = False

    async def initialize(self) -> None:
        self.knowledge_manager = KnowledgeManager()
        await self._init_knowledge_manager()

        self.audit_store = AuditStore(self.db_path)
        audit_agent = AuditTrailAgent(self.audit_store, self.config)
        self.audit_agent = audit_agent

        self.claims_agent = ClaimsAdjudicationAgent(
            knowledge_manager=self.knowledge_manager,
        )

        seed_regulatory_events(self.regulatory_calendar)

        self.message_bus = AsyncMessageBus()
        bus_adapter = _EscalationBusAdapter(self.message_bus)
        self.escalation_agent = EscalationAgent(message_bus=bus_adapter)

        self.orchestrator = OrchestratorAgent(
            knowledge_manager=self.knowledge_manager,
            config=self.config,
            llm_client=self.llm_client,
            audit_agent=audit_agent,
            escalation_agent=self.escalation_agent,
        )
        self.message_bus.subscribe(self.orchestrator.agent_name, self.orchestrator.process)
        self.message_bus.subscribe(self.escalation_agent.agent_name, self.escalation_agent.process)
        if self.claims_agent:
            self.message_bus.subscribe(self.claims_agent.agent_name, self.claims_agent.process)
        self.audit_engine = AuditQueryEngine(self.audit_store)
        await self.message_bus.start()

        health = await self.health_check()
        if not health["is_healthy"]:
            raise SystemInitializationError(f"Health check failed: {health['issues']}")
        self._initialized = True

    async def process(
        self,
        clinical_note: Union[str, bytes, dict],
        patient_context: Optional[dict] = None,
        source_type: str = "auto",
        workflow_type: str = "MEDICAL_CODING",
    ) -> Union[MediComplyResult, dict]:
        if not self._initialized or not self.orchestrator:
            raise SystemNotInitializedError("Call initialize() before processing")
        if workflow_type == "CLAIMS_ADJUDICATION":
            claim_payload = clinical_note if isinstance(clinical_note, dict) else (patient_context or {}).get("claim")
            if not claim_payload:
                raise ProcessingError("CLAIMS_ADJUDICATION workflow requires claim payload")
            return await self.adjudicate_claim(claim_payload)
        patient_context = dict(patient_context or {})
        patient_context.setdefault("code_set_versions", self._get_current_versions_payload())
        patient_context.setdefault("code_set_versions_raw", self.regulatory_calendar.get_current_code_set_versions())
        patient_context.setdefault("knowledge_base_version", self._get_kb_version_id())
        dos_validation = self._validate_date_of_service(patient_context)
        try:
            result = await self.orchestrator.run_pipeline(
                clinical_document=clinical_note,
                source_type=source_type,
                patient_context=patient_context,
                workflow_type=workflow_type,
            )
            result = self._apply_version_metadata(result, patient_context)
            if dos_validation:
                result = self._attach_dos_validation(result, dos_validation)
            return await self._handle_escalation_if_needed(
                result=result,
                clinical_note=clinical_note,
                patient_context=patient_context or {},
            )
        except Exception as exc:  # pragma: no cover - surfaced in tests
            raise ProcessingError(str(exc)) from exc

    async def process_batch(
        self,
        documents: List[dict],
        max_concurrent: int = 5,
    ) -> List[MediComplyResult]:
        if not self._initialized:
            raise SystemNotInitializedError("System not initialized")
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _process(doc: dict) -> MediComplyResult:
            async with semaphore:
                return await self.process(
                    clinical_note=doc.get("clinical_note", ""),
                    patient_context=doc.get("patient_context"),
                    source_type=doc.get("source_type", "auto"),
                    workflow_type=doc.get("workflow_type", "MEDICAL_CODING"),
                )

        tasks = [_process(doc) for doc in documents]
        return await asyncio.gather(*tasks)

    async def health_check(self) -> dict:
        checks: dict[str, dict[str, Any]] = {}
        issues: list[str] = []

        # Knowledge base readiness
        kb_check: dict[str, Any] = {"status": "OK", "icd10_codes": 0, "cpt_codes": 0}
        km = self.knowledge_manager
        if km and getattr(km, "is_initialized", False):
            counts = getattr(km, "code_count", {})
            kb_check["icd10_codes"] = counts.get("icd10", 0)
            kb_check["cpt_codes"] = counts.get("cpt", 0)
            if kb_check["icd10_codes"] == 0 or kb_check["cpt_codes"] == 0:
                kb_check["status"] = "WARNING"
                issues.append("Knowledge base counts incomplete")
        else:
            kb_check["status"] = "NOT_READY"
            issues.append("Knowledge base not initialized")
        checks["knowledge_base"] = kb_check

        # Vector store status
        vector_status = "NOT_READY"
        if km and getattr(km, "vector_store", None):
            vector_status = "OK" if km.vector_store.is_initialized else "DEGRADED"
            if vector_status != "OK":
                issues.append("Vector store unavailable")
        else:
            issues.append("Vector store not configured")
        checks["vector_store"] = {"status": vector_status}

        # Audit store readiness
        audit_status = "OK" if self.audit_store else "NOT_READY"
        if audit_status != "OK":
            issues.append("Audit store not configured")
        checks["audit_store"] = {"status": audit_status}

        # LLM client status
        checks["llm_client"] = {"status": "OK" if self.llm_client else "NOT_CONFIGURED"}

        # Agent orchestration readiness
        agents_check: dict[str, Any] = {"status": "NOT_READY", "agents_loaded": 0}
        orchestrator = self.orchestrator
        if orchestrator:
            agent_attrs = [
                "nlp_pipeline",
                "retrieval_agent",
                "coding_agent",
                "compliance_agent",
                "audit_agent",
                "escalation_agent",
            ]
            loaded = sum(1 for attr in agent_attrs if getattr(orchestrator, attr, None) is not None)
            agents_check["agents_loaded"] = loaded
            if loaded == len(agent_attrs):
                agents_check["status"] = "OK"
            elif loaded >= 4:
                agents_check["status"] = "WARNING"
                issues.append(f"Only {loaded} orchestrator agents loaded")
            else:
                issues.append("Insufficient orchestrator agents loaded")
        else:
            issues.append("Orchestrator not instantiated")
        checks["agents"] = agents_check

        acceptable_statuses = {"OK", "NOT_CONFIGURED"}
        is_healthy = all(entry.get("status") in acceptable_statuses for entry in checks.values()) and not issues
        return {
            "is_healthy": is_healthy,
            "checks": checks,
            "issues": issues,
            "system_version": "1.0.0",
        }

    async def get_audit_trail(self, trace_id: str) -> Optional[WorkflowTrace]:
        if not self.audit_store:
            raise SystemNotInitializedError("Audit store unavailable")
        return self.audit_store.retrieve(trace_id)

    async def search_audits(self, query: AuditQuery) -> AuditQueryResult:
        if not self.audit_engine:
            raise SystemNotInitializedError("Audit engine unavailable")
        return self.audit_engine.search(query)

    async def get_system_stats(self) -> dict:
        if not self.audit_store:
            raise SystemNotInitializedError("Audit store unavailable")
        return self.audit_store.get_statistics().model_dump()

    async def get_escalation_queue_stats(self) -> dict:
        if not self.escalation_agent:
            raise SystemNotInitializedError("Escalation agent unavailable")
        return await self.escalation_agent.get_queue_stats()

    async def adjudicate_claim(self, claim_data: dict) -> dict:
        if not self._initialized or not self.claims_agent:
            raise SystemNotInitializedError("System not initialized")

        claim_input = ClaimInput.model_validate(claim_data)
        claim_result = await self.claims_agent.adjudicate_claim(claim_input)

        guardrail_notes, guardrail_passed = self._run_claim_guardrails()
        guardrail_total = len(guardrail_notes)
        compliance_passed = claim_result.compliance_checks_passed + guardrail_passed
        compliance_total = claim_result.compliance_checks_total + guardrail_total
        warnings = list(claim_result.warnings) + guardrail_notes

        updated_result = claim_result.model_copy(
            update={
                "compliance_checks_passed": compliance_passed,
                "compliance_checks_total": max(compliance_total, 1),
                "warnings": warnings,
                "audit_trail_id": claim_result.audit_trail_id or f"CLAIM-{uuid.uuid4().hex[:8]}",
            }
        )

        self._record_claim_audit(updated_result)
        return updated_result.model_dump()

    async def adjudicate_claims_batch(self, claims: List[dict]) -> List[dict]:
        results: List[dict] = []
        for claim in claims:
            results.append(await self.adjudicate_claim(claim))
        return results

    def get_upcoming_regulatory_changes(self, days_ahead: int = 30):
        return self.regulatory_calendar.get_upcoming_events(days_ahead=days_ahead)

    # ------------------------------------------------------------------
    # Regulatory calendar integration
    # ------------------------------------------------------------------

    def _coerce_date(self, value: Any) -> Optional[date]:
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value).date()
            except ValueError:
                return None
        return None

    def _get_kb_version_date(self) -> date:
        km = self.knowledge_manager
        version = getattr(km, "current_version", None)
        created_at = getattr(version, "created_at", None)
        if created_at:
            return created_at.date()
        return date.today()

    def _get_kb_version_id(self) -> str:
        km = self.knowledge_manager
        if km and getattr(km, "current_version", None):
            return km.current_version.version_id
        return "UNKNOWN"

    def _get_current_versions_payload(self) -> Dict[str, str]:
        raw = self.regulatory_calendar.get_current_code_set_versions()
        return {
            "icd10_version": raw.get("ICD-10-CM"),
            "cpt_version": raw.get("CPT"),
            "hcpcs_version": raw.get("HCPCS"),
            "ncci_version": raw.get("NCCI"),
            "knowledge_base_version": self._get_kb_version_id(),
        }

    def _validate_date_of_service(self, patient_context: dict) -> Optional[dict]:
        dos_value = patient_context.get("date_of_service") or patient_context.get("dos")
        dos = self._coerce_date(dos_value)
        if not dos:
            return None

        kb_version_date = self._get_kb_version_date()
        validation = self.regulatory_calendar.validate_date_of_service(dos, kb_version_date)
        fiscal_year = self.regulatory_calendar.get_fiscal_year(dos)
        code_set_versions = self.regulatory_calendar.get_current_code_set_versions()
        kb_version_id = self._get_kb_version_id()

        normalized_versions = self._get_current_versions_payload()
        normalized_versions["knowledge_base_version"] = kb_version_id

        patient_context["fiscal_year"] = fiscal_year
        patient_context["code_set_versions"] = normalized_versions
        patient_context["code_set_versions_raw"] = code_set_versions
        patient_context["regulatory_validation"] = validation.model_dump()
        patient_context["knowledge_base_version"] = kb_version_id

        return {
            "validation": validation,
            "fiscal_year": fiscal_year,
            "code_set_versions": normalized_versions,
            "kb_version_id": kb_version_id,
        }

    def _apply_version_metadata(self, result: MediComplyResult, patient_context: dict) -> MediComplyResult:
        updates: Dict[str, Any] = {}
        code_versions = patient_context.get("code_set_versions") or self.regulatory_calendar.get_current_code_set_versions()
        kb_version = patient_context.get("knowledge_base_version") or self._get_kb_version_id()

        if isinstance(code_versions, dict) and "knowledge_base_version" not in code_versions:
            code_versions = dict(code_versions)
            code_versions["knowledge_base_version"] = kb_version

        if not result.code_set_versions:
            updates["code_set_versions"] = code_versions
        if result.knowledge_base_version in {"", "UNKNOWN"}:
            updates["knowledge_base_version"] = kb_version

        metrics_updates: Dict[str, Any] = {}
        if getattr(result, "metrics", None) and getattr(result.metrics, "knowledge_base_version", "UNKNOWN") in {"", "UNKNOWN"}:
            metrics_updates["knowledge_base_version"] = kb_version
        if metrics_updates:
            updates["metrics"] = result.metrics.model_copy(update=metrics_updates)

        return result.model_copy(update=updates) if updates else result

    def _attach_dos_validation(self, result: MediComplyResult, dos_context: dict) -> MediComplyResult:
        validation = dos_context["validation"]
        staleness_detector: StalenessDetector = self.regulatory_calendar.staleness
        risk = staleness_detector.get_staleness_risk(validation.staleness_days)

        warnings = list(result.warnings)
        code_versions = dos_context.get("code_set_versions", {})
        if risk == "CRITICAL":
            codes_changed = [ev.title for ev in validation.regulatory_changes_between]
            code_note = f" Code sets impacted: {', '.join(codes_changed)}." if codes_changed else ""
            active_versions = ", ".join(
                f"{k}:{v}" for k, v in code_versions.items() if v
            )
            version_note = f" Active versions -> {active_versions}." if active_versions else ""
            warnings.append(
                f"WARNING: DOS {validation.date_of_service} is {validation.staleness_days} days after KB version; staleness CRITICAL.{code_note}{version_note} Recommend KB update before coding."
            )
        elif risk == "HIGH":
            warnings.append(
                f"CAUTION: DOS {validation.date_of_service} is {validation.staleness_days} days after KB version; review recent code set updates."
            )

        for w in validation.warnings:
            warnings.append(f"DOS validation note: {w}")

        regulatory_payload = result.regulatory_validation or {}
        if not regulatory_payload:
            regulatory_payload = dos_context["validation"].model_dump()
            regulatory_payload["fiscal_year"] = dos_context.get("fiscal_year")
            regulatory_payload["code_set_versions"] = dos_context.get("code_set_versions")
            regulatory_payload["knowledge_base_version"] = dos_context.get("kb_version_id")

        code_versions = result.code_set_versions or {}
        if not code_versions:
            code_versions = dos_context.get("code_set_versions", {})

        return result.model_copy(
            update={
                "warnings": warnings,
                "regulatory_validation": regulatory_payload,
                "code_set_versions": code_versions,
                "knowledge_base_version": dos_context.get("kb_version_id", result.knowledge_base_version),
            }
        )

    async def _handle_escalation_if_needed(
        self,
        result: MediComplyResult,
        clinical_note: Union[str, bytes, dict],
        patient_context: dict,
    ) -> MediComplyResult:
        should_escalate_flag, trigger = self._evaluate_escalation_trigger(result)
        if not should_escalate_flag or trigger is None:
            return result

        if not self.escalation_agent:
            return result

        escalation_context = self._build_escalation_context(result, clinical_note, patient_context)
        attempted_output = self._serialize_attempted_output(result)
        compliance_failures = self._extract_compliance_failures(result)
        confidence_scores = self._extract_confidence_scores(result)

        case = await self.escalation_agent.escalate(
            trigger=trigger,
            source_agent=self._determine_source_agent(trigger),
            context=escalation_context,
            attempted_output=attempted_output,
            compliance_failures=compliance_failures,
            confidence_scores=confidence_scores,
        )
        escalation_record = self._build_escalation_record(case, trigger)
        return self._build_escalated_result(result, case, escalation_record)

    def _evaluate_escalation_trigger(self, result: MediComplyResult) -> Tuple[bool, Optional[EscalationTrigger]]:
        report = result.compliance_report
        if report is None:
            return False, None
        if report.overall_decision == "PASS":
            return False, None

        max_retries = getattr(self.config.guardrail, "max_retries", result.retry_count)
        confidence_score = result.coding_result.overall_confidence if result.coding_result else 0.0
        compliance_data = report.model_dump()
        should_flag, trigger = should_escalate(
            confidence_score=confidence_score,
            retry_count=result.retry_count,
            compliance_result=compliance_data,
            max_retries=max_retries,
            confidence_threshold=getattr(self.config.guardrail, "escalation_threshold", confidence_score),
        )

        if report.overall_decision in {"BLOCK", "ESCALATE"}:
            return True, trigger or EscalationTrigger.COMPLIANCE_HARD_FAIL
        if result.retry_count >= max_retries:
            return True, trigger or EscalationTrigger.MAX_RETRIES_EXCEEDED
        return should_flag, trigger

    def _build_escalation_context(
        self,
        result: MediComplyResult,
        clinical_note: Union[str, bytes, dict],
        patient_context: dict,
    ) -> Dict[str, Any]:
        raw_note = clinical_note.decode("utf-8", errors="ignore") if isinstance(clinical_note, (bytes, bytearray)) else str(clinical_note)
        reasoning_history = self._extract_reasoning_history(result)
        return {
            "workflow_type": "MEDICAL_CODING",
            "patient_context": patient_context,
            "clinical_summary": result.audit_report_summary,
            "original_input": raw_note,
            "reasoning_history": reasoning_history,
            "confidence": result.coding_result.overall_confidence if result.coding_result else 0.0,
            "audit_trail_id": result.trace_id,
        }

    def _extract_reasoning_history(self, result: MediComplyResult) -> List[Dict[str, Any]]:
        if not result.coding_result:
            return []
        chains = []
        for decision in list(result.coding_result.diagnosis_codes) + list(result.coding_result.procedure_codes):
            for step in decision.reasoning_chain:
                if hasattr(step, "model_dump"):
                    chains.append(step.model_dump())
                else:
                    chains.append(dict(step))
                if len(chains) >= 10:
                    return chains
        return chains

    def _extract_compliance_failures(self, result: MediComplyResult) -> List[Dict[str, Any]]:
        report = result.compliance_report
        if not report:
            return []

        failures: List[Dict[str, Any]] = []

        def _collect(checks: Any) -> None:
            for check in checks or []:
                passed = getattr(check, "passed", True)
                if passed is False:
                    failures.append(check.model_dump() if hasattr(check, "model_dump") else dict(check))

        _collect(getattr(report, "layer1_results", []))
        _collect([report.layer2_pre_result] if getattr(report, "layer2_pre_result", None) else [])
        _collect([report.layer2_post_result] if getattr(report, "layer2_post_result", None) else [])
        _collect(getattr(report, "layer3_results", []))
        _collect(getattr(report, "layer4_results", []))
        _collect(getattr(report, "layer5_results", []))
        return failures

    def _extract_confidence_scores(self, result: MediComplyResult) -> Dict[str, float]:
        scores: Dict[str, float] = {}
        if not result.coding_result:
            return scores
        scores["overall_confidence"] = result.coding_result.overall_confidence
        code_scores = [
            getattr(decision, "confidence_score", 0.0)
            for decision in list(result.coding_result.diagnosis_codes) + list(result.coding_result.procedure_codes)
        ]
        if code_scores:
            scores["min_code_confidence"] = min(code_scores)
            scores["max_code_confidence"] = max(code_scores)
        return scores

    def _serialize_attempted_output(self, result: MediComplyResult) -> Optional[Dict[str, Any]]:
        if result.coding_result and hasattr(result.coding_result, "model_dump"):
            return result.coding_result.model_dump()
        return None

    def _determine_source_agent(self, trigger: EscalationTrigger) -> str:
        if trigger in {EscalationTrigger.COMPLIANCE_HARD_FAIL, EscalationTrigger.PROMPT_INJECTION_DETECTED}:
            return "ComplianceGuardAgent"
        if trigger == EscalationTrigger.MAX_RETRIES_EXCEEDED:
            return "RetryController"
        return "MedicalCodingAgent"

    def _build_escalation_record(
        self,
        case: EscalationCase,
        trigger: EscalationTrigger,
    ) -> EscalationRecord:
        eta_seconds = max(int((case.sla_deadline - case.created_at).total_seconds()), 60)
        estimated = f"{eta_seconds // 60}m"
        return EscalationRecord(
            escalation_id=case.case_id,
            escalated_at=case.created_at,
            reason=trigger.value,
            trigger_stage="COMPLIANCE",
            trigger_details=[case.suggested_action or "Manual review required"],
            context_for_reviewer={
                "patient_context": case.patient_context,
                "clinical_summary": case.clinical_summary,
            },
            priority=case.priority.value,
            estimated_review_time=estimated,
        )

    def _build_escalated_result(
        self,
        result: MediComplyResult,
        case: EscalationCase,
        escalation_record: EscalationRecord,
    ) -> MediComplyResult:
        summary = (
            self.escalation_agent.build_escalation_summary(case)
            if self.escalation_agent
            else result.audit_report_summary
        )
        return result.model_copy(
            update={
                "status": "ESCALATED",
                "coding_result": None,
                "audit_report_summary": summary,
                "escalation": escalation_record,
            }
        )

    def _run_claim_guardrails(self) -> Tuple[List[str], int]:
        """Run lightweight guardrails for claims adjudication (layer 1 model checks)."""
        notes: List[str] = []
        passed_count = 0
        try:
            chain = GuardrailChain(
                knowledge_manager=self.knowledge_manager,
                llm_client=self.llm_client,
                config=self.config,
                use_case=UseCase.CLAIMS_ADJUDICATION,
            )
            env = chain._resolve_environment()
            model_cfg = chain._resolve_model_config()
            layer1 = chain.layer1.run_checks(model_cfg, env, use_case=UseCase.CLAIMS_ADJUDICATION)
            if getattr(layer1, "passed", False):
                notes.append("Guardrail: model selection passed for claims adjudication")
                passed_count += 1
            else:
                reason = "; ".join(getattr(layer1, "reasons", []) or []) or "Model selection guard failed"
                notes.append(f"Guardrail warning: {reason}")
        except Exception as exc:  # pragma: no cover
            notes.append(f"Guardrail warning: unable to run claims guardrails ({exc})")
        return notes, passed_count

    def _record_claim_audit(self, claim_result: Any) -> None:
        if not self.audit_store:
            return
        trace_id = getattr(claim_result, "audit_trail_id", None) or f"CLAIM-{uuid.uuid4().hex[:8]}"
        record_data = {
            "trace_id": trace_id,
            "workflow_type": "CLAIMS_ADJUDICATION",
            "claim_result": claim_result.model_dump() if hasattr(claim_result, "model_dump") else dict(claim_result),
        }
        hash_chain = self.audit_store.hash_chain
        record_hash, prev_hash = hash_chain.create_chain_link(record_data)

        serialized = hash_chain._canonical_serialize(record_data)
        created_at = datetime.now().isoformat()
        total_lines = len(getattr(claim_result, "line_results", []) or [])
        try:
            with self.audit_store._get_connection() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    """
                    INSERT INTO audit_records (
                        trace_id, workflow_type, created_at, encounter_type,
                        record_data, record_hash, previous_hash, risk_score,
                        risk_level, compliance_decision, overall_confidence,
                        total_codes, was_escalated, processing_time_ms,
                        system_version, knowledge_base_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace_id,
                        "CLAIMS_ADJUDICATION",
                        created_at,
                        getattr(claim_result, "claim_status", None),
                        serialized,
                        record_hash,
                        prev_hash,
                        None,
                        getattr(claim_result, "claim_status", None),
                        getattr(claim_result, "claim_status", None),
                        None,
                        total_lines,
                        getattr(claim_result, "claim_status", "") in {"DENIED", "PENDED"},
                        getattr(claim_result, "processing_time_ms", None),
                        "1.0.0",
                        None,
                    ),
                )
        except Exception:
            # Audit write best-effort; failures are non-blocking
            return

    async def _init_knowledge_manager(self) -> None:
        if not self.knowledge_manager:
            return
        from medi_comply.knowledge.seed_data import seed_all_data

        seed_all_data(self.knowledge_manager)
        if self.knowledge_manager.vector_store:
            self.knowledge_manager.vector_store.initialize()
        self.knowledge_manager._initialized = True

    async def shutdown(self) -> None:
        if self.message_bus:
            await self.message_bus.stop()
        self._initialized = False
        self.orchestrator = None
        self.claims_agent = None
        self.escalation_agent = None
        self.message_bus = None
        self.audit_engine = None
        self.audit_store = None
        self.knowledge_manager = None
        self.audit_agent = None


class SystemNotInitializedError(Exception):
    """Raised when system APIs are used before initialization."""


class SystemInitializationError(Exception):
    """Raised when initialization health checks fail."""


class ProcessingError(Exception):
    """Raised when unrecoverable errors occur during processing."""
