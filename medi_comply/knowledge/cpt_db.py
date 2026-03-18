"""
MEDI-COMPLY — CPT code database with modifiers and RVUs.

Provides lookups, modifier validation, add-on detection, and global-period
information for CPT / HCPCS codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class CPTModifier:
    """A CPT modifier definition."""

    code: str
    description: str
    applies_to_categories: list[str] = field(default_factory=list)


@dataclass
class CPTCodeEntry:
    """Complete structured representation of a CPT / HCPCS code."""

    code: str
    description: str
    long_description: str = ""
    category: str = ""
    subcategory: str = ""
    rvu_work: float = 0.0
    rvu_facility_pe: float = 0.0
    rvu_malpractice: float = 0.0
    is_add_on: bool = False
    is_separate_procedure: bool = False
    requires_modifier: bool = False
    common_modifiers: list[str] = field(default_factory=list)
    global_period: str = "XXX"
    professional_component: bool = False
    technical_component: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "code": self.code,
            "description": self.description,
            "long_description": self.long_description,
            "category": self.category,
            "subcategory": self.subcategory,
            "rvu_work": self.rvu_work,
            "rvu_facility_pe": self.rvu_facility_pe,
            "rvu_malpractice": self.rvu_malpractice,
            "is_add_on": self.is_add_on,
            "is_separate_procedure": self.is_separate_procedure,
            "requires_modifier": self.requires_modifier,
            "common_modifiers": self.common_modifiers,
            "global_period": self.global_period,
            "professional_component": self.professional_component,
            "technical_component": self.technical_component,
        }

    @property
    def total_rvu(self) -> float:
        """Sum of work + facility PE + malpractice RVUs."""
        return self.rvu_work + self.rvu_facility_pe + self.rvu_malpractice


# ---------------------------------------------------------------------------
# CPT Database
# ---------------------------------------------------------------------------


class CPTDatabase:
    """In-memory CPT / HCPCS code database.

    Provides O(1) code existence checks, modifier validation, add-on
    detection, and keyword search.
    """

    def __init__(self) -> None:
        self._codes: dict[str, CPTCodeEntry] = {}
        self._modifiers: dict[str, CPTModifier] = {}
        self._description_index: dict[str, list[str]] = {}

    # -- Loading -----------------------------------------------------------

    def load(self, entries: list[CPTCodeEntry]) -> None:
        """Bulk-load CPT code entries.

        Parameters
        ----------
        entries:
            List of :class:`CPTCodeEntry` to load.
        """
        for entry in entries:
            self._codes[entry.code] = entry
        self._rebuild_index()

    def load_modifiers(self, modifiers: list[CPTModifier]) -> None:
        """Load modifier definitions.

        Parameters
        ----------
        modifiers:
            List of :class:`CPTModifier` to load.
        """
        for mod in modifiers:
            self._modifiers[mod.code] = mod

    def _rebuild_index(self) -> None:
        """Build an inverted keyword index over descriptions."""
        self._description_index.clear()
        for code, entry in self._codes.items():
            text = f"{entry.description} {entry.long_description} {entry.category} {entry.subcategory}"
            for token in text.lower().split():
                cleaned = token.strip(".,;:()")
                if len(cleaned) > 2:
                    self._description_index.setdefault(cleaned, []).append(code)

    # -- Core lookups (O(1)) -----------------------------------------------

    def code_exists(self, code: str) -> bool:
        """O(1) hash lookup — anti-hallucination check for CPT codes."""
        return code in self._codes

    def get_code(self, code: str) -> Optional[CPTCodeEntry]:
        """Retrieve the full entry for a CPT code, or ``None``."""
        return self._codes.get(code)

    def is_add_on(self, code: str) -> bool:
        """Check whether a code is an add-on procedure."""
        entry = self._codes.get(code)
        return entry.is_add_on if entry else False

    # -- Modifiers ---------------------------------------------------------

    def get_modifier(self, modifier_code: str) -> Optional[CPTModifier]:
        """Look up a modifier definition."""
        return self._modifiers.get(modifier_code)

    def get_common_modifiers(self, cpt_code: str) -> list[str]:
        """Return the list of commonly used modifiers for a CPT code."""
        entry = self._codes.get(cpt_code)
        return list(entry.common_modifiers) if entry else []

    def modifier_exists(self, modifier_code: str) -> bool:
        """Check whether a modifier is defined."""
        return modifier_code in self._modifiers

    # -- RVU ---------------------------------------------------------------

    def get_rvu(self, code: str) -> Optional[float]:
        """Return total RVU for a code."""
        entry = self._codes.get(code)
        return entry.total_rvu if entry else None

    # -- Search ------------------------------------------------------------

    def search_by_description(self, text: str) -> list[CPTCodeEntry]:
        """Keyword search across CPT code descriptions.

        Parameters
        ----------
        text:
            Free-text query.

        Returns
        -------
        list[CPTCodeEntry]
            Matching entries sorted by relevance.
        """
        tokens = [t.strip(".,;:()").lower() for t in text.split() if len(t) > 2]
        if not tokens:
            return []
        scores: dict[str, int] = {}
        for token in tokens:
            for code in self._description_index.get(token, []):
                scores[code] = scores.get(code, 0) + 1
        ranked = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        return [self._codes[c] for c in ranked[:50] if c in self._codes]

    def get_codes_by_category(self, category: str) -> list[CPTCodeEntry]:
        """Return all codes in a given category.

        Parameters
        ----------
        category:
            Category string (e.g. ``"E/M"``, ``"Lab"``).
        """
        return [e for e in self._codes.values() if e.category == category]

    # -- Stats -------------------------------------------------------------

    @property
    def code_count(self) -> int:
        """Total number of CPT codes loaded."""
        return len(self._codes)

    @property
    def modifier_count(self) -> int:
        """Total number of modifiers loaded."""
        return len(self._modifiers)

    def __repr__(self) -> str:
        return f"CPTDatabase(codes={self.code_count}, modifiers={self.modifier_count})"
