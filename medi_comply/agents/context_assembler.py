"""
MEDI-COMPLY — Context Assembler.

Takes retrieval results and enriches them with all relevant rules,
guidelines, Excludes1/2 warnings, NCCI bundles, and cross-entity relationships.
Packages everything for the final Coding Agent.
"""

from __future__ import annotations

from typing import Any

from medi_comply.schemas.retrieval import (
    CodeRetrievalContext, ConditionCodeCandidates, ProcedureCodeCandidates,
    ExcludesWarning, NCCIEditWarning, MedNecessityInfo, ModifierSuggestion,
    GuidelineReference
)
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.knowledge.knowledge_manager import KnowledgeManager


class ContextAssembler:
    """Enriches candidate codes with coding rules and guidelines."""

    async def assemble(
        self,
        scr: StructuredClinicalRepresentation,
        condition_candidates: list[ConditionCodeCandidates],
        procedure_candidates: list[ProcedureCodeCandidates],
        knowledge_manager: KnowledgeManager
    ) -> CodeRetrievalContext:
        """Assemble complete retrieval context for the coding agent."""
        
        # 1. Enrich individual condition candidates
        for cc in condition_candidates:
            self._enrich_condition_candidates(cc, knowledge_manager, scr.patient_context)
            
        # 2. Extract all condition codes for medical necessity checks
        all_dx_codes = []
        for cc in condition_candidates:
            all_dx_codes.extend([c.code for c in cc.candidates])
            
        # 3. Enrich individual procedure candidates
        for pc in procedure_candidates:
            self._enrich_procedure_candidates(pc, knowledge_manager, all_dx_codes, scr)
            
        # 4. Build cross-entity matrices
        excludes_matrix = self._build_excludes_matrix(condition_candidates, knowledge_manager)
        ncci_matrix = self._build_ncci_matrix(procedure_candidates, knowledge_manager)
        
        # 5. Get global guidelines
        encounter_type = scr.patient_context.get("encounter_type", "OUTPATIENT")
        encounter_guidelines = self._get_encounter_type_guidelines(encounter_type, knowledge_manager)
        cross_guidelines = self._get_cross_entity_guidelines(condition_candidates, knowledge_manager)
        
        # 6. Package
        context = CodeRetrievalContext(
            scr_id=scr.scr_id,
            patient_context=scr.patient_context,
            condition_candidates=condition_candidates,
            procedure_candidates=procedure_candidates,
            cross_entity_guidelines=cross_guidelines,
            overall_excludes_matrix=excludes_matrix,
            overall_ncci_matrix=ncci_matrix,
            encounter_type_guidelines=encounter_guidelines,
            retrieval_summary={
                "total_conditions": len(condition_candidates),
                "total_procedures": len(procedure_candidates),
                "excludes_warnings": len(excludes_matrix),
                "ncci_warnings": len(ncci_matrix)
            }
        )
        
        # 7. Apply token limits
        return self._limit_context_window(context)

    def _enrich_condition_candidates(
        self,
        candidates_group: ConditionCodeCandidates,
        km: KnowledgeManager,
        patient_context: dict[str, Any]
    ) -> None:
        """Enrich condition codes with Excludes, instructions, and guidelines."""
        valid_cands = []
        
        for cand in candidates_group.candidates:
            if not km.icd10_db.code_exists(cand.code):
                continue
                
            code_data = km.icd10_db._codes[cand.code]
            
            # Age/Gender validation
            patient_age = patient_context.get("age", 0)
            patient_gender = patient_context.get("gender", "unspecified").lower()
            
            # If male and code is O-chapter (Pregnancy), invalid
            if patient_gender == "male" and cand.code.startswith("O"):
                cand.valid_for_gender = False
            # Prevent male codes on female patients, etc. (Simplistic for hackathon)
            if patient_gender == "female" and cand.code.startswith("N4"):
                cand.valid_for_gender = False
                
            # Pull instructions
            if hasattr(code_data, "excludes1"):
                cand.excludes1 = code_data.excludes1
            if hasattr(code_data, "excludes2"):
                cand.excludes2 = list(code_data.excludes2) if hasattr(code_data.excludes2, "__iter__") else []
            if hasattr(code_data, "use_additional_code"):
                cand.requires_additional_codes = getattr(code_data, "use_additional_code", [])
            if hasattr(code_data, "code_first"):
                cand.code_first_references = getattr(code_data, "code_first", [])
                
            # Formatting strings for the group
            candidates_group.use_additional_instructions.extend(cand.requires_additional_codes)
            candidates_group.code_first_instructions.extend(cand.code_first_references)
            
            valid_cands.append(cand)
            
        candidates_group.candidates = valid_cands
        
        # Deduplicate group-level instructions
        candidates_group.use_additional_instructions = list(set(candidates_group.use_additional_instructions))
        candidates_group.code_first_instructions = list(set(candidates_group.code_first_instructions))

    def _enrich_procedure_candidates(
        self,
        candidates_group: ProcedureCodeCandidates,
        km: KnowledgeManager,
        all_condition_codes: list[str],
        scr: StructuredClinicalRepresentation
    ) -> None:
        """Enrich procedure codes with medical necessity and modifiers."""
        valid_cands = []
        
        for cand in candidates_group.candidates:
            if not km.cpt_db.code_exists(cand.code):
                continue
                
            # Check medical necessity
            if km.med_necessity:
                med_nec = km.med_necessity.check_medical_necessity(
                    cand.code, all_condition_codes
                )
                if med_nec:
                    candidates_group.medical_necessity.append(MedNecessityInfo(
                        procedure_code=cand.code,
                        is_covered=med_nec.is_medically_necessary,
                        lcd_id=med_nec.lcd_ref,
                        lcd_title="",
                        covered_by_diagnoses=med_nec.covered_dx_matches,
                        uncovered_diagnoses=med_nec.uncovered_dx,
                        documentation_requirements=med_nec.documentation_requirements
                    ))
                    
            valid_cands.append(cand)
            
        candidates_group.candidates = valid_cands
        candidates_group.modifier_suggestions = self._suggest_modifiers(candidates_group, scr)

    def _build_excludes_matrix(
        self,
        all_condition_candidates: list[ConditionCodeCandidates],
        km: KnowledgeManager
    ) -> list[ExcludesWarning]:
        """Check all candidate pairs across entities for Excludes conflicts."""
        warnings: list[ExcludesWarning] = []
        
        # Flatten grouped candidates into a single tracking structure
        # (group_idx, candidate_code)
        tracked_codes = []
        for i, group in enumerate(all_condition_candidates):
            for cand in group.candidates:
                tracked_codes.append((i, cand.code))
                
        # Compare all pairs (only between DIFFERENT condition groups)
        for i in range(len(tracked_codes)):
            for j in range(i + 1, len(tracked_codes)):
                grp1, code1 = tracked_codes[i]
                grp2, code2 = tracked_codes[j]
                
                if grp1 == grp2:
                    continue  # Only cross-entity
                    
                # Check Excludes
                conflict = km.check_excludes(code1, code2)
                if conflict.has_conflict:
                    warnings.append(ExcludesWarning(
                        code1=code1,
                        code2=code2,
                        excludes_type="EXCLUDES1",
                        description=getattr(conflict, "description", ""),
                        resolution=getattr(conflict, "resolution", "")
                    ))
                    
        # Remove duplicates
        unique_warnings = []
        seen = set()
        for w in warnings:
            pair = tuple(sorted([w.code1, w.code2]))
            if pair not in seen:
                seen.add(pair)
                unique_warnings.append(w)
                
        return unique_warnings

    def _build_ncci_matrix(
        self,
        all_procedure_candidates: list[ProcedureCodeCandidates],
        km: KnowledgeManager
    ) -> list[NCCIEditWarning]:
        """Check all candidate pairs across entities for NCCI bundling conflicts."""
        warnings: list[NCCIEditWarning] = []
        
        tracked_codes = []
        for i, group in enumerate(all_procedure_candidates):
            for cand in group.candidates:
                tracked_codes.append((i, cand.code))
                
        for i in range(len(tracked_codes)):
            for j in range(i + 1, len(tracked_codes)):
                grp1, code1 = tracked_codes[i]
                grp2, code2 = tracked_codes[j]
                
                if grp1 == grp2:
                    continue
                    
                edit = km.ncci_engine.check_pair(code1, code2)
                if edit.is_bundled:
                    warnings.append(NCCIEditWarning(
                        code1=edit.column1_code,
                        code2=edit.column2_code,
                        edit_type="BUNDLED",
                        modifier_allowed=edit.modifier_allowed,
                        rationale=edit.rationale,
                        recommendation=f"Use modifier {edit.modifier_allowed} if appropriate" if edit.modifier_allowed else "Cannot bill together"
                    ))
                    
        unique_warnings = []
        seen = set()
        for w in warnings:
            pair = tuple(sorted([w.code1, w.code2]))
            if pair not in seen:
                seen.add(pair)
                unique_warnings.append(w)
                
        return unique_warnings

    def _get_encounter_type_guidelines(
        self,
        encounter_type: str,
        km: KnowledgeManager
    ) -> list[GuidelineReference]:
        """Pull generic guidelines based on encounter type."""
        refs = []
        type_upper = encounter_type.upper()
        
        # Basic OCG pull matching encounter setting
        for gl in km.guidelines._guidelines.values():
            applies = False
            if type_upper == "INPATIENT" and "inpatient" in gl.rule_text.lower():
                applies = True
            elif type_upper in ["OUTPATIENT", "EMERGENCY"] and "outpatient" in gl.rule_text.lower():
                applies = True
                
            if applies:
                refs.append(GuidelineReference(
                    guideline_id=gl.guideline_id,
                    title=gl.title,
                    section=gl.section,
                    relevance_score=0.9,
                    key_rule="Applies to " + type_upper + " encounters.",
                    full_text=gl.rule_text[:200] + "..."
                ))
        return refs[:3]  # Keep context small

    def _get_cross_entity_guidelines(
        self,
        conditions: list[ConditionCodeCandidates],
        km: KnowledgeManager
    ) -> list[GuidelineReference]:
        """Pull guidelines bridging multiple identified conditions."""
        refs: list[GuidelineReference] = []
        
        # Simple heuristic: if we have both Diabetes and CKD
        texts = [c.normalized_text.lower() for c in conditions]
        has_dm = any("diabetes" in t for t in texts)
        has_ckd = any("kidney" in t or "ckd" in t for t in texts)
        
        if has_dm and has_ckd:
            refs.append(GuidelineReference(
                guideline_id="OCG-I-C-4-a-2",
                title="Diabetes with Kidney Complications",
                section="Endocrine",
                relevance_score=1.0,
                key_rule="Assume relationship between diabetes and CKD unless specified otherwise. Code E11.22 followed by N18.-",
                full_text="If patient has both diabetes mellitus and chronic kidney disease, assume casual relationship..."
            ))
            
        return refs

    def _suggest_modifiers(
        self,
        procedure: ProcedureCodeCandidates,
        scr: StructuredClinicalRepresentation
    ) -> list[ModifierSuggestion]:
        """Suggest modifiers based on the procedure and full SCR context."""
        suggestions: list[ModifierSuggestion] = []
        
        # Check laterality
        norm_proc = procedure.normalized_text.lower()
        if "left" in norm_proc or " lt " in procedure.procedure_text.lower():
            suggestions.append(ModifierSuggestion(
                modifier="LT",
                description="Left Side",
                reason="Laterality specified as left in source text.",
                confidence=0.95
            ))
        elif "right" in norm_proc or " rt " in procedure.procedure_text.lower():
            suggestions.append(ModifierSuggestion(
                modifier="RT",
                description="Right Side",
                reason="Laterality specified as right in source text.",
                confidence=0.95
            ))
        elif "bilateral" in norm_proc:
            suggestions.append(ModifierSuggestion(
                modifier="50",
                description="Bilateral Procedure",
                reason="Procedure noted as bilateral.",
                confidence=0.95
            ))
            
        return suggestions

    def _limit_context_window(
        self,
        context: CodeRetrievalContext,
        max_candidates_per_entity: int = 10,
        max_guidelines: int = 20
    ) -> CodeRetrievalContext:
        """Truncate excessive candidates to prevent token overflow."""
        for c in context.condition_candidates:
            c.candidates = c.candidates[:max_candidates_per_entity]
            c.relevant_guidelines = c.relevant_guidelines[:max_guidelines]
            
        for p in context.procedure_candidates:
            p.candidates = p.candidates[:max_candidates_per_entity]
            
        context.cross_entity_guidelines = context.cross_entity_guidelines[:max_guidelines]
        context.encounter_type_guidelines = context.encounter_type_guidelines[:max_guidelines]
        return context
