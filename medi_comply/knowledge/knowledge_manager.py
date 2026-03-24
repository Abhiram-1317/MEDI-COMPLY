"""
MEDI-COMPLY — Unified knowledge manager.

Single entry point that all agents use to access ICD-10 codes, CPT codes,
NCCI edits, medical necessity rules, coding guidelines, and vector search.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from medi_comply.knowledge.icd10_db import ICD10CodeEntry, ICD10Database, ValidationResult
from medi_comply.knowledge.cpt_db import CPTCodeEntry, CPTDatabase
from medi_comply.knowledge.ncci_engine import MUECheckResult, NCCICheckResult, NCCIEngine
from medi_comply.knowledge.medical_necessity import MedicalNecessityEngine, MedNecessityResult
from medi_comply.knowledge.lcd_ncd_engine import (
    LCDNCDEngine,
    LCDNCDDatabase,
    MedicalNecessityResult as LCDMedicalNecessityResult,
    seed_lcd_ncd_data,
)
from medi_comply.knowledge.coding_guidelines import (
    CodingGuideline,
    CodingGuidelinesDatabase,
    CodingGuidelinesEngine,
    GuidelineComplianceResult,
    GuidelineLookupResult,
    CodingGuidelinesStore,
    seed_coding_guidelines,
)
from medi_comply.knowledge.vector_store import CodeSearchResult, GuidelineSearchResult, MedicalVectorStore
from medi_comply.knowledge.knowledge_updater import KnowledgeUpdater, KnowledgeVersion, UpdateSource
from medi_comply.knowledge.payer_policy_engine import (
    AuthRequirement,
    AuthRequirementRule,
    CoveredServiceRule,
    MemberCostSharing,
    PayerClaimCheckResult,
    PayerPolicyDatabase,
    PayerPolicyEngine,
    ServiceCategory,
    seed_payer_policies,
)


logger = logging.getLogger(__name__)


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


class _LegacyGuidelineAdapter:
    """Compatibility shim exposing legacy guideline store API."""

    def __init__(self, db: CodingGuidelinesDatabase, engine: CodingGuidelinesEngine) -> None:
        self._db = db
        self._engine = engine
        self._guidelines = {g.guideline_id: g for g in db.get_all_guidelines()}

    def get_guideline(self, guideline_id: str) -> Optional[CodingGuideline]:
        return self._db.get_guideline(guideline_id)

    def search_guidelines(self, keywords: list[str]) -> list[CodingGuideline]:
        return self._db.search(" ".join(keywords))

    def get_guidelines_for_code(self, icd10_code: str) -> list[CodingGuideline]:
        return self._db.find_by_icd10_code(icd10_code)

    @property
    def guideline_count(self) -> int:
        return self._db.get_guideline_count()

    def generate_citation(self, guideline_id: str) -> str:
        return self._engine.format_guideline_citation(guideline_id)


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
        self.lcd_ncd_db: LCDNCDDatabase = LCDNCDDatabase()
        seed_lcd_ncd_data(self.lcd_ncd_db)
        self.lcd_ncd_engine: LCDNCDEngine = LCDNCDEngine(database=self.lcd_ncd_db)
        self.payer_policy_db: PayerPolicyDatabase = PayerPolicyDatabase()
        seed_payer_policies(self.payer_policy_db)
        self.payer_policy_engine: PayerPolicyEngine = PayerPolicyEngine(database=self.payer_policy_db)
        self.coding_guidelines_db: CodingGuidelinesDatabase = CodingGuidelinesDatabase()
        seed_coding_guidelines(self.coding_guidelines_db)
        self.coding_guidelines_engine: CodingGuidelinesEngine = CodingGuidelinesEngine(database=self.coding_guidelines_db)
        self.guidelines: _LegacyGuidelineAdapter = _LegacyGuidelineAdapter(self.coding_guidelines_db, self.coding_guidelines_engine)
        self.vector_store: MedicalVectorStore = MedicalVectorStore()
        # Knowledge updater manages versioned KB updates with staging and rollback
        self.knowledge_updater: KnowledgeUpdater = KnowledgeUpdater(self)
        self.current_version: KnowledgeVersion = self.knowledge_updater.current_version
        self.current_version_id: str = self.current_version.version_id
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
            "lcd_ncd": self.lcd_ncd_db.get_determination_count(),
            "guidelines": self.coding_guidelines_db.get_guideline_count(),
        }

    # -- Initialization ----------------------------------------------------

    def initialize(self) -> None:
        """Load all databases from seed data and build vector indexes.

        Imports :func:`seed_all_data` from :mod:`seed_data` and populates
        every knowledge store.
        """
        from medi_comply.knowledge.seed_data import seed_all_data

        seed_all_data(self)
        try:
            self.vector_store.initialize()
        except Exception as exc:  # pragma: no cover - defensive safeguard
            logger.warning("Vector store initialization raised an exception: %s", exc)

        if self.vector_store.is_initialized:
            self._build_vector_index()
        else:
            logger.warning("Vector store unavailable — continuing with keyword-only search")

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
        for g in self.coding_guidelines_db.get_all_guidelines():
            guideline_docs.append(g.model_dump())
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
    ) -> LCDMedicalNecessityResult:
        """Check whether a procedure is medically necessary (LCD/NCD).

        Parameters
        ----------
        cpt_code:
            CPT / HCPCS code.
        dx_codes:
            Supporting ICD-10-CM diagnosis codes.

        Returns
        -------
        LCDMedicalNecessityResult
        """
        return self.lcd_ncd_engine.check_medical_necessity(
            cpt_code=cpt_code,
            icd10_codes=dx_codes,
        )

    def check_lcd_ncd_medical_necessity(
        self,
        cpt_code: str,
        icd10_codes: list[str],
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        state: Optional[str] = None,
        clinical_info: Optional[dict] = None,
    ) -> LCDMedicalNecessityResult:
        """LCD/NCD-based medical necessity evaluation.

        Delegates to :class:`LCDNCDEngine` for coverage determinations. This is
        the preferred path for agents that need detailed coverage reasoning,
        recommendations, and policy-aware checks (age, gender, frequency).
        """

        return self.lcd_ncd_engine.check_medical_necessity(
            cpt_code=cpt_code,
            icd10_codes=icd10_codes,
            patient_age=patient_age,
            patient_gender=patient_gender,
            state=state,
            clinical_info=clinical_info,
        )

    def get_covered_diagnoses(self, cpt_code: str, state: Optional[str] = None) -> list[str]:
        """List ICD-10 codes that establish necessity for a CPT (LCD/NCD)."""

        return self.lcd_ncd_engine.get_covered_diagnoses(cpt_code, state)

    def get_documentation_requirements(self, cpt_code: str) -> list[str]:
        """Documentation required by LCD/NCD for a CPT."""

        return self.lcd_ncd_engine.get_required_documentation(cpt_code)

    def is_procedure_covered(self, cpt_code: str, icd10_code: str, state: Optional[str] = None) -> bool:
        """Boolean coverage check for a CPT/ICD pairing (LCD/NCD)."""

        return self.lcd_ncd_engine.is_procedure_covered(cpt_code, icd10_code, state)

    # -- Payer Policy Engine --------------------------------------------

    def check_auth_requirement(
        self,
        payer_id: str,
        cpt_code: str,
        service_category: Optional[ServiceCategory] = None,
        is_emergency: bool = False,
    ) -> AuthRequirementRule:
        """Payer-specific prior authorization requirement lookup."""

        return self.payer_policy_engine.check_auth_requirement(
            payer_id=payer_id,
            cpt_code=cpt_code,
            service_category=service_category,
            is_emergency=is_emergency,
        )

    def check_payer_coverage(
        self,
        payer_id: str,
        cpt_code: str,
        icd10_codes: list[str],
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        place_of_service: Optional[str] = None,
    ) -> CoveredServiceRule:
        """Check payer coverage constraints for a CPT/ICD combination."""

        return self.payer_policy_engine.check_coverage(
            payer_id=payer_id,
            cpt_code=cpt_code,
            icd10_codes=icd10_codes,
            patient_age=patient_age,
            patient_gender=patient_gender,
            place_of_service=place_of_service,
        )

    def get_allowed_amount(
        self,
        payer_id: str,
        cpt_code: str,
        modifier: Optional[str] = None,
        is_facility: bool = False,
    ) -> Optional[float]:
        """Return payer allowed amount for a CPT code with modifier context."""

        return self.payer_policy_engine.get_allowed_amount(
            payer_id=payer_id,
            cpt_code=cpt_code,
            modifier=modifier,
            is_facility=is_facility,
        )

    def calculate_member_cost(
        self,
        payer_id: str,
        cpt_code: str,
        is_in_network: bool,
        deductible_met: bool = False,
    ) -> MemberCostSharing:
        """Estimate member cost sharing for a CPT code under a payer plan."""

        return self.payer_policy_engine.calculate_member_responsibility(
            payer_id=payer_id,
            cpt_code=cpt_code,
            is_in_network=is_in_network,
            deductible_met=deductible_met,
        )

    def check_timely_filing(self, payer_id: str, date_of_service: str, submission_date: str) -> bool:
        """Verify timely filing compliance for the payer."""

        return self.payer_policy_engine.check_timely_filing(
            payer_id=payer_id,
            date_of_service=date_of_service,
            submission_date=submission_date,
        )

    def run_payer_claim_check(
        self,
        payer_id: str,
        cpt_code: str,
        icd10_codes: list[str],
        date_of_service: str,
        submission_date: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        place_of_service: Optional[str] = None,
        is_in_network: bool = True,
        auth_on_file: Optional[bool] = None,
    ) -> PayerClaimCheckResult:
        """Run comprehensive payer policy checks for a claim line."""

        return self.payer_policy_engine.run_payer_claim_check(
            payer_id=payer_id,
            cpt_code=cpt_code,
            icd10_codes=icd10_codes,
            date_of_service=date_of_service,
            submission_date=submission_date,
            patient_age=patient_age,
            patient_gender=patient_gender,
            place_of_service=place_of_service,
            is_in_network=is_in_network,
            auth_on_file=auth_on_file,
        )

    def get_auth_matrix(self, payer_id: str) -> dict[str, AuthRequirement]:
        """Return CPT → auth requirement mapping for a payer."""

        return self.payer_policy_engine.get_auth_matrix(payer_id)

    def get_appeal_info(self, payer_id: str) -> dict:
        """Return appeal timelines and levels for a payer."""

        return self.payer_policy_engine.get_appeal_info(payer_id)

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
                for g in self.coding_guidelines_db.find_by_icd10_code(code):
                    if g.guideline_id not in seen:
                        results.append(g)
                        seen.add(g.guideline_id)

        if keywords:
            query = " ".join(keywords)
            for g in self.coding_guidelines_db.search(query):
                if g.guideline_id not in seen:
                    results.append(g)
                    seen.add(g.guideline_id)

        return results

    # -- Official Coding Guidelines --------------------------------------

    def get_applicable_guidelines(
        self,
        icd10_codes: list[str],
        encounter_type: str,
        clinical_context: Optional[dict] = None,
    ) -> GuidelineLookupResult:
        """Retrieve applicable OCG guidelines for codes and encounter type."""

        return self.coding_guidelines_engine.get_applicable_guidelines(
            icd10_codes=icd10_codes,
            encounter_type=EncounterType(encounter_type),
            clinical_context=clinical_context,
        )

    def check_guideline_compliance(
        self,
        icd10_codes: list[str],
        encounter_type: str,
        primary_dx: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
    ) -> GuidelineComplianceResult:
        """Evaluate coding compliance against Official Coding Guidelines."""

        return self.coding_guidelines_engine.check_compliance(
            icd10_codes=icd10_codes,
            encounter_type=EncounterType(encounter_type),
            primary_dx=primary_dx,
            patient_age=patient_age,
            patient_gender=patient_gender,
        )

    def get_sequencing_rules(self, icd10_codes: list[str], encounter_type: str) -> dict:
        """Get sequencing guidance for provided codes and encounter."""

        return self.coding_guidelines_engine.get_sequencing_rules(icd10_codes, EncounterType(encounter_type))

    def get_combination_guidance(self, conditions: list[str]) -> dict:
        """Return combination-code guidance for condition list."""

        return self.coding_guidelines_engine.get_combination_guidance(conditions)

    def get_uncertain_diagnosis_rule(self, encounter_type: str) -> Optional[CodingGuideline]:
        """Return uncertain-diagnosis guideline for encounter type."""

        return self.coding_guidelines_engine.get_uncertain_diagnosis_rule(EncounterType(encounter_type))

    def format_guideline_citation(self, guideline_id: str) -> str:
        """Format guideline citation for reasoning chains."""

        return self.coding_guidelines_engine.format_guideline_citation(guideline_id)

    def get_coding_tips(self, icd10_code: str) -> list[str]:
        """Return practical coding tips for an ICD-10 code."""

        return self.coding_guidelines_engine.get_coding_tips(icd10_code)

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
        normalized_type = code_type.lower()

        if normalized_type == "icd10":
            search_func = self.vector_store.search_icd10
        elif normalized_type == "cpt":
            search_func = self.vector_store.search_cpt
        else:
            return []

        vector_results: list[CodeSearchResult] = []
        if self.vector_store.is_initialized:
            try:
                vector_results = search_func(clinical_text, top_k)
            except Exception as exc:  # pragma: no cover - safety net
                logger.error("Vector search failed for %s codes: %s", normalized_type, exc)
                vector_results = []

        if vector_results:
            return vector_results

        return self._keyword_search_fallback(clinical_text, normalized_type, top_k)

    def _keyword_search_fallback(
        self,
        clinical_text: str,
        code_type: str,
        top_k: int,
    ) -> list[CodeSearchResult]:
        query_terms = {
            term.lower()
            for term in clinical_text.split()
            if len(term) > 2
        }
        if not query_terms:
            return []

        registry = self.icd10_db._codes if code_type == "icd10" else self.cpt_db._codes
        scored: list[tuple[float, str, Any]] = []
        for code, entry in registry.items():
            desc_terms = set(entry.description.lower().split())
            overlap = len(query_terms & desc_terms)
            if not overlap:
                continue
            score = overlap / len(query_terms)
            scored.append((score, code, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        top = scored[:top_k]
        results: list[CodeSearchResult] = []
        for score, code, entry in top:
            results.append(
                CodeSearchResult(
                    code=code,
                    description=entry.description,
                    similarity_score=score,
                    metadata={"fallback": "keyword"},
                )
            )
        return results

    # -- Version -----------------------------------------------------------

    def get_knowledge_version(self, as_record: bool = False):
        """Return the active knowledge base version.

        Parameters
        ----------
        as_record:
            When True, return the full :class:`KnowledgeVersion` record. When False,
            return a legacy semantic version string for backward compatibility.
        """

        if as_record:
            return self.knowledge_updater.current_version

        current = self.knowledge_updater.current_version
        if hasattr(current, "metadata"):
            semantic = current.metadata.get("semantic_version")
            legacy = current.metadata.get("legacy_version")
            if semantic:
                return str(semantic)
            if legacy:
                return str(legacy)

        # Fallback to static semantic version for compatibility with existing tests/clients
        return self.VERSION

    def get_knowledge_version_history(self) -> list[KnowledgeVersion]:
        """Return all knowledge base versions (active and historical)."""

        return self.knowledge_updater.get_version_history()

    async def check_for_knowledge_updates(self) -> list[dict]:
        """Check configured feeds for available knowledge updates."""

        return await self.knowledge_updater.check_for_updates()

    async def apply_knowledge_update(self, update_data: dict, source: UpdateSource) -> KnowledgeVersion:
        """Run the 8-step update protocol for provided update data."""

        version = await self.knowledge_updater.process_update(update_data, source)
        # Update cached version identifiers for audit traceability
        self.current_version = self.knowledge_updater.current_version
        self.current_version_id = self.current_version.version_id
        return version

    def approve_knowledge_update(self, version_id: str, approved_by: str) -> bool:
        """Approve a pending human-review update and promote it."""

        return self.knowledge_updater.approve_update(version_id, approved_by)

    def rollback_knowledge(self, version_id: str, reason: str) -> bool:
        """Rollback to a previous knowledge base version."""

        return self.knowledge_updater.rollback(version_id, reason)

    def get_feed_status(self) -> list[dict]:
        """Return status for all configured knowledge update feeds."""

        return self.knowledge_updater.get_feed_status()

    def __repr__(self) -> str:
        return (
            f"KnowledgeManager(initialized={self._initialized}, "
            f"counts={self.code_count})"
        )
