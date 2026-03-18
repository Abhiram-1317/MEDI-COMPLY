"""
MEDI-COMPLY — ICD-10-CM code database with hierarchy and rule enforcement.

Provides O(1) code lookups, hierarchy traversal, Excludes1/2 checking,
age/gender validation, and specificity analysis.  This is the primary
anti-hallucination firewall — every code proposed by the AI is verified here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ICD10CodeEntry:
    """Complete structured representation of an ICD-10-CM code."""

    code: str
    description: str
    long_description: str = ""
    chapter: str = ""
    chapter_title: str = ""
    block: str = ""
    block_title: str = ""
    category: str = ""
    category_title: str = ""
    is_billable: bool = True
    parent_code: Optional[str] = None
    child_codes: list[str] = field(default_factory=list)
    excludes1: list[str] = field(default_factory=list)
    excludes2: list[str] = field(default_factory=list)
    includes_notes: list[str] = field(default_factory=list)
    code_first: list[str] = field(default_factory=list)
    use_additional: list[str] = field(default_factory=list)
    code_also: list[str] = field(default_factory=list)
    valid_for_gender: str = "BOTH"
    valid_age_range: tuple[int, int] = (0, 150)
    requires_7th_character: bool = False
    seventh_characters: dict[str, str] = field(default_factory=dict)
    manifestation_code: bool = False
    etiology_code: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "code": self.code,
            "description": self.description,
            "long_description": self.long_description,
            "chapter": self.chapter,
            "chapter_title": self.chapter_title,
            "block": self.block,
            "block_title": self.block_title,
            "category": self.category,
            "category_title": self.category_title,
            "is_billable": self.is_billable,
            "parent_code": self.parent_code,
            "child_codes": self.child_codes,
            "excludes1": self.excludes1,
            "excludes2": self.excludes2,
            "includes_notes": self.includes_notes,
            "code_first": self.code_first,
            "use_additional": self.use_additional,
            "code_also": self.code_also,
            "valid_for_gender": self.valid_for_gender,
            "valid_age_range": list(self.valid_age_range),
            "requires_7th_character": self.requires_7th_character,
            "seventh_characters": self.seventh_characters,
            "manifestation_code": self.manifestation_code,
            "etiology_code": self.etiology_code,
        }


@dataclass
class ValidationResult:
    """Outcome of validating an ICD-10 code assignment."""

    is_valid: bool
    code: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ICD-10 Database
# ---------------------------------------------------------------------------


class ICD10Database:
    """In-memory ICD-10-CM code database.

    Provides O(1) code existence checks (the hallucination firewall),
    hierarchy traversal, Excludes1/2 validation, and demographic checks.
    """

    def __init__(self) -> None:
        self._codes: dict[str, ICD10CodeEntry] = {}
        self._description_index: dict[str, list[str]] = {}

    # -- Loading -----------------------------------------------------------

    def load(self, entries: list[ICD10CodeEntry]) -> None:
        """Bulk-load code entries and rebuild keyword index.

        Parameters
        ----------
        entries:
            List of :class:`ICD10CodeEntry` to load.
        """
        for entry in entries:
            self._codes[entry.code] = entry
        self._rebuild_description_index()

    def _rebuild_description_index(self) -> None:
        """Build an inverted index from lowercase keywords to code lists."""
        self._description_index.clear()
        for code, entry in self._codes.items():
            tokens = set(entry.description.lower().split())
            tokens.update(entry.long_description.lower().split())
            for token in tokens:
                cleaned = token.strip(".,;:()")
                if len(cleaned) > 2:
                    self._description_index.setdefault(cleaned, []).append(code)

    # -- Core lookups (O(1)) -----------------------------------------------

    def code_exists(self, code: str) -> bool:
        """O(1) hash lookup — primary anti-hallucination check."""
        return code in self._codes

    def get_code(self, code: str) -> Optional[ICD10CodeEntry]:
        """Retrieve the full entry for a code, or ``None``."""
        return self._codes.get(code)

    def is_billable(self, code: str) -> bool:
        """Check whether a code is terminal / billable."""
        entry = self._codes.get(code)
        return entry.is_billable if entry else False

    def is_manifestation(self, code: str) -> bool:
        """Check whether a code is a manifestation (cannot be primary)."""
        entry = self._codes.get(code)
        return entry.manifestation_code if entry else False

    # -- Hierarchy ---------------------------------------------------------

    def get_parent(self, code: str) -> Optional[ICD10CodeEntry]:
        """Return the parent code entry, or ``None``."""
        entry = self._codes.get(code)
        if entry and entry.parent_code:
            return self._codes.get(entry.parent_code)
        return None

    def get_children(self, code: str) -> list[ICD10CodeEntry]:
        """Return direct child entries of a code."""
        entry = self._codes.get(code)
        if not entry:
            return []
        return [self._codes[c] for c in entry.child_codes if c in self._codes]

    def get_siblings(self, code: str) -> list[ICD10CodeEntry]:
        """Return sibling entries (children of the same parent, excluding self)."""
        parent = self.get_parent(code)
        if not parent:
            return []
        return [
            self._codes[c]
            for c in parent.child_codes
            if c != code and c in self._codes
        ]

    def has_higher_specificity(self, code: str) -> tuple[bool, list[str]]:
        """Check whether more specific child codes exist.

        Returns
        -------
        tuple[bool, list[str]]
            (has_children, list_of_child_codes)
        """
        entry = self._codes.get(code)
        if not entry:
            return False, []
        children = [c for c in entry.child_codes if c in self._codes]
        return bool(children), children

    # -- Excludes checking -------------------------------------------------

    def check_excludes1(self, code1: str, code2: str) -> tuple[bool, str]:
        """Check if two codes violate an Excludes1 rule.

        Excludes1 means the two conditions cannot occur together — the codes
        are mutually exclusive.

        Returns
        -------
        tuple[bool, str]
            (is_excluded, reason)
        """
        entry1 = self._codes.get(code1)
        entry2 = self._codes.get(code2)
        if not entry1 or not entry2:
            return False, ""

        # Direct match
        if code2 in entry1.excludes1:
            return True, f"{code1} Excludes1 {code2}: codes are mutually exclusive"
        if code1 in entry2.excludes1:
            return True, f"{code2} Excludes1 {code1}: codes are mutually exclusive"

        # Category-level match (e.g. E10.xx excludes E11.xx)
        for excl in entry1.excludes1:
            if excl.endswith(".xx") and code2.startswith(excl[:-3]):
                return True, f"{code1} Excludes1 {excl} (matches {code2})"
        for excl in entry2.excludes1:
            if excl.endswith(".xx") and code1.startswith(excl[:-3]):
                return True, f"{code2} Excludes1 {excl} (matches {code1})"

        return False, ""

    def check_excludes2(self, code1: str, code2: str) -> tuple[bool, str]:
        """Check if two codes have an Excludes2 relationship.

        Excludes2 means the condition *is* not included but *can* coexist
        if documented.

        Returns
        -------
        tuple[bool, str]
            (is_excluded, advisory_message)
        """
        entry1 = self._codes.get(code1)
        entry2 = self._codes.get(code2)
        if not entry1 or not entry2:
            return False, ""

        if code2 in entry1.excludes2:
            return True, (
                f"{code1} Excludes2 {code2}: not included here but may coexist "
                "if documented separately"
            )
        if code1 in entry2.excludes2:
            return True, (
                f"{code2} Excludes2 {code1}: not included here but may coexist "
                "if documented separately"
            )
        return False, ""

    # -- Instruction lookups -----------------------------------------------

    def get_use_additional_instructions(self, code: str) -> list[str]:
        """Return 'Use additional code' notes for a code."""
        entry = self._codes.get(code)
        return list(entry.use_additional) if entry else []

    def get_code_first_instructions(self, code: str) -> list[str]:
        """Return 'Code first' notes for a code."""
        entry = self._codes.get(code)
        return list(entry.code_first) if entry else []

    # -- Search ------------------------------------------------------------

    def search_by_description(self, text: str) -> list[ICD10CodeEntry]:
        """Keyword search across code descriptions.

        Parameters
        ----------
        text:
            Free-text query.  Each word is searched as a keyword.

        Returns
        -------
        list[ICD10CodeEntry]
            Matching entries sorted by relevance (number of keyword hits).
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

    # -- Validation --------------------------------------------------------

    def validate_code(
        self,
        code: str,
        patient_age: int = 30,
        patient_gender: str = "BOTH",
    ) -> ValidationResult:
        """Comprehensive validation of an ICD-10 code assignment.

        Checks existence, billability, specificity, age, gender, and
        manifestation status.

        Parameters
        ----------
        code:
            ICD-10-CM code string.
        patient_age:
            Patient age in years.
        patient_gender:
            ``"MALE"``, ``"FEMALE"``, or ``"BOTH"`` (unknown).

        Returns
        -------
        ValidationResult
        """
        result = ValidationResult(is_valid=True, code=code)

        # Existence
        entry = self._codes.get(code)
        if not entry:
            result.is_valid = False
            result.errors.append(f"Code {code} does not exist in ICD-10-CM database")
            return result

        # Billability
        if not entry.is_billable:
            result.is_valid = False
            has_children, children = self.has_higher_specificity(code)
            result.errors.append(
                f"Code {code} is not billable (category-level code)"
            )
            if has_children:
                result.suggestions.extend(
                    [f"Consider more specific code: {c}" for c in children[:5]]
                )

        # Specificity warning
        has_children, children = self.has_higher_specificity(code)
        if has_children and entry.is_billable:
            result.warnings.append(
                f"More specific codes available: {', '.join(children[:5])}"
            )

        # Age validation
        min_age, max_age = entry.valid_age_range
        if not (min_age <= patient_age <= max_age):
            result.is_valid = False
            result.errors.append(
                f"Code {code} not valid for age {patient_age} "
                f"(valid range: {min_age}-{max_age})"
            )

        # Gender validation
        if entry.valid_for_gender != "BOTH" and patient_gender != "BOTH":
            if entry.valid_for_gender != patient_gender:
                result.is_valid = False
                result.errors.append(
                    f"Code {code} is only valid for gender "
                    f"'{entry.valid_for_gender}', patient is '{patient_gender}'"
                )

        # Manifestation check
        if entry.manifestation_code:
            result.warnings.append(
                f"Code {code} is a manifestation code — cannot be sequenced as "
                "the principal/primary diagnosis"
            )

        # Use-additional instructions
        if entry.use_additional:
            for note in entry.use_additional:
                result.warnings.append(f"Use additional: {note}")

        return result

    # -- Stats -------------------------------------------------------------

    @property
    def code_count(self) -> int:
        """Total number of codes loaded."""
        return len(self._codes)

    def __repr__(self) -> str:
        return f"ICD10Database(codes={self.code_count})"
