"""
MEDI-COMPLY — Combination Code Handler

Detects opportunities to use ICD-10 combination codes (e.g., T2DM + CKD -> E11.22)
based on Official Coding Guidelines, replacing individual codes.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.coding_result import SingleCodeDecision
from medi_comply.schemas.retrieval import ConditionCodeCandidates


class AdditionalCodeRequirement(BaseModel):
    """Instruction to add another code to complete a combination rule."""
    instruction: str
    suggested_code: Optional[str] = None
    determination_source: str


class CombinationCodeSuggestion(BaseModel):
    """A suggested replacement using a combination code."""
    combination_type: str
    individual_conditions: list[str]
    suggested_combination_code: str
    code_description: str
    replaces_codes: list[str]
    additional_codes_needed: list[AdditionalCodeRequirement]
    guideline_ref: str
    confidence: float


# Simplified internal rules lookup for the agent
COMBINATION_RULES = {
    "diabetes_complications": {
        "description": "When diabetes and a complication are documented together, use E08-E13 combo.",
        "triggers": [
            (["diabetes", "nephropathy"], "E11.22"),
            (["diabetes", "chronic kidney disease"], "E11.22"),
            (["diabetes", "ckd"], "E11.22"),
            (["diabetes", "retinopathy"], "E11.319"),
            (["diabetes", "neuropathy"], "E11.40"),
            (["diabetes", "peripheral angiopathy"], "E11.51"),
            (["diabetes", "foot ulcer"], "E11.621"),
            (["diabetes", "hyperglycemia"], "E11.65"),
            (["diabetes", "hypoglycemia"], "E11.649"),
        ],
        "additional_patterns": {
            "E11.22": ["N18.- (CKD stage)"],
            "E11.319": ["H35.xx (retinopathy type)"]
        },
        "guideline": "OCG-I-C-4-a"
    },
    "hypertension_heart_ckd": {
        "description": "Hypertension + heart disease + CKD.",
        "triggers": [
            (["hypertension", "heart failure", "ckd"], "I13.0"),
            (["hypertension", "heart disease", "ckd"], "I13.10"),
            (["hypertension", "chronic kidney disease"], "I12.9"),
            (["hypertension", "heart failure"], "I11.0"),
            (["hypertension", "heart disease"], "I11.9")
        ],
        "additional_patterns": {
            "I13.0": ["I50.x", "N18.x"],
            "I13.10": ["N18.x"],
            "I12.9": ["N18.x"],
            "I11.0": ["I50.x"]
        },
        "guideline_heart": "OCG-I-C-9-a-1",
        "guideline_ckd": "OCG-I-C-9-a-2",
        "guideline_both": "OCG-I-C-9-a-3"
    }
}


class CombinationCodeHandler:
    """Evaluates conditions to suggest and apply combination codes."""

    def __init__(self, knowledge_manager: KnowledgeManager) -> None:
        self.km = knowledge_manager

    def detect_combinations(
        self,
        conditions: list[ConditionCodeCandidates],
        scr: StructuredClinicalRepresentation
    ) -> list[CombinationCodeSuggestion]:
        """Analyze all conditions and detect where combination codes should be used."""
        suggestions = []
        suggestions.extend(self._detect_diabetes_complications(conditions, scr))
        suggestions.extend(self._detect_hypertension_combinations(conditions, scr))
        return suggestions

    def apply_combination(
        self,
        coding_decisions: list[SingleCodeDecision],
        suggestions: list[CombinationCodeSuggestion]
    ) -> list[SingleCodeDecision]:
        """Apply combination code suggestions to the coding decisions."""
        if not suggestions:
            return coding_decisions

        # In a real implementation this would merge SingleCodeDecisions, swapping out
        # individuals for the combination code. For hackathon simplicity, the decision engine
        # will lean heavily on the LLM processing these suggestions directly.
        pass

    def _detect_diabetes_complications(
        self,
        conditions: list[ConditionCodeCandidates],
        scr: StructuredClinicalRepresentation
    ) -> list[CombinationCodeSuggestion]:
        suggestions = []
        
        # Look for diabetes
        dm_conditions = [c for c in conditions if "diabetes" in c.normalized_text.lower()]
        
        if not dm_conditions:
            return suggestions

        rule = COMBINATION_RULES["diabetes_complications"]
        
        for dm in dm_conditions:
            for triggers, combo_code in rule["triggers"]:
                # The first trigger is always "diabetes"
                comp_keyword = triggers[1]
                
                # Look for complication in other conditions or inside the diabetes string itself
                comp_match = [c for c in conditions if comp_keyword in c.normalized_text.lower()]
                
                if comp_match or comp_keyword in dm.normalized_text.lower():
                    # We have a combination match
                    matched_items = [dm.condition_text]
                    replaces = []
                    
                    if dm.candidates:
                        replaces.append(dm.candidates[0].code)
                        
                    for c in comp_match:
                        if c.condition_entity_id != dm.condition_entity_id:
                            matched_items.append(c.condition_text)
                            if c.candidates:
                                replaces.append(c.candidates[0].code)

                    desc = self.km.icd10_db.get_code(combo_code).description if self.km.icd10_db.get_code(combo_code) else f"Combo Code {combo_code}"

                    addtl_needed = []
                    stage_code = self._infer_ckd_stage_code(scr)
                    if combo_code in rule["additional_patterns"]:
                        for req in rule["additional_patterns"][combo_code]:
                            suggested_code = stage_code if "N18" in req and stage_code else None
                            addtl_needed.append(AdditionalCodeRequirement(
                                instruction=f"Use additional code {req}",
                                suggested_code=suggested_code,
                                determination_source="combination rule"
                            ))

                    suggestions.append(CombinationCodeSuggestion(
                        combination_type="diabetes_complications",
                        individual_conditions=matched_items,
                        suggested_combination_code=combo_code,
                        code_description=desc,
                        replaces_codes=list(set(replaces)),
                        additional_codes_needed=addtl_needed,
                        guideline_ref=rule["guideline"],
                        confidence=0.95
                    ))
                    break # Only apply the first matching diabetes complication per DM condition

        return suggestions

    def _detect_hypertension_combinations(
        self,
        conditions: list[ConditionCodeCandidates],
        scr: StructuredClinicalRepresentation
    ) -> list[CombinationCodeSuggestion]:
        # Similar simple string-matching logic for HTN + CKD + Heart
        suggestions = []
        htn = [c for c in conditions if "hypos" in c.normalized_text.lower() or "htn" in c.normalized_text.lower() or "hypertension" in c.normalized_text.lower()]
        
        if not htn:
            return suggestions
            
        has_ckd = any("ckd" in c.normalized_text.lower() or "kidney" in c.normalized_text.lower() for c in conditions)
        has_hf = any("heart failure" in c.normalized_text.lower() for c in conditions)
        
        htn_ent = htn[0]
        replaces = []
        if htn_ent.candidates:
            replaces.append(htn_ent.candidates[0].code)

        if has_ckd and has_hf:
            suggestions.append(CombinationCodeSuggestion(
                combination_type="hypertension_heart_ckd",
                individual_conditions=["hypertension", "heart failure", "chronic kidney disease"],
                suggested_combination_code="I13.0",
                code_description="Hypertensive heart and chronic kidney disease with heart failure",
                replaces_codes=replaces,
                additional_codes_needed=[],
                guideline_ref="OCG-I-C-9-a-3",
                confidence=0.95
            ))
        elif has_ckd:
            suggestions.append(CombinationCodeSuggestion(
                combination_type="hypertension_ckd",
                individual_conditions=["hypertension", "chronic kidney disease"],
                suggested_combination_code="I12.9",
                code_description="Hypertensive chronic kidney disease",
                replaces_codes=replaces,
                additional_codes_needed=[],
                guideline_ref="OCG-I-C-9-a-2",
                confidence=0.95
            ))
        elif has_hf:
            suggestions.append(CombinationCodeSuggestion(
                combination_type="hypertension_heart",
                individual_conditions=["hypertension", "heart failure"],
                suggested_combination_code="I11.0",
                code_description="Hypertensive heart disease with heart failure",
                replaces_codes=replaces,
                additional_codes_needed=[],
                guideline_ref="OCG-I-C-9-a-1",
                confidence=0.95
            ))

        return suggestions

    def _check_use_additional_requirements(self, code: str) -> list[str]:
        return []

    def _check_code_first_requirements(self, code: str) -> list[str]:
        return []

    def _infer_ckd_stage_code(self, scr: StructuredClinicalRepresentation) -> Optional[str]:
        """Best-effort extraction of CKD stage codes from the SCR text."""

        stage_map = [
            ("stage 5", "N18.5"),
            ("stage v", "N18.5"),
            ("stage 4", "N18.4"),
            ("stage iv", "N18.4"),
            ("stage 3b", "N18.32"),
            ("stage 3a", "N18.31"),
            ("stage 3", "N18.30"),
            ("stage iii", "N18.30"),
            ("stage 2", "N18.2"),
            ("stage ii", "N18.2"),
            ("stage 1", "N18.1"),
            ("stage i", "N18.1"),
            ("end stage", "N18.6"),
        ]

        texts: list[str] = []
        for condition in getattr(scr, "conditions", []):
            text = ""
            if isinstance(condition, dict):
                text = condition.get("text") or condition.get("normalized_text") or ""
            else:
                text = getattr(condition, "text", "") or getattr(condition, "normalized_text", "")
            if text:
                texts.append(text.lower())

        haystack = " ".join(texts)
        for phrase, code in stage_map:
            if phrase in haystack:
                return code
        return None
