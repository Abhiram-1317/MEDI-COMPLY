import json

import pytest

from medi_comply.knowledge.coding_guidelines import (
    CodingGuideline,
    CodingGuidelinesDatabase,
    CodingGuidelinesEngine,
    EncounterType,
    GuidelineCategory,
    GuidelineSection,
    Severity,
    seed_coding_guidelines,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seeded_db() -> CodingGuidelinesDatabase:
    db = CodingGuidelinesDatabase()
    seed_coding_guidelines(db)
    return db


@pytest.fixture(scope="module")
def engine(seeded_db: CodingGuidelinesDatabase) -> CodingGuidelinesEngine:
    return CodingGuidelinesEngine(database=seeded_db)


# ---------------------------------------------------------------------------
# CodingGuidelinesDatabase basics
# ---------------------------------------------------------------------------


def test_add_and_retrieve_guideline() -> None:
    db = CodingGuidelinesDatabase()
    guideline = CodingGuideline(
        guideline_id="TEST-GUIDE",
        section=GuidelineSection.SECTION_I,
        category=GuidelineCategory.GENERAL,
        title="Test Guideline",
        description="Test description",
        key_rules=["Rule"],
        common_mistakes=["Mistake"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-A99"],
        applicable_chapters=[1],
    )
    db.add_guideline(guideline)
    retrieved = db.get_guideline("TEST-GUIDE")
    assert retrieved is not None
    assert retrieved.guideline_id == "TEST-GUIDE"


def test_find_by_section(seeded_db: CodingGuidelinesDatabase) -> None:
    section_guidelines = seeded_db.find_by_section(GuidelineSection.SECTION_I)
    assert section_guidelines
    assert all(g.section == GuidelineSection.SECTION_I for g in section_guidelines)


def test_find_by_category(seeded_db: CodingGuidelinesDatabase) -> None:
    diabetes_guidelines = seeded_db.find_by_category(GuidelineCategory.DIABETES)
    assert diabetes_guidelines
    ids = {g.guideline_id for g in diabetes_guidelines}
    assert "OCG-I-C-4-a" in ids


def test_find_by_chapter(seeded_db: CodingGuidelinesDatabase) -> None:
    circulatory = seeded_db.find_by_chapter(9)
    assert circulatory
    assert all(9 in g.applicable_chapters for g in circulatory)


def test_find_by_encounter_type(seeded_db: CodingGuidelinesDatabase) -> None:
    outpatient_guidelines = seeded_db.find_by_encounter_type(EncounterType.OUTPATIENT)
    assert outpatient_guidelines
    assert all(EncounterType.OUTPATIENT in g.applicable_encounter_types for g in outpatient_guidelines)


def test_find_by_icd10_code(seeded_db: CodingGuidelinesDatabase) -> None:
    matches = seeded_db.find_by_icd10_code("E11.22")
    ids = {g.guideline_id for g in matches}
    assert "OCG-I-C-4-a" in ids


def test_search_keyword(seeded_db: CodingGuidelinesDatabase) -> None:
    results = seeded_db.search("diabetes")
    ids = {g.guideline_id for g in results}
    assert "OCG-I-C-4-a" in ids


def test_get_all_guidelines(seeded_db: CodingGuidelinesDatabase) -> None:
    all_guidelines = seeded_db.get_all_guidelines()
    assert len(all_guidelines) == seeded_db.get_guideline_count()
    assert len(all_guidelines) > 0


def test_guideline_count(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline_count() >= 30


# ---------------------------------------------------------------------------
# Seed data presence
# ---------------------------------------------------------------------------


def test_seed_loads_minimum_30(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline_count() >= 30


def test_section_i_guidelines_exist(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-I-A-1") is not None
    assert seeded_db.get_guideline("OCG-I-A-2") is not None


def test_section_ii_guidelines_exist(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-II-A") is not None


def test_section_iii_guidelines_exist(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-III-A") is not None


def test_section_iv_guidelines_exist(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-IV-A") is not None


def test_diabetes_guideline_exists(seeded_db: CodingGuidelinesDatabase) -> None:
    g = seeded_db.get_guideline("OCG-I-C-4-a")
    assert g is not None
    assert "Diabetes" in g.title
    assert any("combination" in rule.lower() for rule in g.key_rules)


def test_excludes_guideline_exists(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-I-A-6") is not None


def test_uncertain_inpatient_guideline_exists(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-II-H") is not None


def test_uncertain_outpatient_guideline_exists(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-IV-D") is not None


def test_laterality_guideline_exists(seeded_db: CodingGuidelinesDatabase) -> None:
    assert seeded_db.get_guideline("OCG-I-A-13") is not None


def test_circulatory_guidelines_exist(seeded_db: CodingGuidelinesDatabase) -> None:
    ch9 = seeded_db.find_by_chapter(9)
    ids = {g.guideline_id for g in ch9}
    assert "OCG-I-C-9-a" in ids


# ---------------------------------------------------------------------------
# Guideline retrieval
# ---------------------------------------------------------------------------


def test_get_applicable_diabetes(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["E11.22"], EncounterType.OUTPATIENT)
    ids = {g.guideline_id for g in result.guidelines_found}
    assert "OCG-I-C-4-a" in ids


def test_get_applicable_injury(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["S72.001A"], EncounterType.INPATIENT)
    ids = {g.guideline_id for g in result.guidelines_found}
    assert "OCG-I-C-19-a" in ids


def test_get_applicable_multiple_codes(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["E11.22", "S72.001A"], EncounterType.INPATIENT)
    ids = {g.guideline_id for g in result.guidelines_found}
    assert "OCG-I-C-4-a" in ids
    assert "OCG-I-C-19-a" in ids


def test_most_relevant_returned(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["E11.22"], EncounterType.OUTPATIENT)
    assert result.most_relevant is not None
    max_priority = max(g.priority for g in result.guidelines_found)
    assert result.most_relevant.priority == max_priority


def test_context_summary_generated(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["E11.22"], EncounterType.OUTPATIENT)
    assert result.context_summary


# ---------------------------------------------------------------------------
# Compliance checking
# ---------------------------------------------------------------------------


def test_compliant_coding_passes(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["E11.22", "N18.3"], EncounterType.OUTPATIENT, primary_dx="E11.22")
    assert compliance.is_compliant
    assert not compliance.violations
    assert not compliance.warnings


def test_excludes1_violation_detected(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["R05", "J18.9"], EncounterType.INPATIENT, primary_dx="J18.9")
    ids = {v.violation_id for v in compliance.violations}
    assert "EXCLUDES1-SYMPTOM" in ids
    assert all(v.severity == Severity.WARNING for v in compliance.violations)


def test_unspecified_when_specific_available(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["E11.9"], EncounterType.OUTPATIENT)
    ids = {v.guideline_id for v in compliance.violations}
    assert "OCG-I-A-2" in ids
    assert all(v.severity == Severity.WARNING for v in compliance.violations)


def test_wrong_sequencing_detected(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["N18.30", "E11.22"], EncounterType.INPATIENT, primary_dx="E11.22")
    ids = {v.violation_id for v in compliance.violations}
    assert "SEQ-PRIMARY" in ids


def test_missing_laterality_flagged(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["M25.569"], EncounterType.OUTPATIENT)
    warning_ids = {w.guideline_id for w in compliance.warnings}
    assert "OCG-I-A-13" in warning_ids


def test_missing_7th_character_flagged(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["S72.001"], EncounterType.INPATIENT)
    ids = {v.violation_id for v in compliance.violations}
    assert "7TH-S72.001" in ids
    assert not compliance.is_compliant


def test_uncertain_outpatient_violation(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["J18.9?"], EncounterType.OUTPATIENT)
    warning_ids = {w.guideline_id for w in compliance.warnings}
    assert "OCG-IV-D" in warning_ids
    assert compliance.is_compliant


def test_uncertain_inpatient_allowed(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["J18.9?"], EncounterType.INPATIENT)
    assert compliance.is_compliant
    assert not compliance.warnings


def test_missing_use_additional_flagged(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["N18.30"], EncounterType.OUTPATIENT)
    warning_ids = {w.guideline_id for w in compliance.warnings}
    assert "OCG-I-A-9" in warning_ids


def test_manifestation_as_primary_flagged(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["H36.0"], EncounterType.INPATIENT)
    ids = {v.guideline_id for v in compliance.violations}
    assert "OCG-I-A-7" in ids
    assert not compliance.is_compliant


def test_compliance_score(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["E11.9", "N18.30"], EncounterType.OUTPATIENT, primary_dx="E11.9")
    assert pytest.approx(compliance.compliance_score, rel=1e-3) == 0.88


# ---------------------------------------------------------------------------
# Specific methods
# ---------------------------------------------------------------------------


def test_get_sequencing_rules(engine: CodingGuidelinesEngine) -> None:
    rules = engine.get_sequencing_rules(["E11.22", "N18.3"], EncounterType.OUTPATIENT)
    assert rules["principal"] == "E11.22"
    assert "OCG-II-A" in rules["citation"]


def test_get_combination_guidance_diabetes(engine: CodingGuidelinesEngine) -> None:
    guidance = engine.get_combination_guidance(["diabetes", "nephropathy"])
    assert guidance["use_combination_code"] is True
    assert guidance["recommended_code"] == "E11.22"


def test_get_uncertain_diagnosis_rule_inpatient(engine: CodingGuidelinesEngine) -> None:
    rule = engine.get_uncertain_diagnosis_rule(EncounterType.INPATIENT)
    assert rule is not None
    assert rule.guideline_id == "OCG-II-H"


def test_get_uncertain_diagnosis_rule_outpatient(engine: CodingGuidelinesEngine) -> None:
    rule = engine.get_uncertain_diagnosis_rule(EncounterType.OUTPATIENT)
    assert rule is not None
    assert rule.guideline_id == "OCG-IV-D"


def test_format_guideline_citation(engine: CodingGuidelinesEngine) -> None:
    citation = engine.format_guideline_citation("OCG-I-C-4-a")
    assert "OCG-I-C-4-a" in citation
    assert "Official Coding Guideline" in citation


def test_get_coding_tips(engine: CodingGuidelinesEngine) -> None:
    tips = engine.get_coding_tips("E11.22")
    assert tips


def test_get_chapter_guidelines(engine: CodingGuidelinesEngine) -> None:
    ch9 = engine.get_chapter_guidelines(9)
    ids = {g.guideline_id for g in ch9}
    assert "OCG-I-C-9-a" in ids


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def test_guideline_has_examples(seeded_db: CodingGuidelinesDatabase) -> None:
    for g in seeded_db.get_all_guidelines():
        assert g.examples


def test_example_has_correct_codes(seeded_db: CodingGuidelinesDatabase) -> None:
    for g in seeded_db.get_all_guidelines():
        for ex in g.examples:
            assert ex.correct_codes


def test_example_has_incorrect_codes(seeded_db: CodingGuidelinesDatabase) -> None:
    for g in seeded_db.get_all_guidelines():
        for ex in g.examples:
            assert ex.incorrect_codes


def test_example_has_explanation(seeded_db: CodingGuidelinesDatabase) -> None:
    for g in seeded_db.get_all_guidelines():
        for ex in g.examples:
            assert ex.explanation


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_code_list(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance([], EncounterType.OUTPATIENT)
    assert compliance.is_compliant
    assert compliance.codes_checked == []


def test_unknown_code(engine: CodingGuidelinesEngine) -> None:
    result = engine.get_applicable_guidelines(["ZZZ.999"], EncounterType.OUTPATIENT)
    assert result.guidelines_found
    assert result.total_found == len(result.guidelines_found)


def test_result_serialization(engine: CodingGuidelinesEngine) -> None:
    compliance = engine.check_compliance(["E11.22"], EncounterType.OUTPATIENT)
    dumped = compliance.model_dump()
    assert isinstance(dumped, dict)
    assert json.loads(compliance.model_dump_json())
