"""
MEDI-COMPLY — NCCI edit checker (PTP bundling + Medically Unlikely Edits).

Enforces CMS National Correct Coding Initiative rules to prevent
improper code pair submissions and excessive unit billing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class NCCIEditPair:
    """A Procedure-to-Procedure (PTP) NCCI edit pair."""

    column1_code: str
    column2_code: str
    effective_date: str = "2024-01-01"
    deletion_date: Optional[str] = None
    modifier_indicator: str = "0"  # 0=no modifier, 1=modifier allowed, 9=N/A
    ptp_edit_rationale: str = "Standards of medical practice"


@dataclass
class MUEEntry:
    """Medically Unlikely Edit — maximum units per service."""

    cpt_code: str
    mue_value: int
    mue_rationale: str = "Clinical: nature of service"
    mue_adjudication: str = "line"  # "line" or "claim"


@dataclass
class NCCICheckResult:
    """Result of checking an NCCI PTP edit between two codes."""

    is_bundled: bool
    column1_code: str
    column2_code: str
    modifier_allowed: bool = False
    rationale: str = ""
    can_override_with_modifier: bool = False


@dataclass
class MUECheckResult:
    """Result of checking a Medically Unlikely Edit."""

    passes: bool
    max_units: int
    submitted_units: int
    rationale: str = ""
    cpt_code: str = ""


# ---------------------------------------------------------------------------
# NCCI Engine
# ---------------------------------------------------------------------------


class NCCIEngine:
    """Enforces CMS NCCI edits — PTP bundling and MUE limits.

    PTP edits identify pairs of CPT codes that should not be billed
    together.  MUEs cap the number of units for a single code.
    """

    def __init__(self) -> None:
        # key = frozenset({code1, code2}) for O(1) pair lookup
        self._edit_pairs: dict[frozenset[str], NCCIEditPair] = {}
        # Ordered lookup: (col1, col2) -> edit
        self._ordered_pairs: dict[tuple[str, str], NCCIEditPair] = {}
        self._mue_entries: dict[str, MUEEntry] = {}

    # -- Loading -----------------------------------------------------------

    def load_edit_pairs(self, pairs: list[NCCIEditPair]) -> None:
        """Bulk-load PTP edit pairs.

        Parameters
        ----------
        pairs:
            List of :class:`NCCIEditPair` to load.
        """
        for pair in pairs:
            key = frozenset({pair.column1_code, pair.column2_code})
            self._edit_pairs[key] = pair
            self._ordered_pairs[(pair.column1_code, pair.column2_code)] = pair

    def load_mue_entries(self, entries: list[MUEEntry]) -> None:
        """Bulk-load MUE entries.

        Parameters
        ----------
        entries:
            List of :class:`MUEEntry` to load.
        """
        for entry in entries:
            self._mue_entries[entry.cpt_code] = entry

    # -- PTP Checking ------------------------------------------------------

    def check_pair(self, cpt1: str, cpt2: str) -> NCCICheckResult:
        """Check whether two CPT codes have an NCCI PTP edit.

        Parameters
        ----------
        cpt1:
            First CPT code.
        cpt2:
            Second CPT code.

        Returns
        -------
        NCCICheckResult
        """
        key = frozenset({cpt1, cpt2})
        pair = self._edit_pairs.get(key)
        if not pair:
            return NCCICheckResult(
                is_bundled=False,
                column1_code=cpt1,
                column2_code=cpt2,
                rationale="No NCCI edit exists between these codes",
            )

        modifier_allowed = pair.modifier_indicator == "1"
        return NCCICheckResult(
            is_bundled=True,
            column1_code=pair.column1_code,
            column2_code=pair.column2_code,
            modifier_allowed=modifier_allowed,
            rationale=pair.ptp_edit_rationale,
            can_override_with_modifier=modifier_allowed,
        )

    def check_all_pairs(self, cpt_codes: list[str]) -> list[NCCICheckResult]:
        """Check every pairwise combination for NCCI edits.

        Parameters
        ----------
        cpt_codes:
            List of CPT codes to cross-check.

        Returns
        -------
        list[NCCICheckResult]
            Only results where ``is_bundled`` is ``True``.
        """
        results: list[NCCICheckResult] = []
        for c1, c2 in combinations(cpt_codes, 2):
            result = self.check_pair(c1, c2)
            if result.is_bundled:
                results.append(result)
        return results

    def check_mutually_exclusive(self, cpt1: str, cpt2: str) -> bool:
        """Check if two codes are mutually exclusive (bundled, no modifier)."""
        result = self.check_pair(cpt1, cpt2)
        return result.is_bundled and not result.modifier_allowed

    # -- MUE Checking ------------------------------------------------------

    def check_mue(self, cpt_code: str, units: int) -> MUECheckResult:
        """Check whether submitted units exceed the MUE limit.

        Parameters
        ----------
        cpt_code:
            CPT code to check.
        units:
            Number of units submitted.

        Returns
        -------
        MUECheckResult
        """
        entry = self._mue_entries.get(cpt_code)
        if not entry:
            return MUECheckResult(
                passes=True,
                max_units=999,
                submitted_units=units,
                rationale="No MUE entry found — no unit limit enforced",
                cpt_code=cpt_code,
            )

        passes = units <= entry.mue_value
        return MUECheckResult(
            passes=passes,
            max_units=entry.mue_value,
            submitted_units=units,
            rationale=entry.mue_rationale if not passes else "Within MUE limit",
            cpt_code=cpt_code,
        )

    # -- Stats -------------------------------------------------------------

    @property
    def edit_pair_count(self) -> int:
        """Total number of PTP edit pairs loaded."""
        return len(self._edit_pairs)

    @property
    def mue_count(self) -> int:
        """Total number of MUE entries loaded."""
        return len(self._mue_entries)

    def __repr__(self) -> str:
        return f"NCCIEngine(pairs={self.edit_pair_count}, mue={self.mue_count})"
