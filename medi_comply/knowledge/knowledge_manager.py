"""
MEDI-COMPLY — Unified knowledge manager.

Single entry point that all agents use to access ICD-10 codes, CPT codes,
NCCI edits, medical necessity rules, coding guidelines, and vector search.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from medi_comply.knowledge.icd10_db import ICD10CodeEntry, ICD10Database, ValidationResult
from medi_comply.knowledge.cpt_db import CPTCodeEntry, CPTDatabase
from medi_comply.knowledge.ncci_engine import MUECheckResult, NCCICheckResult, NCCIEngine
from medi_comply.knowledge.medical_necessity import MedicalNecessityEngine, MedNecessityResult
from medi_comply.knowledge.coding_guidelines import CodingGuideline, CodingGuidelinesStore
from medi_comply.knowledge.vector_store import CodeSearchResult, GuidelineSearchResult, MedicalVectorStore


# ---------------------------------------------------------------------------
# Excludes check result
# ---------------------------------------------------------------------------


class ExcludesCheckResult:
    """Result of checking Excludes1 and Excludes2 between two codes."""

    def __init__(
        self,
        has_conflict: bool,
        excludes1: bool = False,
        excludes2: bool = False,
        message: str = "",
    ) -> None:
        self.has_conflict = has_conflict
        self.excludes1 = excludes1
        self.excludes2 = excludes2
        self.message = message


# ---------------------------------------------------------------------------
# Knowledge Manager
# ---------------------------------------------------------------------------


class KnowledgeManager:
    """Unified interface to all medical knowledge sources.

    Agents should **never** access the individual databases directly —
    this manager provides a single, consistent API for all lookups,
    validations, and searches.
    """

    VERSION = "1.0.0"

    def __init__(self) -> None:
        self.icd10_db: ICD10Database = ICD10Database()
        self.cpt_db: CPTDatabase = CPTDatabase()
        self.ncci_engine: NCCIEngine = NCCIEngine()
        self.med_necessity: MedicalNecessityEngine = MedicalNecessityEngine()
        self.guidelines: CodingGuidelinesStore = CodingGuidelinesStore()
        self.vector_store: MedicalVectorStore = MedicalVectorStore()
        self._initialized: bool = False
        self._last_updated: Optional[datetime] = None

    # -- Properties --------------------------------------------------------

    @property
    def is_initialized(self) -> bool:
        """Whether the knowledge base has been loaded."""
        return self._initialized

    @property
    def last_updated(self) -> Optional[datetime]:
        """Timestamp of the last data load."""
        return self._last_updated

    @property
    def code_count(self) -> dict[str, int]:
        """Summary counts of loaded data."""
        return {
            "icd10": self.icd10_db.code_count,
            "cpt": self.cpt_db.code_count,
            "ncci_pairs": self.ncci_engine.edit_pair_count,
            "mue": self.ncci_engine.mue_count,
            "lcd": self.med_necessity.lcd_count,
            "guidelines": self.guidelines.guideline_count,
        }

    # -- Initialization ----------------------------------------------------

    def initialize(self) -> None:
        """Load all databases from seed data and build vector indexes.

        Imports :func:`seed_all_data` from :mod:`seed_data` and populates
        every knowledge store.
        """
        from medi_comply.knowledge.seed_data import seed_all_data

        seed_all_data(self)
        self.vector_store.initialize()
        self._build_vector_index()
        self._initialized = True
        self._last_updated = datetime.now(timezone.utc)

    def _build_vector_index(self) -> None:
        """Populate ChromaDB collections from the loaded databases."""
        if not self.vector_store.is_initialized:
            return

        # ICD-10 codes
        icd10_docs = []
        for code_str in list(self.icd10_db._codes.keys()):
            entry = self.icd10_db.get_code(code_str)
            if entry:
                icd10_docs.append(entry.to_dict())
        self.vector_store.add_codes(icd10_docs, "icd10_codes")

        # CPT codes
        cpt_docs = []
        for code_str in list(self.cpt_db._codes.keys()):
            entry = self.cpt_db.get_code(code_str)
            if entry:
                cpt_docs.append(entry.to_dict())
        self.vector_store.add_codes(cpt_docs, "cpt_codes")

        # Guidelines
        guideline_docs = []
        for gid in list(self.guidelines._guidelines.keys()):
            g = self.guidelines.get_guideline(gid)
            if g:
                guideline_docs.append(g.to_dict())
        self.vector_store.add_guidelines(guideline_docs)

    # -- ICD-10 Lookups ----------------------------------------------------

    def lookup_icd10(self, code: str) -> Optional[ICD10CodeEntry]:
        """Look up an ICD-10 code.

        Parameters
        ----------
        code:
            ICD-10-CM code string.

        Returns
        -------
        Optional[ICD10CodeEntry]
        """
        return self.icd10_db.get_code(code)

    def validate_code_exists(self, code: str, code_type: str = "icd10") -> bool:
        """O(1) check whether a code exists — anti-hallucination gate.

        Parameters
        ----------
        code:
            Code string.
        code_type:
            ``"icd10"`` or ``"cpt"``.

        Returns
        -------
        bool
        """
        if code_type == "icd10":
            return self.icd10_db.code_exists(code)
        elif code_type == "cpt":
            return self.cpt_db.code_exists(code)
        return False

    def validate_icd10_assignment(
        self,
        code: str,
        patient_age: int = 30,
        patient_gender: str = "BOTH",
    ) -> ValidationResult:
        """Full validation of an ICD-10 code assignment.

        Parameters
        ----------
        code:
            ICD-10-CM code.
        patient_age:
            Patient age in years.
        patient_gender:
            ``"MALE"``, ``"FEMALE"``, or ``"BOTH"``.

        Returns
        -------
        ValidationResult
        """
        return self.icd10_db.validate_code(code, patient_age, patient_gender)

    def get_use_additional(self, code: str) -> list[str]:
        """Return Use-additional-code instructions for an ICD-10 code."""
        return self.icd10_db.get_use_additional_instructions(code)

    def get_code_first(self, code: str) -> list[str]:
        """Return Code-first instructions for an ICD-10 code."""
        return self.icd10_db.get_code_first_instructions(code)

    # -- CPT Lookups -------------------------------------------------------

    def lookup_cpt(self, code: str) -> Optional[CPTCodeEntry]:
        """Look up a CPT code.

        Parameters
        ----------
        code:
            CPT / HCPCS code string.

        Returns
        -------
        Optional[CPTCodeEntry]
        """
        return self.cpt_db.get_code(code)

    # -- Excludes ----------------------------------------------------------

    def check_excludes(self, code1: str, code2: str) -> ExcludesCheckResult:
        """Check Excludes1 and Excludes2 between two ICD-10 codes.

        Parameters
        ----------
        code1:
            First ICD-10-CM code.
        code2:
            Second ICD-10-CM code.

        Returns
        -------
        ExcludesCheckResult
        """
        excl1, msg1 = self.icd10_db.check_excludes1(code1, code2)
        excl2, msg2 = self.icd10_db.check_excludes2(code1, code2)
        message = msg1 or msg2
        return ExcludesCheckResult(
            has_conflict=excl1,
            excludes1=excl1,
            excludes2=excl2,
            message=message,
        )

    # -- NCCI Edits --------------------------------------------------------

    def check_ncci_edits(self, cpt_codes: list[str]) -> list[NCCICheckResult]:
        """Check all pairwise NCCI edits among a list of CPT codes.

        Parameters
        ----------
        cpt_codes:
            List of CPT codes.

        Returns
        -------
        list[NCCICheckResult]
            Only pairs with active edits.
        """
        return self.ncci_engine.check_all_pairs(cpt_codes)

    def check_mue(self, cpt_code: str, units: int) -> MUECheckResult:
        """Check MUE limits for a CPT code.

        Parameters
        ----------
        cpt_code:
            CPT code.
        units:
            Number of units submitted.

        Returns
        -------
        MUECheckResult
        """
        return self.ncci_engine.check_mue(cpt_code, units)

    # -- Medical Necessity -------------------------------------------------

    def check_medical_necessity(
        self,
        cpt_code: str,
        dx_codes: list[str],
    ) -> MedNecessityResult:
        """Check whether a procedure is medically necessary.

        Parameters
        ----------
        cpt_code:
            CPT / HCPCS code.
        dx_codes:
            Supporting ICD-10-CM diagnosis codes.

        Returns
        -------
        MedNecessityResult
        """
        return self.med_necessity.check_medical_necessity(cpt_code, dx_codes)

    # -- Coding Guidelines -------------------------------------------------

    def get_relevant_guidelines(
        self,
        codes: Optional[list[str]] = None,
        keywords: Optional[list[str]] = None,
    ) -> list[CodingGuideline]:
        """Find coding guidelines relevant to given codes or keywords.

        Parameters
        ----------
        codes:
            ICD-10 codes to look up guidelines for.
        keywords:
            Search keywords.

        Returns
        -------
        list[CodingGuideline]
        """
        results: list[CodingGuideline] = []
        seen: set[str] = set()

        if codes:
            for code in codes:
                for g in self.guidelines.get_guidelines_for_code(code):
                    if g.guideline_id not in seen:
                        results.append(g)
                        seen.add(g.guideline_id)

        if keywords:
            for g in self.guidelines.search_guidelines(keywords):
                if g.guideline_id not in seen:
                    results.append(g)
                    seen.add(g.guideline_id)

        return results

    # -- Vector Search -----------------------------------------------------

    def search_codes(
        self,
        clinical_text: str,
        code_type: str = "icd10",
        top_k: int = 10,
    ) -> list[CodeSearchResult]:
        """Semantic search for codes matching clinical text.

        Parameters
        ----------
        clinical_text:
            Natural-language description.
        code_type:
            ``"icd10"`` or ``"cpt"``.
        top_k:
            Maximum results.

        Returns
        -------
        list[CodeSearchResult]
        """
        if code_type == "icd10":
            return self.vector_store.search_icd10(clinical_text, top_k)
        elif code_type == "cpt":
            return self.vector_store.search_cpt(clinical_text, top_k)
        return []

    # -- Version -----------------------------------------------------------

    def get_knowledge_version(self) -> str:
        """Return the knowledge base version string."""
        return self.VERSION

    def __repr__(self) -> str:
        return (
            f"KnowledgeManager(initialized={self._initialized}, "
            f"counts={self.code_count})"
        )
