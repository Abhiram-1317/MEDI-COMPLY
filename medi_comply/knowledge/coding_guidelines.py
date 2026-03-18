"""
MEDI-COMPLY — Official Coding Guidelines store.

Stores ICD-10-CM Official Guidelines for Coding and Reporting, enabling
lookup by guideline ID, keyword search, and code-based retrieval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CodingExample:
    """A concrete coding example within a guideline."""

    scenario: str
    correct_coding: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class CodingGuideline:
    """An ICD-10-CM Official Coding Guideline entry."""

    guideline_id: str
    section: str
    subsection: str = ""
    chapter: str = ""
    title: str = ""
    rule_text: str = ""
    applies_to_codes: list[str] = field(default_factory=list)
    scenario_keywords: list[str] = field(default_factory=list)
    examples: list[CodingExample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "guideline_id": self.guideline_id,
            "section": self.section,
            "subsection": self.subsection,
            "chapter": self.chapter,
            "title": self.title,
            "rule_text": self.rule_text,
            "applies_to_codes": self.applies_to_codes,
            "scenario_keywords": self.scenario_keywords,
            "examples": [
                {"scenario": e.scenario, "correct_coding": e.correct_coding, "explanation": e.explanation}
                for e in self.examples
            ],
        }


# ---------------------------------------------------------------------------
# Coding Guidelines Store
# ---------------------------------------------------------------------------


class CodingGuidelinesStore:
    """Indexed store for ICD-10-CM Official Coding Guidelines.

    Supports lookup by ID, keyword-based search, and code-based retrieval.
    """

    def __init__(self) -> None:
        self._guidelines: dict[str, CodingGuideline] = {}
        self._code_index: dict[str, list[str]] = {}  # code prefix -> guideline IDs
        self._keyword_index: dict[str, list[str]] = {}  # keyword -> guideline IDs

    # -- Loading -----------------------------------------------------------

    def load(self, guidelines: list[CodingGuideline]) -> None:
        """Bulk-load guideline entries and rebuild indexes.

        Parameters
        ----------
        guidelines:
            List of :class:`CodingGuideline` to load.
        """
        for g in guidelines:
            self._guidelines[g.guideline_id] = g
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        """Build code and keyword inverted indexes."""
        self._code_index.clear()
        self._keyword_index.clear()
        for gid, g in self._guidelines.items():
            # Code index
            for code_range in g.applies_to_codes:
                self._code_index.setdefault(code_range, []).append(gid)
            # Keyword index
            all_keywords = list(g.scenario_keywords)
            all_keywords.extend(g.title.lower().split())
            for kw in all_keywords:
                cleaned = kw.strip(".,;:()\"'").lower()
                if len(cleaned) > 2:
                    self._keyword_index.setdefault(cleaned, []).append(gid)

    # -- Lookups -----------------------------------------------------------

    def get_guideline(self, guideline_id: str) -> Optional[CodingGuideline]:
        """Retrieve a guideline by its ID.

        Parameters
        ----------
        guideline_id:
            Guideline identifier (e.g. ``"OCG-I-C-4-a"``).

        Returns
        -------
        Optional[CodingGuideline]
        """
        return self._guidelines.get(guideline_id)

    def search_guidelines(self, keywords: list[str]) -> list[CodingGuideline]:
        """Search guidelines by keywords.

        Parameters
        ----------
        keywords:
            List of search terms.

        Returns
        -------
        list[CodingGuideline]
            Matching guidelines sorted by relevance.
        """
        scores: dict[str, int] = {}
        for kw in keywords:
            cleaned = kw.strip(".,;:()\"'").lower()
            for gid in self._keyword_index.get(cleaned, []):
                scores[gid] = scores.get(gid, 0) + 1
        ranked = sorted(scores.keys(), key=lambda g: scores[g], reverse=True)
        return [self._guidelines[g] for g in ranked if g in self._guidelines]

    def get_guidelines_for_code(self, icd10_code: str) -> list[CodingGuideline]:
        """Find guidelines that apply to a specific ICD-10 code.

        Matches against both exact codes and code range prefixes in
        ``applies_to_codes``.

        Parameters
        ----------
        icd10_code:
            ICD-10-CM code string.

        Returns
        -------
        list[CodingGuideline]
        """
        results: set[str] = set()
        for code_range, gids in self._code_index.items():
            # Range match:  "E08-E13" matches E11.22
            if "-" in code_range:
                parts = code_range.split("-")
                if len(parts) == 2:
                    start, end = parts[0].strip(), parts[1].strip()
                    code_prefix = icd10_code[:len(start)]
                    if start <= code_prefix <= end:
                        results.update(gids)
            # Prefix match: "I10" matches I10
            elif icd10_code.startswith(code_range):
                results.update(gids)
            # Exact match
            elif icd10_code == code_range:
                results.update(gids)
        return [self._guidelines[g] for g in results if g in self._guidelines]

    def get_guidelines_for_scenario(self, scenario_description: str) -> list[CodingGuideline]:
        """Find guidelines relevant to a clinical scenario description.

        Parameters
        ----------
        scenario_description:
            Free-text description of the clinical scenario.

        Returns
        -------
        list[CodingGuideline]
        """
        tokens = [t.strip(".,;:()\"'").lower() for t in scenario_description.split()]
        return self.search_guidelines(tokens)

    def generate_citation(self, guideline_id: str) -> str:
        """Generate a human-readable citation string for a guideline.

        Parameters
        ----------
        guideline_id:
            Guideline identifier.

        Returns
        -------
        str
            Citation string (e.g. ``"Per OCG Section I.C.4.a — Diabetes..."``).
        """
        g = self._guidelines.get(guideline_id)
        if not g:
            return f"Guideline {guideline_id} not found"
        section_path = f"Section {g.section}"
        if g.subsection:
            section_path += f".{g.subsection}"
        if g.chapter:
            section_path += f".{g.chapter}"
        return f"Per OCG {section_path} — {g.title}"

    # -- Stats -------------------------------------------------------------

    @property
    def guideline_count(self) -> int:
        """Total number of guidelines loaded."""
        return len(self._guidelines)

    def __repr__(self) -> str:
        return f"CodingGuidelinesStore(guidelines={self.guideline_count})"
