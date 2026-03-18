"""
MEDI-COMPLY — Clinical note section parser.

Detects standard clinical note sections (HPI, Assessment, etc.) via
regex-based header matching and extracts section boundaries with
character offsets for evidence tracking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from medi_comply.nlp.document_ingester import IngestedDocument


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClinicalSection:
    """A detected section within a clinical note."""
    section_type: str
    header_text: str
    content: str
    start_char: int = 0
    end_char: int = 0
    start_line: int = 0
    end_line: int = 0
    page: int = 1
    confidence: float = 0.95


# ---------------------------------------------------------------------------
# Section header definitions
# ---------------------------------------------------------------------------

SECTION_HEADERS: dict[str, list[str]] = {
    "CHIEF_COMPLAINT": [
        "chief complaint", "cc", "reason for visit", "presenting complaint",
        "reason for encounter", "chief concern",
    ],
    "HPI": [
        "history of present illness", "hpi", "history of the present illness",
        "present illness", "subjective",
    ],
    "PAST_MEDICAL_HISTORY": [
        "past medical history", "pmh", "pmhx", "medical history",
        "past history", "significant past history",
    ],
    "MEDICATIONS": [
        "medications", "current medications", "meds", "medication list",
        "home medications", "home meds", "active medications",
    ],
    "ALLERGIES": [
        "allergies", "drug allergies", "allergy", "adverse reactions", "nkda",
    ],
    "REVIEW_OF_SYSTEMS": [
        "review of systems", "ros", "systems review", "system review",
    ],
    "PHYSICAL_EXAM": [
        "physical exam", "physical examination", "pe", "exam", "objective",
        "examination", "physical findings",
    ],
    "VITALS": [
        "vital signs", "vitals", "vs",
    ],
    "LABS": [
        "laboratory", "lab results", "labs", "laboratory results",
        "laboratory data", "lab data", "diagnostics",
    ],
    "IMAGING": [
        "imaging", "radiology", "imaging results", "radiologic studies",
    ],
    "ASSESSMENT": [
        "assessment", "impression", "clinical impression", "diagnosis",
        "diagnoses", "assessment and plan", "a/p", "assessment/plan",
    ],
    "PLAN": [
        "plan", "treatment plan", "recommendations", "disposition",
    ],
    "PROCEDURES": [
        "procedures", "procedures performed", "operative findings",
        "procedure note", "operations",
    ],
}

# Pre-compile pattern: headers followed by optional ":" or newline
_HEADER_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = []


def _build_patterns() -> None:
    """Build compiled regex patterns for section headers."""
    if _HEADER_PATTERNS:
        return
    for section_type, variants in SECTION_HEADERS.items():
        for variant in variants:
            escaped = re.escape(variant)
            # For short abbreviations (2-4 chars), require word boundary
            if len(variant) <= 4 and variant.isalpha():
                anchor = rf"\b{escaped}\b"
            else:
                anchor = escaped
            # Allow header followed by colon (with optional text), dash, or newline
            pat = re.compile(
                rf"(?:^|\n)\s*(?:{anchor})\s*[:\-]\s*",
                re.IGNORECASE | re.MULTILINE,
            )
            _HEADER_PATTERNS.append((section_type, variant, pat))
    # Sort by longest pattern first so "assessment and plan" matches before "assessment"
    _HEADER_PATTERNS.sort(key=lambda x: -len(x[1]))


# ---------------------------------------------------------------------------
# Section Parser
# ---------------------------------------------------------------------------

class ClinicalSectionParser:
    """Parses clinical notes into recognized sections.

    Uses regex-based header detection (case-insensitive).  Headers are
    typically followed by ``:``, ``-``, or a newline.  Section content is
    the text between consecutive headers.
    """

    def parse(self, document: IngestedDocument) -> list[ClinicalSection]:
        """Parse a document into clinical sections.

        Parameters
        ----------
        document:
            An ingested document.

        Returns
        -------
        list[ClinicalSection]
        """
        _build_patterns()
        text = document.raw_text
        if not text.strip():
            return []

        headers = self._find_section_headers(text)
        if not headers:
            # No recognized headers — return entire text as UNSTRUCTURED
            return [ClinicalSection(
                section_type="UNSTRUCTURED",
                header_text="",
                content=text,
                start_char=0,
                end_char=len(text),
                start_line=0,
                end_line=text.count("\n"),
                page=1,
                confidence=0.5,
            )]

        sections = self._extract_section_content(text, headers)
        sections = self._resolve_page_lines(sections, document)
        return sections

    def _find_section_headers(self, text: str) -> list[tuple[str, str, int, int]]:
        """Locate section headers in text.

        Returns
        -------
        list[tuple[str, str, int, int]]
            Each tuple: (section_type, header_text, match_start, content_start)
        """
        found: list[tuple[str, str, int, int]] = []
        used_spans: list[tuple[int, int]] = []

        for section_type, variant, pattern in _HEADER_PATTERNS:
            for m in pattern.finditer(text):
                start = m.start()
                end = m.end()
                # Skip if overlapping with an already-found header
                overlap = any(s <= start < e or s < end <= e for s, e in used_spans)
                if overlap:
                    continue
                header_text = m.group().strip().rstrip(":-").strip()
                found.append((section_type, header_text, start, end))
                used_spans.append((start, end))

        # Sort by position
        found.sort(key=lambda x: x[2])
        return found

    def _extract_section_content(
        self,
        text: str,
        headers: list[tuple[str, str, int, int]],
    ) -> list[ClinicalSection]:
        """Extract content between headers.

        Parameters
        ----------
        text:
            Full document text.
        headers:
            Sorted list of header locations.

        Returns
        -------
        list[ClinicalSection]
        """
        sections: list[ClinicalSection] = []
        for i, (section_type, header_text, match_start, content_start) in enumerate(headers):
            if i + 1 < len(headers):
                content_end = headers[i + 1][2]
            else:
                content_end = len(text)

            content = text[content_start:content_end].strip()
            sections.append(ClinicalSection(
                section_type=section_type,
                header_text=header_text,
                content=content,
                start_char=content_start,
                end_char=content_end,
                confidence=0.95,
            ))
        return sections

    def _resolve_page_lines(
        self,
        sections: list[ClinicalSection],
        document: IngestedDocument,
    ) -> list[ClinicalSection]:
        """Resolve page and line numbers using the document's char map.

        Parameters
        ----------
        sections:
            Sections to enrich.
        document:
            Source document with char-to-page-line map.

        Returns
        -------
        list[ClinicalSection]
        """
        sorted_keys = sorted(document.char_to_page_line_map.keys())
        for section in sections:
            section.page, section.start_line = self._lookup(
                section.start_char, sorted_keys, document.char_to_page_line_map
            )
            _, section.end_line = self._lookup(
                max(section.end_char - 1, 0), sorted_keys, document.char_to_page_line_map
            )
        return sections

    @staticmethod
    def _lookup(
        char_offset: int,
        sorted_keys: list[int],
        mapping: dict[int, tuple[int, int]],
    ) -> tuple[int, int]:
        """Binary-search the char map for the closest line.

        Parameters
        ----------
        char_offset:
            Character offset.
        sorted_keys:
            Sorted start-of-line offsets.
        mapping:
            char → (page, line) dict.

        Returns
        -------
        tuple[int, int]
            (page, line).
        """
        if not sorted_keys:
            return 1, 0
        lo, hi = 0, len(sorted_keys) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sorted_keys[mid] <= char_offset:
                lo = mid
            else:
                hi = mid - 1
        return mapping.get(sorted_keys[lo], (1, 0))

    # -- Utilities ---------------------------------------------------------

    @staticmethod
    def get_section(
        sections: list[ClinicalSection],
        section_type: str,
    ) -> Optional[ClinicalSection]:
        """Return the first section matching a given type.

        Parameters
        ----------
        sections:
            Parsed sections.
        section_type:
            Section type to find.

        Returns
        -------
        Optional[ClinicalSection]
        """
        for s in sections:
            if s.section_type == section_type:
                return s
        return None

    @staticmethod
    def handle_numbered_lists(content: str) -> list[str]:
        """Parse numbered list items (``1. ... 2. ...`` patterns).

        Parameters
        ----------
        content:
            Section text.

        Returns
        -------
        list[str]
        """
        items = re.split(r"\n\s*\d+[\.\)]\s*", content)
        return [item.strip() for item in items if item.strip()]
