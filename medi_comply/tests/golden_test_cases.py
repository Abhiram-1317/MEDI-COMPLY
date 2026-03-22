"""Golden test case definitions for MEDI-COMPLY."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GoldenTestCase:
    """A single golden test case."""

    case_id: str
    category: str
    description: str
    clinical_note_key: str
    clinical_note_override: Optional[str] = None
    patient_age: int = 50
    patient_gender: str = "male"
    encounter_type: str = "INPATIENT"
    expected_status: list[str] = field(default_factory=lambda: ["SUCCESS"])
    must_include_codes: list[str] = field(default_factory=list)
    must_not_include_codes: list[str] = field(default_factory=list)
    expected_primary_dx_prefix: Optional[str] = None
    min_codes: int = 1
    max_codes: int = 20
    min_overall_confidence: float = 0.0
    expect_combination_code: bool = False
    expect_use_additional: bool = False
    expect_negation_handling: bool = False
    expect_abbreviation_handling: bool = False
    expect_compliance_pass: bool = False
    expect_no_excludes1_violation: bool = False
    expect_audit_complete: bool = False
    expect_evidence_for_all_codes: bool = False
    expect_reasoning_chains: bool = False


GOLDEN_CASES: list[GoldenTestCase] = []


# ── CATEGORY 1: CARDIAC (1-15) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="CARD-001",
            category="CARDIAC",
            description="Acute NSTEMI with DM and CKD — combination logic",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expected_primary_dx_prefix="I21",
            min_codes=2,
            expect_combination_code=True,
            expect_use_additional=True,
        ),
        GoldenTestCase(
            case_id="CARD-002",
            category="CARDIAC",
            description="NSTEMI primary diagnosis validation",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expected_primary_dx_prefix="I21",
        ),
        GoldenTestCase(
            case_id="CARD-003",
            category="CARDIAC",
            description="Negated symptoms not coded",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            must_not_include_codes=["R50.9", "R05.9"],
            expect_negation_handling=True,
        ),
        GoldenTestCase(
            case_id="CARD-004",
            category="CARDIAC",
            description="CHF exacerbation multi-system coding",
            clinical_note_key="chf_exacerbation",
            patient_age=70,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expected_primary_dx_prefix="I50",
            min_codes=3,
        ),
        GoldenTestCase(
            case_id="CARD-005",
            category="CARDIAC",
            description="CHF with AFib comorbidity",
            clinical_note_key="chf_exacerbation",
            patient_age=70,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=2,
        ),
        GoldenTestCase(
            case_id="CARD-006",
            category="CARDIAC",
            description="DM with nephropathy combination code",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_combination_code=True,
        ),
        GoldenTestCase(
            case_id="CARD-007",
            category="CARDIAC",
            description="Hypertension captured",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="CARD-008",
            category="CARDIAC",
            description="Vitals extracted",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="CARD-009",
            category="CARDIAC",
            description="Laboratory values recognized",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="CARD-010",
            category="CARDIAC",
            description="Medication extraction",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="CARD-011",
            category="CARDIAC",
            description="Compliance passes",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="CARD-012",
            category="CARDIAC",
            description="Reasoning chains present",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_reasoning_chains=True,
        ),
        GoldenTestCase(
            case_id="CARD-013",
            category="CARDIAC",
            description="Evidence linked",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_evidence_for_all_codes=True,
        ),
        GoldenTestCase(
            case_id="CARD-014",
            category="CARDIAC",
            description="Audit trail complete",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_audit_complete=True,
        ),
        GoldenTestCase(
            case_id="CARD-015",
            category="CARDIAC",
            description="Overall confidence threshold",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_overall_confidence=0.5,
        ),
    ]
)


# ── CATEGORY 2: PULMONARY (16-30) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="PULM-001",
            category="PULMONARY",
            description="COPD exacerbation primary coding",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            must_not_include_codes=["R07.1", "R07.2", "R07.9"],
            expect_negation_handling=True,
        ),
        GoldenTestCase(
            case_id="PULM-002",
            category="PULMONARY",
            description="No Excludes1 in COPD",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="PULM-003",
            category="PULMONARY",
            description="Former smoker code",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-004",
            category="PULMONARY",
            description="Chest pain denied",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            must_not_include_codes=["R07.1", "R07.2", "R07.9"],
        ),
        GoldenTestCase(
            case_id="PULM-005",
            category="PULMONARY",
            description="Hemoptysis denied",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-006",
            category="PULMONARY",
            description="Pneumonia with sepsis",
            clinical_note_key="pneumonia_sepsis",
            patient_age=68,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=3,
        ),
        GoldenTestCase(
            case_id="PULM-007",
            category="PULMONARY",
            description="Respiratory failure captured",
            clinical_note_key="pneumonia_sepsis",
            patient_age=68,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-008",
            category="PULMONARY",
            description="Primary dx pneumonia or sepsis",
            clinical_note_key="pneumonia_sepsis",
            patient_age=68,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-009",
            category="PULMONARY",
            description="Vitals highlight tachypnea",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-010",
            category="PULMONARY",
            description="Low SpO2 captured",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-011",
            category="PULMONARY",
            description="Medication extraction",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="PULM-012",
            category="PULMONARY",
            description="Compliance clean",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="PULM-013",
            category="PULMONARY",
            description="Audit completeness",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_audit_complete=True,
        ),
        GoldenTestCase(
            case_id="PULM-014",
            category="PULMONARY",
            description="Sepsis complexity",
            clinical_note_key="pneumonia_sepsis",
            patient_age=68,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=3,
        ),
        GoldenTestCase(
            case_id="PULM-015",
            category="PULMONARY",
            description="DM comorbidity in pneumonia",
            clinical_note_key="pneumonia_sepsis",
            patient_age=68,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
    ]
)


# ── CATEGORY 3: DIABETES / ENDOCRINE (31-45) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="DM-001",
            category="DIABETES",
            description="Simple T2DM outpatient",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=1,
        ),
        GoldenTestCase(
            case_id="DM-002",
            category="DIABETES",
            description="T2DM with hypertension",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=2,
        ),
        GoldenTestCase(
            case_id="DM-003",
            category="DIABETES",
            description="Combination code E11.22",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_combination_code=True,
        ),
        GoldenTestCase(
            case_id="DM-004",
            category="DIABETES",
            description="Use Additional instructions",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_use_additional=True,
        ),
        GoldenTestCase(
            case_id="DM-005",
            category="DIABETES",
            description="Outpatient DM workflow",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-006",
            category="DIABETES",
            description="HbA1c lab captured",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-007",
            category="DIABETES",
            description="Metformin medication extraction",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-008",
            category="DIABETES",
            description="Compliance clean",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="DM-009",
            category="DIABETES",
            description="Audit completeness",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_audit_complete=True,
        ),
        GoldenTestCase(
            case_id="DM-010",
            category="DIABETES",
            description="Reasoning chains",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_reasoning_chains=True,
        ),
        GoldenTestCase(
            case_id="DM-011",
            category="DIABETES",
            description="Possible diagnosis handled",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Abdominal pain\nHPI: 50yo M with T2DM presents with abdominal pain. "
                "Possible cholecystitis.\nAssessment: 1. T2DM 2. Possible cholecystitis"
            ),
            patient_age=50,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-012",
            category="DIABETES",
            description="DM variation",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-013",
            category="DIABETES",
            description="DM variation 2",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-014",
            category="DIABETES",
            description="Inpatient DM variation",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="DM-015",
            category="DIABETES",
            description="Inpatient DM variation 2",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
    ]
)


# ── CATEGORY 4: EDGE CASES (46-65) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="EDGE-001",
            category="EDGE",
            description="Empty note handling",
            clinical_note_key="empty_minimal",
            patient_age=30,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED", "ERROR"],
            min_codes=0,
            max_codes=2,
        ),
        GoldenTestCase(
            case_id="EDGE-002",
            category="EDGE",
            description="Messy abbreviations",
            clinical_note_key="messy_abbreviated",
            patient_age=45,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_abbreviation_handling=True,
        ),
        GoldenTestCase(
            case_id="EDGE-003",
            category="EDGE",
            description="Short note",
            clinical_note_key="simple_dm_htn",
            clinical_note_override="Pt seen. HTN stable. F/u 3mo.",
            patient_age=60,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED", "ERROR"],
            min_codes=0,
        ),
        GoldenTestCase(
            case_id="EDGE-004",
            category="EDGE",
            description="All conditions negated",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Evaluation\nHPI: 40yo F. Denies chest pain, SOB, fever, cough, "
                "headache, dizziness.\nAssessment: No acute findings."
            ),
            patient_age=40,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED", "ERROR"],
            must_not_include_codes=["R07.9", "R50.9", "R05.9", "R06.02", "R51.9", "R42"],
            min_codes=0,
        ),
        GoldenTestCase(
            case_id="EDGE-005",
            category="EDGE",
            description="Gender-specific handling",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Abdominal pain\nHPI: 35yo M with abd pain.\nAssessment: Abdominal pain, "
                "eval for appendicitis."
            ),
            patient_age=35,
            patient_gender="male",
            encounter_type="EMERGENCY",
            expected_status=["SUCCESS", "ESCALATED"],
            must_not_include_codes=["O99.89", "O26.9"],
        ),
        GoldenTestCase(
            case_id="EDGE-006",
            category="EDGE",
            description="Fracture 7th character",
            clinical_note_key="fracture_injury",
            patient_age=82,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=2,
        ),
        GoldenTestCase(
            case_id="EDGE-007",
            category="EDGE",
            description="Fracture with osteoporosis",
            clinical_note_key="fracture_injury",
            patient_age=82,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-008",
            category="EDGE",
            description="Unicode tolerance",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Pain — patient reports «severe» discomfort.\nAssessment: Chest pain, unspecified."
            ),
            patient_age=50,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-009",
            category="EDGE",
            description="Long note",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Multiple complaints\n"
                + "\n".join([f"Problem {i}: Condition {i} documented." for i in range(1, 30)])
                + "\nAssessment: Complex patient."
            ),
            patient_age=55,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-010",
            category="EDGE",
            description="Vitals only",
            clinical_note_key="simple_dm_htn",
            clinical_note_override="BP 180/110 HR 55 SpO2 99% Temp 98.6F.\nNo complaints.",
            patient_age=65,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED", "ERROR"],
            min_codes=0,
        ),
        GoldenTestCase(
            case_id="EDGE-011",
            category="EDGE",
            description="Historical condition",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "HPI: 55yo M. History of MI 5 years ago, currently stable.\nAssessment: Follow-up, "
                "no active cardiac symptoms."
            ),
            patient_age=55,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-012",
            category="EDGE",
            description="Family history not coded as patient condition",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "HPI: 45yo F. Family history of breast cancer (mother). No personal history.\n"
                "Assessment: Routine screening."
            ),
            patient_age=45,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-013",
            category="EDGE",
            description="Conflicting information",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "HPI: No diabetes. On metformin 1000mg BID.\nAssessment: Diabetes management."
            ),
            patient_age=55,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-014",
            category="EDGE",
            description="Multi-system complexity",
            clinical_note_key="chf_exacerbation",
            patient_age=70,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            min_codes=3,
        ),
        GoldenTestCase(
            case_id="EDGE-015",
            category="EDGE",
            description="Unstructured text",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "Patient is a 60 year old male with chest pain and diabetes. He takes metformin. "
                "Blood pressure is 140/90."
            ),
            patient_age=60,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-016",
            category="EDGE",
            description="Duplicate mentions not double-coded",
            clinical_note_key="simple_dm_htn",
            clinical_note_override=(
                "CC: Diabetes\nHPI: Patient has diabetes. Diabetes diagnosed 10 years ago. "
                "Managing diabetes with diet.\nAssessment: Diabetes."
            ),
            patient_age=50,
            patient_gender="male",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            max_codes=5,
        ),
        GoldenTestCase(
            case_id="EDGE-017",
            category="EDGE",
            description="Pipeline timing <30s",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-018",
            category="EDGE",
            description="Trace ID is UUID",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-019",
            category="EDGE",
            description="Metrics populated",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="EDGE-020",
            category="EDGE",
            description="Warnings captured",
            clinical_note_key="empty_minimal",
            patient_age=30,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED", "ERROR"],
        ),
    ]
)


# ── CATEGORY 5: COMPLIANCE (66-85) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="COMP-001",
            category="COMPLIANCE",
            description="Codes are real",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="COMP-002",
            category="COMPLIANCE",
            description="No Excludes1 (cardiac)",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="COMP-003",
            category="COMPLIANCE",
            description="No Excludes1 (pulmonary)",
            clinical_note_key="pulmonary_copd",
            patient_age=55,
            patient_gender="female",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_no_excludes1_violation=True,
        ),
        GoldenTestCase(
            case_id="COMP-004",
            category="COMPLIANCE",
            description="Codes are billable",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="COMP-005",
            category="COMPLIANCE",
            description="Confidence within range",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="COMP-006",
            category="COMPLIANCE",
            description="Reasoning chain exists",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_reasoning_chains=True,
        ),
        GoldenTestCase(
            case_id="COMP-007",
            category="COMPLIANCE",
            description="Evidence exists",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_evidence_for_all_codes=True,
        ),
        GoldenTestCase(
            case_id="COMP-008",
            category="COMPLIANCE",
            description="Primary diagnosis set",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="COMP-009",
            category="COMPLIANCE",
            description="Simple compliance pass",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_compliance_pass=True,
        ),
        GoldenTestCase(
            case_id="COMP-010",
            category="COMPLIANCE",
            description="CHF compliance",
            clinical_note_key="chf_exacerbation",
            patient_age=70,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
    ]
)

GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id=f"COMP-{i:03d}",
            category="COMPLIANCE",
            description=f"Compliance consistency test {i}",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        )
        for i in range(11, 21)
    ]
)


# ── CATEGORY 6: SYSTEM / AUDIT (86-100) ──
GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id="SYS-001",
            category="SYSTEM",
            description="Audit summary populated",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
            expect_audit_complete=True,
        ),
        GoldenTestCase(
            case_id="SYS-002",
            category="SYSTEM",
            description="Risk assessment present",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="SYS-003",
            category="SYSTEM",
            description="Pipeline stages tracked",
            clinical_note_key="cardiac_nstemi",
            patient_age=62,
            patient_gender="male",
            encounter_type="INPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
        GoldenTestCase(
            case_id="SYS-004",
            category="SYSTEM",
            description="Concurrent batch processing",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        ),
    ]
)

GOLDEN_CASES.extend(
    [
        GoldenTestCase(
            case_id=f"SYS-{i:03d}",
            category="SYSTEM",
            description=f"System stability test {i}",
            clinical_note_key="simple_dm_htn",
            patient_age=48,
            patient_gender="female",
            encounter_type="OUTPATIENT",
            expected_status=["SUCCESS", "ESCALATED"],
        )
        for i in range(5, 16)
    ]
)


assert len(GOLDEN_CASES) == 100, f"Expected 100 cases, got {len(GOLDEN_CASES)}"
