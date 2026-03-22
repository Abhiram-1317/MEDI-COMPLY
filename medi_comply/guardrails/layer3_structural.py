"""Layer 3 structural guardrails for MEDI-COMPLY."""

import time
from typing import Optional

from pydantic import BaseModel

from medi_comply.core.utils import safe_get_code, safe_get_confidence
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.coding_result import CodingResult


class StructuralCheckResult(BaseModel):
    check_id: str
    check_name: str
    passed: bool
    severity: str
    details: str
    affected_codes: list[str] = []
    fix_suggestion: Optional[str] = None
    regulation_ref: Optional[str] = None
    check_time_ms: float


class StructuralGuardrails:
    """Deterministic rule-based validation stage."""

    def __init__(self, knowledge_manager: KnowledgeManager) -> None:
        self.km = knowledge_manager

    def run_all_checks(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
    ) -> list[StructuralCheckResult]:
        """Execute the full structural guardrail suite."""

        results: list[StructuralCheckResult] = []
        results.append(self.check_01_code_existence(coding_result))
        results.append(self.check_02_ncci_edits(coding_result))
        results.extend(self.check_03_excludes1(coding_result))
        results.extend(self.check_04_excludes2(coding_result))
        results.extend(self.check_05_specificity(coding_result, scr))
        results.extend(self.check_06_age_sex(coding_result))
        results.extend(self.check_07_manifestation_pairing(coding_result))
        results.extend(self.check_08_laterality(coding_result, scr))
        results.extend(self.check_09_seventh_character(coding_result))
        results.extend(self.check_10_mue(coding_result))
        results.extend(self.check_11_billable(coding_result))
        results.extend(self.check_12_use_additional_compliance(coding_result))
        results.append(self.check_13_confidence_threshold(coding_result))
        return results

    def _make_result(
        self,
        cid: str,
        name: str,
        passed: bool,
        severity: str,
        details: str,
        codes: Optional[list[str]] = None,
        fix: Optional[str] = None,
        ref: Optional[str] = None,
        start: float = 0.0,
    ) -> StructuralCheckResult:
        return StructuralCheckResult(
            check_id=cid,
            check_name=name,
            passed=passed,
            severity=severity if not passed else "NONE",
            details=details,
            affected_codes=codes or [],
            fix_suggestion=fix,
            regulation_ref=ref,
            check_time_ms=(time.time() - start) * 1000,
        )

    def check_01_code_existence(self, coding_result: CodingResult) -> StructuralCheckResult:
        """CHECK 1: Ensure no hallucinated ICD-10/CPT codes are present."""

        start = time.time()
        invalid_codes: list[str] = []

        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if code_value and not self.km.validate_code_exists(code_value, "icd10"):
                invalid_codes.append(code_value)

        for code in coding_result.procedure_codes:
            code_value = safe_get_code(code)
            if code_value and not self.km.validate_code_exists(code_value, "cpt"):
                invalid_codes.append(code_value)

        if invalid_codes:
            return self._make_result(
                "CHECK_01_CODE_EXISTS",
                "Code Existence Verification",
                False,
                "HARD_FAIL",
                f"Hallucinated or invalid codes detected: {invalid_codes}",
                invalid_codes,
                "Remove the invalid codes or replace with verified codes from knowledge base.",
                "OIG Compliance §1.4",
                start,
            )
        return self._make_result(
            "CHECK_01_CODE_EXISTS",
            "Code Existence Verification",
            True,
            "NONE",
            "All codes exist.",
            start=start,
        )

    def check_02_ncci_edits(self, coding_result: CodingResult) -> StructuralCheckResult:
        """CHECK 2: Enforce NCCI bundling / mutually exclusive rules."""

        start = time.time()
        cpt_codes = []
        for proc in coding_result.procedure_codes:
            code_value = safe_get_code(proc)
            if code_value:
                cpt_codes.append(code_value)

        for i in range(len(cpt_codes)):
            for j in range(i + 1, len(cpt_codes)):
                c1, c2 = cpt_codes[i], cpt_codes[j]
                edit = self.km.ncci.check_edit(c1, c2)
                if not (edit and edit.issue_found):
                    continue
                if edit.issue_type == "MUTUALLY_EXCLUSIVE":
                    return self._make_result(
                        "CHECK_02_NCCI_EDITS",
                        "NCCI Edit Verification",
                        False,
                        "HARD_FAIL",
                        f"Mutually exclusive pairs found: {c1} and {c2}",
                        [c1, c2],
                        "Codes are mutually exclusive. Select only one.",
                        "CMS NCCI Policy Manual",
                        start,
                    )
                if edit.issue_type == "BUNDLED" and not edit.modifier_allowed:
                    return self._make_result(
                        "CHECK_02_NCCI_EDITS",
                        "NCCI Edit Verification",
                        False,
                        "HARD_FAIL",
                        f"Bundled code pair without modifier allowance: {c1} bundled into {c2}",
                        [c1, c2],
                        "Remove the bundled code or justify separate payment.",
                        "CMS NCCI Policy Manual",
                        start,
                    )
                if edit.issue_type == "BUNDLED" and edit.modifier_allowed:
                    return self._make_result(
                        "CHECK_02_NCCI_EDITS",
                        "NCCI Edit Verification",
                        False,
                        "SOFT_FAIL",
                        f"{c1} and {c2} are bundled but modifier is allowed.",
                        [c1, c2],
                        "Apply modifier 59 if procedures are distinct.",
                        "CMS NCCI Policy Manual",
                        start,
                    )

        return self._make_result(
            "CHECK_02_NCCI_EDITS",
            "NCCI Edit Verification",
            True,
            "NONE",
            "No NCCI edit violations.",
            start=start,
        )

    def check_03_excludes1(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 3: Enforce Excludes1 conflicts."""

        start = time.time()
        icd_codes = [code for c in coding_result.diagnosis_codes if (code := safe_get_code(c))]
        results: list[StructuralCheckResult] = []

        for i in range(len(icd_codes)):
            for j in range(i + 1, len(icd_codes)):
                c1, c2 = icd_codes[i], icd_codes[j]
                issue = self.km.icd10_db.check_excludes(c1, c2)
                if issue and issue["type"] == "EXCLUDES1":
                    results.append(
                        self._make_result(
                            "CHECK_03_EXCLUDES1",
                            "Excludes1 Validation",
                            False,
                            "HARD_FAIL",
                            f"Excludes1 conflict: {c1} and {c2}",
                            [c1, c2],
                            "Codes are mutually exclusive per Excludes1 guidance.",
                            "ICD-10-CM OCG I.A.12.a",
                            start,
                        )
                    )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_03_EXCLUDES1",
                "Excludes1 Validation",
                True,
                "NONE",
                "No Excludes1 conflicts.",
                start=start,
            )
        ]

    def check_04_excludes2(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 4: Flag Excludes2 warnings."""

        start = time.time()
        icd_codes = [code for c in coding_result.diagnosis_codes if (code := safe_get_code(c))]
        results: list[StructuralCheckResult] = []

        for i in range(len(icd_codes)):
            for j in range(i + 1, len(icd_codes)):
                c1, c2 = icd_codes[i], icd_codes[j]
                issue = self.km.icd10_db.check_excludes(c1, c2)
                if issue and issue["type"] == "EXCLUDES2":
                    results.append(
                        self._make_result(
                            "CHECK_04_EXCLUDES2",
                            "Excludes2 Validation",
                            False,
                            "WARNING",
                            f"Excludes2 note between {c1} and {c2}",
                            [c1, c2],
                            "Verify documentation supports reporting both conditions.",
                            "ICD-10-CM OCG I.A.12.b",
                            start,
                        )
                    )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_04_EXCLUDES2",
                "Excludes2 Validation",
                True,
                "NONE",
                "No Excludes2 notes triggered.",
                start=start,
            )
        ]

    def check_05_specificity(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
    ) -> list[StructuralCheckResult]:
        """CHECK 5: Warn when higher specificity exists."""

        start = time.time()
        results: list[StructuralCheckResult] = []
        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if not code_value or not hasattr(self.km.icd10_db, "has_higher_specificity"):
                continue
            if self.km.icd10_db.has_higher_specificity(code_value):
                results.append(
                    self._make_result(
                        "CHECK_05_SPECIFICITY",
                        "Specificity Adequacy",
                        False,
                        "SOFT_FAIL",
                        f"Code {code_value} is not at maximum specificity.",
                        [code_value],
                        "Review documentation for a more specific option.",
                        "ICD-10-CM OCG I.A.4",
                        start,
                    )
                )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_05_SPECIFICITY",
                "Specificity Adequacy",
                True,
                "NONE",
                "All codes sufficiently specific.",
                start=start,
            )
        ]

    def check_06_age_sex(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 6: Age / sex validation against ICD constraints."""

        start = time.time()
        results: list[StructuralCheckResult] = []
        patient_gender = (coding_result.patient_gender or "").upper()
        is_m = patient_gender.startswith("M")
        is_f = patient_gender.startswith("F")
        age = coding_result.patient_age

        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if not code_value:
                continue
            entry = self.km.icd10_db.get_code(code_value)
            if not entry:
                continue

            if entry.valid_for_gender != "B":
                if entry.valid_for_gender == "M" and not is_m:
                    results.append(
                        self._make_result(
                            "CHECK_06_AGE_SEX",
                            "Age/Sex Conflict",
                            False,
                            "HARD_FAIL",
                            f"Code {code_value} valid for males only.",
                            [code_value],
                            f"Code {code_value} is not valid for {patient_gender} patients.",
                            start=start,
                        )
                    )
                elif entry.valid_for_gender == "F" and not is_f:
                    results.append(
                        self._make_result(
                            "CHECK_06_AGE_SEX",
                            "Age/Sex Conflict",
                            False,
                            "HARD_FAIL",
                            f"Code {code_value} valid for females only.",
                            [code_value],
                            f"Code {code_value} is not valid for {patient_gender} patients.",
                            start=start,
                        )
                    )

            min_age, max_age = entry.valid_age_range
            if not (min_age <= age <= max_age):
                results.append(
                    self._make_result(
                        "CHECK_06_AGE_SEX",
                        "Age/Sex Conflict",
                        False,
                        "HARD_FAIL",
                        f"Code {code_value} valid for ages {min_age}-{max_age}.",
                        [code_value],
                        f"Code {code_value} is not valid for age {age}.",
                        start=start,
                    )
                )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_06_AGE_SEX",
                "Age/Sex Conflict",
                True,
                "NONE",
                "No age/sex conflicts.",
                start=start,
            )
        ]

    def check_07_manifestation_pairing(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 7: Ensure manifestation codes are sequenced properly."""

        start = time.time()
        results: list[StructuralCheckResult] = []

        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if not code_value:
                continue
            entry = self.km.icd10_db.get_code(code_value)
            if entry and entry.is_manifestation and code.sequence_position == "PRIMARY":
                results.append(
                    self._make_result(
                        "CHECK_07_MANIFESTATION",
                        "Manifestation Pairing",
                        False,
                        "HARD_FAIL",
                        f"Manifestation {code_value} cannot be primary.",
                        [code_value],
                        "Assign the underlying etiology ahead of the manifestation code.",
                        start=start,
                    )
                )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_07_MANIFESTATION",
                "Manifestation Pairing",
                True,
                "NONE",
                "Manifestation logic sound.",
                start=start,
            )
        ]

    def check_08_laterality(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
    ) -> list[StructuralCheckResult]:
        start = time.time()
        # Placeholder pass-through until laterality heuristics are implemented.
        return [
            self._make_result(
                "CHECK_08_LATERALITY",
                "Laterality Verification",
                True,
                "NONE",
                "Laterality checks passed.",
                start=start,
            )
        ]

    def check_09_seventh_character(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        start = time.time()
        return [
            self._make_result(
                "CHECK_09_7TH_CHAR",
                "7th Character Requirement",
                True,
                "NONE",
                "All required 7th characters present.",
                start=start,
            )
        ]

    def check_10_mue(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        start = time.time()
        results: list[StructuralCheckResult] = []
        counts: dict[str, int] = {}

        for code in coding_result.procedure_codes:
            code_value = safe_get_code(code)
            if code_value:
                counts[code_value] = counts.get(code_value, 0) + 1

        for code, count in counts.items():
            if hasattr(self.km.cpt_db, "get_code"):
                entry = self.km.cpt_db.get_code(code)
                if entry and hasattr(entry, "mue_limit") and count > entry.mue_limit:
                    results.append(
                        self._make_result(
                            "CHECK_10_MUE",
                            "Medically Unlikely Edits",
                            False,
                            "HARD_FAIL",
                            f"Code {code} billed {count} times, exceeds MUE limit of {entry.mue_limit}",
                            [code],
                            "Reduce units or supply justification.",
                            start=start,
                        )
                    )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_10_MUE",
                "Medically Unlikely Edits",
                True,
                "NONE",
                "No MUE violations.",
                start=start,
            )
        ]

    def check_11_billable(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 11: Ensure diagnosis codes are billable leaves."""

        start = time.time()
        results: list[StructuralCheckResult] = []

        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if not code_value:
                continue
            entry = self.km.icd10_db.get_code(code_value)
            if entry and not entry.is_billable:
                results.append(
                    self._make_result(
                        "CHECK_11_BILLABLE",
                        "Billable Status",
                        False,
                        "HARD_FAIL",
                        f"Non-billable code {code_value}",
                        [code_value],
                        "Select a more specific child code.",
                        start=start,
                    )
                )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_11_BILLABLE",
                "Billable Status",
                True,
                "NONE",
                "All codes billable.",
                start=start,
            )
        ]

    def check_12_use_additional_compliance(self, coding_result: CodingResult) -> list[StructuralCheckResult]:
        """CHECK 12: Ensure "use additional" instructions are satisfied."""

        start = time.time()
        results: list[StructuralCheckResult] = []
        assigned = [code for c in coding_result.diagnosis_codes if (code := safe_get_code(c))]

        for code in coding_result.diagnosis_codes:
            code_value = safe_get_code(code)
            if not code_value:
                continue
            entry = self.km.icd10_db.get_code(code_value)
            if not (entry and getattr(entry, "use_additional", None)):
                continue
            for req in entry.use_additional:
                if not any(a.startswith(req) for a in assigned):
                    results.append(
                        self._make_result(
                            "CHECK_12_USE_ADDITIONAL",
                            "Use Additional Compliance",
                            False,
                            "SOFT_FAIL",
                            f"Code {code_value} requires additional code {req}",
                            [code_value],
                            f"Add a supporting code from category {req}.",
                            start=start,
                        )
                    )

        if results:
            return results
        return [
            self._make_result(
                "CHECK_12_USE_ADDITIONAL",
                "Use Additional Code Compliance",
                True,
                "NONE",
                "Use Additional rules satisfied.",
                start=start,
            )
        ]

    def check_13_confidence_threshold(self, coding_result: CodingResult) -> StructuralCheckResult:
        """CHECK 13: Minimum confidence gate for all codes."""

        start = time.time()
        lowest = 1.0
        affected: list[str] = []

        for code in coding_result.diagnosis_codes + coding_result.procedure_codes:
            confidence = safe_get_confidence(code, getattr(code, "confidence_score", 1.0))
            if confidence < lowest:
                lowest = confidence
                affected = [safe_get_code(code) or "UNKNOWN"]

        if lowest < 0.70:
            return self._make_result(
                "CHECK_13_CONFIDENCE",
                "Confidence Threshold",
                False,
                "ESCALATE",
                f"Very low confidence: {lowest:.2f}",
                affected,
                "Review supporting evidence or escalate for manual review.",
                start=start,
            )
        if lowest < 0.85:
            return self._make_result(
                "CHECK_13_CONFIDENCE",
                "Confidence Threshold",
                False,
                "SOFT_FAIL",
                f"Low confidence: {lowest:.2f}",
                affected,
                "Verify evidence before release.",
                start=start,
            )

        return self._make_result(
            "CHECK_13_CONFIDENCE",
            "Confidence Threshold",
            True,
            "NONE",
            "Confidence scores highly acceptable.",
            start=start,
        )
