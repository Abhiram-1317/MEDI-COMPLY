"""
MEDI-COMPLY — Medical necessity engine (LCD / NCD coverage determination).

Checks whether a procedure code is medically necessary given the
diagnosis codes present on the claim, based on Local Coverage
Determinations (LCDs).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CoverageDetermination:
    """A Local Coverage Determination (LCD) entry."""

    lcd_id: str
    lcd_title: str
    procedure_codes: list[str] = field(default_factory=list)
    covered_icd10_codes: list[str] = field(default_factory=list)
    documentation_requirements: list[str] = field(default_factory=list)
    frequency_limits: str = ""


@dataclass
class MedNecessityResult:
    """Result of a medical necessity check."""

    is_medically_necessary: bool
    lcd_ref: str = ""
    covered_dx_matches: list[str] = field(default_factory=list)
    uncovered_dx: list[str] = field(default_factory=list)
    documentation_requirements: list[str] = field(default_factory=list)
    frequency_notes: str = ""


# ---------------------------------------------------------------------------
# Medical Necessity Engine
# ---------------------------------------------------------------------------


class MedicalNecessityEngine:
    """Checks procedure-diagnosis pairs against LCD coverage rules.

    For each procedure code, the engine looks up the applicable LCD(s)
    and determines whether any of the submitted diagnosis codes satisfy
    the coverage criteria.
    """

    def __init__(self) -> None:
        # procedure_code -> list of LCDs that cover it
        self._lcd_by_procedure: dict[str, list[CoverageDetermination]] = {}
        self._lcds: dict[str, CoverageDetermination] = {}

    # -- Loading -----------------------------------------------------------

    def load(self, determinations: list[CoverageDetermination]) -> None:
        """Bulk-load LCD entries.

        Parameters
        ----------
        determinations:
            List of :class:`CoverageDetermination` to load.
        """
        for lcd in determinations:
            self._lcds[lcd.lcd_id] = lcd
            for proc in lcd.procedure_codes:
                self._lcd_by_procedure.setdefault(proc, []).append(lcd)

    # -- Checking ----------------------------------------------------------

    def check_medical_necessity(
        self,
        procedure_code: str,
        diagnosis_codes: list[str],
    ) -> MedNecessityResult:
        """Determine whether a procedure is medically necessary.

        Parameters
        ----------
        procedure_code:
            CPT / HCPCS code for the procedure.
        diagnosis_codes:
            List of ICD-10-CM codes supporting the procedure.

        Returns
        -------
        MedNecessityResult
        """
        lcds = self._lcd_by_procedure.get(procedure_code, [])
        if not lcds:
            return MedNecessityResult(
                is_medically_necessary=True,
                lcd_ref="No LCD on file",
                covered_dx_matches=[],
                uncovered_dx=diagnosis_codes,
                frequency_notes="No frequency limits on file",
            )

        # Aggregate covered diagnoses from all applicable LCDs
        all_covered: set[str] = set()
        doc_reqs: list[str] = []
        freq_notes: list[str] = []
        lcd_refs: list[str] = []

        for lcd in lcds:
            all_covered.update(lcd.covered_icd10_codes)
            doc_reqs.extend(lcd.documentation_requirements)
            if lcd.frequency_limits:
                freq_notes.append(lcd.frequency_limits)
            lcd_refs.append(lcd.lcd_id)

        # Match submitted dx against covered dx
        # Support prefix matching: if LCD has "N18" it covers N18.1, N18.2, etc.
        covered_matches: list[str] = []
        uncovered: list[str] = []
        for dx in diagnosis_codes:
            matched = False
            for covered_code in all_covered:
                if dx == covered_code or dx.startswith(covered_code):
                    covered_matches.append(dx)
                    matched = True
                    break
            if not matched:
                uncovered.append(dx)

        is_necessary = len(covered_matches) > 0

        return MedNecessityResult(
            is_medically_necessary=is_necessary,
            lcd_ref=", ".join(lcd_refs),
            covered_dx_matches=covered_matches,
            uncovered_dx=uncovered,
            documentation_requirements=list(set(doc_reqs)),
            frequency_notes="; ".join(freq_notes) if freq_notes else "",
        )

    def get_covered_diagnoses(self, procedure_code: str) -> list[str]:
        """Return all ICD-10 codes that satisfy medical necessity for a procedure.

        Parameters
        ----------
        procedure_code:
            CPT / HCPCS code.

        Returns
        -------
        list[str]
            Covered ICD-10 codes.
        """
        lcds = self._lcd_by_procedure.get(procedure_code, [])
        result: set[str] = set()
        for lcd in lcds:
            result.update(lcd.covered_icd10_codes)
        return sorted(result)

    def get_documentation_requirements(self, procedure_code: str) -> list[str]:
        """Return documentation requirements for a procedure.

        Parameters
        ----------
        procedure_code:
            CPT / HCPCS code.

        Returns
        -------
        list[str]
            Documentation requirements.
        """
        lcds = self._lcd_by_procedure.get(procedure_code, [])
        reqs: list[str] = []
        for lcd in lcds:
            reqs.extend(lcd.documentation_requirements)
        return list(set(reqs))

    # -- Stats -------------------------------------------------------------

    @property
    def lcd_count(self) -> int:
        """Total number of LCDs loaded."""
        return len(self._lcds)

    def __repr__(self) -> str:
        return f"MedicalNecessityEngine(lcds={self.lcd_count})"
