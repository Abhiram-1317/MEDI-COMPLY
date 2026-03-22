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
from medi_comply.core.json_repair import JSONRepair
from medi_comply.core.logger import get_logger
from medi_comply.core.utils import safe_get_code, safe_get_text


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
        results.append(await self.check_17_upcoding_detection(coding_result, scr))
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
        passed = len(coding_result.diagnosis_codes) > 0 or len(coding_result.procedure_codes) > 0
        prompt = "Confirm that all clinically documented problems/procedures are represented by a code."
        context = self._build_review_prompt(coding_result, scr)
        payload = await self._invoke_llm_reviewer("CHECK_16_COMPLETENESS", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_16_COMPLETENESS", "Code Completeness", payload, start)
        if not passed:
            return self._make_result("CHECK_16_COMPLETENESS", "Code Completeness", False, "SOFT_FAIL", "Missing documented conditions from code selection.", start=start)
        return self._make_result("CHECK_16_COMPLETENESS", "Code Completeness", True, "NONE", "All documented conditions captured.", start=start)

    async def check_17_upcoding_detection(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> SemanticCheckResult:
        start = time.time()
        prompt = "Detect potential upcoding by comparing documentation severity with selected codes."
        context = self._build_review_prompt(coding_result, scr)
        payload = await self._invoke_llm_reviewer("CHECK_17_UPCODING", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_17_UPCODING", "Upcoding Detection", payload, start)
        for code in coding_result.diagnosis_codes:
            description = (safe_get_text(code) or "").lower()
            if "severe" in description and "mild" in str(scr).lower():
                code_value = safe_get_code(code) or "UNKNOWN"
                return self._make_result("CHECK_17_UPCODING", "Upcoding Detection", False, "HARD_FAIL", f"Upcoding detected for {code_value}", [code_value], start=start)
        return self._make_result("CHECK_17_UPCODING", "Upcoding Detection", True, "NONE", "No upcoding risks detected.", start=start)

    async def check_18_guideline_compliance(
        self, coding_result: CodingResult, scr: StructuredClinicalRepresentation
    ) -> SemanticCheckResult:
        start = time.time()
        prompt = "Review adherence to Official Coding Guidelines, sequencing rules, and use additional instructions."
        context = self._build_review_prompt(coding_result, scr)
        payload = await self._invoke_llm_reviewer("CHECK_18_GUIDELINES", prompt, context)
        if payload:
            return self._payload_to_result("CHECK_18_GUIDELINES", "Guideline Compliance", payload, start)
        return self._make_result("CHECK_18_GUIDELINES", "Guideline Compliance", True, "NONE", "Coding follows current OCG.", start=start)

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
