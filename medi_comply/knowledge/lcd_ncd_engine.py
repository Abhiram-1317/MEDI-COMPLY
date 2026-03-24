"""LCD/NCD Coverage Determination Engine.

This module provides an in-memory coverage determination engine for MEDI-COMPLY
that evaluates medical necessity using LCDs (Local Coverage Determinations) and
NCDs (National Coverage Determinations). It mirrors patterns used by the NCCI
engine but focuses on coverage criteria, documentation, and clinical necessity.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CoverageType(str, Enum):
    """Coverage determination type."""

    NCD = "NCD"
    LCD = "LCD"


class CoverageStatus(str, Enum):
    """Outcome of coverage lookup."""

    COVERED = "COVERED"
    NOT_COVERED = "NOT_COVERED"
    CONDITIONAL = "CONDITIONAL"
    NOT_SPECIFIED = "NOT_SPECIFIED"


class MedicalNecessityStatus(str, Enum):
    """Outcome of medical necessity evaluation."""

    MEETS_CRITERIA = "MEETS_CRITERIA"
    PARTIALLY_MEETS = "PARTIALLY_MEETS"
    DOES_NOT_MEET = "DOES_NOT_MEET"
    INSUFFICIENT_DOCUMENTATION = "INSUFFICIENT_DOCUMENTATION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ClinicalCriterion(BaseModel):
    """A clinical rule that should be satisfied for coverage."""

    criterion_id: str
    description: str
    criterion_type: str
    required: bool = True
    value_range: Optional[Dict[str, object]] = None


class FrequencyLimit(BaseModel):
    """Limits on how often a service can be billed."""

    max_units: int
    time_period: str
    description: str


class AgeRestriction(BaseModel):
    """Age-based coverage boundaries."""

    min_age: Optional[int] = None
    max_age: Optional[int] = None
    description: str


class CoverageDetermination(BaseModel):
    """LCD or NCD definition."""

    determination_id: str
    determination_type: CoverageType
    title: str
    effective_date: str
    end_date: Optional[str] = None
    contractor_name: Optional[str] = None
    contractor_number: Optional[str] = None
    applicable_states: Optional[List[str]] = None
    covered_cpt_codes: List[str] = Field(default_factory=list)
    covered_icd10_codes: List[str] = Field(default_factory=list)
    non_covered_icd10_codes: List[str] = Field(default_factory=list)
    documentation_requirements: List[str] = Field(default_factory=list)
    clinical_criteria: List[ClinicalCriterion] = Field(default_factory=list)
    frequency_limits: Optional[FrequencyLimit] = None
    age_restrictions: Optional[AgeRestriction] = None
    gender_restrictions: Optional[str] = None
    additional_notes: List[str] = Field(default_factory=list)
    source_url: Optional[str] = None

    def is_active(self, as_of_date: Optional[str] = None) -> bool:
        """Check if the determination is active on the provided date."""

        if not self.effective_date:
            return True
        as_of = datetime.strptime(as_of_date, "%Y-%m-%d") if as_of_date else datetime.utcnow()
        start = datetime.strptime(self.effective_date, "%Y-%m-%d")
        end = datetime.strptime(self.end_date, "%Y-%m-%d") if self.end_date else None
        if as_of < start:
            return False
        if end and as_of > end:
            return False
        return True


class MedicalNecessityResult(BaseModel):
    """Result of a medical necessity evaluation."""

    procedure_code: str
    diagnosis_codes: List[str]
    determination_used: Optional[CoverageDetermination] = None
    coverage_status: CoverageStatus = CoverageStatus.NOT_SPECIFIED
    medical_necessity_status: MedicalNecessityStatus = MedicalNecessityStatus.NOT_APPLICABLE
    covered_diagnoses_found: List[str] = Field(default_factory=list)
    uncovered_diagnoses: List[str] = Field(default_factory=list)
    non_covered_diagnoses_found: List[str] = Field(default_factory=list)
    met_criteria: List[str] = Field(default_factory=list)
    unmet_criteria: List[str] = Field(default_factory=list)
    missing_documentation: List[str] = Field(default_factory=list)
    frequency_check_passed: Optional[bool] = None
    age_check_passed: Optional[bool] = None
    gender_check_passed: Optional[bool] = None
    recommendations: List[str] = Field(default_factory=list)
    confidence: float = 0.5
    reasoning: str = ""

    @property
    def is_medically_necessary(self) -> bool:
        """Convenience boolean mirroring legacy API expectations."""

        return self.coverage_status not in {CoverageStatus.NOT_COVERED}


class LCDNCDDatabase:
    """In-memory LCD/NCD storage and lookup."""

    def __init__(self) -> None:
        self._determinations: Dict[str, CoverageDetermination] = {}

    def add_determination(self, determination: CoverageDetermination) -> None:
        self._determinations[determination.determination_id] = determination

    def get_determination(self, determination_id: str) -> Optional[CoverageDetermination]:
        return self._determinations.get(determination_id)

    def find_by_cpt(self, cpt_code: str) -> List[CoverageDetermination]:
        code = cpt_code.strip()
        return [d for d in self._determinations.values() if code in d.covered_cpt_codes]

    def find_by_icd10(self, icd10_code: str) -> List[CoverageDetermination]:
        code = icd10_code.upper().strip()
        return [d for d in self._determinations.values() if _match_icd10(code, d.covered_icd10_codes)]

    def find_by_cpt_and_region(self, cpt_code: str, state: Optional[str] = None) -> List[CoverageDetermination]:
        candidates = self.find_by_cpt(cpt_code)
        if state:
            state_upper = state.upper()
            return [d for d in candidates if d.determination_type == CoverageType.NCD or not d.applicable_states or state_upper in d.applicable_states]
        return candidates

    def get_all_ncds(self) -> List[CoverageDetermination]:
        return [d for d in self._determinations.values() if d.determination_type == CoverageType.NCD]

    def get_all_lcds(self) -> List[CoverageDetermination]:
        return [d for d in self._determinations.values() if d.determination_type == CoverageType.LCD]

    def get_active_determinations(self, as_of_date: Optional[str] = None) -> List[CoverageDetermination]:
        return [d for d in self._determinations.values() if d.is_active(as_of_date)]

    def get_determination_count(self) -> Dict[str, int]:
        return {
            CoverageType.NCD.value: len(self.get_all_ncds()),
            CoverageType.LCD.value: len(self.get_all_lcds()),
        }


class LCDNCDEngine:
    """Main LCD/NCD engine for medical necessity evaluation."""

    def __init__(self, database: Optional[LCDNCDDatabase] = None) -> None:
        self.database = database or LCDNCDDatabase()
        if not self.database.get_determination_count():
            seed_lcd_ncd_data(self.database)

    def check_medical_necessity(
        self,
        cpt_code: str,
        icd10_codes: List[str],
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
        state: Optional[str] = None,
        clinical_info: Optional[dict] = None,
    ) -> MedicalNecessityResult:
        """Evaluate coverage and necessity for a CPT/ICD combination."""

        clinical_info = clinical_info or {}
        determinations = self.database.find_by_cpt_and_region(cpt_code, state)
        if not determinations:
            return MedicalNecessityResult(
                procedure_code=cpt_code,
                diagnosis_codes=icd10_codes,
                coverage_status=CoverageStatus.NOT_SPECIFIED,
                medical_necessity_status=MedicalNecessityStatus.NOT_APPLICABLE,
                reasoning="No LCD/NCD found for this CPT code.",
                recommendations=["Verify coverage manually or review payer policy."],
                confidence=0.3,
            )

        best_result: Optional[MedicalNecessityResult] = None
        for determination in determinations:
            result = self._evaluate_determination(
                determination,
                cpt_code,
                icd10_codes,
                patient_age,
                patient_gender,
                clinical_info,
            )
            if not best_result or result.confidence > best_result.confidence:
                best_result = result

        assert best_result is not None
        best_result.reasoning = self.generate_necessity_reasoning(best_result)
        return best_result

    def _evaluate_determination(
        self,
        determination: CoverageDetermination,
        cpt_code: str,
        icd10_codes: List[str],
        patient_age: Optional[int],
        patient_gender: Optional[str],
        clinical_info: dict,
    ) -> MedicalNecessityResult:
        covered_matches = _filter_matching_icd10(icd10_codes, determination.covered_icd10_codes)
        noncovered_matches = _filter_matching_icd10(icd10_codes, determination.non_covered_icd10_codes)
        uncovered = [code for code in icd10_codes if code.upper() not in {c.upper() for c in covered_matches}]

        age_ok = self._check_age(determination, patient_age)
        gender_ok = self._check_gender(determination, patient_gender)
        freq_ok = self._check_frequency(determination, clinical_info)

        met_criteria, unmet_criteria = self._check_clinical_criteria(determination, clinical_info)
        missing_docs = self._check_documentation(determination, clinical_info)

        coverage_status = self._derive_coverage_status(covered_matches, noncovered_matches, met_criteria, unmet_criteria, missing_docs)
        necessity_status = self._derive_necessity_status(coverage_status, unmet_criteria, missing_docs, age_ok, gender_ok, freq_ok)

        recommendations = self._build_recommendations(
            determination,
            coverage_status,
            necessity_status,
            uncovered,
            unmet_criteria,
            missing_docs,
            noncovered_matches,
            age_ok,
            gender_ok,
            freq_ok,
        )

        confidence = self._estimate_confidence(coverage_status, necessity_status, covered_matches, noncovered_matches)

        return MedicalNecessityResult(
            procedure_code=cpt_code,
            diagnosis_codes=icd10_codes,
            determination_used=determination,
            coverage_status=coverage_status,
            medical_necessity_status=necessity_status,
            covered_diagnoses_found=covered_matches,
            uncovered_diagnoses=uncovered,
            non_covered_diagnoses_found=noncovered_matches,
            met_criteria=met_criteria,
            unmet_criteria=unmet_criteria,
            missing_documentation=missing_docs,
            frequency_check_passed=freq_ok,
            age_check_passed=age_ok,
            gender_check_passed=gender_ok,
            recommendations=recommendations,
            confidence=confidence,
        )

    def get_covered_diagnoses(self, cpt_code: str, state: Optional[str] = None) -> List[str]:
        determinations = self.database.find_by_cpt_and_region(cpt_code, state)
        codes: List[str] = []
        for det in determinations:
            codes.extend(det.covered_icd10_codes)
        return sorted(set(codes))

    def get_required_documentation(self, cpt_code: str) -> List[str]:
        docs: List[str] = []
        for det in self.database.find_by_cpt(cpt_code):
            docs.extend(det.documentation_requirements)
        return sorted(set(docs))

    def get_clinical_criteria(self, cpt_code: str) -> List[ClinicalCriterion]:
        criteria: List[ClinicalCriterion] = []
        for det in self.database.find_by_cpt(cpt_code):
            criteria.extend(det.clinical_criteria)
        seen = set()
        unique: List[ClinicalCriterion] = []
        for crit in criteria:
            if crit.criterion_id not in seen:
                seen.add(crit.criterion_id)
                unique.append(crit)
        return unique

    def is_procedure_covered(self, cpt_code: str, icd10_code: str, state: Optional[str] = None) -> bool:
        determinations = self.database.find_by_cpt_and_region(cpt_code, state)
        for det in determinations:
            if _code_matches(icd10_code, det.covered_icd10_codes):
                return True
        return False

    def get_frequency_limit(self, cpt_code: str) -> Optional[FrequencyLimit]:
        for det in self.database.find_by_cpt(cpt_code):
            if det.frequency_limits:
                return det.frequency_limits
        return None

    def generate_necessity_reasoning(self, result: MedicalNecessityResult) -> str:
        determination_id = result.determination_used.determination_id if result.determination_used else "None"
        parts = [
            f"Determination: {determination_id}",
            f"Coverage status: {result.coverage_status}",
            f"Necessity status: {result.medical_necessity_status}",
        ]
        if result.covered_diagnoses_found:
            parts.append(f"Covered diagnoses present: {', '.join(result.covered_diagnoses_found)}")
        if result.non_covered_diagnoses_found:
            parts.append(f"Non-covered diagnoses present: {', '.join(result.non_covered_diagnoses_found)}")
        if result.unmet_criteria:
            parts.append(f"Unmet criteria: {', '.join(result.unmet_criteria)}")
        if result.missing_documentation:
            parts.append(f"Missing documentation: {', '.join(result.missing_documentation)}")
        if result.recommendations:
            parts.append(f"Recommendations: {', '.join(result.recommendations)}")
        return "; ".join(parts)

    def suggest_covered_alternatives(self, cpt_code: str, icd10_codes: List[str]) -> List[Dict[str, object]]:
        alternatives: List[Dict[str, object]] = []
        determinations = self.database.find_by_cpt(cpt_code)
        for det in determinations:
            for code in det.covered_icd10_codes:
                if not _code_matches_any(code, icd10_codes):
                    alternatives.append({
                        "icd10_code": code,
                        "description": "Covered diagnosis from LCD/NCD",
                        "relevance": 0.7,
                    })
        # Deduplicate
        seen = set()
        uniq: List[Dict[str, object]] = []
        for alt in alternatives:
            code_raw = alt.get("icd10_code")
            if not code_raw:
                continue
            code = str(code_raw).upper()
            if code not in seen:
                seen.add(code)
                uniq.append(alt)
        return uniq

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_age(self, determination: CoverageDetermination, patient_age: Optional[int]) -> Optional[bool]:
        if not determination.age_restrictions or patient_age is None:
            return None
        min_age = determination.age_restrictions.min_age
        max_age = determination.age_restrictions.max_age
        if min_age is not None and patient_age < min_age:
            return False
        if max_age is not None and patient_age > max_age:
            return False
        return True

    def _check_gender(self, determination: CoverageDetermination, gender: Optional[str]) -> Optional[bool]:
        if not determination.gender_restrictions or not gender:
            return None
        return gender.upper().startswith(determination.gender_restrictions.upper())

    def _check_frequency(self, determination: CoverageDetermination, clinical_info: dict) -> Optional[bool]:
        if not determination.frequency_limits:
            return None
        used = clinical_info.get("frequency_used") or clinical_info.get("times_performed")
        if used is None:
            return None
        try:
            used_int = int(used)
        except (TypeError, ValueError):
            return None
        return used_int <= determination.frequency_limits.max_units

    def _check_clinical_criteria(self, determination: CoverageDetermination, clinical_info: dict) -> tuple[List[str], List[str]]:
        if not clinical_info:
            return [], []
        met: List[str] = []
        unmet: List[str] = []
        provided_ids = set(clinical_info.get("met_criteria", []))
        evidence = clinical_info.get("evidence", {})
        for criterion in determination.clinical_criteria:
            if criterion.criterion_id in provided_ids:
                met.append(criterion.criterion_id)
                continue
            # Basic heuristic by type
            if criterion.criterion_type in evidence:
                met.append(criterion.criterion_id)
                continue
            if criterion.required:
                unmet.append(criterion.criterion_id)
        return met, unmet

    def _check_documentation(self, determination: CoverageDetermination, clinical_info: dict) -> List[str]:
        if "documentation" not in clinical_info:
            return []
        provided_docs = set(doc.lower() for doc in clinical_info.get("documentation", []))
        missing: List[str] = []
        for requirement in determination.documentation_requirements:
            if requirement.lower() not in provided_docs:
                missing.append(requirement)
        return missing

    def _derive_coverage_status(
        self,
        covered_matches: List[str],
        noncovered_matches: List[str],
        met_criteria: List[str],
        unmet_criteria: List[str],
        missing_docs: List[str],
    ) -> CoverageStatus:
        if covered_matches and not noncovered_matches and not unmet_criteria and not missing_docs:
            return CoverageStatus.COVERED
        if noncovered_matches:
            return CoverageStatus.NOT_COVERED
        if covered_matches:
            return CoverageStatus.CONDITIONAL
        return CoverageStatus.NOT_SPECIFIED

    def _derive_necessity_status(
        self,
        coverage_status: CoverageStatus,
        unmet_criteria: List[str],
        missing_docs: List[str],
        age_ok: Optional[bool],
        gender_ok: Optional[bool],
        freq_ok: Optional[bool],
    ) -> MedicalNecessityStatus:
        blockers = [age_ok is False, gender_ok is False, freq_ok is False]
        if coverage_status == CoverageStatus.NOT_SPECIFIED:
            return MedicalNecessityStatus.NOT_APPLICABLE
        if any(blockers) or coverage_status == CoverageStatus.NOT_COVERED:
            return MedicalNecessityStatus.DOES_NOT_MEET
        if missing_docs or unmet_criteria:
            return MedicalNecessityStatus.PARTIALLY_MEETS
        if coverage_status == CoverageStatus.COVERED:
            return MedicalNecessityStatus.MEETS_CRITERIA
        return MedicalNecessityStatus.PARTIALLY_MEETS

    def _build_recommendations(
        self,
        determination: CoverageDetermination,
        coverage_status: CoverageStatus,
        necessity_status: MedicalNecessityStatus,
        uncovered: List[str],
        unmet_criteria: List[str],
        missing_docs: List[str],
        noncovered_matches: List[str],
        age_ok: Optional[bool],
        gender_ok: Optional[bool],
        freq_ok: Optional[bool],
    ) -> List[str]:
        recs: List[str] = []
        if coverage_status in {CoverageStatus.NOT_SPECIFIED, CoverageStatus.NOT_COVERED}:
            recs.append("Verify payer policy or consider alternative covered diagnoses if clinically appropriate.")
        if uncovered:
            recs.append("Consider adding clinically appropriate covered ICD-10 codes listed in the LCD/NCD.")
        if noncovered_matches:
            recs.append("Remove non-covered ICD-10 codes if they are not clinically required.")
        if unmet_criteria:
            recs.extend([f"Document and meet clinical criterion {cid}." for cid in unmet_criteria])
        if missing_docs:
            recs.extend([f"Include documentation: {doc}." for doc in missing_docs])
        if age_ok is False:
            recs.append("Patient age outside allowed range; confirm indication or seek exception.")
        if gender_ok is False:
            recs.append("Patient gender does not meet restriction for this policy.")
        if freq_ok is False:
            recs.append("Service exceeds frequency limits; delay or document medical necessity for additional units.")
        if not recs:
            recs.append("No additional recommendations. Criteria satisfied.")
        return recs

    def _estimate_confidence(
        self,
        coverage_status: CoverageStatus,
        necessity_status: MedicalNecessityStatus,
        covered_matches: List[str],
        noncovered_matches: List[str],
    ) -> float:
        score = 0.5
        if coverage_status == CoverageStatus.COVERED and necessity_status == MedicalNecessityStatus.MEETS_CRITERIA:
            score = 0.9
        elif coverage_status == CoverageStatus.CONDITIONAL:
            score = 0.7
        elif coverage_status == CoverageStatus.NOT_COVERED or noncovered_matches:
            score = 0.2
        elif coverage_status == CoverageStatus.NOT_SPECIFIED:
            score = 0.3
        if covered_matches:
            score += 0.05
        return min(1.0, max(0.0, score))


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _code_matches(code: str, patterns: List[str]) -> bool:
    code_upper = code.upper()
    for pattern in patterns:
        if _single_code_match(code_upper, pattern.upper()):
            return True
    return False


def _code_matches_any(pattern: str, codes: List[str]) -> bool:
    pat_upper = pattern.upper()
    return any(_single_code_match(code.upper(), pat_upper) for code in codes)


def _single_code_match(code: str, pattern: str) -> bool:
    # Supports wildcard patterns like "M80.-" or trailing "*" prefix.
    if pattern.endswith("-"):
        prefix = pattern[:-1]
        return code.startswith(prefix)
    if pattern.endswith("*"):
        prefix = pattern[:-1]
        return code.startswith(prefix)
    return code == pattern


def _match_icd10(code: str, patterns: List[str]) -> bool:
    return _code_matches(code, patterns)


def _filter_matching_icd10(codes: List[str], patterns: List[str]) -> List[str]:
    matches: List[str] = []
    for code in codes:
        if _match_icd10(code, patterns):
            matches.append(code)
    return matches


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


def seed_lcd_ncd_data(database: LCDNCDDatabase) -> None:
    """Populate the in-memory database with sample LCD/NCD entries."""

    determinations: List[CoverageDetermination] = [
        CoverageDetermination(
            determination_id="NCD-220.6",
            determination_type=CoverageType.NCD,
            title="NCD for PET Scans",
            effective_date="2018-01-01",
            covered_cpt_codes=["78815", "78816", "78814"],
            covered_icd10_codes=["C34.-", "C50.-", "C18.-", "C61", "C78.-"],
            non_covered_icd10_codes=["Z13.89"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="PET-INDICATION", description="Oncology indication present", criterion_type="DIAGNOSIS", required=True),
            ],
            documentation_requirements=["Imaging order", "Oncologist note"],
            additional_notes=["Requires evidence of malignancy"],
            source_url="https://www.cms.gov/medicare-coverage-database/details/ncd-details.aspx?NCDId=211",
        ),
        CoverageDetermination(
            determination_id="NCD-190.1",
            determination_type=CoverageType.NCD,
            title="NCD for Prostate Cancer Screening",
            effective_date="2017-01-01",
            covered_cpt_codes=["G0102", "G0103", "77057"],
            covered_icd10_codes=["Z12.5"],
            gender_restrictions="M",
            age_restrictions=AgeRestriction(min_age=50, max_age=75, description="Men 50-75"),
            documentation_requirements=["Shared decision making note"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="PROSTATE-SCREEN", description="Asymptomatic screening", criterion_type="SCREENING", required=False),
            ],
        ),
        CoverageDetermination(
            determination_id="NCD-210.7",
            determination_type=CoverageType.NCD,
            title="NCD for MRI of the Brain",
            effective_date="2016-01-01",
            covered_cpt_codes=["70551", "70552", "70553"],
            covered_icd10_codes=["G35", "G45.-", "I63.-", "R90.82"],
            documentation_requirements=["Neurologist note", "Prior imaging report"],
        ),
        CoverageDetermination(
            determination_id="NCD-220.1",
            determination_type=CoverageType.NCD,
            title="NCD for CT Scans",
            effective_date="2015-01-01",
            covered_cpt_codes=["71260", "71270", "70450", "70486"],
            covered_icd10_codes=["R91.1", "I63.-", "R51", "C79.-"],
            documentation_requirements=["CT order"],
        ),
        CoverageDetermination(
            determination_id="NCD-190.32",
            determination_type=CoverageType.NCD,
            title="NCD for Lung Cancer Screening",
            effective_date="2021-01-01",
            covered_cpt_codes=["G0297", "71271"],
            covered_icd10_codes=["Z87.891", "F17.210", "Z12.2"],
            age_restrictions=AgeRestriction(min_age=50, max_age=80, description="High-risk age range"),
            documentation_requirements=["Smoking history", "Shared decision making"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="PACK-YEARS", description=">=20 pack-year smoking history", criterion_type="HISTORY", required=True),
            ],
        ),
        CoverageDetermination(
            determination_id="LCD-L33831",
            determination_type=CoverageType.LCD,
            title="LCD for Vitamin D Testing",
            effective_date="2019-01-01",
            contractor_name="Palmetto GBA",
            contractor_number="11301",
            applicable_states=["NC", "SC", "VA", "WV"],
            covered_cpt_codes=["82306"],
            covered_icd10_codes=["E55.0", "E55.9", "M80.-", "M81.-", "N18.-", "K90.-"],
            frequency_limits=FrequencyLimit(max_units=1, time_period="per_year", description="Once per 12 months unless abnormal"),
            documentation_requirements=["Signs/symptoms or risk factors"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="VITD-SYMPTOMS", description="Symptoms or risk factors documented", criterion_type="SYMPTOM", required=True),
            ],
        ),
        CoverageDetermination(
            determination_id="LCD-L35396",
            determination_type=CoverageType.LCD,
            title="LCD for Advanced Musculoskeletal Imaging",
            effective_date="2018-06-01",
            covered_cpt_codes=["73221", "73721", "72141"],
            covered_icd10_codes=["M84.-", "D16.-", "M86.-"],
            documentation_requirements=["Failed 6 weeks conservative treatment"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="MSK-FAILED-CONSERVATIVE", description="6 weeks conservative treatment failure", criterion_type="PRIOR_TREATMENT", required=True),
            ],
        ),
        CoverageDetermination(
            determination_id="LCD-L36256",
            determination_type=CoverageType.LCD,
            title="LCD for Cardiac Catheterization",
            effective_date="2017-03-01",
            covered_cpt_codes=["93458", "93459", "93460"],
            covered_icd10_codes=["I20.-", "I21.-", "I25.-", "R07.9"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="STRESS-TEST", description="Positive stress test or acute MI presentation", criterion_type="IMAGING", required=True),
            ],
            documentation_requirements=["Stress test results or acute presentation documentation"],
        ),
        CoverageDetermination(
            determination_id="LCD-L33560",
            determination_type=CoverageType.LCD,
            title="LCD for Nerve Conduction Studies",
            effective_date="2016-05-01",
            covered_cpt_codes=["95907", "95908", "95909", "95910"],
            covered_icd10_codes=["G56.-", "G57.-", "G62.-", "M54.1"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="NEURO-DURATION", description=">3 months symptoms", criterion_type="DURATION", required=True),
                ClinicalCriterion(criterion_id="NEURO-FAILED-CONSERVATIVE", description="Failed conservative treatment", criterion_type="PRIOR_TREATMENT", required=True),
            ],
            frequency_limits=FrequencyLimit(max_units=2, time_period="per_episode", description="Per extremity per diagnosis"),
            documentation_requirements=["Neurologic exam findings"],
        ),
        CoverageDetermination(
            determination_id="LCD-L33637",
            determination_type=CoverageType.LCD,
            title="LCD for Allergy Testing",
            effective_date="2015-09-01",
            covered_cpt_codes=["95004", "95024"],
            covered_icd10_codes=["J30.-", "J45.-", "L23.-", "L50.-"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="ALLERGY-SYMPTOMS", description="Documented allergic symptoms", criterion_type="SYMPTOM", required=True),
            ],
            frequency_limits=FrequencyLimit(max_units=1, time_period="per_year", description="Once per allergen category per year"),
        ),
        CoverageDetermination(
            determination_id="LCD-L33829",
            determination_type=CoverageType.LCD,
            title="LCD for Sleep Studies",
            effective_date="2017-02-01",
            covered_cpt_codes=["95810", "95811"],
            covered_icd10_codes=["G47.3", "R06.83", "E66.-"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="ESS>10", description="Epworth Sleepiness Scale > 10", criterion_type="SCORE", required=True),
                ClinicalCriterion(criterion_id="BMI>35", description="BMI > 35", criterion_type="VITAL", required=False),
                ClinicalCriterion(criterion_id="WITNESSED-APNEA", description="Witnessed apneas", criterion_type="SYMPTOM", required=False),
            ],
            documentation_requirements=["Epworth score"],
        ),
        CoverageDetermination(
            determination_id="LCD-L33768",
            determination_type=CoverageType.LCD,
            title="LCD for Physical Therapy",
            effective_date="2016-11-01",
            covered_cpt_codes=["97110", "97112", "97116"],
            covered_icd10_codes=["M54.-", "S72.-", "S82.-", "M17.-", "M16.-"],
            frequency_limits=FrequencyLimit(max_units=12, time_period="per_episode", description="Up to 12 visits per episode"),
            documentation_requirements=["Functional limitation documentation"],
        ),
        CoverageDetermination(
            determination_id="LCD-L34519",
            determination_type=CoverageType.LCD,
            title="LCD for Genetic Testing",
            effective_date="2018-08-01",
            covered_cpt_codes=["81211", "81213", "81432"],
            covered_icd10_codes=["Z15.-", "Z80.-", "C50.-", "C56.-"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="FAMILY-HX", description="Family history meeting criteria", criterion_type="HISTORY", required=True),
                ClinicalCriterion(criterion_id="ACTIVE-CANCER", description="Active cancer", criterion_type="DIAGNOSIS", required=False),
            ],
            age_restrictions=AgeRestriction(min_age=18, max_age=None, description="Adults 18+"),
            documentation_requirements=["Family history documentation"],
        ),
        CoverageDetermination(
            determination_id="LCD-L34998",
            determination_type=CoverageType.LCD,
            title="LCD for Cardiac Rehab",
            effective_date="2019-04-01",
            covered_cpt_codes=["93797", "93798"],
            covered_icd10_codes=["I21.-", "I22.-", "Z95.1", "Z98.61"],
            documentation_requirements=["Cardiac event documentation"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="RECENT-MI", description="Recent MI or surgery", criterion_type="EVENT", required=True),
            ],
        ),
        CoverageDetermination(
            determination_id="LCD-L35020",
            determination_type=CoverageType.LCD,
            title="LCD for Diabetic Eye Exam",
            effective_date="2015-04-01",
            covered_cpt_codes=["92250", "92228"],
            covered_icd10_codes=["E11.-", "E10.-", "E13.-"],
            frequency_limits=FrequencyLimit(max_units=1, time_period="per_year", description="Annual exam"),
            documentation_requirements=["Diabetes diagnosis documentation"],
        ),
        CoverageDetermination(
            determination_id="LCD-L35125",
            determination_type=CoverageType.LCD,
            title="LCD for Chronic Kidney Disease Monitoring",
            effective_date="2020-01-01",
            covered_cpt_codes=["80069", "82570"],
            covered_icd10_codes=["N18.-", "E11.22", "I12.0"],
            frequency_limits=FrequencyLimit(max_units=4, time_period="per_year", description="Quarterly"),
            documentation_requirements=["CKD staging"],
        ),
        CoverageDetermination(
            determination_id="LCD-L36001",
            determination_type=CoverageType.LCD,
            title="LCD for Oncology Supportive Care",
            effective_date="2021-01-01",
            covered_cpt_codes=["96372", "96375"],
            covered_icd10_codes=["C50.-", "C18.-", "C34.-", "C90.-"],
            documentation_requirements=["Chemotherapy plan"],
            clinical_criteria=[
                ClinicalCriterion(criterion_id="CHEMO-ACTIVE", description="Active chemotherapy", criterion_type="TREATMENT", required=True),
            ],
        ),
    ]

    for det in determinations:
        database.add_determination(det)


__all__ = [
    "CoverageType",
    "CoverageStatus",
    "MedicalNecessityStatus",
    "ClinicalCriterion",
    "FrequencyLimit",
    "AgeRestriction",
    "CoverageDetermination",
    "MedicalNecessityResult",
    "LCDNCDDatabase",
    "LCDNCDEngine",
    "seed_lcd_ncd_data",
]
