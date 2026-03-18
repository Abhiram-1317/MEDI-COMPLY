"""
MEDI-COMPLY — Compliance Feedback Generator.

Generates structured feedback from compliance failures.
"""

from typing import Optional
from pydantic import BaseModel
from medi_comply.guardrails.layer3_structural import StructuralCheckResult
from medi_comply.guardrails.layer4_semantic import SemanticCheckResult
from medi_comply.guardrails.layer5_output import OutputCheckResult


class FeedbackItem(BaseModel):
    check_id: str
    severity: str
    issue: str
    action_required: str
    affected_codes: list[str] = []
    suggested_replacement: Optional[str] = None
    fix_type: str = "CHANGE_CODE"


class ComplianceFeedback(BaseModel):
    overall_decision: str
    total_checks: int
    passed: int
    failed: int
    hard_fails: int
    soft_fails: int
    
    feedback_items: list[FeedbackItem] = []
    retry_allowed: bool = True
    retry_count: int = 1
    max_retries: int = 3
    human_review_items: list[str] = []


class ComplianceFeedbackGenerator:
    """
    Generates structured feedback from compliance failures.
    This feedback is fed back to the MedicalCodingAgent for retry.
    """
    
    def generate_feedback(
        self,
        structural_results: list[StructuralCheckResult],
        semantic_results: list[SemanticCheckResult],
        output_results: list[OutputCheckResult],
        retry_count: int = 1,
        max_retries: int = 3
    ) -> ComplianceFeedback:
        """Analyze all check results and generate actionable feedback."""
        
        all_results = structural_results + semantic_results + output_results
        
        total_checks = len(all_results)
        passed = sum(1 for r in all_results if r.passed)
        failed = total_checks - passed
        
        hard_fails = sum(1 for r in all_results if "HARD_FAIL" in r.severity)
        soft_fails = sum(1 for r in all_results if r.severity == "SOFT_FAIL")
        escalations = sum(1 for r in all_results if r.severity == "ESCALATE")
        security_alerts = sum(1 for r in all_results if "SECURITY" in r.severity)
        
        decision = self._classify_overall_decision(
            hard_fails, soft_fails, escalations, security_alerts, retry_count, max_retries
        )
        
        items = []
        for r in all_results:
             if not r.passed:
                  fix = getattr(r, "fix_suggestion", None) or "Review documentation to address compliance issues."
                  codes = getattr(r, "affected_codes", [])
                  items.append(FeedbackItem(
                      check_id=r.check_id,
                      severity=r.severity,
                      issue=r.details,
                      action_required=fix,
                      affected_codes=codes,
                      fix_type="GENERAL_FIX"
                  ))
                  
        return ComplianceFeedback(
            overall_decision=decision,
            total_checks=total_checks,
            passed=passed,
            failed=failed,
            hard_fails=hard_fails,
            soft_fails=soft_fails,
            feedback_items=items,
            retry_allowed=(decision == "RETRY"),
            retry_count=retry_count,
            max_retries=max_retries,
            human_review_items=[r.details for r in all_results if r.severity in ["ESCALATE", "HARD_FAIL_SECURITY"]]
        )
    
    def _classify_overall_decision(
        self,
        hard_fails: int,
        soft_fails: int,
        escalations: int,
        security_alerts: int,
        retry_count: int,
        max_retries: int
    ) -> str:
        if security_alerts > 0:
             return "BLOCK"
        if escalations > 0:
             return "ESCALATE"
        if retry_count >= max_retries and (hard_fails > 0 or soft_fails > 0):
             return "ESCALATE"
        if hard_fails > 0 or soft_fails > 0:
             return "RETRY"
        return "PASS"
    
    def format_for_retry_prompt(self, feedback: ComplianceFeedback) -> str:
        """Format feedback into a string that can be inserted into the RETRY_PROMPT."""
        lines = ["COMPLIANCE ISSUES FOUND:"]
        for i, item in enumerate(feedback.feedback_items, 1):
            lines.append(f" {i}. [{item.severity}] {item.issue}")
            lines.append(f"    ACTION: {item.action_required}")
            if item.affected_codes:
                lines.append(f"    AFFECTED CODES: {', '.join(item.affected_codes)}")
        lines.append("\nPlease re-evaluate your coding addressing each issue above.")
        return "\n".join(lines)
