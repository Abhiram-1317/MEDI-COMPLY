"""
MEDI-COMPLY — Document ingester for multiple clinical input formats.

Accepts plain text, PDF, FHIR JSON, and structured dict inputs, producing
a unified IngestedDocument with page/line offset mappings for evidence tracking.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PageInfo:
    """A single page of an ingested document."""
    page_number: int
    text: str
    line_offsets: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class IngestedDocument:
    """Unified representation of an ingested clinical document."""
    document_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_type: str = "PLAIN_TEXT"
    raw_text: str = ""
    pages: list[PageInfo] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    char_to_page_line_map: dict[int, tuple[int, int]] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Document type keywords
# ---------------------------------------------------------------------------

_DOC_TYPE_PATTERNS: dict[str, list[str]] = {
    "DISCHARGE_SUMMARY": ["discharge summary", "discharge date", "admission date", "discharge diagnosis"],
    "ER_NOTE": ["emergency department", "emergency room", "triage", "chief complaint", "ED course"],
    "OP_NOTE": ["operative report", "preoperative diagnosis", "postoperative diagnosis", "procedure performed",
                "anesthesia", "surgeon"],
    "CONSULTATION": ["consultation", "consult note", "reason for consultation", "consulting physician"],
    "PROGRESS_NOTE": ["progress note", "subjective", "objective", "assessment and plan", "daily note"],
}


# ---------------------------------------------------------------------------
# Document Ingester
# ---------------------------------------------------------------------------

class DocumentIngester:
    """Accepts clinical documents in multiple formats and produces a unified
    :class:`IngestedDocument` with character-to-page-line mappings.

    Supported formats: plain text, PDF, FHIR JSON, structured dict.
    """

    def ingest(
        self,
        input_data: Union[str, bytes, dict, Path],
        source_type: str = "auto",
    ) -> IngestedDocument:
        """Ingest a clinical document.

        Parameters
        ----------
        input_data:
            Raw input — a string, bytes (PDF), dict (FHIR/structured), or file path.
        source_type:
            One of ``"auto"``, ``"PLAIN_TEXT"``, ``"PDF"``, ``"FHIR"``, ``"STRUCTURED"``.

        Returns
        -------
        IngestedDocument
        """
        if source_type == "auto":
            source_type = self._detect_source_type(input_data)

        if source_type == "PLAIN_TEXT":
            return self._ingest_text(str(input_data))
        elif source_type == "PDF":
            return self._ingest_pdf(input_data)
        elif source_type == "FHIR":
            if not isinstance(input_data, dict):
                raise ValueError("FHIR source requires dict input")
            return self._ingest_fhir(input_data)
        elif source_type == "STRUCTURED":
            if not isinstance(input_data, dict):
                raise ValueError("STRUCTURED source requires dict input")
            return self._ingest_structured(input_data)
        else:
            raise ValueError(f"Unknown source_type: {source_type}")

    # -- Internal methods --------------------------------------------------

    def _detect_source_type(self, input_data: Any) -> str:
        """Auto-detect the source type of the input data.

        Parameters
        ----------
        input_data:
            The raw input.

        Returns
        -------
        str
        """
        if isinstance(input_data, bytes):
            return "PDF"
        if isinstance(input_data, Path):
            if input_data.suffix.lower() == ".pdf":
                return "PDF"
            return "PLAIN_TEXT"
        if isinstance(input_data, dict):
            if "resourceType" in input_data:
                return "FHIR"
            return "STRUCTURED"
        return "PLAIN_TEXT"

    def _ingest_text(self, text: str) -> IngestedDocument:
        """Ingest plain text.

        Parameters
        ----------
        text:
            Raw clinical note text.

        Returns
        -------
        IngestedDocument
        """
        lines = text.split("\n")
        line_offsets: list[tuple[int, int]] = []
        offset = 0
        for line in lines:
            line_offsets.append((offset, offset + len(line)))
            offset += len(line) + 1  # +1 for newline

        page = PageInfo(page_number=1, text=text, line_offsets=line_offsets)
        doc = IngestedDocument(
            source_type="PLAIN_TEXT",
            raw_text=text,
            pages=[page],
            metadata={"document_type": self._detect_document_type(text)},
        )
        doc.char_to_page_line_map = self._build_char_map(doc.pages)
        return doc

    def _ingest_pdf(self, pdf_data: Union[bytes, Path]) -> IngestedDocument:
        """Ingest a PDF document.

        Falls back to treating the path/bytes as plain text if PDF libraries
        are unavailable.

        Parameters
        ----------
        pdf_data:
            PDF bytes or file path.

        Returns
        -------
        IngestedDocument
        """
        text = ""
        pages: list[PageInfo] = []

        # Try pdfplumber first, then PyPDF2
        try:
            import pdfplumber  # type: ignore

            path = pdf_data if isinstance(pdf_data, (str, Path)) else None
            if path:
                with pdfplumber.open(path) as pdf:
                    for i, page in enumerate(pdf.pages):
                        page_text = page.extract_text() or ""
                        pages.append(self._make_page(i + 1, page_text, len(text)))
                        text += page_text + "\n"
            else:
                import io
                with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:  # type: ignore[arg-type]
                    for i, page in enumerate(pdf.pages):
                        page_text = page.extract_text() or ""
                        pages.append(self._make_page(i + 1, page_text, len(text)))
                        text += page_text + "\n"
        except ImportError:
            try:
                from PyPDF2 import PdfReader  # type: ignore
                import io
                reader = PdfReader(io.BytesIO(pdf_data) if isinstance(pdf_data, bytes) else str(pdf_data))
                for i, page in enumerate(reader.pages):
                    page_text = page.extract_text() or ""
                    pages.append(self._make_page(i + 1, page_text, len(text)))
                    text += page_text + "\n"
            except ImportError:
                # Fallback: treat as text
                if isinstance(pdf_data, bytes):
                    text = pdf_data.decode("utf-8", errors="replace")
                else:
                    text = Path(pdf_data).read_text(encoding="utf-8", errors="replace")
                return self._ingest_text(text)

        doc = IngestedDocument(
            source_type="PDF",
            raw_text=text.rstrip(),
            pages=pages,
            metadata={"document_type": self._detect_document_type(text)},
        )
        doc.char_to_page_line_map = self._build_char_map(doc.pages)
        return doc

    def _ingest_fhir(self, fhir_json: dict) -> IngestedDocument:
        """Ingest a FHIR DocumentReference or DiagnosticReport.

        Parameters
        ----------
        fhir_json:
            FHIR resource dict.

        Returns
        -------
        IngestedDocument
        """
        resource_type = fhir_json.get("resourceType", "")
        text = ""
        metadata: dict[str, Any] = {}

        if resource_type == "DocumentReference":
            for content in fhir_json.get("content", []):
                attachment = content.get("attachment", {})
                if "data" in attachment:
                    import base64
                    text = base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
                elif "url" in attachment:
                    text = attachment.get("url", "")
            metadata["patient_id"] = fhir_json.get("subject", {}).get("reference", "")
            metadata["document_date"] = fhir_json.get("date", "")
        elif resource_type == "DiagnosticReport":
            text = fhir_json.get("conclusion", "")
            for pv in fhir_json.get("presentedForm", []):
                if "data" in pv:
                    import base64
                    text = base64.b64decode(pv["data"]).decode("utf-8", errors="replace")
            metadata["patient_id"] = fhir_json.get("subject", {}).get("reference", "")
        else:
            # Generic: try to find text field
            text = fhir_json.get("text", {}).get("div", "")
            text = re.sub(r"<[^>]+>", "", text)  # Strip HTML

        doc = self._ingest_text(text)
        doc.source_type = "FHIR"
        doc.metadata.update(metadata)
        return doc

    def _ingest_structured(self, data: dict) -> IngestedDocument:
        """Ingest a structured dict with pre-separated sections.

        Parameters
        ----------
        data:
            Dict with ``"text"`` or ``"sections"`` key.

        Returns
        -------
        IngestedDocument
        """
        text = data.get("text", "")
        if not text and "sections" in data:
            parts = []
            for sec_name, sec_text in data["sections"].items():
                parts.append(f"{sec_name}:\n{sec_text}\n")
            text = "\n".join(parts)

        doc = self._ingest_text(text)
        doc.source_type = "STRUCTURED"
        for key in ("patient_id", "encounter_id", "document_date", "provider_name"):
            if key in data:
                doc.metadata[key] = data[key]
        return doc

    # -- Helpers -----------------------------------------------------------

    def _make_page(self, page_num: int, text: str, global_offset: int) -> PageInfo:
        """Build a PageInfo with line offsets relative to global text.

        Parameters
        ----------
        page_num:
            Page number.
        text:
            Page text.
        global_offset:
            Starting char offset in the full document text.

        Returns
        -------
        PageInfo
        """
        lines = text.split("\n")
        offsets: list[tuple[int, int]] = []
        offset = global_offset
        for line in lines:
            offsets.append((offset, offset + len(line)))
            offset += len(line) + 1
        return PageInfo(page_number=page_num, text=text, line_offsets=offsets)

    def _build_char_map(self, pages: list[PageInfo]) -> dict[int, tuple[int, int]]:
        """Build absolute char offset → (page, line) mapping.

        Parameters
        ----------
        pages:
            Pages with line offsets.

        Returns
        -------
        dict[int, tuple[int, int]]
            Maps the *start* char of each line to (page_number, line_index).
        """
        result: dict[int, tuple[int, int]] = {}
        for page in pages:
            for line_idx, (start, _) in enumerate(page.line_offsets):
                result[start] = (page.page_number, line_idx)
        return result

    def _detect_document_type(self, text: str) -> str:
        """Classify the document type from content keywords.

        Parameters
        ----------
        text:
            Full document text.

        Returns
        -------
        str
        """
        text_lower = text.lower()
        best_type = "PROGRESS_NOTE"
        best_score = 0
        for doc_type, keywords in _DOC_TYPE_PATTERNS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > best_score:
                best_score = score
                best_type = doc_type
        return best_type
