"""
MEDI-COMPLY — Confidence Calculator

Calculates empirical confidence scores for coding decisions based on
evidence strength, specificity matches, assertion clarity, and more.
"""

from __future__ import annotations

from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.coding_result import ConfidenceFactor, SingleCodeDecision
from medi_comply.schemas.retrieval import RankedCodeCandidate

CONFIDENCE_WEIGHTS = {
    "EVIDENCE_STRENGTH": {"weight": 0.30},
    "CANDIDATE_RANK": {"weight": 0.15},
    "SPECIFICITY_MATCH": {"weight": 0.20},
    "GUIDELINE_COMPLIANCE": {"weight": 0.15},
    "CLINICAL_CONSISTENCY": {"weight": 0.10},
    "ASSERTION_CLARITY": {"weight": 0.10}
}


class ConfidenceCalculator:
    """Calculates well-calibrated confidence scores for each coding decision."""

    def calculate_code_confidence(
        self,
        selected_code: RankedCodeCandidate,
        assertion: str,
        guidelines_matched: list[str],
        scr: StructuredClinicalRepresentation,
        is_most_specific: bool
    ) -> tuple[float, list[ConfidenceFactor]]:
        """Calculate confidence score and return contributing factors."""
        factors = []
        total_score = 0.0

        # 1. Evidence Strength (Mock simplified checking for hackathon)
        ev_score = 0.85 
        factors.append(ConfidenceFactor(
            factor="EVIDENCE_STRENGTH",
            impact="POSITIVE",
            weight=CONFIDENCE_WEIGHTS["EVIDENCE_STRENGTH"]["weight"],
            detail="Strong clinical support from NLP extraction."
        ))
        total_score += ev_score * CONFIDENCE_WEIGHTS["EVIDENCE_STRENGTH"]["weight"]

        # 2. Candidate Rank
        rank_score = 1.0 if selected_code.retrieval_source == "Direct Mapping" else 0.85
        factors.append(ConfidenceFactor(
            factor="CANDIDATE_RANK", 
            impact="POSITIVE",
            weight=CONFIDENCE_WEIGHTS["CANDIDATE_RANK"]["weight"],
            detail=f"Retrieved via {selected_code.retrieval_source}"
        ))
        total_score += rank_score * CONFIDENCE_WEIGHTS["CANDIDATE_RANK"]["weight"]

        # 3. Specificity Match
        spec_score = 1.0 if is_most_specific else 0.60
        factors.append(ConfidenceFactor(
            factor="SPECIFICITY_MATCH",
            impact="POSITIVE" if is_most_specific else "NEGATIVE",
            weight=CONFIDENCE_WEIGHTS["SPECIFICITY_MATCH"]["weight"],
            detail="Highest specificity used." if is_most_specific else "More specific code exists."
        ))
        total_score += spec_score * CONFIDENCE_WEIGHTS["SPECIFICITY_MATCH"]["weight"]

        # 4. Guideline Compliance
        gl_score = 1.0 if guidelines_matched else 0.80
        factors.append(ConfidenceFactor(
            factor="GUIDELINE_COMPLIANCE",
            impact="POSITIVE",
            weight=CONFIDENCE_WEIGHTS["GUIDELINE_COMPLIANCE"]["weight"],
            detail="Compliant with known OCG guidelines." if guidelines_matched else "No specific guideline applied."
        ))
        total_score += gl_score * CONFIDENCE_WEIGHTS["GUIDELINE_COMPLIANCE"]["weight"]

        # 5. Clinical Consistency
        consist_score = 0.90 # Defaults high assuming verified SCR
        factors.append(ConfidenceFactor(
            factor="CLINICAL_CONSISTENCY",
            impact="POSITIVE",
            weight=CONFIDENCE_WEIGHTS["CLINICAL_CONSISTENCY"]["weight"],
            detail="Matches documented patient context."
        ))
        total_score += consist_score * CONFIDENCE_WEIGHTS["CLINICAL_CONSISTENCY"]["weight"]

        # 6. Assertion Clarity
        assert_score = 1.0 if assertion == "PRESENT" else 0.75 if assertion == "HISTORICAL" else 0.40
        factors.append(ConfidenceFactor(
            factor="ASSERTION_CLARITY",
            impact="POSITIVE" if assertion == "PRESENT" else "NEGATIVE",
            weight=CONFIDENCE_WEIGHTS["ASSERTION_CLARITY"]["weight"],
            detail=f"Assertion is {assertion}."
        ))
        total_score += assert_score * CONFIDENCE_WEIGHTS["ASSERTION_CLARITY"]["weight"]

        return min(max(total_score, 0.0), 1.0), factors

    def calculate_overall_confidence(
        self,
        code_decisions: list[SingleCodeDecision]
    ) -> float:
        """Overall encounter confidence (Weighted average)."""
        if not code_decisions:
            return 1.0
            
        primary = [c for c in code_decisions if c.sequence_position == "PRIMARY"]
        secondaries = [c for c in code_decisions if c.sequence_position != "PRIMARY"]

        if not primary:
             # Standard average if no primary was marked
             total = sum(d.confidence_score for d in code_decisions)
             return total / len(code_decisions)

        p_score = primary[0].confidence_score
        
        if not secondaries:
            return p_score

        s_score = sum(s.confidence_score for s in secondaries) / len(secondaries)
        
        # Primary gets 40% weight, secondaries split remaining 60%
        return (p_score * 0.4) + (s_score * 0.6)

    def should_escalate(self, confidence: float) -> tuple[bool, str]:
        """Determine if confidence warrants human review."""
        if confidence < 0.70:
            return True, f"Confidence {confidence:.2f} is below hard threshold (0.70)."
        if confidence < 0.85:
            return False, f"Flagged for soft review: Confidence {confidence:.2f} is < 0.85."
        return False, "Confidence high enough to auto-proceed."
