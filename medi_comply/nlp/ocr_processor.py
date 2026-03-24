"""OCR processing utilities for MEDI-COMPLY.

This module ingests clinical documents (PDF, images, structured text), runs OCR
when needed, and produces layout-aware text with character offsets suitable for
evidence tracking in audit trails. All models use Pydantic v2.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

import io

try:  # Optional dependency
    pytesseract = importlib.import_module("pytesseract")
    from PIL import Image
except Exception:  # pragma: no cover - optional
    pytesseract = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

try:  # Optional dependency
    pdf2image = importlib.import_module("pdf2image")
    convert_from_bytes = getattr(pdf2image, "convert_from_bytes", None)
except Exception:  # pragma: no cover - optional
    convert_from_bytes = None  # type: ignore[assignment]

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class DocumentFormat(str, Enum):
    PDF = "PDF"
    PNG = "PNG"
    JPEG = "JPEG"
    TIFF = "TIFF"
    BMP = "BMP"
    TEXT = "TEXT"
    FHIR_JSON = "FHIR_JSON"
    HL7_MESSAGE = "HL7_MESSAGE"


class OCRProvider(str, Enum):
    TESSERACT = "TESSERACT"
    AZURE = "AZURE"
    MOCK = "MOCK"


# ---------------------------------------------------------------------------
# Layout models
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class LineLayout(BaseModel):
    line_number: int
    text: str
    bounding_box: Optional[BoundingBox] = None
    char_offset_start: int
    char_offset_end: int
    confidence: float = Field(ge=0.0, le=1.0)
    page_number: int


class PageLayout(BaseModel):
    page_number: int
    width: float
    height: float
    lines: List[LineLayout] = Field(default_factory=list)
    raw_text: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


class OCRResult(BaseModel):
    document_id: str
    document_hash: str
    source_format: DocumentFormat
    provider_used: OCRProvider
    total_pages: int
    pages: List[PageLayout]
    full_text: str
    processing_time_ms: float
    overall_confidence: float
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_successful: bool = True


class IngestionResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    ocr_result: OCRResult
    position_tracker: "TextPositionTracker"
    document_fingerprint: str
    is_clean: bool
    ingestion_time_ms: float


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


class DocumentFingerprinter:
    """Provides SHA-256 fingerprints for audit integrity."""

    @staticmethod
    def fingerprint(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def fingerprint_file(file_path: str) -> str:
        with open(file_path, "rb") as handle:
            return DocumentFingerprinter.fingerprint(handle.read())

    @staticmethod
    def verify(content: bytes, expected_hash: str) -> bool:
        return DocumentFingerprinter.fingerprint(content) == expected_hash


class TextPositionTracker:
    """Tracks character offsets per page/line for evidence mapping."""

    def __init__(self) -> None:
        self._page_ranges: List[Tuple[int, int, int]] = []  # (page, start, end)
        self._lines: Dict[Tuple[int, int], str] = {}
        self._index_built = False

    def add_page(self, page_number: int, text: str) -> Tuple[int, int]:
        start = self._page_ranges[-1][2] + 1 if self._page_ranges else 0
        end = start + len(text)
        self._page_ranges.append((page_number, start, end))
        return start, end

    def build_index(self) -> None:
        self._index_built = True

    def get_position(self, char_offset: int) -> Tuple[int, int]:
        if not self._index_built:
            raise RuntimeError("TextPositionTracker index not built")
        for page, start, end in self._page_ranges:
            if start <= char_offset <= end:
                rel = char_offset - start
                # Find line by cumulative lengths
                cumulative = 0
                line_number = 1
                while (page, line_number) in self._lines:
                    line_text = self._lines[(page, line_number)]
                    cumulative += len(line_text)
                    if rel <= cumulative:
                        return page, line_number
                    line_number += 1
        return -1, -1

    def get_line_at(self, page: int, line: int) -> Optional[str]:
        return self._lines.get((page, line))

    def get_text_span(self, start_offset: int, end_offset: int) -> str:
        if not self._index_built:
            raise RuntimeError("TextPositionTracker index not built")
        parts: List[str] = []
        for page, start, end in self._page_ranges:
            if end < start_offset or start > end_offset:
                continue
            page_start = max(start, start_offset)
            page_end = min(end, end_offset)
            for line_number in sorted(k[1] for k in self._lines if k[0] == page):
                line_text = self._lines[(page, line_number)]
                line_abs_start = start + sum(len(self._lines[(page, i)]) for i in range(1, line_number))
                line_abs_end = line_abs_start + len(line_text)
                if line_abs_end < page_start or line_abs_start > page_end:
                    continue
                seg_start = max(line_abs_start, page_start) - line_abs_start
                seg_end = min(line_abs_end, page_end) - line_abs_start
                parts.append(line_text[seg_start:seg_end])
        return "".join(parts)

    def register_line(self, page: int, line_number: int, text: str) -> None:
        self._lines[(page, line_number)] = text


# ---------------------------------------------------------------------------
# OCR provider wrappers
# ---------------------------------------------------------------------------


class MockOCRProvider:
    """Synthetic OCR provider used for tests and offline development."""

    def process(self, text_content: str) -> List[PageLayout]:
        pages: List[PageLayout] = []
        chunk_size = 3000
        for idx, start in enumerate(range(0, len(text_content), chunk_size), start=1):
            page_text = text_content[start : start + chunk_size]
            lines_raw = page_text.splitlines() or [page_text]
            lines: List[LineLayout] = []
            char_cursor = 0
            for line_idx, line in enumerate(lines_raw, start=1):
                line_len = len(line)
                lines.append(
                    LineLayout(
                        line_number=line_idx,
                        text=line,
                        bounding_box=BoundingBox(x=0.0, y=float(line_idx - 1), width=100.0, height=12.0),
                        char_offset_start=char_cursor,
                        char_offset_end=char_cursor + line_len,
                        confidence=0.95,
                        page_number=idx,
                    )
                )
                char_cursor += line_len + 1  # include newline
            pages.append(
                PageLayout(
                    page_number=idx,
                    width=612.0,
                    height=792.0,
                    lines=lines,
                    raw_text=page_text,
                    confidence=0.95,
                )
            )
        if not pages:
            pages.append(
                PageLayout(
                    page_number=1,
                    width=612.0,
                    height=792.0,
                    lines=[],
                    raw_text=text_content,
                    confidence=0.95,
                )
            )
        return pages


class TesseractWrapper:
    """Lightweight Tesseract wrapper with availability guard."""

    @staticmethod
    def is_available() -> bool:
        return pytesseract is not None and Image is not None

    @staticmethod
    def process_image(image_bytes: bytes) -> List[LineLayout]:
        if not TesseractWrapper.is_available():
            raise RuntimeError("pytesseract is not installed. Install tesseract-ocr and pytesseract.")
        image = Image.open(io.BytesIO(image_bytes))  # type: ignore[name-defined]
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)  # type: ignore[attr-defined]
        lines: List[LineLayout] = []
        char_cursor = 0
        for i in range(len(data["text"])):
            text = data["text"][i]
            if not text.strip():
                continue
            conf = float(data.get("conf", [0])[i])
            bbox = BoundingBox(x=float(data["left"][i]), y=float(data["top"][i]), width=float(data["width"][i]), height=float(data["height"][i]))
            start = char_cursor
            end = start + len(text)
            lines.append(
                LineLayout(
                    line_number=len(lines) + 1,
                    text=text,
                    bounding_box=bbox,
                    char_offset_start=start,
                    char_offset_end=end,
                    confidence=max(conf / 100.0, 0.0),
                    page_number=1,
                )
            )
            char_cursor = end + 1
        return lines

    @staticmethod
    def process_image_with_layout(image_bytes: bytes) -> PageLayout:
        lines = TesseractWrapper.process_image(image_bytes)
        raw_text = "\n".join(line.text for line in lines)
        return PageLayout(
            page_number=1,
            width=0.0,
            height=0.0,
            lines=lines,
            raw_text=raw_text,
            confidence=sum(line.confidence for line in lines) / len(lines) if lines else 0.0,
        )


# ---------------------------------------------------------------------------
# OCR Processor
# ---------------------------------------------------------------------------


class OCRProcessor:
    """Runs OCR using the configured provider and returns structured layout."""

    def __init__(self, provider: OCRProvider = OCRProvider.MOCK, config: Optional[Dict[str, Any]] = None) -> None:
        self.provider = provider
        self.config = config or {}
        self._mock = MockOCRProvider()

    async def process_file(self, file_path: str) -> OCRResult:
        start = datetime.now(timezone.utc)
        fmt = self._detect_format(file_path)
        with open(file_path, "rb") as handle:
            content = handle.read()
        return await self.process_bytes(content, fmt, filename=os.path.basename(file_path))

    async def process_bytes(self, content: bytes, format: DocumentFormat, filename: Optional[str] = None) -> OCRResult:
        start = datetime.now(timezone.utc)
        original_text: Optional[str] = None
        pages: List[PageLayout]
        if format == DocumentFormat.TEXT:
            text = content.decode("utf-8", errors="ignore")
            pages = self._mock.process(text)
            original_text = text
        elif format == DocumentFormat.PDF:
            pages = await self._process_pdf(content)
        elif format in {DocumentFormat.PNG, DocumentFormat.JPEG, DocumentFormat.TIFF, DocumentFormat.BMP}:
            pages = await self._process_image(content)
        else:
            # Fallback: treat as text
            text = content.decode("utf-8", errors="ignore")
            pages = self._mock.process(text)
            original_text = text

        tracker = self._build_position_tracker(pages)
        fingerprint = DocumentFingerprinter.fingerprint(content)
        full_text = original_text if original_text is not None else "\n\f\n".join(page.raw_text for page in pages)
        overall_conf = self._calculate_overall_confidence(pages)
        warnings = self._generate_warnings(pages)
        processing_time_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return OCRResult(
            document_id=str(uuid.uuid4()),
            document_hash=fingerprint,
            source_format=format,
            provider_used=self.provider,
            total_pages=len(pages),
            pages=pages,
            full_text=full_text,
            processing_time_ms=processing_time_ms,
            overall_confidence=overall_conf,
            warnings=warnings,
            metadata={"filename": filename} if filename else {},
            is_successful=True,
        )

    async def process_text(self, text: str, source_name: Optional[str] = None) -> OCRResult:
        return await self.process_bytes(text.encode("utf-8"), DocumentFormat.TEXT, filename=source_name)

    async def _process_pdf(self, content: bytes) -> List[PageLayout]:
        if self.provider == OCRProvider.MOCK:
            return self._mock.process(content.decode("utf-8", errors="ignore"))
        if self.provider == OCRProvider.TESSERACT:
            if convert_from_bytes is None:
                raise RuntimeError("pdf2image is not installed; install pdf2image and poppler to process PDFs")
            images = await asyncio.to_thread(convert_from_bytes, content)
            pages: List[PageLayout] = []
            for idx, image in enumerate(images, start=1):
                img_bytes = self._pil_to_bytes(image)
                page = await asyncio.to_thread(TesseractWrapper.process_image_with_layout, img_bytes)
                page.page_number = idx
                pages.append(page)
            return pages
        if self.provider == OCRProvider.AZURE:
            raise NotImplementedError("Azure Document Intelligence integration is not implemented in this mock module")
        return self._mock.process(content.decode("utf-8", errors="ignore"))

    async def _process_image(self, content: bytes) -> List[PageLayout]:
        if self.provider == OCRProvider.MOCK:
            text = "Synthetic OCR content"
            return self._mock.process(text)
        if self.provider == OCRProvider.TESSERACT:
            page = await asyncio.to_thread(TesseractWrapper.process_image_with_layout, content)
            page.page_number = 1
            return [page]
        if self.provider == OCRProvider.AZURE:
            raise NotImplementedError("Azure Document Intelligence integration is not implemented in this mock module")
        return self._mock.process("Synthetic OCR content")

    def _detect_format(self, file_path: str) -> DocumentFormat:
        ext = os.path.splitext(file_path)[1].lower()
        mapping = {
            ".pdf": DocumentFormat.PDF,
            ".png": DocumentFormat.PNG,
            ".jpg": DocumentFormat.JPEG,
            ".jpeg": DocumentFormat.JPEG,
            ".tiff": DocumentFormat.TIFF,
            ".bmp": DocumentFormat.BMP,
            ".txt": DocumentFormat.TEXT,
            ".hl7": DocumentFormat.HL7_MESSAGE,
            ".json": DocumentFormat.FHIR_JSON,
        }
        return mapping.get(ext, DocumentFormat.TEXT)

    def _build_position_tracker(self, pages: List[PageLayout]) -> TextPositionTracker:
        tracker = TextPositionTracker()
        for page in pages:
            start, _ = tracker.add_page(page.page_number, page.raw_text)
            for line in page.lines:
                tracker.register_line(page.page_number, line.line_number, line.text)
        tracker.build_index()
        return tracker

    def _calculate_overall_confidence(self, pages: List[PageLayout]) -> float:
        total_chars = sum(len(p.raw_text) for p in pages) or 1
        weighted = sum(len(p.raw_text) * p.confidence for p in pages)
        return round(weighted / total_chars, 4)

    def _generate_warnings(self, pages: List[PageLayout]) -> List[str]:
        warnings: List[str] = []
        for page in pages:
            if page.confidence < 0.8:
                warnings.append(f"Low confidence on page {page.page_number}")
            if not page.raw_text.strip():
                warnings.append(f"Empty page detected: {page.page_number}")
            if len(page.raw_text) < 50:
                warnings.append(f"Very short page {page.page_number}; possible scan issue")
        return warnings

    @staticmethod
    def _pil_to_bytes(image: Any) -> bytes:
        import io

        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()


# ---------------------------------------------------------------------------
# Document Ingester facade
# ---------------------------------------------------------------------------


@dataclass
class SanitizationResult:
    is_clean: bool
    issues: List[str]


class DocumentIngester:
    """High-level ingestion: OCR + fingerprint + basic sanitization."""

    def __init__(self, provider: OCRProvider = OCRProvider.MOCK) -> None:
        self.ocr = OCRProcessor(provider=provider)

    async def ingest(self, file_path: str) -> IngestionResult:
        start = datetime.now(timezone.utc)
        ocr_result = await self.ocr.process_file(file_path)
        tracker = self._build_tracker(ocr_result)
        sanitation = self._sanitize(ocr_result)
        ingestion_time_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return IngestionResult(
            ocr_result=ocr_result,
            position_tracker=tracker,
            document_fingerprint=ocr_result.document_hash,
            is_clean=sanitation.is_clean,
            ingestion_time_ms=ingestion_time_ms,
        )

    async def ingest_bytes(self, content: bytes, format: DocumentFormat) -> IngestionResult:
        start = datetime.now(timezone.utc)
        ocr_result = await self.ocr.process_bytes(content, format)
        tracker = self._build_tracker(ocr_result)
        sanitation = self._sanitize(ocr_result)
        ingestion_time_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return IngestionResult(
            ocr_result=ocr_result,
            position_tracker=tracker,
            document_fingerprint=ocr_result.document_hash,
            is_clean=sanitation.is_clean,
            ingestion_time_ms=ingestion_time_ms,
        )

    async def ingest_text(self, text: str) -> IngestionResult:
        return await self.ingest_bytes(text.encode("utf-8"), DocumentFormat.TEXT)

    def _sanitize(self, ocr_result: OCRResult) -> SanitizationResult:
        # Placeholder: integrate guardrails/security PHI detection
        return SanitizationResult(is_clean=True, issues=[])

    def _build_tracker(self, ocr_result: OCRResult) -> TextPositionTracker:
        tracker = TextPositionTracker()
        for page in ocr_result.pages:
            tracker.add_page(page.page_number, page.raw_text)
            for line in page.lines:
                tracker.register_line(page.page_number, line.line_number, line.text)
        tracker.build_index()
        return tracker


__all__ = [
    "DocumentFormat",
    "OCRProvider",
    "BoundingBox",
    "LineLayout",
    "PageLayout",
    "OCRResult",
    "DocumentFingerprinter",
    "TextPositionTracker",
    "MockOCRProvider",
    "TesseractWrapper",
    "OCRProcessor",
    "DocumentIngester",
    "IngestionResult",
]
