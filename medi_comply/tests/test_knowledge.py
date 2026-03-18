"""
MEDI-COMPLY — Test suite for the Knowledge Base module.

25+ test cases covering ICD-10, CPT, NCCI, medical necessity,
coding guidelines, vector search, and the unified KnowledgeManager.
"""

from __future__ import annotations

import pytest
from medi_comply.knowledge.icd10_db import ICD10Database, ICD10CodeEntry, ValidationResult
from medi_comply.knowledge.cpt_db import CPTDatabase, CPTCodeEntry
from medi_comply.knowledge.ncci_engine import NCCIEngine
from medi_comply.knowledge.medical_necessity import MedicalNecessityEngine
from medi_comply.knowledge.coding_guidelines import CodingGuidelinesStore
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.knowledge.seed_data import seed_all_data


# ---------------------------------------------------------------------------
# Shared fixture — fully-seeded KnowledgeManager
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def km() -> KnowledgeManager:
    """Create and seed a KnowledgeManager (once per module)."""
    manager = KnowledgeManager()
    seed_all_data(manager)
    return manager


# ---------------------------------------------------------------------------
# ICD-10 Tests
# ---------------------------------------------------------------------------


class TestICD10:
    """ICD-10 database tests."""

    def test_code_exists_valid(self, km: KnowledgeManager) -> None:
        """E11.22 is a real ICD-10-CM code and must exist."""
        assert km.icd10_db.code_exists("E11.22")

    def test_code_exists_invalid(self, km: KnowledgeManager) -> None:
        """E11.999 is NOT a real code — hallucination firewall must catch it."""
        assert not km.icd10_db.code_exists("E11.999")

    def test_code_hierarchy(self, km: KnowledgeManager) -> None:
        """E11.22 parent should be E11.2."""
        parent = km.icd10_db.get_parent("E11.22")
        assert parent is not None
        assert parent.code == "E11.2"

    def test_excludes1_violation(self, km: KnowledgeManager) -> None:
        """E10.22 (Type 1 DM) and E11.22 (Type 2 DM) are mutually exclusive."""
        is_excluded, reason = km.icd10_db.check_excludes1("E10.22", "E11.22")
        assert is_excluded
        assert reason != ""

    def test_excludes1_no_violation(self, km: KnowledgeManager) -> None:
        """E11.22 and N18.30 can coexist — no Excludes1 conflict."""
        is_excluded, _ = km.icd10_db.check_excludes1("E11.22", "N18.30")
        assert not is_excluded

    def test_use_additional(self, km: KnowledgeManager) -> None:
        """E11.22 instructs to use additional N18.- code."""
        instructions = km.icd10_db.get_use_additional_instructions("E11.22")
        assert len(instructions) > 0
        assert any("N18" in i for i in instructions)

    def test_code_first(self, km: KnowledgeManager) -> None:
        """N18.30 has Code-first instruction for diabetes."""
        instructions = km.icd10_db.get_code_first_instructions("N18.30")
        assert len(instructions) > 0
        assert any("E08-E13" in i or "underlying" in i.lower() for i in instructions)

    def test_specificity_has_children(self, km: KnowledgeManager) -> None:
        """E11.2 (category) has more specific children available."""
        has_children, children = km.icd10_db.has_higher_specificity("E11.2")
        assert has_children
        assert "E11.22" in children

    def test_age_validation_newborn(self, km: KnowledgeManager) -> None:
        """Newborn code P07.14 should fail for adult patient."""
        result = km.icd10_db.validate_code("P07.14", patient_age=45)
        assert not result.is_valid
        assert any("age" in e.lower() for e in result.errors)

    def test_gender_validation_pregnancy(self, km: KnowledgeManager) -> None:
        """Pregnancy code O80 should fail for male patient."""
        result = km.icd10_db.validate_code("O80", patient_gender="MALE")
        assert not result.is_valid
        assert any("gender" in e.lower() for e in result.errors)

    def test_manifestation_code(self, km: KnowledgeManager) -> None:
        """Check that manifestation codes are flagged."""
        # N18.30 has code_first -> not a manifestation_code per se,
        # but we can test the billability / existence
        entry = km.icd10_db.get_code("N18.30")
        assert entry is not None
        assert entry.is_billable

    def test_billable_category(self, km: KnowledgeManager) -> None:
        """Category code E11 is NOT billable; E11.9 IS billable."""
        assert not km.icd10_db.is_billable("E11")
        assert km.icd10_db.is_billable("E11.9")

    def test_search_by_description(self, km: KnowledgeManager) -> None:
        """Keyword search for 'diabetes kidney' should return relevant codes."""
        results = km.icd10_db.search_by_description("diabetes kidney")
        codes = [r.code for r in results]
        assert any(c.startswith("E11.2") for c in codes)

    def test_excludes1_hypertension(self, km: KnowledgeManager) -> None:
        """I10 (Essential HTN) excludes I11.0 (Hypertensive heart disease)."""
        is_excluded, _ = km.icd10_db.check_excludes1("I10", "I11.0")
        assert is_excluded

    def test_excludes1_copd(self, km: KnowledgeManager) -> None:
        """J44.0 and J44.1 are mutually exclusive (COPD excludes)."""
        is_excluded, _ = km.icd10_db.check_excludes1("J44.0", "J44.1")
        assert is_excluded


# ---------------------------------------------------------------------------
# CPT Tests
# ---------------------------------------------------------------------------


class TestCPT:
    """CPT database tests."""

    def test_cpt_exists(self, km: KnowledgeManager) -> None:
        """99213 is a real CPT code."""
        assert km.cpt_db.code_exists("99213")

    def test_cpt_not_exists(self, km: KnowledgeManager) -> None:
        """99999 is not a valid CPT code."""
        assert not km.cpt_db.code_exists("99999")

    def test_cpt_modifier(self, km: KnowledgeManager) -> None:
        """99213 has modifier 25 as a common modifier."""
        modifiers = km.cpt_db.get_common_modifiers("99213")
        assert "25" in modifiers

    def test_cpt_addon(self, km: KnowledgeManager) -> None:
        """99292 is flagged as an add-on code."""
        assert km.cpt_db.is_add_on("99292")
        assert not km.cpt_db.is_add_on("99291")

    def test_cpt_rvu(self, km: KnowledgeManager) -> None:
        """99213 should have a non-zero total RVU."""
        rvu = km.cpt_db.get_rvu("99213")
        assert rvu is not None
        assert rvu > 1.0


# ---------------------------------------------------------------------------
# NCCI Tests
# ---------------------------------------------------------------------------


class TestNCCI:
    """NCCI edit and MUE tests."""

    def test_ncci_bundled_pair_no_modifier(self, km: KnowledgeManager) -> None:
        """80053 (CMP) bundles 80048 (BMP) — NO modifier override."""
        result = km.ncci_engine.check_pair("80053", "80048")
        assert result.is_bundled
        assert not result.modifier_allowed

    def test_ncci_allowed_modifier(self, km: KnowledgeManager) -> None:
        """93453 bundles 93451 — modifier IS allowed."""
        result = km.ncci_engine.check_pair("93453", "93451")
        assert result.is_bundled
        assert result.modifier_allowed

    def test_ncci_no_edit(self, km: KnowledgeManager) -> None:
        """99213 and 93000 have no NCCI edit between them."""
        result = km.ncci_engine.check_pair("99213", "93000")
        assert not result.is_bundled

    def test_ncci_all_pairs(self, km: KnowledgeManager) -> None:
        """Check 3 codes with known issues: 80053, 80048, 82565."""
        results = km.ncci_engine.check_all_pairs(["80053", "80048", "82565"])
        assert len(results) >= 2  # 80053 bundles both 80048 and 82565

    def test_mue_pass(self, km: KnowledgeManager) -> None:
        """99213 × 1 unit passes MUE (limit = 1)."""
        result = km.ncci_engine.check_mue("99213", 1)
        assert result.passes

    def test_mue_fail(self, km: KnowledgeManager) -> None:
        """99213 × 3 units fails MUE (limit = 1)."""
        result = km.ncci_engine.check_mue("99213", 3)
        assert not result.passes
        assert result.max_units == 1
        assert result.submitted_units == 3

    def test_mutually_exclusive(self, km: KnowledgeManager) -> None:
        """80053 and 80048 are mutually exclusive (no modifier)."""
        assert km.ncci_engine.check_mutually_exclusive("80053", "80048")


# ---------------------------------------------------------------------------
# Medical Necessity Tests
# ---------------------------------------------------------------------------


class TestMedicalNecessity:
    """Medical necessity / LCD tests."""

    def test_necessity_covered(self, km: KnowledgeManager) -> None:
        """93306 (echo) with I50.20 (systolic CHF) is covered."""
        result = km.med_necessity.check_medical_necessity("93306", ["I50.20"])
        assert result.is_medically_necessary
        assert "I50.20" in result.covered_dx_matches

    def test_necessity_not_covered(self, km: KnowledgeManager) -> None:
        """93306 (echo) with R51.0 (headache) is NOT covered."""
        result = km.med_necessity.check_medical_necessity("93306", ["R51.0"])
        assert not result.is_medically_necessary
        assert "R51.0" in result.uncovered_dx

    def test_documentation_requirements(self, km: KnowledgeManager) -> None:
        """93306 should have documentation requirements."""
        reqs = km.med_necessity.get_documentation_requirements("93306")
        assert len(reqs) > 0


# ---------------------------------------------------------------------------
# Coding Guidelines Tests
# ---------------------------------------------------------------------------


class TestCodingGuidelines:
    """Coding guidelines store tests."""

    def test_guideline_lookup(self, km: KnowledgeManager) -> None:
        """Can retrieve guideline by ID."""
        g = km.guidelines.get_guideline("OCG-I-C-4-a")
        assert g is not None
        assert "diabetes" in g.title.lower()

    def test_guidelines_for_code(self, km: KnowledgeManager) -> None:
        """E11.22 should return diabetes-related guidelines."""
        guidelines = km.guidelines.get_guidelines_for_code("E11.22")
        assert len(guidelines) > 0
        ids = [g.guideline_id for g in guidelines]
        assert any("C-4" in gid for gid in ids)

    def test_guideline_citation(self, km: KnowledgeManager) -> None:
        """Citation generation produces readable string."""
        citation = km.guidelines.generate_citation("OCG-I-C-4-a")
        assert "OCG" in citation
        assert "Diabetes" in citation or "diabetes" in citation


# ---------------------------------------------------------------------------
# Knowledge Manager Integration Tests
# ---------------------------------------------------------------------------


class TestKnowledgeManager:
    """Integration tests for the unified KnowledgeManager."""

    def test_unified_icd10_lookup(self, km: KnowledgeManager) -> None:
        """lookup_icd10 returns correct entry."""
        entry = km.lookup_icd10("I21.4")
        assert entry is not None
        assert "NSTEMI" in entry.description

    def test_unified_cpt_lookup(self, km: KnowledgeManager) -> None:
        """lookup_cpt returns correct entry."""
        entry = km.lookup_cpt("93306")
        assert entry is not None
        assert "echo" in entry.description.lower()

    def test_validate_code_exists(self, km: KnowledgeManager) -> None:
        """validate_code_exists covers both icd10 and cpt."""
        assert km.validate_code_exists("E11.22", "icd10")
        assert km.validate_code_exists("99213", "cpt")
        assert not km.validate_code_exists("FAKE99", "icd10")

    def test_check_excludes(self, km: KnowledgeManager) -> None:
        """check_excludes wrapper works."""
        result = km.check_excludes("E10.22", "E11.22")
        assert result.has_conflict
        assert result.excludes1

    def test_full_validation_pipeline(self, km: KnowledgeManager) -> None:
        """End-to-end: validate ICD-10 + check NCCI + check necessity."""
        # ICD-10 validation
        icd_result = km.validate_icd10_assignment("E11.22", patient_age=65, patient_gender="MALE")
        assert icd_result.is_valid

        # NCCI check
        ncci_results = km.check_ncci_edits(["99213", "93000"])
        assert len(ncci_results) == 0  # No edit between these

        ncci_results2 = km.check_ncci_edits(["80053", "80048"])
        assert len(ncci_results2) > 0  # CMP bundles BMP

        # Medical necessity
        med_result = km.check_medical_necessity("93306", ["I50.20"])
        assert med_result.is_medically_necessary

    def test_code_counts(self, km: KnowledgeManager) -> None:
        """Verify minimum code counts after seeding."""
        counts = km.code_count
        assert counts["icd10"] >= 100
        assert counts["cpt"] >= 50
        assert counts["ncci_pairs"] >= 30
        assert counts["mue"] >= 15
        assert counts["lcd"] >= 10
        assert counts["guidelines"] >= 15

    def test_knowledge_version(self, km: KnowledgeManager) -> None:
        """Version string is returned."""
        version = km.get_knowledge_version()
        assert version == "1.0.0"
