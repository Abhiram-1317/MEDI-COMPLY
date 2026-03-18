"""
MEDI-COMPLY — Evidence tracker for source span tracking.

Maintains a complete chain from every extracted entity back to its exact
location (page, line, char offset) in the source document.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from medi_comply.nlp.document_ingester import IngestedDocument
from medi_comply.nlp.section_parser import ClinicalSection


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SourceEvidence:
    """Evidence linking an entity to its source location."""
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    entity_id: str = ""
    section: str = ""
    page: int = 1
    line: int = 0
    char_offset: tuple[int, int] = (0, 0)
    exact_text: str = ""
    surrounding_text: str = ""
    confidence: float = 0.95
    extraction_method: str = "RULE_BASED"


# ---------------------------------------------------------------------------
# Evidence Tracker
# ---------------------------------------------------------------------------

class EvidenceTracker:
    """Tracks and verifies source evidence for extracted entities.

    Every entity extracted by the NLP pipeline is linked back to an
    exact position in the source document via a :class:`SourceEvidence`
    record.
    """

    def create_evidence(
        self,
        entity_text: str,
        document: IngestedDocument,
        section: Optional[ClinicalSection],
        char_start: int,
        char_end: int,
        entity_id: str = "",
        extraction_method: str = "RULE_BASED",
        context_window: int = 100,
    ) -> SourceEvidence:
        """Create an evidence record for an entity.

        Parameters
        ----------
        entity_text:
            The raw text of the entity.
        document:
            The source document.
        section:
            The section the entity was found in, if known.
        char_start:
            Absolute character start offset.
        char_end:
            Absolute character end offset.
        entity_id:
            UUID of the linked entity.
        extraction_method:
            ``"RULE_BASED"``, ``"LLM_EXTRACTED"``, or ``"HYBRID"``.
        context_window:
            Number of chars before/after for surrounding text.

        Returns
        -------
        SourceEvidence
        """
        page, line = self.map_char_to_page_line(char_start, document)
        surrounding = self.get_surrounding_text(document, char_start, char_end, context_window)

        return SourceEvidence(
            entity_id=entity_id,
            section=section.section_type if section else "",
            page=page,
            line=line,
            char_offset=(char_start, char_end),
            exact_text=entity_text,
            surrounding_text=surrounding,
            extraction_method=extraction_method,
        )

    def verify_evidence(self, evidence: SourceEvidence, document: IngestedDocument) -> bool:
        """Verify that the text at the stated offset actually matches.

        Parameters
        ----------
        evidence:
            The evidence record to verify.
        document:
            The source document.

        Returns
        -------
        bool
        """
        start, end = evidence.char_offset
        if start < 0 or end > len(document.raw_text):
            return False
        actual = document.raw_text[start:end]
        return actual.lower() == evidence.exact_text.lower()

    def get_surrounding_text(
        self,
        document: IngestedDocument,
        char_start: int,
        char_end: int,
        window: int = 100,
    ) -> str:
        """Get text surrounding a span with an ellipsis border.

        Parameters
        ----------
        document:
            Source document.
        char_start:
            Start of the target span.
        char_end:
            End of the target span.
        window:
            Characters to include before and after.

        Returns
        -------
        str
        """
        text = document.raw_text
        ctx_start = max(0, char_start - window)
        ctx_end = min(len(text), char_end + window)
        prefix = "..." if ctx_start > 0 else ""
        suffix = "..." if ctx_end < len(text) else ""
        return prefix + text[ctx_start:ctx_end] + suffix

    def map_char_to_page_line(
        self,
        char_offset: int,
        document: IngestedDocument,
    ) -> tuple[int, int]:
        """Map a character offset to (page, line).

        Parameters
        ----------
        char_offset:
            Absolute character offset.
        document:
            Source document with char-to-page-line map.

        Returns
        -------
        tuple[int, int]
        """
        if not document.char_to_page_line_map:
            return 1, 0

        sorted_keys = sorted(document.char_to_page_line_map.keys())
        # Binary search for the closest key <= char_offset
        lo, hi = 0, len(sorted_keys) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if sorted_keys[mid] <= char_offset:
                lo = mid
            else:
                hi = mid - 1
        return document.char_to_page_line_map.get(sorted_keys[lo], (1, 0))
