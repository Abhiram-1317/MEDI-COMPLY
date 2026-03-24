"""
MEDI-COMPLY — Orchestrator for guardrail pipeline.

EXECUTION ORDER (Compliance Cage):
    1. Layer 1 — Model foundation (pre-LLM safety gates)
    2. Layer 3 — Structural (deterministic coding/claims constraints)
    3. Layer 4 — Semantic (reasoning/evidence checks)
    4. Layer 5 — Output (schema/security/format)
"""

import logging
import time
from typing import Any, Dict, List, Optional

from medi_comply.agents.escalation_agent import EscalationTrigger, should_escalate
from medi_comply.schemas.coding_result import CodingResult
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.retrieval import CodeRetrievalContext
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.compliance.parity_checker import ParityChecker, ParityCheckResult

from medi_comply.guardrails.layer1_model import (
    Environment,
    Layer1ModelGuard,
    ModelConfig,
    ModelRegistry,
    Provider,
    UseCase,
)
from medi_comply.guardrails.layer2_prompts import InjectionSeverity, Layer2PromptGuard
from medi_comply.guardrails.layer3_structural import StructuralGuardrails
from medi_comply.guardrails.layer4_semantic import SemanticGuardrails
from medi_comply.guardrails.layer5_output import OutputValidator
from medi_comply.guardrails.feedback_generator import ComplianceFeedbackGenerator
from medi_comply.guardrails.compliance_report import ComplianceReportGenerator, ComplianceReport


class GuardrailChain:
    """
    Orchestrates all guardrail layers in sequence.
    
    EXECUTION ORDER:
    1. Layer 1 (Model Foundation) — block invalid models early
    2. Layer 3 (Structural)
    3. Layer 4 (Semantic)
    4. Layer 5 (Output)
    """
    
    def __init__(
        self,
        knowledge_manager: KnowledgeManager,
        llm_client: Any = None,
        config: Any = None,
        use_case: UseCase = UseCase.MEDICAL_CODING,
    ):
        self.logger = logging.getLogger(__name__)
        self.structural = StructuralGuardrails(knowledge_manager)
        self.semantic = SemanticGuardrails(llm_client, config)
        self.output_validator = OutputValidator()
        self.feedback_gen = ComplianceFeedbackGenerator()
        self.report_gen = ComplianceReportGenerator()
        self.layer1 = Layer1ModelGuard(ModelRegistry())
        self.layer2 = Layer2PromptGuard()
        self.config = config
        self.use_case = use_case
        self.parity_checker = ParityChecker()
    
    async def validate(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
        retrieval_context: CodeRetrievalContext = None,
        raw_llm_outputs: list[str] = None,
        skip_semantic: bool = False,
        retry_count: int = 1,
        max_retries: int = 3
    ) -> ComplianceReport:
        """Run the complete guardrail chain."""
        start_time = time.time()
        
        # Resolve environment and model config
        env_enum = self._resolve_environment()
        model_cfg = self._resolve_model_config()

        # 1. Layer 1: Model Foundation
        layer1_result = self.layer1.run_checks(
            model_config=model_cfg,
            environment=env_enum,
            use_case=self.use_case,
            sample_predictions=None,
            sample_actuals=None,
        )

        if not layer1_result.passed:
            message = f"Layer1 model guard failed in env={env_enum.value} for model={model_cfg.name}"
            if env_enum == Environment.PRODUCTION:
                self.logger.error(message)
                return self._generate_block_report([], [], [], [], start_time, coding_result.coding_result_id, layer1_result)
            self.logger.warning(message)

        # 2. Layer 2 (PRE-LLM): prompt/input guardrails
        user_input = getattr(coding_result, "coding_summary", "") or str(scr)
        encounter_type_value = getattr(coding_result, "encounter_type", "outpatient") or "outpatient"
        layer2_pre = self.layer2.run_checks(user_input=user_input, encounter_type=str(encounter_type_value), model_output=None)

        if layer2_pre.injection and getattr(layer2_pre.injection, "severity", None) == InjectionSeverity.CRITICAL:
            return self._generate_escalation_report(
                trigger=EscalationTrigger.PROMPT_INJECTION_DETECTED,
                layer1_result=layer1_result,
                layer2_pre=layer2_pre,
                start_time=start_time,
                cr_id=coding_result.coding_result_id,
                retry_count=retry_count,
                last_confidence=coding_result.overall_confidence,
                failed_checks=[self._model_dump_safe(layer2_pre.injection)],
            )

        if layer2_pre.blocked or not layer2_pre.pre_llm_passed:
            self.logger.error("Layer2 pre-LLM guard blocked request: %s", layer2_pre.issues)
            return self._generate_block_report(
                layer1_result.checks,
                [],
                [],
                [],
                start_time,
                coding_result.coding_result_id,
                layer1_result,
                layer2_pre_result=layer2_pre,
            )

        # 3. Layer 2 (POST-LLM): output validation and rule compliance
        layer2_post = None
        if raw_llm_outputs:
            primary_output = raw_llm_outputs[0]
            layer2_post = self.layer2.run_checks(user_input=user_input, encounter_type=str(encounter_type_value), model_output=primary_output)
            if layer2_post.injection and getattr(layer2_post.injection, "severity", None) == InjectionSeverity.CRITICAL:
                return self._generate_escalation_report(
                    trigger=EscalationTrigger.PROMPT_INJECTION_DETECTED,
                    layer1_result=layer1_result,
                    layer2_pre=layer2_pre,
                    layer2_post=layer2_post,
                    start_time=start_time,
                    cr_id=coding_result.coding_result_id,
                    retry_count=retry_count,
                    last_confidence=coding_result.overall_confidence,
                    failed_checks=[self._model_dump_safe(layer2_post.injection)],
                )

            if layer2_post.blocked or not layer2_post.post_llm_passed:
                self.logger.error("Layer2 post-LLM guard failed: %s", layer2_post.issues)
                return self._generate_block_report(
                    layer1_result.checks,
                    [],
                    [],
                    [],
                    start_time,
                    coding_result.coding_result_id,
                    layer1_result,
                    layer2_pre_result=layer2_pre,
                    layer2_post_result=layer2_post,
                )

        # 4. Layer 3: Structural Checks
        structural_results = self.structural.run_all_checks(coding_result, scr)

        # 5. Layer 4: Semantic Checks
        if skip_semantic:
            semantic_results = []
        else:
            semantic_results = await self.semantic.run_all_checks(coding_result, scr, retrieval_context)

        # 6. Layer 5: Output Validation
        output_results = self.output_validator.run_all_checks(coding_result, raw_llm_outputs)

        if self._has_security_alert(output_results):
            return self._generate_block_report(
                layer1_result.checks,
                structural_results,
                semantic_results,
                output_results,
                start_time,
                coding_result.coding_result_id,
                layer1_result,
                layer2_pre_result=layer2_pre,
                layer2_post_result=layer2_post,
            )

        # Parity check (MHPAEA) after standard compliance checks
        parity_result = self._run_parity_check(coding_result)
        parity_severity = self._parity_severity(parity_result)
        if parity_result and parity_result.violations and parity_severity == "WARNING":
            self.logger.warning("Parity warnings detected: %s", [v.description for v in parity_result.violations])
        if parity_severity == "CRITICAL":
            return self._generate_block_report(
                layer1_result.checks,
                structural_results,
                semantic_results,
                output_results,
                start_time,
                coding_result.coding_result_id,
                layer1_result,
                layer2_pre_result=layer2_pre,
                layer2_post_result=layer2_post,
                parity_result=parity_result,
            )

        processing_time = (time.time() - start_time) * 1000
        report = self.report_gen.generate_report(
            layer1_result.checks,
            structural_results,
            semantic_results,
            output_results,
            coding_result.coding_result_id,
            processing_time,
            layer2_pre_result=layer2_pre,
            layer2_post_result=layer2_post,
            parity_result=parity_result,
        )

        # Adjust report decision based on parity violations
        if parity_result and parity_result.violations:
            if parity_severity == "ERROR" and report.overall_decision == "PASS":
                report.overall_decision = "ESCALATE"
                report.escalation_required = True
                report.escalation_trigger = EscalationTrigger.COMPLIANCE_HARD_FAIL
            report.failed_checks.append(parity_result.model_dump())

        # Attach complete feedback with loop controls
        feedback = self.feedback_gen.generate_feedback(
            structural_results,
            semantic_results,
            output_results,
            retry_count,
            max_retries,
        )
        report.feedback = feedback
        report.overall_decision = feedback.overall_decision

        # Escalation routing when automation cannot converge
        failed_checks = self._collect_failed_checks(
            layer1_checks=layer1_result.checks,
            layer2_pre=layer2_pre,
            layer2_post=layer2_post,
            structural_results=structural_results,
            semantic_results=semantic_results,
            output_results=output_results,
        )
        if parity_result and parity_result.violations:
            failed_checks.append(parity_result.model_dump())

        # Scenario: semantic upcoding hard fail triggers immediate escalation
        if self._has_upcoding_issue(semantic_results):
            return self._generate_escalation_report(
                trigger=EscalationTrigger.UPCODING_SUSPECTED,
                layer1_result=layer1_result,
                layer2_pre=layer2_pre,
                layer2_post=layer2_post,
                structural_results=structural_results,
                semantic_results=semantic_results,
                output_results=output_results,
                start_time=start_time,
                cr_id=coding_result.coding_result_id,
                retry_count=retry_count,
                last_confidence=coding_result.overall_confidence,
                failed_checks=failed_checks,
            )

        # Scenario: exhausted retries or low confidence after final attempt
        retries_exhausted = retry_count >= max_retries and report.overall_decision != "PASS"
        low_confidence_final = retries_exhausted and coding_result.overall_confidence < 0.7
        should_flag, trigger = should_escalate(
            confidence_score=coding_result.overall_confidence,
            retry_count=retry_count,
            compliance_result=report.model_dump(),
            max_retries=max_retries,
            confidence_threshold=0.7,
        )

        if report.overall_decision in {"BLOCK", "ESCALATE"} or retries_exhausted or low_confidence_final:
            if low_confidence_final:
                final_trigger = EscalationTrigger.LOW_CONFIDENCE
            elif report.overall_decision in {"BLOCK", "ESCALATE"}:
                final_trigger = EscalationTrigger.COMPLIANCE_HARD_FAIL
            elif retries_exhausted:
                final_trigger = trigger or EscalationTrigger.MAX_RETRIES_EXCEEDED
            else:
                final_trigger = trigger or EscalationTrigger.AGENT_ERROR
            return self._generate_escalation_report(
                trigger=final_trigger,
                layer1_result=layer1_result,
                layer2_pre=layer2_pre,
                layer2_post=layer2_post,
                structural_results=structural_results,
                semantic_results=semantic_results,
                output_results=output_results,
                start_time=start_time,
                cr_id=coding_result.coding_result_id,
                retry_count=retry_count,
                last_confidence=coding_result.overall_confidence,
                failed_checks=failed_checks,
                parity_result=parity_result,
            )

        return report
        
    def _has_security_alert(self, output_results: list) -> bool:
        return any(not getattr(r, "passed", True) and "SECURITY" in getattr(r, "severity", "") for r in output_results)
        
    def _generate_block_report(
        self,
        layer1_results: list,
        structural_results: list,
        semantic_results: list,
        output_results: list,
        start_time: float,
        cr_id: str,
        layer1_result_obj: Any = None,
        layer2_pre_result: Any = None,
        layer2_post_result: Any = None,
        parity_result: ParityCheckResult | None = None,
    ) -> ComplianceReport:
        report = self.report_gen.generate_report(
            layer1_results if layer1_results else (layer1_result_obj.checks if layer1_result_obj else []),
            structural_results,
            semantic_results,
            output_results,
            cr_id,
            (time.time() - start_time) * 1000,
            layer2_pre_result=layer2_pre_result,
            layer2_post_result=layer2_post_result,
            parity_result=parity_result,
        )
        report.overall_decision = "BLOCK"
        return report

    def _generate_escalation_report(
        self,
        trigger: EscalationTrigger,
        layer1_result: Any,
        layer2_pre: Any,
        start_time: float,
        cr_id: str,
        retry_count: int,
        last_confidence: float,
        failed_checks: List[Dict[str, Any]],
        layer2_post: Any = None,
        structural_results: Optional[List[Any]] = None,
        semantic_results: Optional[List[Any]] = None,
        output_results: Optional[List[Any]] = None,
        parity_result: ParityCheckResult | None = None,
    ) -> ComplianceReport:
        processing_time = (time.time() - start_time) * 1000
        report = self.report_gen.generate_report(
            layer1_result.checks if layer1_result else [],
            structural_results or [],
            semantic_results or [],
            output_results or [],
            cr_id,
            processing_time,
            layer2_pre_result=layer2_pre,
            layer2_post_result=layer2_post,
            parity_result=parity_result,
        )
        report.overall_decision = "ESCALATION_REQUIRED"
        report.escalation_required = True
        report.escalation_trigger = trigger
        report.failed_checks = failed_checks
        report.retry_count = retry_count
        report.last_confidence_score = last_confidence
        return report

    def _run_parity_check(self, coding_result: CodingResult) -> ParityCheckResult | None:
        try:
            payer_id = getattr(coding_result, "payer_id", None) or "COMPLIANT_PLAN"
            cpt_codes = [c.code for c in getattr(coding_result, "procedure_codes", []) if getattr(c, "code", None)]
            icd_codes = [d.code for d in getattr(coding_result, "diagnosis_codes", []) if getattr(d, "code", None)]
            if not cpt_codes:
                return None
            encounter_type_value = getattr(coding_result, "encounter_type", "outpatient") or "outpatient"
            is_inpatient = "inpatient" in str(encounter_type_value).lower()
            is_in_network = getattr(coding_result, "is_in_network", True)
            return self.parity_checker.check_claim_parity(
                payer_id=payer_id,
                cpt_code=cpt_codes[0],
                icd10_codes=icd_codes,
                is_in_network=is_in_network,
                is_inpatient=is_inpatient,
            )
        except Exception:
            self.logger.exception("Parity check failed; continuing without blocking")
            return None

    def _parity_severity(self, parity_result: ParityCheckResult | None) -> str:
        if not parity_result or not parity_result.violations:
            return "NONE"
        order = {"CRITICAL": 3, "ERROR": 2, "WARNING": 1}
        max_sev = max((order.get(v.severity, 0) for v in parity_result.violations), default=0)
        for sev, rank in order.items():
            if rank == max_sev:
                return sev
        return "WARNING"

    def _collect_failed_checks(
        self,
        layer1_checks: List[Any],
        layer2_pre: Any,
        layer2_post: Any,
        structural_results: List[Any],
        semantic_results: List[Any],
        output_results: List[Any],
    ) -> List[Dict[str, Any]]:
        failures: List[Dict[str, Any]] = []

        def _collect(checks: List[Any]) -> None:
            for check in checks or []:
                if getattr(check, "passed", True) is False:
                    failures.append(self._model_dump_safe(check))

        _collect(layer1_checks)
        if layer2_pre and not getattr(layer2_pre, "overall_passed", True):
            failures.append(self._model_dump_safe(layer2_pre))
        if layer2_post and not getattr(layer2_post, "overall_passed", True):
            failures.append(self._model_dump_safe(layer2_post))
        _collect(structural_results)
        _collect(semantic_results)
        _collect(output_results)
        return failures

    def _has_upcoding_issue(self, semantic_results: List[Any]) -> bool:
        for check in semantic_results or []:
            if getattr(check, "check_id", "") == "CHECK_17_UPCODING" and getattr(check, "passed", True) is False:
                return True
        return False

    def _model_dump_safe(self, obj: Any) -> Dict[str, Any]:
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        if hasattr(obj, "dict"):
            return obj.dict()
        try:
            return dict(obj)
        except Exception:
            return {"value": str(obj)}

    def _resolve_environment(self) -> Environment:
        env_value = "development"
        if self.config is not None:
            env_value = getattr(self.config, "environment", "development") or "development"
        env_value = env_value.lower()
        if env_value.startswith("prod"):
            return Environment.PRODUCTION
        if env_value.startswith("stag"):
            return Environment.STAGING
        return Environment.DEVELOPMENT

    def _resolve_model_config(self) -> ModelConfig:
        model_name = "gpt-4o"
        if self.config is not None and hasattr(self.config, "llm"):
            model_name = getattr(self.config.llm, "model_name", "gpt-4o") or "gpt-4o"
        cfg = self.layer1.registry.get(model_name)
        if cfg:
            return cfg
        # Fallback mock config for unknown models (treated as non-HIPAA, low domain score)
        return ModelConfig(
            name=model_name,
            provider=Provider.MOCK,
            medical_domain_score=0.5,
            hipaa_compliant=False,
            max_context_window=16_000,
            supports_json_mode=True,
            description="Fallback mock config for unregistered model",
        )
