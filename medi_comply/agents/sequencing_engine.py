"""
MEDI-COMPLY — Sequencing Engine

Determines correct primary/secondary order using Official Coding Guidelines.
"""

from __future__ import annotations

import collections
from typing import Any, Optional

from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.coding_result import SingleCodeDecision


class SequencingEngine:
    """Determines the correct order of diagnosis codes based on OCG."""

    def __init__(self, knowledge_manager: KnowledgeManager) -> None:
        self.km = knowledge_manager

    def determine_sequence(
        self,
        code_decisions: list[SingleCodeDecision],
        encounter_type: str,
        chief_complaint: Optional[str],
        scr: StructuredClinicalRepresentation
    ) -> list[SingleCodeDecision]:
        """
        Reorder codes with correct sequencing.
        Sets sequence_position and sequence_number.
        """
        if not code_decisions:
            return []

        # Separate diagnoses vs procedures
        dx_codes = [c for c in code_decisions if c.code_type == "ICD10"]
        cpt_codes = [c for c in code_decisions if c.code_type == "CPT"]

        if not dx_codes:
            for i, cpt in enumerate(cpt_codes):
                cpt.sequence_position = "PRIMARY" if i == 0 else "ADDITIONAL"
                cpt.sequence_number = i + 1
            return cpt_codes

        # Filter out manifestation codes from being primary
        eligible_for_primary = [c for c in dx_codes if self._is_primary_eligible(c.code)]
        
        if not eligible_for_primary:
            # Fallback if somehow all are manifestations (shouldn't happen with proper 'code first')
            eligible_for_primary = dx_codes

        primary_dx = None
        if encounter_type.upper() == "INPATIENT":
            primary_dx = self._determine_principal_dx_inpatient(eligible_for_primary, chief_complaint, scr)
        else:
            primary_dx = self._determine_primary_dx_outpatient(eligible_for_primary, chief_complaint, scr)

        if not primary_dx and eligible_for_primary:
             primary_dx = eligible_for_primary[0]

        # Reconstruct list with primary first
        sequenced = [primary_dx] if primary_dx else []
        for dx in dx_codes:
            if dx != primary_dx:
                sequenced.append(dx)

        # Apply specific manifestation order rules (Etiology -> Manifestation)
        sequenced = self._apply_etiology_manifestation_order(sequenced)

        # Assign ordering properties
        for i, dx in enumerate(sequenced):
            dx.sequence_position = "PRIMARY" if i == 0 else "SECONDARY"
            dx.sequence_number = i + 1

        # CPT codes follow after DX
        for i, cpt in enumerate(cpt_codes):
            cpt.sequence_position = "ADDITIONAL"
            cpt.sequence_number = i + 1

        return sequenced + cpt_codes

    def _is_primary_eligible(self, code: str) -> bool:
        # Avoid V, W, X, Y external causes and manifestation codes
        prefix = code[0].upper()
        if prefix in ("V", "W", "X", "Y"):
            return False
            
        entry = self.km.icd10_db.get_code(code)
        if entry and entry.code_first:
            return False # Must code underlying etiology first
            
        return True

    def _determine_principal_dx_inpatient(
        self, codes: list[SingleCodeDecision], chief_complaint: Optional[str], scr: StructuredClinicalRepresentation
    ) -> Optional[SingleCodeDecision]:
        """OCG Sec II: chiefly responsible for admission."""
        # Check against clinical summary / chief complaint 
        summary = scr.clinical_summary.lower() if scr.clinical_summary else ""
        reason = chief_complaint.lower() if chief_complaint else ""
        combined = summary + " " + reason
        
        for code in codes:
            # E.g. MI rules
            if code.code.startswith("I21") and "nstemi" in combined or "stemi" in combined or "infarction" in combined:
                return code
        
        # Heuristic fallback: the one most directly mentioned in summary
        for code in codes:
            search_desc = code.description.split()[0].lower() # e.g. "Acute"
            if len(code.description.split()) > 1:
                search_desc = code.description.split()[1].lower() # better keyword
                
            if search_desc in combined and len(search_desc) > 3:
                return code
                
        return codes[0] if codes else None

    def _determine_primary_dx_outpatient(
        self, codes: list[SingleCodeDecision], chief_complaint: Optional[str], scr: StructuredClinicalRepresentation
    ) -> Optional[SingleCodeDecision]:
        """OCG Sec IV: chiefly responsible for services provided."""
        return self._determine_principal_dx_inpatient(codes, chief_complaint, scr)

    def _apply_etiology_manifestation_order(self, codes: list[SingleCodeDecision]) -> list[SingleCodeDecision]:
        # Sort so any code that says "Code First X" goes AFTER X
        # For hackathon, keep it simple preserving primary rank, stable sort
        return codes
