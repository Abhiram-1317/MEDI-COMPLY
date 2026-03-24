import pytest

from medi_comply.knowledge.lcd_ncd_engine import (
    AgeRestriction,
    ClinicalCriterion,
    CoverageDetermination,
    CoverageStatus,
    CoverageType,
    LCDNCDDatabase,
    LCDNCDEngine,
    MedicalNecessityStatus,
    MedicalNecessityResult,
    seed_lcd_ncd_data,
)


@pytest.fixture()
def seeded_db() -> LCDNCDDatabase:
    db = LCDNCDDatabase()
    seed_lcd_ncd_data(db)
    return db


@pytest.fixture()
def engine(seeded_db: LCDNCDDatabase) -> LCDNCDEngine:
    return LCDNCDEngine(database=seeded_db)


# LCDNCDDatabase basic operations


def test_add_and_retrieve_determination():
    db = LCDNCDDatabase()
    det = CoverageDetermination(
        determination_id="TEST-1",
        determination_type=CoverageType.NCD,
        title="Test",
        effective_date="2020-01-01",
        covered_cpt_codes=["11111"],
        covered_icd10_codes=["A00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    db.add_determination(det)
    assert db.get_determination("TEST-1") == det


def test_find_by_cpt():
    db = LCDNCDDatabase()
    det = CoverageDetermination(
        determination_id="TEST-2",
        determination_type=CoverageType.LCD,
        title="Test",
        effective_date="2020-01-01",
        covered_cpt_codes=["22222"],
        covered_icd10_codes=["B00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    db.add_determination(det)
    assert db.find_by_cpt("22222") == [det]


def test_find_by_icd10():
    db = LCDNCDDatabase()
    det = CoverageDetermination(
        determination_id="TEST-3",
        determination_type=CoverageType.LCD,
        title="Test",
        effective_date="2020-01-01",
        covered_cpt_codes=["33333"],
        covered_icd10_codes=["C00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    db.add_determination(det)
    assert db.find_by_icd10("C00") == [det]


def test_find_by_cpt_and_region():
    db = LCDNCDDatabase()
    lcd = CoverageDetermination(
        determination_id="TEST-4",
        determination_type=CoverageType.LCD,
        title="Test",
        effective_date="2020-01-01",
        applicable_states=["CA"],
        covered_cpt_codes=["44444"],
        covered_icd10_codes=["D00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    ncd = CoverageDetermination(
        determination_id="TEST-5",
        determination_type=CoverageType.NCD,
        title="Test",
        effective_date="2020-01-01",
        covered_cpt_codes=["44444"],
        covered_icd10_codes=["D01"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    db.add_determination(lcd)
    db.add_determination(ncd)
    results = db.find_by_cpt_and_region("44444", state="CA")
    assert lcd in results and ncd in results
    results_no_state = db.find_by_cpt_and_region("44444", state="NY")
    assert ncd in results_no_state and lcd not in results_no_state


def test_get_all_ncds():
    db = LCDNCDDatabase()
    db.add_determination(
        CoverageDetermination(
            determination_id="NCD-TEST",
            determination_type=CoverageType.NCD,
            title="Test",
            effective_date="2020-01-01",
            covered_cpt_codes=["55555"],
            covered_icd10_codes=["E00"],
            non_covered_icd10_codes=[],
            documentation_requirements=[],
            clinical_criteria=[],
        )
    )
    assert all(det.determination_type == CoverageType.NCD for det in db.get_all_ncds())


def test_get_all_lcds():
    db = LCDNCDDatabase()
    db.add_determination(
        CoverageDetermination(
            determination_id="LCD-TEST",
            determination_type=CoverageType.LCD,
            title="Test",
            effective_date="2020-01-01",
            covered_cpt_codes=["66666"],
            covered_icd10_codes=["F00"],
            non_covered_icd10_codes=[],
            documentation_requirements=[],
            clinical_criteria=[],
        )
    )
    assert all(det.determination_type == CoverageType.LCD for det in db.get_all_lcds())


def test_get_active_determinations():
    db = LCDNCDDatabase()
    active = CoverageDetermination(
        determination_id="ACTIVE",
        determination_type=CoverageType.NCD,
        title="Active",
        effective_date="2020-01-01",
        covered_cpt_codes=["77777"],
        covered_icd10_codes=["G00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    expired = CoverageDetermination(
        determination_id="EXPIRED",
        determination_type=CoverageType.NCD,
        title="Expired",
        effective_date="2010-01-01",
        end_date="2011-01-01",
        covered_cpt_codes=["88888"],
        covered_icd10_codes=["H00"],
        non_covered_icd10_codes=[],
        documentation_requirements=[],
        clinical_criteria=[],
    )
    db.add_determination(active)
    db.add_determination(expired)
    assert active in db.get_active_determinations()
    assert expired not in db.get_active_determinations()


def test_determination_count():
    db = LCDNCDDatabase()
    db.add_determination(
        CoverageDetermination(
            determination_id="COUNT-NCD",
            determination_type=CoverageType.NCD,
            title="Test",
            effective_date="2020-01-01",
            covered_cpt_codes=["99999"],
            covered_icd10_codes=["I00"],
            non_covered_icd10_codes=[],
            documentation_requirements=[],
            clinical_criteria=[],
        )
    )
    db.add_determination(
        CoverageDetermination(
            determination_id="COUNT-LCD",
            determination_type=CoverageType.LCD,
            title="Test",
            effective_date="2020-01-01",
            covered_cpt_codes=["99998"],
            covered_icd10_codes=["I01"],
            non_covered_icd10_codes=[],
            documentation_requirements=[],
            clinical_criteria=[],
        )
    )
    counts = db.get_determination_count()
    assert counts[CoverageType.NCD.value] == 1
    assert counts[CoverageType.LCD.value] == 1


# Seed data assertions


def test_seed_data_loads(seeded_db: LCDNCDDatabase):
    assert len(seeded_db.get_active_determinations()) >= 15


def test_seed_data_has_ncds(seeded_db: LCDNCDDatabase):
    assert len(seeded_db.get_all_ncds()) >= 1


def test_seed_data_has_lcds(seeded_db: LCDNCDDatabase):
    assert len(seeded_db.get_all_lcds()) >= 1


def test_vitamin_d_lcd_exists(seeded_db: LCDNCDDatabase):
    matches = [det for det in seeded_db.get_all_lcds() if "82306" in det.covered_cpt_codes]
    assert matches


def test_cardiac_cath_lcd_exists(seeded_db: LCDNCDDatabase):
    matches = [det for det in seeded_db.get_all_lcds() if det.determination_id == "LCD-L36256"]
    assert matches


# Medical necessity behavior


def test_covered_diagnosis_passes(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    assert res.coverage_status == CoverageStatus.COVERED


def test_uncovered_diagnosis_fails(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["J06.9"])
    assert res.coverage_status in {CoverageStatus.NOT_COVERED, CoverageStatus.NOT_SPECIFIED}


def test_no_lcd_ncd_returns_not_specified(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("00000", ["E55.9"])
    assert res.coverage_status == CoverageStatus.NOT_SPECIFIED


def test_wildcard_icd10_matching(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["M80.00XA"])
    assert res.covered_diagnoses_found


def test_hierarchical_code_matching(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["M80.00XA"])
    assert any(code.startswith("M80.") for code in res.covered_diagnoses_found)


def test_non_covered_list_detected(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("78815", ["Z13.89"])
    assert res.coverage_status == CoverageStatus.NOT_COVERED


def test_age_restriction_passes(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("77057", ["Z12.5"], patient_age=60, patient_gender="M")
    assert res.coverage_status in {CoverageStatus.COVERED, CoverageStatus.CONDITIONAL}


def test_age_restriction_fails(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("77057", ["Z12.5"], patient_age=40, patient_gender="M")
    assert res.age_check_passed is False
    assert res.medical_necessity_status == MedicalNecessityStatus.DOES_NOT_MEET


def test_gender_restriction_passes(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("77057", ["Z12.5"], patient_age=60, patient_gender="M")
    assert res.gender_check_passed in {None, True}


def test_gender_restriction_fails(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("77057", ["Z12.5"], patient_age=60, patient_gender="F")
    assert res.gender_check_passed is False


def test_frequency_limit_returned(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"], clinical_info={"frequency_used": 2})
    assert res.frequency_check_passed in {None, False}


# MedicalNecessityResult contents


def test_result_has_covered_diagnoses(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    assert "E55.9" in res.covered_diagnoses_found


def test_result_has_uncovered_diagnoses(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["J06.9", "E55.9"])
    assert any(code for code in res.uncovered_diagnoses if code == "J06.9")


def test_result_has_recommendations(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["J06.9"])
    assert res.recommendations


def test_result_has_reasoning(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    assert res.reasoning


def test_result_confidence(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    assert 0.0 <= res.confidence <= 1.0


# Helper methods


def test_get_covered_diagnoses(engine: LCDNCDEngine):
    covered = engine.get_covered_diagnoses("82306")
    assert "E55.9" in covered


def test_get_required_documentation(engine: LCDNCDEngine):
    docs = engine.get_required_documentation("82306")
    assert docs


def test_get_clinical_criteria(engine: LCDNCDEngine):
    criteria = engine.get_clinical_criteria("82306")
    assert criteria


def test_is_procedure_covered_true(engine: LCDNCDEngine):
    assert engine.is_procedure_covered("82306", "E55.9") is True


def test_is_procedure_covered_false(engine: LCDNCDEngine):
    assert engine.is_procedure_covered("82306", "J06.9") is False


def test_suggest_covered_alternatives(engine: LCDNCDEngine):
    alternatives = engine.suggest_covered_alternatives("82306", ["J06.9"])
    assert alternatives


# Edge cases


def test_multiple_diagnoses_partial_coverage(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9", "J06.9"])
    assert res.coverage_status in {CoverageStatus.COVERED, CoverageStatus.CONDITIONAL}
    assert "E55.9" in res.covered_diagnoses_found
    assert "J06.9" in res.uncovered_diagnoses


def test_empty_diagnosis_list(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", [])
    assert res.coverage_status == CoverageStatus.NOT_SPECIFIED


def test_conditional_coverage(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"], clinical_info={"documentation": []})
    assert res.coverage_status in {CoverageStatus.CONDITIONAL, CoverageStatus.COVERED}


def test_reasoning_generation(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    reasoning = engine.generate_necessity_reasoning(res)
    assert res.determination_used is not None
    assert res.determination_used.determination_id in reasoning


def test_result_serialization(engine: LCDNCDEngine):
    res = engine.check_medical_necessity("82306", ["E55.9"])
    assert isinstance(res, MedicalNecessityResult)
    assert res.model_dump()
    assert res.model_dump_json()
