"""
MEDI-COMPLY — AI-powered adversarial review (Layer 4).
A separate LLM reviews the coding agent's decisions independently.
Catches logical errors, upcoding, missing conditions, and reasoning flaws.
"""

import time
from typing import Any, Optional
from pydantic import BaseModel

from medi_comply.schemas.coding_result import CodingResult
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.retrieval import CodeRetrievalContext
from medi_comply.knowledge.coding_guidelines import CodingGuidelinesEngine, EncounterType, Severity
from medi_comply.core.json_repair import JSONRepair
from medi_comply.core.logger import get_logger
from medi_comply.core.utils import safe_get_code, safe_get_text
from medi_comply.compliance.fraud_detector import FraudDetector, FraudSeverity


logger = get_logger(__name__)


class SemanticCheckResult(BaseModel):
    check_id: str
    check_name: str
    passed: bool
    severity: str
    details: str
    affected_codes: list[str] = []
    reviewer_confidence: float = 1.0
    reviewer_reasoning: str = ""
    check_time_ms: float


class SemanticGuardrails:
    """
    Layer 4: AI-powered adversarial review.
    A separate LLM reviews the coding agent's decisions independently.
    Catches logical errors, upcoding, missing conditions, and 
    reasoning flaws that structural checks cannot detect.
    """
    
    def __init__(self, llm_client: Any = None, config: Any = None):
        self.llm_client = llm_client
        self.config = config
        self.coding_guidelines_engine = CodingGuidelinesEngine()
    
    async def run_all_checks(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
        retrieval_context: CodeRetrievalContext
    ) -> list[SemanticCheckResult]:
        """Run all semantic checks. Can be disabled if no LLM available."""
        if not self.llm_client:
            return [self._skip_result("No LLM available for semantic review")]
        
        results = []
        results.append(await self.check_14_evidence_sufficiency(coding_result, scr))
        results.append(await self.check_15_reasoning_validity(coding_result))
        results.append(await self.check_16_completeness(coding_result, scr))
        results.append(await self.check_17_upcoding_detection(coding_result, scr, retrieval_context))
        results.append(await self.check_18_guideline_compliance(coding_result, scr))
        return results

    def _make_result(self, cid, name, passed, severity, details, codes=[], conf=1.0, reasoning="", start=0.0):
        return SemanticCheckResult(
            check_id=cid, check_name=name, passed=passed, severity=severity if not passed else "NONE",
            details=details, affected_codes=codes, reviewer_confidence=conf,
            reviewer_reasoning=reasoning, check_time_ms=(time.time() - start) * 1000
        )
    
    def _skip_result(self, reason: str) -> SemanticCheckResult:
        """Return a skipped check result when LLM is unavailable."""
        return SemanticCheckResult(
            check_id="SKIPPED", check_name="Layer 4 Skipped", passed=True,
            severity="NONE", details=reason, check_time_ms=0.0
        )
    
    async def check_14_evidence_sufficiency(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> SemanticCheckResult:
        start = time.time()
        prompt = "Evaluate whether the assigned codes are supported by the documented evidence."
        context = self._build_review_prompt(coding_result, scr)
        payload = await self._invoke_llm_reviewer("CHECK_14_EVIDENCE", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_14_EVIDENCE", "Evidence Sufficiency", payload, start)
        return self._make_result("CHECK_14_EVIDENCE", "Evidence Sufficiency", True, "NONE", "Evidence fully supports assignments.", start=start)
    
    async def check_15_reasoning_validity(
        self, coding_result: CodingResult
    ) -> SemanticCheckResult:
        start = time.time()
        prompt = "Review the reasoning chains for logical soundness. Flag contradictions or unsupported leaps."
        context = self._format_codes_for_prompt(coding_result)
        payload = await self._invoke_llm_reviewer("CHECK_15_REASONING", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_15_REASONING", "Reasoning Valid", payload, start)
        return self._make_result("CHECK_15_REASONING", "Reasoning Valid", True, "NONE", "Reasoning is logical.", start=start)

    async def check_16_completeness(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> SemanticCheckResult:
        start = time.time()
        has_codes = len(coding_result.diagnosis_codes) > 0 or len(coding_result.procedure_codes) > 0
        any_evidence = any(dec.clinical_evidence for dec in (coding_result.diagnosis_codes + coding_result.procedure_codes))
        prompt = "Confirm that all clinically documented problems/procedures are represented by a code."
        context = self._build_review_prompt(coding_result, scr)
        if has_codes and not any_evidence:
            return self._make_result(
                "CHECK_16_COMPLETENESS",
                "Code Completeness",
                False,
                "HARD_FAIL",
                "Codes assigned without linked clinical evidence (potential phantom billing).",
                start=start,
            )
        payload = await self._invoke_llm_reviewer("CHECK_16_COMPLETENESS", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_16_COMPLETENESS", "Code Completeness", payload, start)
        if not has_codes:
            return self._make_result("CHECK_16_COMPLETENESS", "Code Completeness", False, "SOFT_FAIL", "Missing documented conditions from code selection.", start=start)
        return self._make_result("CHECK_16_COMPLETENESS", "Code Completeness", True, "NONE", "All documented conditions captured.", start=start)

    async def check_17_upcoding_detection(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
        retrieval_context: Optional[CodeRetrievalContext] = None,
    ) -> SemanticCheckResult:
        start = time.time()
        ncci_engine = getattr(self.config, "ncci_engine", None) if self.config else None
        knowledge_manager = getattr(self.config, "knowledge_manager", None) if self.config else None
        fraud_detector = FraudDetector(ncci_engine=ncci_engine, knowledge_manager=knowledge_manager)

        assigned_codes = self._build_assigned_codes_payload(coding_result)
        confidence_scores = {item["code"]: item.get("confidence", 0.5) for item in assigned_codes}
        evidence_snippets = self._collect_clinical_evidence(coding_result, scr)
        encounter_type = coding_result.encounter_type

        fraud_result = fraud_detector.scan_coding_decision(
            assigned_codes=assigned_codes,
            clinical_evidence=evidence_snippets,
            encounter_type=encounter_type,
            patient_demographics=getattr(scr, "patient_context", {}) or {},
            confidence_scores=confidence_scores,
        )

        alerts = fraud_result.alerts
        if not alerts:
            return self._make_result("CHECK_17_UPCODING", "Upcoding Detection", True, "NONE", "No fraud or upcoding indicators detected.", start=start)

        highest = self._highest_alert_severity(alerts)
        affected = [a.code_involved for a in alerts if a.code_involved]
        suggestions = []
        for alert in alerts:
            suggestions.extend(fraud_detector.suggest_correct_codes(alert))
        suggestion_lines = [f"{s['code']}: {s['description']} ({s['reasoning']})" for s in suggestions]

        alert_lines = [
            f"[{a.severity}] {a.fraud_type}: {a.description} | code={a.code_involved} | rule={a.rule_triggered} | action={a.recommended_action}"
            for a in alerts
        ]
        details_parts = [
            f"Risk Score: {fraud_result.overall_risk_score:.2f} ({fraud_result.risk_level})",
            "Alerts:\n- " + "\n- ".join(alert_lines),
        ]
        if suggestion_lines:
            details_parts.append("Suggested corrections:\n- " + "\n- ".join(suggestion_lines))

        passed = highest == FraudSeverity.LOW
        severity_map = {
            FraudSeverity.LOW: "NONE",
            FraudSeverity.MEDIUM: "SOFT_FAIL",
            FraudSeverity.HIGH: "HARD_FAIL",
            FraudSeverity.CRITICAL: "HARD_FAIL",
        }
        severity = severity_map.get(highest, "FAIL") if not passed else "NONE"
        if highest == FraudSeverity.CRITICAL:
            details_parts.append("ESCALATION: UPCODING_SUSPECTED")

        return self._make_result(
            "CHECK_17_UPCODING",
            "Upcoding Detection",
            passed,
            severity,
            " | ".join(details_parts),
            affected,
            start=start,
        )

    async def check_18_guideline_compliance(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> SemanticCheckResult:
        start = time.time()

        icd10_codes = [safe_get_code(c) or c.code for c in coding_result.diagnosis_codes]
        encounter = EncounterType(coding_result.encounter_type)
        primary_dx = safe_get_code(coding_result.principal_diagnosis) if coding_result.principal_diagnosis else (icd10_codes[0] if icd10_codes else None)

        compliance = self.coding_guidelines_engine.check_compliance(
            icd10_codes=icd10_codes,
            encounter_type=encounter,
            primary_dx=primary_dx,
            patient_age=coding_result.patient_age,
            patient_gender=coding_result.patient_gender,
        )

        sequencing = self.coding_guidelines_engine.get_sequencing_rules(icd10_codes, encounter)
        combination = self.coding_guidelines_engine.get_combination_guidance([c.description for c in coding_result.diagnosis_codes])

        violation_lines = [
            f"{v.guideline_id} ({v.severity.value}): {v.guideline_title} — {v.description} | Correction: {v.correction} | Excerpt: {v.guideline_text}"
            for v in compliance.violations
        ]
        warning_lines = [
            f"{w.guideline_id} (WARNING): {w.description} | Suggestion: {w.suggestion}"
            for w in compliance.warnings
        ]

        citations = [self.coding_guidelines_engine.format_guideline_citation(gid) for gid in compliance.applicable_guidelines[:6]]

        detail_parts = []
        if violation_lines:
            detail_parts.append("Violations detected:\n- " + "\n- ".join(violation_lines))
        if warning_lines:
            detail_parts.append("Warnings:\n- " + "\n- ".join(warning_lines))
        if sequencing:
            detail_parts.append(
                f"Sequencing guidance: principal={sequencing.get('principal')} | {sequencing.get('rationale')} | {sequencing.get('citation')}"
            )
        if combination.get("use_combination_code"):
            detail_parts.append(
                f"Combination suggestion: use {combination.get('recommended_code')} because {combination.get('reason')} ({combination.get('citation')})"
            )
        if citations:
            detail_parts.append("Citations: " + "; ".join(citations))

        has_error = any(v.severity == Severity.ERROR for v in compliance.violations)

        if has_error:
            details = " | ".join(detail_parts) if detail_parts else "OCG violations present."
            return self._make_result(
                "CHECK_18_GUIDELINES",
                "Guideline Compliance",
                False,
                "HARD_FAIL",
                details,
                icd10_codes,
                start=start,
            )

        details = " | ".join(detail_parts) if detail_parts else "Coding follows current OCG."
        return self._make_result(
            "CHECK_18_GUIDELINES",
            "Guideline Compliance",
            True,
            "NONE",
            details,
            icd10_codes,
            start=start,
        )

    async def _invoke_llm_reviewer(self, check_id: str, system_prompt: str, user_prompt: str) -> Optional[dict]:
        if not self.llm_client or not hasattr(self.llm_client, "chat"):
            return None
        response = await self.llm_client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.0,
            max_tokens=600,
            response_format="json",
        )
        if not response.success:
            logger.warning("Layer4 check %s LLM failure: %s", check_id, response.error)
            return None
        payload = response.parsed_json or JSONRepair.extract_json(response.content)
        if not payload:
            logger.warning("Layer4 check %s returned unparsable output", check_id)
        return payload

    def _payload_to_result(
        self,
        check_id: str,
        name: str,
        payload: dict,
        start: float,
    ) -> SemanticCheckResult:
        passed = bool(payload.get("passed", True))
        severity = payload.get("severity", "NONE") if not passed else "NONE"
        details = payload.get("details", "LLM review completed.")
        codes = payload.get("affected_codes") or []
        conf = float(payload.get("reviewer_confidence", 0.9))
        reasoning = payload.get("reviewer_reasoning", "")
        return self._make_result(check_id, name, passed, severity, details, codes, conf, reasoning, start)

    def _build_assigned_codes_payload(self, coding_result: CodingResult) -> list[dict]:
        payload: list[dict] = []
        for decision in coding_result.diagnosis_codes + coding_result.procedure_codes:
            payload.append(
                {
                    "code": safe_get_code(decision) or decision.code,
                    "code_type": decision.code_type,
                    "description": safe_get_text(decision) or decision.description,
                    "modifiers": getattr(decision, "modifiers", []) or [],
                    "alternatives": [alt.model_dump() for alt in (decision.alternatives_considered or [])],
                    "clinical_evidence": [ev.source_text for ev in (decision.clinical_evidence or []) if getattr(ev, "source_text", None)],
                    "confidence": decision.confidence_score,
                }
            )
        return payload

    def _collect_clinical_evidence(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> list[str]:
        snippets: list[str] = []
        for decision in coding_result.diagnosis_codes + coding_result.procedure_codes:
            for ev in (decision.clinical_evidence or []):
                if getattr(ev, "source_text", None):
                    snippets.append(ev.source_text)
        if getattr(scr, "clinical_summary", None):
            snippets.append(scr.clinical_summary)
        patient_ctx = getattr(scr, "patient_context", {}) or {}
        for value in patient_ctx.values():
            if isinstance(value, str) and value.strip():
                snippets.append(value)
        return snippets

    def _highest_alert_severity(self, alerts) -> FraudSeverity:
        order = [FraudSeverity.LOW, FraudSeverity.MEDIUM, FraudSeverity.HIGH, FraudSeverity.CRITICAL]
        max_idx = 0
        for alert in alerts:
            try:
                idx = order.index(alert.severity)
                max_idx = max(max_idx, idx)
            except ValueError:
                continue
        return order[max_idx]

    def _build_review_prompt(self, coding_result: CodingResult, scr: StructuredClinicalRepresentation) -> str:
        dx_lines = [
            f"- {safe_get_code(code) or 'UNKNOWN'}: {safe_get_text(code) or ''}"
            for code in coding_result.diagnosis_codes
        ]
        cpt_lines = [
            f"- {safe_get_code(code) or 'UNKNOWN'}: {safe_get_text(code) or ''}"
            for code in coding_result.procedure_codes
        ]
        patient = scr.patient_context or {}
        patient_line = (
            f"Patient: age={patient.get('age', 'Unknown')}, gender={patient.get('gender', 'Unknown')}, "
            f"encounter={patient.get('encounter_type', 'Unknown')}"
        )
        summary = scr.clinical_summary or ""
        return (
            f"{patient_line}\nClinical Summary: {summary[:300]}\n"
            f"Diagnosis Codes:\n" + ("\n".join(dx_lines) or "- None") + "\n"
            f"Procedure Codes:\n" + ("\n".join(cpt_lines) or "- None") + "\n"
            "Respond in JSON with keys: passed (bool), severity, details, affected_codes (list), reviewer_confidence (0-1), reviewer_reasoning."
        )

    def _format_codes_for_prompt(self, coding_result: CodingResult) -> str:
        chains = []
        for decision in coding_result.diagnosis_codes + coding_result.procedure_codes:
            reasons = [step.detail for step in (decision.reasoning_chain or [])]
            code_value = safe_get_code(decision) or decision.code
            description = safe_get_text(decision) or decision.description
            chains.append(
                f"Code {code_value}: {description}\nReasoning: {' | '.join(reasons) if reasons else 'N/A'}"
            )
        return "\n".join(chains) or "No reasoning available."
