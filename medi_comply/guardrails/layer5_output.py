"""
MEDI-COMPLY — Final output validation before release (Layer 5).
Ensures output schema is correct, no PHI leaks, no injection artifacts.
"""

import time
import json
from typing import Optional, Any
from pydantic import BaseModel

from medi_comply.schemas.coding_result import CodingResult
from medi_comply.guardrails.security_guards import SecurityGuards


class OutputCheckResult(BaseModel):
    check_id: str
    check_name: str
    passed: bool
    severity: str
    details: str
    sanitized_output: Optional[dict] = None
    check_time_ms: float


class OutputValidator:
    """
    Layer 5: Final output validation before release.
    Ensures output schema is correct, no PHI leaks, no injection artifacts.
    """
    
    def run_all_checks(
        self,
        coding_result: CodingResult,
        raw_llm_outputs: list[str] = None
    ) -> list[OutputCheckResult]:
        """Run all output checks."""
        # Use an empty list if None
        raw_outputs = raw_llm_outputs or []
        
        results = []
        results.append(self.check_19_schema_validation(coding_result))
        results.append(self.check_20_phi_detection(coding_result))
        results.append(self.check_21_prompt_injection(coding_result, raw_outputs))
        results.append(self.check_22_completeness(coding_result))
        results.append(self.check_23_hallucination_markers(coding_result))
        return results
    
    def check_19_schema_validation(self, coding_result: CodingResult) -> OutputCheckResult:
        """CHECK 19: JSON SCHEMA VALIDATION"""
        start = time.time()
        passed = True
        details = "Schema is fully valid."
        
        try:
             # Pydantic validates on initialization, but we can do a sanity check on field logic
             if not coding_result.diagnosis_codes and coding_result.coding_summary:
                  passed = False
                  details = "No diagnosis codes found despite having a coding summary."
             if any(d.confidence_score < 0.0 or d.confidence_score > 1.0 for d in coding_result.diagnosis_codes):
                  passed = False
                  details = "Found confidence score outside bounds [0.0, 1.0]."
        except Exception as e:
             passed = False
             details = f"Validation exception: {str(e)}"
             
        return OutputCheckResult(
            check_id="CHECK_19_SCHEMA_VALIDATION",
            check_name="JSON Schema Validation",
            passed=passed,
            severity="HARD_FAIL" if not passed else "NONE",
            details=details,
            check_time_ms=(time.time() - start) * 1000
        )
    
    def check_20_phi_detection(self, coding_result: CodingResult) -> OutputCheckResult:
        """CHECK 20: PHI (Protected Health Information) LEAK DETECTION"""
        start = time.time()
        
        # Serialize to text for scanning
        try:
             text_dump = coding_result.model_dump_json()
        except Exception:
             text_dump = str(coding_result)
             
        matches = SecurityGuards.scan_for_phi(text_dump)
        if matches:
             sanitized = SecurityGuards.sanitize_output(coding_result.model_dump())
             return OutputCheckResult(
                 check_id="CHECK_20_PHI_DETECTION",
                 check_name="PHI Detection",
                 passed=False,
                 severity="HARD_FAIL_SECURITY",
                 details=f"Detected {len(matches)} potential PHI instances. e.g. {matches[0].phi_type}",
                 sanitized_output=sanitized,
                 check_time_ms=(time.time() - start) * 1000
             )
             
        return OutputCheckResult(
            check_id="CHECK_20_PHI_DETECTION",
            check_name="PHI Detection",
            passed=True,
            severity="NONE",
            details="No PHI detected in output.",
            check_time_ms=(time.time() - start) * 1000
        )
    
    def check_21_prompt_injection(
        self, coding_result: CodingResult, raw_llm_outputs: list[str] = None
    ) -> OutputCheckResult:
        """CHECK 21: PROMPT INJECTION DETECTION"""
        start = time.time()
        
        texts_to_scan = raw_llm_outputs or []
        try:
             texts_to_scan.append(coding_result.model_dump_json())
        except Exception:
             texts_to_scan.append(str(coding_result))
             
        for text in texts_to_scan:
             matches = SecurityGuards.scan_for_injection(text)
             if matches:
                  return OutputCheckResult(
                      check_id="CHECK_21_PROMPT_INJECTION",
                      check_name="Prompt Injection Detection",
                      passed=False,
                      severity="HARD_FAIL_SECURITY",
                      details=f"Potential prompt injection detected: {matches[0].injection_type}",
                      check_time_ms=(time.time() - start) * 1000
                  )
                  
        return OutputCheckResult(
            check_id="CHECK_21_PROMPT_INJECTION",
            check_name="Prompt Injection Detection",
            passed=True,
            severity="NONE",
            details="No injection signatures detected.",
            check_time_ms=(time.time() - start) * 1000
        )
    
    def check_22_completeness(self, coding_result: CodingResult) -> OutputCheckResult:
        """CHECK 22: OUTPUT COMPLETENESS"""
        start = time.time()
        
        missing_critical = []
        soft_issues = []
        
        all_codes = coding_result.diagnosis_codes + coding_result.procedure_codes
        
        for code in all_codes:
             if len(code.reasoning_chain) < 2:
                  missing_critical.append(f"Code {code.code} has < 2 reasoning steps.")
             if not code.clinical_evidence:
                  missing_critical.append(f"Code {code.code} is missing clinical evidence.")
             if not code.description:
                  missing_critical.append(f"Code {code.code} is missing description.")
                  
             if not code.guidelines_cited:
                  soft_issues.append(f"Code {code.code} cited no guidelines.")
                  
        if not coding_result.coding_summary:
             missing_critical.append("Missing overall coding summary.")
             
        # Sequence number uniqueness
        seq_nums = [c.sequence_number for c in coding_result.diagnosis_codes]
        if len(seq_nums) != len(set(seq_nums)):
             missing_critical.append("Duplicate sequence numbers detected.")
             
        diagnoses = coding_result.diagnosis_codes
        primary_count = sum(1 for c in diagnoses if c.sequence_position == "PRIMARY")
        if primary_count != 1 and len(diagnoses) > 0:
             missing_critical.append(f"Found {primary_count} primary diagnoses (must be exactly 1).")
             
        passed = (len(missing_critical) == 0)
        severity = "HARD_FAIL" if not passed else ("SOFT_FAIL" if soft_issues else "NONE")
        
        details = ""
        if missing_critical:
             details = "Critical completion issues: " + "; ".join(missing_critical)
        elif soft_issues:
             details = "Minor completion issues: " + "; ".join(soft_issues)
        else:
             details = "Output is complete and well-formed."
             
        return OutputCheckResult(
            check_id="CHECK_22_COMPLETENESS",
            check_name="Output Completeness",
            passed=passed,
            severity=severity,
            details=details,
            check_time_ms=(time.time() - start) * 1000
        )
    
    def check_23_hallucination_markers(self, coding_result: CodingResult) -> OutputCheckResult:
        """CHECK 23: HALLUCINATION MARKER DETECTION"""
        start = time.time()
        
        hallucination_phrases = [
            "i think", "i believe", "in my opinion",
            "probably", "possibly", "perhaps",
            "it seems", "it appears", "it looks like",
            "i'm not sure", "i am not sure", "i'm uncertain",
            "might be", "could be", "may be",
            "based on my training", "as an ai",
            "as a language model", "i don't have access",
            "i cannot verify", "i assume"
        ]
        
        try:
             text_dump = coding_result.model_dump_json().lower()
        except:
             text_dump = str(coding_result).lower()
             
        found_markers = []
        for phrase in hallucination_phrases:
             if phrase in text_dump:
                  found_markers.append(phrase)
                  
        passed = (len(found_markers) == 0)
        
        details = "No hallucination markers found."
        if not passed:
             details = f"Found uncertain/hallucination phrases: {', '.join(found_markers)}"
             
        return OutputCheckResult(
            check_id="CHECK_23_HALLUCINATION_MARKERS",
            check_name="Hallucination Marker Detection",
            passed=passed,
            severity="SOFT_FAIL" if not passed else "NONE",
            details=details,
            check_time_ms=(time.time() - start) * 1000
        )
