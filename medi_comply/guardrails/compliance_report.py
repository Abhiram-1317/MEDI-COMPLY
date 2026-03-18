"""
MEDI-COMPLY — Compliance Report Generator.
Aggregates all evaluation layers into a comprehensive audit result.
"""

import uuid
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel

from medi_comply.guardrails.layer3_structural import StructuralCheckResult
from medi_comply.guardrails.layer4_semantic import SemanticCheckResult
from medi_comply.guardrails.layer5_output import OutputCheckResult
from medi_comply.guardrails.feedback_generator import ComplianceFeedback


class ComplianceReport(BaseModel):
    """Complete compliance validation report."""
    
    report_id: str
    coding_result_id: str
    created_at: datetime
    processing_time_ms: float
    
    # Overall decision
    overall_decision: str               # "PASS", "RETRY", "ESCALATE", "BLOCK"
    
    # Check summaries
    total_checks_run: int
    checks_passed: int
    checks_failed: int
    checks_skipped: int
    
    # Layer-by-layer results
    layer3_results: list[StructuralCheckResult]
    layer4_results: list[SemanticCheckResult]
    layer5_results: list[OutputCheckResult]
    
    # Risk assessment
    overall_risk_score: float           # 0.0 (no risk) to 1.0 (critical risk)
    risk_level: str                     # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    risk_factors: list[str]
    
    # Feedback (if not passing)
    feedback: Optional[ComplianceFeedback] = None
    
    # Security
    security_alerts: list[str]
    phi_detected: bool
    injection_detected: bool


class ComplianceReportGenerator:
    """Generates complete compliance reports from all check results."""
    
    def generate_report(
        self,
        structural_results: list[StructuralCheckResult],
        semantic_results: list[SemanticCheckResult],
        output_results: list[OutputCheckResult],
        coding_result_id: str,
        processing_time_ms: float
    ) -> ComplianceReport:
        all_results = structural_results + semantic_results + output_results
        
        passed = sum(1 for r in all_results if getattr(r, "passed", True))
        skipped = sum(1 for r in semantic_results if r.check_id == "SKIPPED")
        total = len(all_results)
        failed = total - passed - skipped
        
        phi = any("PHI" in getattr(r, "check_id", "") and not getattr(r, "passed", True) for r in output_results)
        inj = any("INJECTION" in getattr(r, "check_id", "") and not getattr(r, "passed", True) for r in output_results)
        security_alerts = [r.details for r in output_results if 'SECURITY' in getattr(r, 'severity', '') and not getattr(r, 'passed', True)]
        
        score, risk_lvl, factors = self.calculate_risk_score(structural_results, semantic_results, output_results)
        
        # Determine overall immediately based on security risk, feedback handles full logic
        if security_alerts:
            decision = "BLOCK"
        elif failed > 0:
            decision = "RETRY"
        else:
            decision = "PASS"
            
        return ComplianceReport(
            report_id=str(uuid.uuid4()),
            coding_result_id=coding_result_id,
            created_at=datetime.now(timezone.utc),
            processing_time_ms=processing_time_ms,
            overall_decision=decision,
            total_checks_run=total,
            checks_passed=passed,
            checks_failed=failed,
            checks_skipped=skipped,
            layer3_results=structural_results,
            layer4_results=semantic_results,
            layer5_results=output_results,
            overall_risk_score=score,
            risk_level=risk_lvl,
            risk_factors=factors,
            security_alerts=security_alerts,
            phi_detected=phi,
            injection_detected=inj
        )
    
    def calculate_risk_score(
        self,
        structural_results: list,
        semantic_results: list,
        output_results: list
    ) -> tuple[float, str, list[str]]:
        score = 0.0
        factors = []
        all_results = structural_results + semantic_results + output_results
        
        for r in all_results:
            if getattr(r, "passed", True): continue
            sev = getattr(r, "severity", "NONE")
            if "SECURITY" in sev:
                score += 0.50
                factors.append(f"Security threat in {r.check_name}")
            elif sev == "HARD_FAIL":
                score += 0.30
                factors.append(f"Critical violation in {r.check_name}")
            elif sev == "ESCALATE":
                score += 0.25
                factors.append(f"Review required in {r.check_name}")
            elif sev == "SOFT_FAIL":
                score += 0.10
                factors.append(f"Minor issue in {r.check_name}")
            elif sev == "WARNING":
                score += 0.03
                
        score = min(max(score, 0.0), 1.0)
        
        if score < 0.10: level = "LOW"
        elif score < 0.30: level = "MEDIUM"
        elif score < 0.60: level = "HIGH"
        else: level = "CRITICAL"
        
        return score, level, factors
    
    def generate_human_readable(self, report: ComplianceReport) -> str:
        status = "✅ ALL CHECKS PASSED" if report.overall_decision == "PASS" else ("⛔ BLOCKED" if report.overall_decision == "BLOCK" else f"⚠️ {report.overall_decision}")
        
        return f"""═══ COMPLIANCE REPORT ═══
Status: {status}
Risk Score: {report.overall_risk_score:.2f} ({report.risk_level})
Checks: {report.checks_passed}/{report.total_checks_run} passed

Layer 3 (Structural): {sum(1 for r in report.layer3_results if r.passed)}/{len(report.layer3_results)} ✅
Layer 4 (Semantic): {sum(1 for r in report.layer4_results if getattr(r, 'passed', True))}/{len(report.layer4_results)} ✅
Layer 5 (Output): {sum(1 for r in report.layer5_results if getattr(r, 'passed', True))}/{len(report.layer5_results)} ✅

{"⚠️ Security Alerts: " + str(report.security_alerts) if report.security_alerts else "No security alerts."}
"""
