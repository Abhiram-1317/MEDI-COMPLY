"""
Layer 2 — Prompt Engineering Guardrails for MEDI-COMPLY.

This module implements prompt-level safeguards (system prompt construction,
input sanitization, prompt-injection detection, and output format validation)
for the five-layer "Compliance Cage" architecture.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class PromptHardRule(str, Enum):
    """Non-negotiable prompt rules for coding and claims use cases."""

    RULE_1 = "NEVER generate a code that doesn't exist in the provided code database"
    RULE_2 = "NEVER assign a code without citing the specific clinical documentation that supports it"
    RULE_3 = "ALWAYS check Excludes1/Excludes2 before finalizing"
    RULE_4 = "ALWAYS code to highest specificity available"
    RULE_5 = "If confidence < 0.85, MUST flag for human review"
    RULE_6 = "NEVER make assumptions about laterality"
    RULE_7 = "NEVER assign a code based on 'probable' or 'suspected' in outpatient settings"
    RULE_8 = "ALWAYS sequence codes per Official Coding Guidelines"
    RULE_9 = "NEVER provide medical advice to patients"
    RULE_10 = "For ANY uncertainty, output: {\"action\": \"ESCALATE\", \"reason\": \"...\"}"


class InjectionSeverity(str, Enum):
    """Severity for prompt injection attempts."""

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SanitizationResult(BaseModel):
    """Result of input sanitization and PHI/off-topic checks."""

    is_safe: bool
    detected_issues: List[str] = Field(default_factory=list)
    sanitized_text: str
    truncated: bool = False
    phi_detected: bool = False
    injection_patterns: List[str] = Field(default_factory=list)


class OutputValidationResult(BaseModel):
    """Result of validating model output JSON structure and required fields."""

    is_valid: bool
    schema_errors: List[str] = Field(default_factory=list)
    missing_fields: List[str] = Field(default_factory=list)
    confidence_issues: List[str] = Field(default_factory=list)
    parsed: Optional[Dict] = None


class InjectionDetectionResult(BaseModel):
    """Detection details for prompt injection attempts."""

    severity: InjectionSeverity
    patterns: List[str] = Field(default_factory=list)
    message: Optional[str] = None


class Layer2Result(BaseModel):
    """Aggregate result for all Layer 2 checks (pre- and post-LLM)."""

    pre_llm_passed: bool
    post_llm_passed: bool
    overall_passed: bool
    system_prompt_used: Optional[str] = None
    issues: List[str] = Field(default_factory=list)
    blocked: bool = False
    sanitization: Optional[SanitizationResult] = None
    injection: Optional[InjectionDetectionResult] = None
    output_validation: Optional[OutputValidationResult] = None


class SystemPromptBuilder:
    """Constructs system prompts that embed Layer 2 hard rules and output schema."""

    def __init__(self) -> None:
        self.rules = list(PromptHardRule)

    def build_coding_prompt(self, encounter_type: str, payer_id: Optional[str] = None) -> str:
        """Build the coding system prompt with hard rules and output schema."""

        encounter_type = (encounter_type or "outpatient").lower()
        rule7_note = (
            "In outpatient settings, NEVER code based on 'probable' or 'suspected'."
            if encounter_type == "outpatient"
            else "For inpatient, follow ICD-10-CM guidance for uncertain diagnoses."
        )

        rules_text = "\n".join(f"- {r.value}" for r in self.rules)
        payer_section = (
            f"\nPayer-specific constraints: adhere to payer_id={payer_id} medical necessity and coverage policies."
            if payer_id
            else ""
        )

        return (
            "You are a certified medical coder (CCS, CPC) operating within MEDI-COMPLY's Compliance Cage.\n"
            "Follow ALL hard rules; violations are not allowed.\n"
            f"Encounter type: {encounter_type}. {rule7_note}\n\n"
            "HARD RULES:\n"
            f"{rules_text}\n\n"
            "OUTPUT FORMAT (strict JSON):\n"
            "{\n"
            "  \"action\": \"ASSIGN\" | \"ESCALATE\",\n"
            "  \"codes\": [ { \"code\": ""ICD10|CPT"", \"system\": ""ICD10|CPT"", \"description\": ""..."", \"confidence_score\": 0.0-1.0, \"reasoning_chain\": [""step...""], \"evidence\": [""cite doc snippet""] } ],\n"
            "  \"overall_confidence\": 0.0-1.0,\n"
            "  \"guidelines_cited\": [""OCG refs""],\n"
            "  \"notes\": ""...""\n"
            "}\n\n"
            "REASONING: Provide a concise chain-of-thought per code with explicit citations to the clinical note sections.\n"
            "If confidence < 0.85 for any code, set action to ESCALATE and include reason.\n"
            f"{payer_section}"
        )

    def build_claims_prompt(self) -> str:
        return (
            "You are a claims adjudication specialist. Apply all Layer 2 hard rules,"
            " verify coverage, modifiers, and NCCI edits before approving. Output JSON per schema with adjudication decisions."
        )

    def build_prior_auth_prompt(self) -> str:
        return (
            "You are a prior authorization reviewer. Apply all Layer 2 hard rules plus payer policy alignment."
            " Require evidence citations and confidence; escalate if documentation is insufficient."
        )

    def get_output_schema(self) -> Dict:
        """Return the required JSON schema for downstream validation."""

        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["ASSIGN", "ESCALATE"]},
                "codes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string"},
                            "system": {"type": "string"},
                            "description": {"type": "string"},
                            "confidence_score": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                            "reasoning_chain": {"type": "array", "items": {"type": "string"}},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["code", "system", "confidence_score", "reasoning_chain", "evidence"],
                    },
                },
                "overall_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "guidelines_cited": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
            },
            "required": ["action", "codes", "overall_confidence"],
        }


class PromptInjectionDetector:
    """Detects prompt injection and role/instruction override attempts."""

    ROLE_OVERRIDE_PATTERNS = [
        r"\byou are now\b",
        r"\bact as\b",
        r"\bpretend to be\b",
    ]
    INSTRUCTION_OVERRIDE_PATTERNS = [
        r"ignore (all )?(previous|earlier) instructions",
        r"disregard (all )?(prior|previous) (guidance|rules)",
        r"forget (what )?i said",
        r"reset (instructions|system)",
    ]
    SYSTEM_EXTRACTION_PATTERNS = [
        r"repeat your instructions",
        r"repeat your system prompt",
        r"show (the )?(system )?prompt",
        r"display hidden instructions",
    ]
    ENCODING_PATTERNS = [r"[A-Za-z0-9+/]{20,}={0,2}\b"]  # base64-like
    DELIMITER_PATTERNS = [r"<system>.*?</system>", r"```.*?```", r"<prompt>.*?</prompt>"]

    def detect(self, text: str) -> InjectionDetectionResult:
        if not text:
            return InjectionDetectionResult(severity=InjectionSeverity.NONE, patterns=[])

        found: List[str] = []

        def _collect(patterns: List[str], label: str) -> None:
            for pat in patterns:
                if re.search(pat, text, flags=re.IGNORECASE | re.DOTALL):
                    found.append(label)

        _collect(self.ROLE_OVERRIDE_PATTERNS, "ROLE_OVERRIDE")
        _collect(self.INSTRUCTION_OVERRIDE_PATTERNS, "INSTRUCTION_OVERRIDE")
        _collect(self.SYSTEM_EXTRACTION_PATTERNS, "SYSTEM_EXTRACTION")
        _collect(self.ENCODING_PATTERNS, "ENCODING_ATTACK")
        _collect(self.DELIMITER_PATTERNS, "DELIMITER_CONFUSION")

        severity = InjectionSeverity.NONE
        if any(tag in found for tag in ["ENCODING_ATTACK", "DELIMITER_CONFUSION"]):
            severity = InjectionSeverity.MEDIUM
        if any(tag in found for tag in ["ROLE_OVERRIDE", "INSTRUCTION_OVERRIDE", "SYSTEM_EXTRACTION"]):
            severity = max(severity, InjectionSeverity.HIGH, key=lambda s: list(InjectionSeverity).index(s))
        if "ENCODING_ATTACK" in found and ("ROLE_OVERRIDE" in found or "INSTRUCTION_OVERRIDE" in found):
            severity = InjectionSeverity.CRITICAL

        return InjectionDetectionResult(
            severity=severity,
            patterns=found,
            message="; ".join(found) if found else None,
        )


class PromptInputSanitizer:
    """Performs basic sanitization, PHI screening, and off-topic detection."""

    MAX_LEN = 50_000
    PHI_PATTERNS = [
        r"\b\d{3}-\d{2}-\d{4}\b",  # SSN
        r"\bMRN:?\s?\d{6,}\b",  # MRN patterns with optional colon/space
        r"\b\d{10}\b",  # phone-like digits
        r"\b\d{3}-\d{3}-\d{4}\b",  # phone with dashes
        r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}",
    ]
    OFF_TOPIC_KEYWORDS = ["recipe", "stock price", "weather", "travel blog", "gaming"]

    def sanitize(self, user_input: str) -> SanitizationResult:
        text = (user_input or "").strip()
        issues: List[str] = []
        truncated = False

        if len(text) > self.MAX_LEN:
            text = text[: self.MAX_LEN]
            truncated = True
            issues.append("input_truncated_max_length")

        phi_hits = [pat for pat in self.PHI_PATTERNS if re.search(pat, text, flags=re.IGNORECASE)]
        if phi_hits:
            issues.append("phi_detected")

        off_topic = any(k in text.lower() for k in self.OFF_TOPIC_KEYWORDS)
        if off_topic:
            issues.append("off_topic_content")

        # Mild injection heuristics to surface context (detailed detection handled separately)
        inj_patterns = []
        if re.search(r"ignore (previous|earlier) instructions", text, flags=re.IGNORECASE):
            inj_patterns.append("ignore_previous_instructions")
            issues.append("potential_injection")

        is_safe = not phi_hits and not off_topic and not truncated
        return SanitizationResult(
            is_safe=is_safe,
            detected_issues=issues,
            sanitized_text=text,
            truncated=truncated,
            phi_detected=bool(phi_hits),
            injection_patterns=inj_patterns,
        )


class OutputFormatEnforcer:
    """Validates that LLM output adheres to the required JSON schema and rules."""

    def __init__(self, schema: Dict) -> None:
        self.schema = schema

    def validate_output(self, llm_response: str) -> OutputValidationResult:
        errors: List[str] = []
        missing: List[str] = []
        confidence_issues: List[str] = []
        parsed = None

        try:
            parsed = json.loads(llm_response)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"invalid_json: {exc}")
            return OutputValidationResult(is_valid=False, schema_errors=errors, missing_fields=missing, confidence_issues=confidence_issues)

        # Top-level required fields
        for field in self.schema.get("required", []):
            if field not in parsed:
                missing.append(field)

        codes = parsed.get("codes", []) if isinstance(parsed, dict) else []
        if not isinstance(codes, list):
            errors.append("codes_not_list")
        else:
            for idx, code_obj in enumerate(codes):
                if not isinstance(code_obj, dict):
                    errors.append(f"code[{idx}]_not_object")
                    continue
                for req in ["code", "system", "confidence_score", "reasoning_chain", "evidence"]:
                    if req not in code_obj:
                        missing.append(f"codes[{idx}].{req}")
                conf = code_obj.get("confidence_score")
                if conf is None or not isinstance(conf, (int, float)) or conf < 0 or conf > 1:
                    confidence_issues.append(f"codes[{idx}].confidence_score_out_of_range")
                if not code_obj.get("reasoning_chain"):
                    errors.append(f"codes[{idx}].reasoning_chain_missing")
                if not code_obj.get("evidence"):
                    errors.append(f"codes[{idx}].evidence_missing")

        overall_conf = parsed.get("overall_confidence")
        if overall_conf is None or not isinstance(overall_conf, (int, float)) or overall_conf < 0 or overall_conf > 1:
            confidence_issues.append("overall_confidence_out_of_range")

        # Escalation expectation when low confidence
        if isinstance(codes, list):
            low_conf = any((c.get("confidence_score", 1.0) < 0.85) for c in codes if isinstance(c, dict))
            if low_conf and parsed.get("action") != "ESCALATE":
                errors.append("low_confidence_without_escalate")

        is_valid = not errors and not missing and not confidence_issues
        return OutputValidationResult(
            is_valid=is_valid,
            schema_errors=errors,
            missing_fields=missing,
            confidence_issues=confidence_issues,
            parsed=parsed if is_valid else parsed,
        )


class Layer2PromptGuard:
    """Runs Layer 2 checks before and after LLM invocation."""

    def __init__(self) -> None:
        self.prompt_builder = SystemPromptBuilder()
        self.sanitizer = PromptInputSanitizer()
        self.injection_detector = PromptInjectionDetector()
        self.output_enforcer = OutputFormatEnforcer(self.prompt_builder.get_output_schema())

    def run_checks(
        self,
        user_input: str,
        encounter_type: str,
        model_output: Optional[str] = None,
    ) -> Layer2Result:
        issues: List[str] = []

        # Pre-LLM checks
        sanitization = self.sanitizer.sanitize(user_input)
        injection = self.injection_detector.detect(user_input)

        if sanitization.detected_issues:
            issues.extend(sanitization.detected_issues)
        if injection.patterns:
            issues.append("injection_detected")

        blocked = injection.severity in {InjectionSeverity.HIGH, InjectionSeverity.CRITICAL}
        pre_passed = sanitization.is_safe and not blocked

        system_prompt = self.prompt_builder.build_coding_prompt(encounter_type)

        # Post-LLM checks
        output_validation: Optional[OutputValidationResult] = None
        post_passed = True
        if model_output is not None and pre_passed:
            output_validation = self.output_enforcer.validate_output(model_output)
            if not output_validation.is_valid:
                post_passed = False
                issues.extend(output_validation.schema_errors)
                issues.extend(output_validation.missing_fields)
                issues.extend(output_validation.confidence_issues)
            else:
                # Spot-check for hard rule compliance (simple heuristics)
                parsed = output_validation.parsed or {}
                if parsed.get("action") == "ASSIGN":
                    low_conf = any(
                        (c.get("confidence_score", 1.0) < 0.85)
                        for c in parsed.get("codes", [])
                        if isinstance(c, dict)
                    )
                    if low_conf:
                        issues.append("hard_rule_5_violation")
                        post_passed = False

        overall_passed = pre_passed and post_passed and not blocked

        return Layer2Result(
            pre_llm_passed=pre_passed,
            post_llm_passed=post_passed,
            overall_passed=overall_passed,
            system_prompt_used=system_prompt,
            issues=list(dict.fromkeys(issues)),  # dedupe while preserving order
            blocked=blocked,
            sanitization=sanitization,
            injection=injection,
            output_validation=output_validation,
        )


class PromptTemplatesRegistry:
    """Registry mapping use cases to system prompt templates."""

    def __init__(self) -> None:
        self.builder = SystemPromptBuilder()
        self.templates: Dict[str, str] = {
            "MEDICAL_CODING_INPATIENT": self.builder.build_coding_prompt("inpatient"),
            "MEDICAL_CODING_OUTPATIENT": self.builder.build_coding_prompt("outpatient"),
            "CLAIMS_ADJUDICATION": self.builder.build_claims_prompt(),
            "PRIOR_AUTHORIZATION": self.builder.build_prior_auth_prompt(),
            "COMPLIANCE_REVIEW": self.builder.build_coding_prompt("outpatient"),
        }

    def get(self, key: str) -> Optional[str]:
        return self.templates.get(key)


# Public API
__all__ = [
    "PromptHardRule",
    "InjectionSeverity",
    "SanitizationResult",
    "OutputValidationResult",
    "InjectionDetectionResult",
    "Layer2Result",
    "SystemPromptBuilder",
    "PromptInjectionDetector",
    "PromptInputSanitizer",
    "OutputFormatEnforcer",
    "Layer2PromptGuard",
    "PromptTemplatesRegistry",
]
