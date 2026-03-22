"""
MEDI-COMPLY — Compliance Feedback Generator.

Generates structured feedback from compliance failures.
"""

from typing import Optional
from pydantic import BaseModel, Field
from medi_comply.guardrails.layer3_structural import StructuralCheckResult
from medi_comply.guardrails.layer4_semantic import SemanticCheckResult
from medi_comply.guardrails.layer5_output import OutputCheckResult


class FeedbackItem(BaseModel):
    check_id: str
    severity: str
    issue: str
    action_required: str
    affected_codes: list[str] = Field(default_factory=list)
    suggested_replacement: Optional[str] = None
    fix_type: str = "CHANGE_CODE"


class ComplianceFeedback(BaseModel):
    overall_decision: str
    total_checks: int
    passed: int
    failed: int
    hard_fails: int
    soft_fails: int
    
    feedback_items: list[FeedbackItem] = Field(default_factory=list)
    retry_allowed: bool = True
    retry_count: int = 1
    max_retries: int = 3
    human_review_items: list[str] = Field(default_factory=list)


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
        failed = sum(1 for r in all_results if not getattr(r, "passed", True))
        passed = total_checks - failed

        hard_fails = sum(
            1 for r in all_results
            if not getattr(r, "passed", True)
            and "HARD_FAIL" in getattr(r, "severity", "")
        )
        soft_fails = sum(
            1 for r in all_results
            if not getattr(r, "passed", True)
            and getattr(r, "severity", "") == "SOFT_FAIL"
        )
        escalations = sum(
            1 for r in all_results
            if not getattr(r, "passed", True)
            and getattr(r, "severity", "") == "ESCALATE"
        )
        security_alerts = sum(
            1 for r in all_results
            if not getattr(r, "passed", True)
            and "SECURITY" in getattr(r, "severity", "")
        )

        decision = self._classify_overall_decision(
            hard_fails, soft_fails, escalations, security_alerts, retry_count, max_retries
        )

        items = self._build_feedback_items(all_results)
        human_review = self._collect_human_review_items(all_results)

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
            human_review_items=human_review,
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
            lines.append(f"    FIX TYPE: {item.fix_type}")
            if item.affected_codes:
                lines.append(f"    AFFECTED CODES: {', '.join(item.affected_codes)}")
        lines.append("\nPlease re-evaluate your coding addressing each issue above.")
        return "\n".join(lines)

    def _build_feedback_items(self, results: list) -> list[FeedbackItem]:
        items: list[FeedbackItem] = []
        for result in results:
            if getattr(result, "passed", True):
                continue
            items.append(self._make_feedback_item(result))
        return items

    def _make_feedback_item(self, result) -> FeedbackItem:
        fix_type, default_action = self._determine_fix_type(result)
        action = getattr(result, "fix_suggestion", None) or default_action
        return FeedbackItem(
            check_id=getattr(result, "check_id", "UNKNOWN"),
            severity=getattr(result, "severity", "UNKNOWN"),
            issue=getattr(result, "details", "Compliance issue detected."),
            action_required=action,
            affected_codes=getattr(result, "affected_codes", []) or [],
            suggested_replacement=getattr(result, "suggested_replacement", None),
            fix_type=fix_type,
        )

    def _determine_fix_type(self, result) -> tuple[str, str]:
        check_id = getattr(result, "check_id", "").upper()
        severity = getattr(result, "severity", "")

        if "SECURITY" in severity:
            return "SECURITY", "Security policy violation detected. Escalate immediately."
        if check_id == "CHECK_03_EXCLUDES1":
            return "REMOVE_CODE", "Codes are mutually exclusive. Remove the incorrect code."
        if check_id == "CHECK_02_NCCI_EDITS":
            return "REMOVE_CODE", "Resolve the NCCI edit by removing or correctly modifying the bundled code."
        if check_id == "CHECK_05_SPECIFICITY":
            return "CHOOSE_MORE_SPECIFIC", "Select a more specific ICD-10 code."
        if check_id == "CHECK_10_MUE":
            return "ADJUST_UNITS", "Reduce the number of billed units to stay within the MUE limit."
        if check_id == "CHECK_11_BILLABLE":
            return "CHANGE_CODE", "Replace non-billable category codes with a terminal code."
        if check_id == "CHECK_12_USE_ADDITIONAL":
            return "ADD_CODE", "Append the required additional code from the referenced category."
        if check_id == "CHECK_13_CONFIDENCE":
            return "REVIEW_EVIDENCE", "Strengthen evidence or escalate for manual validation."
        if check_id == "CHECK_22_COMPLETENESS":
            return "ADD_EVIDENCE", "Complete missing reasoning, evidence, or sequencing details."
        if check_id in {"CHECK_20_PHI_DETECTION", "CHECK_21_PROMPT_INJECTION"}:
            return "SECURITY", "Security controls triggered. Stop automation and escalate."
        return "GENERAL_FIX", "Review documentation to resolve the compliance issue."

    def _collect_human_review_items(self, results: list) -> list[str]:
        items: list[str] = []
        for result in results:
            if getattr(result, "passed", True):
                continue
            severity = getattr(result, "severity", "")
            if severity == "ESCALATE" or "SECURITY" in severity:
                items.append(getattr(result, "details", "Manual review required."))
        return items
