"""Official Coding Guidelines (OCG) engine for MEDI-COMPLY.

Provides authoritative ICD-10-CM guideline storage, lookup, citation
formatting, and compliance checks used by coding agents and guardrails.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class GuidelineSection(str, Enum):
    SECTION_I = "SECTION_I"
    SECTION_II = "SECTION_II"
    SECTION_III = "SECTION_III"
    SECTION_IV = "SECTION_IV"


class GuidelineCategory(str, Enum):
    GENERAL = "GENERAL"
    SEQUENCING = "SEQUENCING"
    COMBINATION = "COMBINATION"
    EXCLUDES = "EXCLUDES"
    SPECIFICITY = "SPECIFICITY"
    LATERALITY = "LATERALITY"
    EPISODE_OF_CARE = "EPISODE_OF_CARE"
    MANIFESTATION = "MANIFESTATION"
    CHAPTER_SPECIFIC = "CHAPTER_SPECIFIC"
    OUTPATIENT = "OUTPATIENT"
    INPATIENT = "INPATIENT"
    UNCERTAIN_DIAGNOSIS = "UNCERTAIN_DIAGNOSIS"
    COMORBIDITY = "COMORBIDITY"
    PREGNANCY = "PREGNANCY"
    INJURY = "INJURY"
    NEOPLASM = "NEOPLASM"
    DIABETES = "DIABETES"
    HYPERTENSION = "HYPERTENSION"
    PAIN = "PAIN"
    MENTAL_HEALTH = "MENTAL_HEALTH"


class EncounterType(str, Enum):
    INPATIENT = "INPATIENT"
    OUTPATIENT = "OUTPATIENT"
    EMERGENCY = "EMERGENCY"
    OBSERVATION = "OBSERVATION"
    AMBULATORY_SURGERY = "AMBULATORY_SURGERY"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class GuidelineExample(BaseModel):
    scenario: str
    correct_codes: List[str]
    correct_sequencing: List[str]
    incorrect_codes: List[str]
    explanation: str
    guideline_reference: str


class CodingGuideline(BaseModel):
    guideline_id: str
    section: GuidelineSection
    category: GuidelineCategory
    title: str
    description: str
    rule_text: str = ""
    applicable_encounter_types: List[EncounterType]
    applicable_icd10_ranges: List[str] = Field(default_factory=list)
    applicable_chapters: List[int] = Field(default_factory=list)
    examples: List[GuidelineExample] = Field(default_factory=list)
    key_rules: List[str] = Field(default_factory=list)
    common_mistakes: List[str] = Field(default_factory=list)
    related_guidelines: List[str] = Field(default_factory=list)
    effective_year: str = "FY2025"
    priority: int = 5


class GuidelineLookupResult(BaseModel):
    query: str
    guidelines_found: List[CodingGuideline]
    most_relevant: Optional[CodingGuideline] = None
    context_summary: str = ""
    total_found: int = 0


class GuidelineViolation(BaseModel):
    violation_id: str
    guideline_id: str
    guideline_title: str
    severity: Severity
    code_involved: str
    description: str
    correction: str
    guideline_text: str


class GuidelineWarning(BaseModel):
    warning_id: str
    guideline_id: str
    code_involved: str
    description: str
    suggestion: str


class GuidelineComplianceResult(BaseModel):
    codes_checked: List[str]
    encounter_type: EncounterType
    applicable_guidelines: List[str]
    violations: List[GuidelineViolation] = Field(default_factory=list)
    warnings: List[GuidelineWarning] = Field(default_factory=list)
    is_compliant: bool = True
    compliance_score: float = 1.0
    reasoning: str = ""


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class CodingGuidelinesDatabase:
    """In-memory storage and retrieval for OCG entries."""

    def __init__(self) -> None:
        self._guidelines: Dict[str, CodingGuideline] = {}

    def add_guideline(self, guideline: CodingGuideline) -> None:
        self._guidelines[guideline.guideline_id] = guideline

    def get_guideline(self, guideline_id: str) -> Optional[CodingGuideline]:
        return self._guidelines.get(guideline_id)

    def find_by_section(self, section: GuidelineSection) -> List[CodingGuideline]:
        return [g for g in self._guidelines.values() if g.section == section]

    def find_by_category(self, category: GuidelineCategory) -> List[CodingGuideline]:
        return [g for g in self._guidelines.values() if g.category == category]

    def find_by_icd10_code(self, icd10_code: str) -> List[CodingGuideline]:
        code = icd10_code.upper().strip()
        matches: List[CodingGuideline] = []
        for g in self._guidelines.values():
            for rng in g.applicable_icd10_ranges:
                if _code_in_range(code, rng):
                    matches.append(g)
                    break
        return matches

    def find_by_chapter(self, chapter: int) -> List[CodingGuideline]:
        return [g for g in self._guidelines.values() if chapter in g.applicable_chapters]

    def find_by_encounter_type(self, encounter_type: EncounterType) -> List[CodingGuideline]:
        return [g for g in self._guidelines.values() if encounter_type in g.applicable_encounter_types]

    def search(self, query: str) -> List[CodingGuideline]:
        terms = {t.strip().lower() for t in query.split() if len(t) > 2}
        results: Dict[str, int] = {}
        for g in self._guidelines.values():
            haystack = " ".join([g.title, g.description, " ".join(g.key_rules)]).lower()
            score = sum(1 for t in terms if t in haystack)
            if score:
                results[g.guideline_id] = score + g.priority
        ranked_ids = sorted(results, key=lambda gid: results[gid], reverse=True)
        return [self._guidelines[gid] for gid in ranked_ids]

    def get_all_guidelines(self) -> List[CodingGuideline]:
        return list(self._guidelines.values())

    def get_guideline_count(self) -> int:
        return len(self._guidelines)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class CodingGuidelinesEngine:
    """Main OCG engine for lookup, citation, and compliance checks."""

    def __init__(self, database: Optional[CodingGuidelinesDatabase] = None) -> None:
        self.database = database or CodingGuidelinesDatabase()
        if not self.database.get_guideline_count():
            seed_coding_guidelines(self.database)

    # -- Lookups ---------------------------------------------------------

    def get_applicable_guidelines(
        self,
        icd10_codes: List[str],
        encounter_type: EncounterType,
        clinical_context: Optional[dict] = None,
    ) -> GuidelineLookupResult:
        clinical_context = clinical_context or {}
        found: List[CodingGuideline] = []
        seen: set[str] = set()

        for code in icd10_codes:
            for g in self.database.find_by_icd10_code(code):
                if g.guideline_id not in seen and encounter_type in g.applicable_encounter_types:
                    found.append(g)
                    seen.add(g.guideline_id)

        for g in self.database.find_by_encounter_type(encounter_type):
            if g.guideline_id not in seen:
                found.append(g)
                seen.add(g.guideline_id)

        found.sort(key=lambda g: g.priority, reverse=True)
        most_relevant = found[0] if found else None
        summary = self._summarize_guidelines(found, clinical_context)
        return GuidelineLookupResult(
            query=", ".join(icd10_codes),
            guidelines_found=found,
            most_relevant=most_relevant,
            context_summary=summary,
            total_found=len(found),
        )

    def check_compliance(
        self,
        icd10_codes: List[str],
        encounter_type: EncounterType,
        primary_dx: Optional[str] = None,
        patient_age: Optional[int] = None,
        patient_gender: Optional[str] = None,
    ) -> GuidelineComplianceResult:
        lookup = self.get_applicable_guidelines(icd10_codes, encounter_type)
        violations: List[GuidelineViolation] = []
        warnings: List[GuidelineWarning] = []

        codes_upper = [c.upper() for c in icd10_codes]

        # 1. Uncertain diagnosis rules (II.H vs IV.D)
        uncertain_guideline = self.get_uncertain_diagnosis_rule(encounter_type)
        if uncertain_guideline:
            if encounter_type == EncounterType.OUTPATIENT:
                for code in codes_upper:
                    if not code.startswith("R") and code.endswith("?"):
                        warnings.append(
                            GuidelineWarning(
                                warning_id="UNCERTAIN-OUT",
                                guideline_id=uncertain_guideline.guideline_id,
                                code_involved=code,
                                description="Outpatient probable/suspected diagnoses should use symptom codes.",
                                suggestion=self.format_guideline_citation(uncertain_guideline.guideline_id),
                            )
                        )
            else:
                # Inpatient: symptoms only without definitive diagnosis should be upgraded if probable exists
                pass

        # 2. Specificity requirements (highest level)
        spec_guideline = self.database.get_guideline("OCG-I-A-2")
        for code in codes_upper:
            if code.endswith(".9") and spec_guideline:
                violations.append(
                    GuidelineViolation(
                        violation_id=f"SPEC-{code}",
                        guideline_id=spec_guideline.guideline_id,
                        guideline_title=spec_guideline.title,
                        severity=Severity.WARNING,
                        code_involved=code,
                        description="Code ends with .9 unspecified; confirm highest specificity is used.",
                        correction="Review documentation for laterality, episode, or stage to increase specificity.",
                        guideline_text=spec_guideline.description,
                    )
                )

        # 3. Sequencing correctness / principal diagnosis selection
        seq_guideline = self.database.get_guideline("OCG-II-A")
        if primary_dx and codes_upper:
            if codes_upper[0] != primary_dx.upper() and seq_guideline:
                violations.append(
                    GuidelineViolation(
                        violation_id="SEQ-PRIMARY",
                        guideline_id=seq_guideline.guideline_id,
                        guideline_title=seq_guideline.title,
                        severity=Severity.WARNING,
                        code_involved=primary_dx,
                        description="Principal/primary diagnosis should be sequenced first when established.",
                        correction=f"Sequence {primary_dx} before other codes.",
                        guideline_text=seq_guideline.description,
                    )
                )

        # 4. Excludes1 logic (symptom + definitive dx together in inpatient)
        excl_guideline = self.database.get_guideline("OCG-I-A-6")
        if encounter_type == EncounterType.INPATIENT and excl_guideline:
            if any(code.startswith("R") for code in codes_upper) and any(not c.startswith("R") for c in codes_upper):
                violations.append(
                    GuidelineViolation(
                        violation_id="EXCLUDES1-SYMPTOM",
                        guideline_id=excl_guideline.guideline_id,
                        guideline_title=excl_guideline.title,
                        severity=Severity.WARNING,
                        code_involved="R-codes",
                        description="Symptoms coded with definitive diagnosis; remove R-codes unless unrelated.",
                        correction="Remove symptom codes when definitive diagnosis is present.",
                        guideline_text=excl_guideline.description,
                    )
                )

        # 5. Combination code usage (diabetes with complication, HTN with CKD)
        combo_guideline = self.database.get_guideline("OCG-I-C-4-a")
        if combo_guideline:
            has_dm = any(code.startswith("E11") for code in codes_upper)
            has_dm_unspecified = any(code == "E11.9" for code in codes_upper)
            has_ckd = any(code.startswith("N18") for code in codes_upper)
            if has_dm_unspecified and has_ckd:
                violations.append(
                    GuidelineViolation(
                        violation_id="COMBO-DM-CKD",
                        guideline_id=combo_guideline.guideline_id,
                        guideline_title=combo_guideline.title,
                        severity=Severity.ERROR,
                        code_involved="E11.9",
                        description="Diabetes with CKD should use combination code (e.g., E11.22) not E11.9 + N18.x.",
                        correction="Replace E11.9 with appropriate diabetes-with-complication code (E11.2x).",
                        guideline_text=combo_guideline.description,
                    )
                )

        # 6. Manifestation/etiology pairing
        manifest_guideline = self.database.get_guideline("OCG-I-A-7")
        if manifest_guideline:
            has_manifest = any(code.startswith("H36") for code in codes_upper)
            if has_manifest and not any(code.startswith("E08") or code.startswith("E11") for code in codes_upper):
                violations.append(
                    GuidelineViolation(
                        violation_id="MANIFESTATION-WITHOUT-ETIOLOGY",
                        guideline_id=manifest_guideline.guideline_id,
                        guideline_title=manifest_guideline.title,
                        severity=Severity.ERROR,
                        code_involved="H36.0",
                        description="Manifestation code present without required underlying etiology code sequenced first.",
                        correction="Add appropriate diabetes etiology code before manifestation.",
                        guideline_text=manifest_guideline.description,
                    )
                )

        # 7. Laterality completeness
        lat_guideline = self.database.get_guideline("OCG-I-A-13")
        if lat_guideline:
            for code in codes_upper:
                if (code.startswith("H") or code.startswith("M")) and code.endswith("9"):
                    warnings.append(
                        GuidelineWarning(
                            warning_id=f"LAT-{code}",
                            guideline_id=lat_guideline.guideline_id,
                            code_involved=code,
                            description="Laterality unspecified; use left/right/bilateral if documented.",
                            suggestion=self.format_guideline_citation(lat_guideline.guideline_id),
                        )
                    )

        # 8. 7th character completeness for injuries
        seventh_guideline = self.database.get_guideline("OCG-I-A-15")
        if seventh_guideline:
            for code in codes_upper:
                if (code.startswith("S") or code.startswith("T")) and len(code.replace(".", "")) < 7:
                    violations.append(
                        GuidelineViolation(
                            violation_id=f"7TH-{code}",
                            guideline_id=seventh_guideline.guideline_id,
                            guideline_title=seventh_guideline.title,
                            severity=Severity.ERROR,
                            code_involved=code,
                            description="Injury codes require 7th character (A/D/S) with padding as needed.",
                            correction="Add appropriate 7th character and placeholder X if required.",
                            guideline_text=seventh_guideline.description,
                        )
                    )

        # 9. Uncertain diagnosis handling already captured; outpatient symptom preference
        # (warnings already added above)

        # 10. Code First / Use Additional compliance (etiology and detail codes)
        codefirst_guideline = self.database.get_guideline("OCG-I-A-9")
        if codefirst_guideline:
            if any(code.startswith("N18") for code in codes_upper) and not any(code.startswith("E11.2") or code.startswith("I12") for code in codes_upper):
                warnings.append(
                    GuidelineWarning(
                        warning_id="CODEFIRST-CKD",
                        guideline_id=codefirst_guideline.guideline_id,
                        code_involved="N18.x",
                        description="CKD present without underlying etiology code (e.g., diabetes or HTN).",
                        suggestion=self.format_guideline_citation(codefirst_guideline.guideline_id),
                    )
                )

        is_compliant = not any(v.severity == Severity.ERROR for v in violations)
        score_penalty = sum(0.05 for _ in violations) + sum(0.02 for _ in warnings)
        compliance_score = max(0.0, 1.0 - score_penalty)

        is_compliant = not any(v.severity == Severity.ERROR for v in violations)
        score_penalty = sum(0.05 for _ in violations) + sum(0.02 for _ in warnings)
        compliance_score = max(0.0, 1.0 - score_penalty)

        reasoning = self._build_compliance_reasoning(lookup.guidelines_found, violations, warnings)

        return GuidelineComplianceResult(
            codes_checked=icd10_codes,
            encounter_type=encounter_type,
            applicable_guidelines=[g.guideline_id for g in lookup.guidelines_found],
            violations=violations,
            warnings=warnings,
            is_compliant=is_compliant,
            compliance_score=compliance_score,
            reasoning=reasoning,
        )

    def get_sequencing_rules(self, icd10_codes: List[str], encounter_type: EncounterType) -> dict:
        principal = icd10_codes[0] if icd10_codes else None
        guideline = self.database.get_guideline("OCG-II-A")
        citation = self.format_guideline_citation("OCG-II-A") if guideline else ""
        return {
            "principal": principal,
            "rationale": "Sequence confirmed diagnosis before symptoms; follow chapter-specific rules for combination codes.",
            "citation": citation,
        }

    def get_combination_guidance(self, conditions: List[str]) -> dict:
        text = " ".join(c.lower() for c in conditions)
        if "diabetes" in text and "nephropathy" in text:
            return {
                "use_combination_code": True,
                "recommended_code": "E11.22",
                "reason": "Diabetes with CKD uses combination code per OCG-I-C-4-a.",
                "citation": self.format_guideline_citation("OCG-I-C-4-a"),
            }
        if "hypertension" in text and "ckd" in text:
            return {
                "use_combination_code": True,
                "recommended_code": "I12.9",
                "reason": "Hypertension with CKD assumes causal link per OCG-I-C-9-a.",
                "citation": self.format_guideline_citation("OCG-I-C-9-a"),
            }
        return {
            "use_combination_code": False,
            "recommended_code": None,
            "reason": "No specific combination guidance found.",
            "citation": "",
        }

    def get_uncertain_diagnosis_rule(self, encounter_type: EncounterType) -> Optional[CodingGuideline]:
        if encounter_type == EncounterType.INPATIENT:
            return self.database.get_guideline("OCG-II-H")
        return self.database.get_guideline("OCG-IV-D")

    def get_chapter_guidelines(self, chapter: int) -> List[CodingGuideline]:
        return self.database.find_by_chapter(chapter)

    def format_guideline_citation(self, guideline_id: str) -> str:
        g = self.database.get_guideline(guideline_id)
        if not g:
            return f"Guideline {guideline_id}"
        return f"Per Official Coding Guideline {g.guideline_id} ({g.title})"

    def get_coding_tips(self, icd10_code: str) -> List[str]:
        tips: List[str] = []
        for g in self.database.find_by_icd10_code(icd10_code):
            tips.extend(g.key_rules[:2])
        if not tips:
            tips.append("Verify specificity and laterality per OCG-I-A-2 and OCG-I-A-13.")
        return tips

    # -- Internals -------------------------------------------------------

    def _summarize_guidelines(self, guidelines: List[CodingGuideline], clinical_context: dict) -> str:
        if not guidelines:
            return "No specific guidelines found for the provided context."
        top = guidelines[:3]
        summaries = [f"{g.guideline_id}: {g.title}" for g in top]
        return "; ".join(summaries)

    def _build_compliance_reasoning(
        self,
        guidelines: List[CodingGuideline],
        violations: List[GuidelineViolation],
        warnings: List[GuidelineWarning],
    ) -> str:
        parts: List[str] = []
        if guidelines:
            parts.append("Applicable: " + ", ".join(g.guideline_id for g in guidelines[:5]))
        if violations:
            parts.append("Violations: " + ", ".join(v.guideline_id for v in violations))
        if warnings:
            parts.append("Warnings: " + ", ".join(w.guideline_id for w in warnings))
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Backwards-compatible store wrapper (legacy API used elsewhere)
# ---------------------------------------------------------------------------


class CodingGuidelinesStore:
    """Compatibility wrapper exposing legacy store interface on top of the engine."""

    def __init__(self) -> None:
        self.engine = CodingGuidelinesEngine()
        self._guidelines_cache = {g.guideline_id: g for g in self.engine.database.get_all_guidelines()}

    def get_guideline(self, guideline_id: str) -> Optional[CodingGuideline]:
        return self.engine.database.get_guideline(guideline_id)

    def search_guidelines(self, keywords: List[str]) -> List[CodingGuideline]:
        return self.engine.database.search(" ".join(keywords))

    def get_guidelines_for_code(self, icd10_code: str) -> List[CodingGuideline]:
        return self.engine.database.find_by_icd10_code(icd10_code)

    @property
    def guideline_count(self) -> int:
        return self.engine.database.get_guideline_count()

    def get_guidelines_for_scenario(self, scenario_description: str) -> List[CodingGuideline]:
        tokens = [t.strip(".,;:()\"'").lower() for t in scenario_description.split()]
        return self.engine.database.search(" ".join(tokens))

    def generate_citation(self, guideline_id: str) -> str:
        return self.engine.format_guideline_citation(guideline_id)


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------


def seed_coding_guidelines(database: CodingGuidelinesDatabase) -> None:
    guidelines: List[CodingGuideline] = []

    def g(
        guideline_id: str,
        section: GuidelineSection,
        category: GuidelineCategory,
        title: str,
        description: str,
        key_rules: List[str],
        common_mistakes: List[str],
        applicable_encounter_types: List[EncounterType],
        applicable_icd10_ranges: List[str],
        applicable_chapters: List[int],
        examples: List[GuidelineExample],
        related: List[str],
        priority: int,
    ) -> None:
        combined_rule_text = " ".join([description] + key_rules)
        guidelines.append(
            CodingGuideline(
                guideline_id=guideline_id,
                section=section,
                category=category,
                title=title,
                description=description,
                rule_text=combined_rule_text,
                applicable_encounter_types=applicable_encounter_types,
                applicable_icd10_ranges=applicable_icd10_ranges,
                applicable_chapters=applicable_chapters,
                examples=examples,
                key_rules=key_rules,
                common_mistakes=common_mistakes,
                related_guidelines=related,
                priority=priority,
            )
        )

    def ex(scenario: str, correct: List[str], seq: List[str], incorrect: List[str], explanation: str, ref: str) -> GuidelineExample:
        return GuidelineExample(
            scenario=scenario,
            correct_codes=correct,
            correct_sequencing=seq,
            incorrect_codes=incorrect,
            explanation=explanation,
            guideline_reference=ref,
        )

    # SECTION I general (a-j)
    g(
        "OCG-I-A-1",
        GuidelineSection.SECTION_I,
        GuidelineCategory.GENERAL,
        "Use Alphabetic Index and Tabular List",
        "Both the Alphabetic Index and Tabular List must be consulted to assign accurate codes.",
        key_rules=["Start with Alphabetic Index, confirm in Tabular", "Follow instructional notes in both"],
        common_mistakes=["Selecting code from Index without validating in Tabular"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Migraine without aura", ["G43.009"], ["G43.009"], ["G43.9"], "Tabular specifies 6th character for intractable/status", "OCG-I-A-1")],
        related=["OCG-I-A-2"],
        priority=9,
    )

    g(
        "OCG-I-A-2",
        GuidelineSection.SECTION_I,
        GuidelineCategory.SPECIFICITY,
        "Level of Detail in Coding",
        "Codes must be reported to the highest level of specificity available.",
        key_rules=["Use all required characters including 7th", "3-character codes only when no 4th/5th/6th/7th exists"],
        common_mistakes=["Reporting unspecified .9 when documentation supports detail"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY, EncounterType.OBSERVATION],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("NSTEMI documented", ["I21.4"], ["I21.4"], ["I21.9"], "NSTEMI requires 4th character 4", "OCG-I-A-2")],
        related=["OCG-I-A-1", "OCG-I-A-15"],
        priority=10,
    )

    g(
        "OCG-I-A-3",
        GuidelineSection.SECTION_I,
        GuidelineCategory.GENERAL,
        "Valid code range",
        "Codes must be drawn from A00.0 through T88.9, Z00-Z99.8 range.",
        key_rules=["Use only valid ICD-10-CM codes", "Verify code exists in Tabular"],
        common_mistakes=["Using placeholders or truncated draft codes"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Code outside range", ["Z00.00"], ["Z00.00"], ["ZZZ.999"], "Invalid code not allowed", "OCG-I-A-3")],
        related=["OCG-I-A-1"],
        priority=8,
    )

    g(
        "OCG-I-A-5",
        GuidelineSection.SECTION_I,
        GuidelineCategory.GENERAL,
        "Includes notes",
        "Includes notes further define the content of a code category.",
        key_rules=["Apply includes notes to clarify scope", "Do not code conditions excluded by notes"],
        common_mistakes=["Ignoring includes notes leading to overcoding"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Strep pharyngitis", ["J02.0"], ["J02.0"], ["J02.9"], "Includes notes point to specific organism", "OCG-I-A-5")],
        related=["OCG-I-A-6"],
        priority=6,
    )

    g(
        "OCG-I-A-6",
        GuidelineSection.SECTION_I,
        GuidelineCategory.EXCLUDES,
        "Excludes notes",
        "Excludes1 means mutually exclusive; Excludes2 means not part of this category but both codes may be used.",
        key_rules=["Excludes1: do not code together", "Excludes2: code both when documentation supports"],
        common_mistakes=["Coding Excludes1 pair together"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Congenital vs acquired stenosis", ["Q24.4"], ["Q24.4"], ["I35.0"], "Excludes1 prevents combining congenital and acquired forms", "OCG-I-A-6")],
        related=["OCG-I-A-5"],
        priority=9,
    )

    g(
        "OCG-I-A-7",
        GuidelineSection.SECTION_I,
        GuidelineCategory.MANIFESTATION,
        "Etiology/Manifestation convention",
        "Use etiology code first, followed by manifestation code in brackets.",
        key_rules=["Sequence etiology before manifestation", "Manifestation codes cannot be principal"],
        common_mistakes=["Sequencing manifestation first"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["E00-H95"],
        applicable_chapters=[4, 7],
        examples=[ex("Diabetic retinopathy", ["E11.319", "H36.0"], ["E11.319", "H36.0"], ["H36.0", "E11.319"], "Etiology E11.319 precedes manifestation H36.0", "OCG-I-A-7")],
        related=["OCG-I-C-4-a"],
        priority=9,
    )

    g(
        "OCG-I-A-9",
        GuidelineSection.SECTION_I,
        GuidelineCategory.COMBINATION,
        "Code First / Use Additional",
        "Follow 'Code First' and 'Use Additional Code' notes to capture etiology and detail.",
        key_rules=["Code First underlying condition", "Use Additional to capture details like organism or stage"],
        common_mistakes=["Ignoring Use Additional code notes"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Sepsis due to E. coli", ["A41.51"], ["A41.51"], ["A41.9"], "Use additional codes not needed when organism specified in combination code", "OCG-I-A-9")],
        related=["OCG-I-A-7"],
        priority=8,
    )

    g(
        "OCG-I-A-12",
        GuidelineSection.SECTION_I,
        GuidelineCategory.GENERAL,
        "Code assignment and clinical criteria",
        "Code assignment is based on provider documentation; clinical criteria alone do not justify codes.",
        key_rules=["Use provider's diagnostic statement", "Do not require clinical criteria if documented"],
        common_mistakes=["Refusing to code documented diagnosis lacking lab confirmation"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Provider documents sepsis", ["A41.9"], ["A41.9"], ["R65.10"], "Sepsis coded from documentation even if SIRS criteria not fully met", "OCG-I-A-12")],
        related=["OCG-II-A"],
        priority=7,
    )

    g(
        "OCG-I-A-13",
        GuidelineSection.SECTION_I,
        GuidelineCategory.LATERALITY,
        "Laterality coding",
        "Assign laterality when available; bilateral when provided; unspecified only when documentation lacks side.",
        key_rules=["Use left/right/bilateral codes when provided", "Avoid unspecified when side documented"],
        common_mistakes=["Using unspecified when documentation provides side"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["H00-H59", "M00-M99"],
        applicable_chapters=[7, 13],
        examples=[ex("Left otitis media", ["H66.92"], ["H66.92"], ["H66.90"], "Laterality required", "OCG-I-A-13")],
        related=["OCG-I-A-2"],
        priority=8,
    )

    g(
        "OCG-I-A-15",
        GuidelineSection.SECTION_I,
        GuidelineCategory.EPISODE_OF_CARE,
        "7th character extensions",
        "Use required 7th character for injury and other chapters; use placeholder X when needed.",
        key_rules=["A=initial, D=subsequent, S=sequela", "Pad with X to reach 7th character"],
        common_mistakes=["Omitting placeholder X leading to invalid code"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["S00-T88"],
        applicable_chapters=[19],
        examples=[ex("Subsequent ankle fracture visit", ["S82.201D"], ["S82.201D"], ["S82.201"], "7th character D required", "OCG-I-A-15")],
        related=["OCG-I-C-19-a"],
        priority=9,
    )

    # Chapter specific (k-v)
    g(
        "OCG-I-C-1",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Chapter 1: Infectious diseases",
        "Report organism-specific codes; use additional codes for resistance.",
        key_rules=["Use combination codes when organism included", "Add Z16.- for drug resistance"],
        common_mistakes=["Omitting resistance code"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["A00-B99"],
        applicable_chapters=[1],
        examples=[ex("MRSA pneumonia", ["J15.212"], ["J15.212"], ["J18.9", "B95.62"], "Combination code captures MRSA", "OCG-I-C-1")],
        related=["OCG-I-A-9"],
        priority=7,
    )

    g(
        "OCG-I-C-4-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.DIABETES,
        "Diabetes mellitus (E08-E13)",
        "Use as many codes as needed to describe diabetes and complications; prefer combination codes.",
        key_rules=["Use combination codes for DM with complications", "Do not code E11.9 when complication exists", "Use additional code for insulin use Z79.4"],
        common_mistakes=["Coding E11.9 with CKD instead of E11.22"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["E08-E13"],
        applicable_chapters=[4],
        examples=[ex("T2DM with CKD stage 3", ["E11.22", "N18.3"], ["E11.22", "N18.3"], ["E11.9", "N18.3"], "Use combination code for DM with CKD", "OCG-I-C-4-a")],
        related=["OCG-I-A-7"],
        priority=10,
    )

    g(
        "OCG-I-C-5-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.MENTAL_HEALTH,
        "Mental health coding",
        "Follow hierarchy for substance-related disorders and pain disorders.",
        key_rules=["Code dependence before abuse", "Differentiate psychogenic pain vs somatic"],
        common_mistakes=["Coding abuse when dependence documented"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["F01-F99"],
        applicable_chapters=[5],
        examples=[ex("Alcohol dependence", ["F10.20"], ["F10.20"], ["F10.10"], "Dependence supersedes abuse", "OCG-I-C-5-a")],
        related=["OCG-III-A"],
        priority=7,
    )

    g(
        "OCG-I-C-6-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Dominant/non-dominant side",
        "For hemiplegia and paresis, code dominant vs non-dominant when known.",
        key_rules=["If handedness unknown, default right-handed", "Code unspecified only when not documented"],
        common_mistakes=["Not assigning dominant side"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["G81-G83"],
        applicable_chapters=[6],
        examples=[ex("Left hemiplegia in right-handed patient", ["G81.94"], ["G81.94"], ["G81.90"], "Use dominant/non-dominant guidance", "OCG-I-C-6-a")],
        related=["OCG-I-A-13"],
        priority=6,
    )

    g(
        "OCG-I-C-9-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.HYPERTENSION,
        "Hypertension and heart/CKD",
        "HTN with heart disease or CKD assumes causal relationship unless stated otherwise.",
        key_rules=["I11.- for HTN heart disease", "I12.- for HTN CKD", "Sequence acute MI rules"],
        common_mistakes=["Coding I10 with heart failure instead of I11.0"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["I10-I99"],
        applicable_chapters=[9],
        examples=[ex("HTN with CHF", ["I11.0"], ["I11.0"], ["I10", "I50.9"], "Assumed causal unless stated otherwise", "OCG-I-C-9-a")],
        related=["OCG-II-A"],
        priority=9,
    )

    g(
        "OCG-I-C-10-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Respiratory: influenza",
        "Use specific influenza codes with manifestations when documented.",
        key_rules=["Use J09-J11 with manifestation codes", "Do not code unspecified when strain known"],
        common_mistakes=["Using J11 when influenza A lab confirmed"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["J09-J11"],
        applicable_chapters=[10],
        examples=[ex("Influenza A with pneumonia", ["J09.X1"], ["J09.X1"], ["J11.00"], "Use code with manifestation", "OCG-I-C-10-a")],
        related=["OCG-I-A-2"],
        priority=7,
    )

    g(
        "OCG-I-C-12-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Skin: pressure ulcers",
        "Code site and stage; unstageable vs unspecified guidance applies.",
        key_rules=["Capture site and stage", "Use unstageable only when covered by eschar/debridement"],
        common_mistakes=["Using unspecified stage when stage documented"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["L89"],
        applicable_chapters=[12],
        examples=[ex("Stage 3 sacral ulcer", ["L89.153"], ["L89.153"], ["L89.150"], "Stage required", "OCG-I-C-12-a")],
        related=["OCG-I-A-2"],
        priority=8,
    )

    g(
        "OCG-I-C-13-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Musculoskeletal site and laterality",
        "Code specific site and laterality; pathological fractures need 7th character.",
        key_rules=["Use laterality codes", "Add 7th character for fractures"],
        common_mistakes=["Missing 7th character for fracture"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY],
        applicable_icd10_ranges=["M00-M99"],
        applicable_chapters=[13],
        examples=[ex("Pathological fracture humerus initial", ["M84.421A"], ["M84.421A"], ["M84.421"], "7th character required", "OCG-I-C-13-a")],
        related=["OCG-I-A-15"],
        priority=8,
    )

    g(
        "OCG-I-C-14-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Genitourinary CKD staging",
        "Use N18.- codes for CKD stage; add Z99.2 for dialysis status when applicable.",
        key_rules=["Code CKD stage", "Add dialysis status if applicable"],
        common_mistakes=["Omitting Z99.2 for dialysis patient"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["N18"],
        applicable_chapters=[14],
        examples=[ex("CKD stage 5 on dialysis", ["N18.5", "Z99.2"], ["N18.5", "Z99.2"], ["N18.9"], "Capture stage and dialysis", "OCG-I-C-14-a")],
        related=["OCG-I-C-4-a"],
        priority=8,
    )

    g(
        "OCG-I-C-15-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.PREGNANCY,
        "Obstetric coding",
        "Use O codes on maternal record, add trimester, and fetus identification when required.",
        key_rules=["Use maternal record only", "Include trimester and fetus number"],
        common_mistakes=["Coding pregnancy codes on newborn"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["O00-O9A"],
        applicable_chapters=[15],
        examples=[ex("Gestational diabetes third trimester", ["O24.410"], ["O24.410"], ["E11.9"], "Use pregnancy chapter codes", "OCG-I-C-15-a")],
        related=["OCG-I-A-2"],
        priority=9,
    )

    g(
        "OCG-I-C-19-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.INJURY,
        "Injury coding and 7th character",
        "Code all injury diagnoses, sequence most serious first, and use 7th character.",
        key_rules=["Sequence most severe injury first", "Use 7th character for episode of care"],
        common_mistakes=["Missing external cause codes"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT, EncounterType.EMERGENCY, EncounterType.AMBULATORY_SURGERY],
        applicable_icd10_ranges=["S00-T88"],
        applicable_chapters=[19],
        examples=[ex("Initial femur fracture", ["S72.001A"], ["S72.001A"], ["S72.001"], "7th character required", "OCG-I-C-19-a")],
        related=["OCG-I-A-15"],
        priority=9,
    )

    g(
        "OCG-I-C-21-a",
        GuidelineSection.SECTION_I,
        GuidelineCategory.CHAPTER_SPECIFIC,
        "Z codes usage",
        "Use Z codes for screenings, history, and status; some may be principal.",
        key_rules=["Z00-Z13 for screenings", "Z79.- for drug therapy status"],
        common_mistakes=["Not using Z79.4 for insulin use"],
        applicable_encounter_types=[EncounterType.INPATIENT, EncounterType.OUTPATIENT],
        applicable_icd10_ranges=["Z00-Z99"],
        applicable_chapters=[21],
        examples=[ex("Annual wellness visit", ["Z00.00"], ["Z00.00"], ["Z71.89"], "Preventive visit uses Z00.00", "OCG-I-C-21-a")],
        related=["OCG-I-A-12"],
        priority=7,
    )

    # SECTION II (w-z)
    g(
        "OCG-II-A",
        GuidelineSection.SECTION_II,
        GuidelineCategory.SEQUENCING,
        "Symptoms vs confirmed diagnosis",
        "When a definitive diagnosis is established, code the diagnosis, not the presenting symptoms.",
        key_rules=["Do not code symptoms when definitive diagnosis documented", "Symptoms may be coded if unrelated"],
        common_mistakes=["Keeping R codes when definitive dx is present"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Pneumonia with cough", ["J18.9"], ["J18.9"], ["R05"], "Code pneumonia instead of symptom cough", "OCG-II-A")],
        related=["OCG-IV-A"],
        priority=10,
    )

    g(
        "OCG-II-B",
        GuidelineSection.SECTION_II,
        GuidelineCategory.SEQUENCING,
        "Two or more interrelated conditions",
        "Either diagnosis may be sequenced first unless circumstances of admission or therapy indicate otherwise.",
        key_rules=["Sequence based on resources/therapy", "Review provider documentation for primary reason"],
        common_mistakes=["Arbitrary sequencing without clinical rationale"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("COPD with pneumonia", ["J44.0", "J18.9"], ["J44.0", "J18.9"], ["J18.9", "J44.0"], "Either may be principal; choose based on admission reason", "OCG-II-B")],
        related=["OCG-II-A"],
        priority=7,
    )

    g(
        "OCG-II-C",
        GuidelineSection.SECTION_II,
        GuidelineCategory.SEQUENCING,
        "Two or more diagnoses both principal",
        "If two or more diagnoses equally meet principal diagnosis definition, any may be sequenced first.",
        key_rules=["When truly equal, either may be principal", "Document rationale"],
        common_mistakes=["Selecting without documenting rationale"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("CHF and COPD exacerbation", ["I50.9", "J44.1"], ["I50.9", "J44.1"], ["J44.1", "I50.9"], "Either may be principal when equally meets definition", "OCG-II-C")],
        related=["OCG-II-B"],
        priority=6,
    )

    g(
        "OCG-II-H",
        GuidelineSection.SECTION_II,
        GuidelineCategory.UNCERTAIN_DIAGNOSIS,
        "Uncertain diagnosis inpatient",
        "For inpatient admissions, code conditions described as probable, suspected, likely, or similar terms as if established.",
        key_rules=["Inpatient: code as if confirmed", "Applies to discharge diagnosis statements"],
        common_mistakes=["Leaving symptoms coded instead of probable dx"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Probable pneumonia at discharge", ["J18.9"], ["J18.9"], ["R05"], "Probable may be coded as confirmed", "OCG-II-H")],
        related=["OCG-IV-D"],
        priority=9,
    )

    # SECTION III (aa)
    g(
        "OCG-III-A",
        GuidelineSection.SECTION_III,
        GuidelineCategory.COMORBIDITY,
        "Additional diagnoses reporting",
        "Report conditions that affect patient care, treatment, diagnostic tests, or length of stay.",
        key_rules=["Include diagnoses requiring clinical evaluation or therapy", "Include conditions that increase nursing care or monitoring"],
        common_mistakes=["Omitting chronic conditions affecting care"],
        applicable_encounter_types=[EncounterType.INPATIENT],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Diabetes managed during stay", ["E11.9"], ["E11.9"], ["(omitted)"], "Report chronic conditions impacting care", "OCG-III-A")],
        related=["OCG-II-A"],
        priority=8,
    )

    # SECTION IV (bb, cc, dd)
    g(
        "OCG-IV-A",
        GuidelineSection.SECTION_IV,
        GuidelineCategory.OUTPATIENT,
        "First-listed diagnosis outpatient",
        "Code the diagnosis, condition, problem, or reason for the encounter shown in the documentation.",
        key_rules=["Outpatient uses first-listed not principal", "Symptoms may be coded when no definitive dx"],
        common_mistakes=["Sequencing chronic conditions ahead of reason for visit"],
        applicable_encounter_types=[EncounterType.OUTPATIENT, EncounterType.EMERGENCY, EncounterType.OBSERVATION],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Visit for sore throat", ["J02.9"], ["J02.9"], ["J00"], "First-listed reason for encounter", "OCG-IV-A")],
        related=["OCG-II-A"],
        priority=9,
    )

    g(
        "OCG-IV-B",
        GuidelineSection.SECTION_IV,
        GuidelineCategory.OUTPATIENT,
        "Preoperative evaluation",
        "Outpatient pre-op exams: first-list reason for surgery, followed by Z01.81 pre-op code and any findings.",
        key_rules=["Sequence surgical condition first", "Add Z01.81- for pre-op evaluation"],
        common_mistakes=["Listing Z01.81 as primary instead of surgical condition"],
        applicable_encounter_types=[EncounterType.OUTPATIENT, EncounterType.AMBULATORY_SURGERY, EncounterType.OBSERVATION],
        applicable_icd10_ranges=["Z01.81"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Pre-op exam for knee arthroplasty", ["M17.11", "Z01.818"], ["M17.11", "Z01.818"], ["Z01.818", "M17.11"], "Surgical condition sequenced before pre-op code", "OCG-IV-B")],
        related=["OCG-II-B", "OCG-IV-A"],
        priority=7,
    )

    g(
        "OCG-IV-D",
        GuidelineSection.SECTION_IV,
        GuidelineCategory.UNCERTAIN_DIAGNOSIS,
        "Uncertain diagnoses outpatient",
        "Do not code diagnoses documented as probable, suspected, questionable, or rule out; code the presenting signs/symptoms.",
        key_rules=["Outpatient: code symptoms, not probable dx", "Use R-codes when no confirmed dx"],
        common_mistakes=["Coding probable pneumonia in outpatient"],
        applicable_encounter_types=[EncounterType.OUTPATIENT, EncounterType.EMERGENCY, EncounterType.OBSERVATION],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Suspected pneumonia outpatient", ["R05"], ["R05"], ["J18.9"], "Use symptom code", "OCG-IV-D")],
        related=["OCG-II-H"],
        priority=10,
    )

    g(
        "OCG-IV-J",
        GuidelineSection.SECTION_IV,
        GuidelineCategory.OUTPATIENT,
        "Code all documented conditions that coexist",
        "Code all documented conditions that coexist and affect care or management.",
        key_rules=["Include chronic conditions affecting care", "Do not code conditions that no longer exist"],
        common_mistakes=["Omitting chronic conditions like COPD during outpatient visit"],
        applicable_encounter_types=[EncounterType.OUTPATIENT, EncounterType.EMERGENCY, EncounterType.OBSERVATION],
        applicable_icd10_ranges=["A00-Z99"],
        applicable_chapters=list(range(1, 22)),
        examples=[ex("Outpatient visit DM and HTN managed", ["E11.9", "I10"], ["E11.9", "I10"], ["(omitted)"], "Report coexisting managed conditions", "OCG-IV-J")],
        related=["OCG-III-A"],
        priority=7,
    )

    # Add extras to reach at least 30 (already 30).

    for guideline in guidelines:
        database.add_guideline(guideline)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _code_in_range(code: str, range_str: str) -> bool:
    if "-" in range_str:
        start, end = [p.strip().upper() for p in range_str.split("-")]
        prefix_len = min(len(start), len(end), len(code))
        return start <= code[:prefix_len] <= end
    # Simple prefix or exact match
    return code.startswith(range_str.upper())
