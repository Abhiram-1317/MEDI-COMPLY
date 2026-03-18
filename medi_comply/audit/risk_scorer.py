"""
MEDI-COMPLY — Calculates audit risk score for each encounter.

Each coding decision receives a risk score between 0.0 and 1.0 that
determines the audit priority: ROUTINE < ELEVATED < URGENT < IMMEDIATE.

Risk is computed by evaluating ten independent factors whose weights
sum to 1.20 (intentionally > 1.0 so that multiple triggers can push
the score to the cap of 1.0).  The risk assessment drives the audit
queue ordering and determines which records require human review.
"""

from __future__ import annotations

from typing import Optional

from medi_comply.audit.audit_models import (
    WorkflowTrace,
    AuditRiskAssessment,
    CodeDecisionRecord,
)


# ────────────────────────────────────────────────────────
# Risk-factor configuration
# ────────────────────────────────────────────────────────

_RISK_FACTORS: dict[str, dict] = {
    "low_confidence_code": {
        "weight": 0.15,
        "description": "One or more codes have confidence below 0.85",
    },
    "unspecified_code_used": {
        "weight": 0.10,
        "description": "Unspecified code used when more specific available",
    },
    "high_severity_codes": {
        "weight": 0.12,
        "description": "High-complexity/severity codes assigned (DRG impact)",
    },
    "multiple_retries": {
        "weight": 0.15,
        "description": "Required multiple compliance retries",
    },
    "combination_code_complexity": {
        "weight": 0.08,
        "description": "Complex combination codes used",
    },
    "weak_evidence_links": {
        "weight": 0.15,
        "description": "Evidence links have low strength scores",
    },
    "many_codes_assigned": {
        "weight": 0.05,
        "description": "High number of codes may indicate over-coding",
    },
    "compliance_soft_fails": {
        "weight": 0.10,
        "description": "Compliance checks had soft failures or skips",
    },
    "upcoding_proximity": {
        "weight": 0.20,
        "description": "Selected code is close to a higher-severity alternative",
    },
    "rule_based_fallback": {
        "weight": 0.10,
        "description": "Rule-based fallback used instead of LLM reasoning",
    },
}

# Codes whose mere presence indicates a high-severity DRG impact.
_HIGH_SEVERITY_PREFIX = (
    "I21", "I22",  # Acute MI
    "J96",          # Respiratory failure
    "R65",          # SIRS / sepsis
    "T80", "T81",   # Complications of procedures
    "A41",          # Sepsis
)

# Thresholds
_LOW_CONFIDENCE_THRESHOLD = 0.85
_WEAK_EVIDENCE_THRESHOLD = 0.50
_HIGH_CODE_COUNT = 15


class AuditRiskScorer:
    """Calculates audit risk score for each encounter to prioritise review.

    All public interfaces are stateless — the scorer may be reused across
    encounters without side-effects.
    """

    RISK_FACTORS = _RISK_FACTORS

    # ── Public entry point ────────────────────────────────

    def calculate_risk(
        self, workflow_trace: WorkflowTrace
    ) -> AuditRiskAssessment:
        """Evaluate a completed workflow trace and return a risk assessment.

        Parameters
        ----------
        workflow_trace:
            A fully-populated ``WorkflowTrace`` from the audit trail.

        Returns
        -------
        AuditRiskAssessment
            Contains the overall score, risk level, triggered factors,
            recommendations, and audit priority.
        """
        score = 0.0
        triggered: list[dict] = []
        recommendations: list[str] = []

        # Evaluate each factor in a fixed order
        checks: list[tuple[str, Optional[dict]]] = [
            ("low_confidence_code", self._check_low_confidence(workflow_trace)),
            ("unspecified_code_used", self._check_unspecified_codes(workflow_trace)),
            ("high_severity_codes", self._check_severity_level(workflow_trace)),
            ("multiple_retries", self._check_retries(workflow_trace)),
            ("weak_evidence_links", self._check_evidence_strength(workflow_trace)),
            ("many_codes_assigned", self._check_code_count(workflow_trace)),
            ("upcoding_proximity", self._check_upcoding_proximity(workflow_trace)),
            ("compliance_soft_fails", self._check_compliance_soft_fails(workflow_trace)),
        ]

        _RECOMMENDATIONS: dict[str, str] = {
            "low_confidence_code": "Review manual assignment for low confidence codes.",
            "unspecified_code_used": "Verify if clinical documentation supports higher specificity.",
            "high_severity_codes": "High severity DRG impact noted; verify clinical indicator thresholds.",
            "multiple_retries": "Multiple compliance rewrites necessary; ensure final sequence is unbundled.",
            "weak_evidence_links": "Some links to clinical evidence appear weak; cross-reference subjective statements.",
            "many_codes_assigned": "Unusually high number of procedure codes. Check for unbundling potential.",
            "upcoding_proximity": "Diagnosis proximity matches severity bump profiles; verify explicit support.",
            "compliance_soft_fails": "Some compliance checks flagged or skipped; review for potential policy gaps.",
        }

        for factor_name, result in checks:
            if result is not None:
                triggered.append(result)
                score += result["weight"]
                rec = _RECOMMENDATIONS.get(factor_name)
                if rec:
                    recommendations.append(rec)

        # Inline checks for combination codes and rule-based fallback
        self._check_combination_codes(workflow_trace, triggered, score)
        score = sum(f["weight"] for f in triggered)  # recalculate after inline adds

        self._check_rule_based_fallback(workflow_trace, triggered, score)
        score = sum(f["weight"] for f in triggered)

        risk_level = self._determine_priority(score)

        return AuditRiskAssessment(
            overall_score=round(min(score, 1.0), 2),
            risk_level=risk_level,
            risk_factors_triggered=triggered,
            recommendations=recommendations,
            audit_priority=risk_level,
        )

    # ── Priority classification ───────────────────────────

    def _determine_priority(self, risk_score: float) -> str:
        """Map a numeric risk score to a categorical priority level."""
        if risk_score < 0.15:
            return "ROUTINE"
        if risk_score < 0.35:
            return "ELEVATED"
        if risk_score < 0.60:
            return "URGENT"
        return "IMMEDIATE"

    # ── Individual risk factor checks ─────────────────────

    def _check_low_confidence(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when overall or per-code confidence is below threshold."""
        overall = trace.final_output.overall_confidence
        if overall < _LOW_CONFIDENCE_THRESHOLD:
            return self._make_factor(
                "low_confidence_code",
                f"Overall confidence score is {overall:.2f}",
            )
        # Also check individual code decisions
        for cd in trace.coding_stage.code_decisions:
            if cd.confidence_score < _LOW_CONFIDENCE_THRESHOLD:
                return self._make_factor(
                    "low_confidence_code",
                    f"Code {cd.code} confidence is {cd.confidence_score:.2f}",
                )
        return None

    def _check_unspecified_codes(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag codes ending in '.9' or similar unspecified markers."""
        for code in trace.coding_stage.code_decisions:
            if code.code.endswith("9") or code.code.endswith(".9"):
                return self._make_factor(
                    "unspecified_code_used",
                    f"Unspecified code: {code.code}",
                )
        return None

    def _check_severity_level(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when high-severity/high-DRG-impact codes are present."""
        for code in trace.coding_stage.code_decisions:
            for prefix in _HIGH_SEVERITY_PREFIX:
                if code.code.startswith(prefix):
                    return self._make_factor(
                        "high_severity_codes",
                        f"High severity code: {code.code} ({code.description[:40]})",
                    )
        return None

    def _check_retries(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when the system required multiple compliance retries."""
        if trace.total_attempts > 1:
            return self._make_factor(
                "multiple_retries",
                f"{trace.total_attempts} attempts used",
            )
        return None

    def _check_evidence_strength(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when any evidence link has a strength below threshold."""
        for code in trace.coding_stage.code_decisions:
            for ev in code.evidence_links:
                if ev.link_strength < _WEAK_EVIDENCE_THRESHOLD:
                    return self._make_factor(
                        "weak_evidence_links",
                        f"Evidence for {code.code} has strength "
                        f"{ev.link_strength:.2f} (< {_WEAK_EVIDENCE_THRESHOLD})",
                    )
        return None

    def _check_code_count(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when an unusually high number of codes is assigned."""
        total = trace.final_output.total_codes
        if total > _HIGH_CODE_COUNT:
            return self._make_factor(
                "many_codes_assigned",
                f"{total} codes mapped (threshold: {_HIGH_CODE_COUNT})",
            )
        return None

    def _check_upcoding_proximity(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when the chosen code is one severity step above the
        most-common alternative, suggesting potential upcoding."""
        for code in trace.coding_stage.code_decisions:
            for alt in code.alternatives_considered:
                # Heuristic: if the alternative's rank was 1 and it was
                # rejected for severity reasons, flag for review.
                if alt.was_candidate_rank == 1 and "severity" in alt.reason_rejected.lower():
                    return self._make_factor(
                        "upcoding_proximity",
                        f"Code {code.code} chosen over rank-1 alt "
                        f"{alt.code}: {alt.reason_rejected[:50]}",
                    )
        return None

    def _check_compliance_soft_fails(
        self, trace: WorkflowTrace
    ) -> Optional[dict]:
        """Flag when compliance checks were skipped or had soft failures."""
        cs = trace.compliance_stage
        if cs.checks_skipped > 0 or cs.checks_failed > 0:
            return self._make_factor(
                "compliance_soft_fails",
                f"{cs.checks_failed} failed, {cs.checks_skipped} skipped "
                f"out of {cs.total_checks_run}",
            )
        return None

    # ── Inline checks (modify triggered list in-place) ────

    def _check_combination_codes(
        self,
        trace: WorkflowTrace,
        triggered: list[dict],
        current_score: float,
    ) -> None:
        """Append combination-code risk if any combo codes are present."""
        already = any(f["factor"] == "combination_code_complexity" for f in triggered)
        if already:
            return
        for code in trace.coding_stage.code_decisions:
            if code.is_combination_code:
                triggered.append(
                    self._make_factor(
                        "combination_code_complexity",
                        "Combination code used in trace.",
                    )
                )
                return

    def _check_rule_based_fallback(
        self,
        trace: WorkflowTrace,
        triggered: list[dict],
        current_score: float,
    ) -> None:
        """Append rule-based-fallback risk if LLM was not used."""
        already = any(f["factor"] == "rule_based_fallback" for f in triggered)
        if already:
            return
        for code in trace.coding_stage.code_decisions:
            if code.decision_method != "LLM_REASONED":
                triggered.append(
                    self._make_factor(
                        "rule_based_fallback",
                        "Fallback logic engaged instead of LLM reasoning.",
                    )
                )
                return

    # ── Helper ────────────────────────────────────────────

    def _make_factor(self, factor_name: str, details: str) -> dict:
        """Build a standardised risk-factor dict."""
        weight = self.RISK_FACTORS[factor_name]["weight"]
        return {
            "factor": factor_name,
            "weight": weight,
            "details": details,
            "contribution": weight,
        }

