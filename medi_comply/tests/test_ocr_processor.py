import asyncio
import json
import re

import pytest

from medi_comply.nlp.ocr_processor import (
    BoundingBox,
    DocumentFingerprinter,
    DocumentIngester,
    DocumentFormat,
    LineLayout,
    MockOCRProvider,
    OCRProcessor,
    OCRProvider,
    OCRResult,
    PageLayout,
    TextPositionTracker,
)


# DocumentFingerprinter -----------------------------------------------------

def test_fingerprint_consistency():
    data = b"hello world"
    h1 = DocumentFingerprinter.fingerprint(data)
    h2 = DocumentFingerprinter.fingerprint(data)
    assert h1 == h2


def test_fingerprint_different_content():
    h1 = DocumentFingerprinter.fingerprint(b"a")
    h2 = DocumentFingerprinter.fingerprint(b"b")
    assert h1 != h2


def test_verify_correct_hash():
    data = b"abc123"
    h = DocumentFingerprinter.fingerprint(data)
    assert DocumentFingerprinter.verify(data, h) is True


def test_verify_incorrect_hash():
    data = b"abc123"
    h = DocumentFingerprinter.fingerprint(data)
    assert DocumentFingerprinter.verify(b"other", h) is False


def test_fingerprint_is_sha256():
    h = DocumentFingerprinter.fingerprint(b"abc123")
    assert re.fullmatch(r"[0-9a-f]{64}", h) is not None


# TextPositionTracker -------------------------------------------------------

def test_add_single_page():
    tracker = TextPositionTracker()
    start, end = tracker.add_page(1, "abcd")
    tracker.build_index()
    assert start == 0
    assert end == 4


def test_add_multiple_pages():
    tracker = TextPositionTracker()
    tracker.add_page(1, "abcd")
    tracker.register_line(1, 1, "abcd")
    tracker.add_page(2, "efghij")
    tracker.register_line(2, 1, "efghij")
    tracker.build_index()
    assert tracker.get_position(0)[0] == 1
    assert tracker.get_position(5)[0] == 2


def test_get_position_first_page():
    tracker = TextPositionTracker()
    tracker.add_page(1, "hello\nworld")
    tracker.register_line(1, 1, "hello")
    tracker.register_line(1, 2, "world")
    tracker.build_index()
    page, line = tracker.get_position(1)
    assert (page, line) == (1, 1)


def test_get_position_second_page():
    tracker = TextPositionTracker()
    tracker.add_page(1, "hello")
    tracker.register_line(1, 1, "hello")
    tracker.add_page(2, "foo\nbar")
    tracker.register_line(2, 1, "foo")
    tracker.register_line(2, 2, "bar")
    tracker.build_index()
    page, line = tracker.get_position(6)  # offset into second page start
    assert (page, line) == (2, 1)


def test_get_line_at():
    tracker = TextPositionTracker()
    tracker.add_page(1, "hello")
    tracker.register_line(1, 1, "hello")
    tracker.build_index()
    assert tracker.get_line_at(1, 1) == "hello"


def test_get_text_span():
    tracker = TextPositionTracker()
    tracker.add_page(1, "hello world")
    tracker.register_line(1, 1, "hello world")
    tracker.build_index()
    assert tracker.get_text_span(0, 5) == "hello"


def test_get_position_out_of_range():
    tracker = TextPositionTracker()
    tracker.add_page(1, "hi")
    tracker.build_index()
    page, line = tracker.get_position(999)
    assert (page, line) == (-1, -1)


# MockOCRProvider -----------------------------------------------------------

def test_mock_splits_pages():
    provider = MockOCRProvider()
    text = "a" * 6000
    pages = provider.process(text)
    assert len(pages) >= 2


def test_mock_splits_lines():
    provider = MockOCRProvider()
    text = "line1\nline2\nline3"
    pages = provider.process(text)
    assert len(pages[0].lines) == 3


def test_mock_confidence():
    provider = MockOCRProvider()
    pages = provider.process("sample text")
    assert pages[0].confidence >= 0.9


def test_mock_short_text_single_page():
    provider = MockOCRProvider()
    pages = provider.process("short")
    assert len(pages) == 1


# OCRProcessor --------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_text_passthrough():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = await processor.process_text("hello world")
    assert isinstance(result, OCRResult)
    assert "hello world" in result.full_text


@pytest.mark.asyncio
async def test_process_text_creates_fingerprint():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = await processor.process_text("hello")
    assert len(result.document_hash) == 64


@pytest.mark.asyncio
async def test_process_text_result_fields():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = await processor.process_text("hello")
    assert result.total_pages >= 1
    assert result.processing_time_ms >= 0
    assert result.overall_confidence > 0


@pytest.mark.asyncio
async def test_process_text_confidence():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = await processor.process_text("hello")
    assert result.overall_confidence >= 0.9


def test_mock_provider_used_by_default():
    processor = OCRProcessor()
    assert processor.provider == OCRProvider.MOCK


# DocumentIngester facade ---------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_text():
    ingester = DocumentIngester(provider=OCRProvider.MOCK)
    result = await ingester.ingest_text("hello")
    assert result.ocr_result.full_text.startswith("hello")


@pytest.mark.asyncio
async def test_ingest_result_has_fingerprint():
    ingester = DocumentIngester(provider=OCRProvider.MOCK)
    result = await ingester.ingest_text("hello")
    assert len(result.document_fingerprint) == 64


@pytest.mark.asyncio
async def test_ingest_result_has_position_tracker():
    ingester = DocumentIngester(provider=OCRProvider.MOCK)
    result = await ingester.ingest_text("hello")
    tracker = result.position_tracker
    tracker.build_index()
    assert tracker.get_line_at(1, 1) == "hello"


@pytest.mark.asyncio
async def test_ingest_timing():
    ingester = DocumentIngester(provider=OCRProvider.MOCK)
    result = await ingester.ingest_text("hello")
    assert result.ingestion_time_ms >= 0


# OCRResult model helpers ---------------------------------------------------

def test_ocr_result_serialization():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = asyncio.run(processor.process_text("hello"))
    data = result.model_dump()
    assert data["document_hash"]
    assert json.loads(result.model_dump_json())


def test_ocr_result_warnings_low_confidence():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    low_page = PageLayout(
        page_number=1,
        width=0.0,
        height=0.0,
        lines=[],
        raw_text="low",
        confidence=0.5,
    )
    warnings = processor._generate_warnings([low_page])
    assert any("Low confidence" in w for w in warnings)


def test_overall_confidence_calculation():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    pages = [
        PageLayout(page_number=1, width=0, height=0, lines=[], raw_text="abcd", confidence=1.0),
        PageLayout(page_number=2, width=0, height=0, lines=[], raw_text="ab", confidence=0.5),
    ]
    overall = processor._calculate_overall_confidence(pages)
    assert 0.6 <= overall <= 1.0


# Edge cases ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_text_input():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    result = await processor.process_text("")
    assert result.full_text == ""
    assert result.total_pages >= 1


@pytest.mark.asyncio
async def test_very_long_text():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    text = "x" * 100_000
    result = await processor.process_text(text)
    assert len(result.full_text) == 100_000


@pytest.mark.asyncio
async def test_special_characters():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    text = "Temp 37°C, dose 5µg ± error"
    result = await processor.process_text(text)
    assert "°" in result.full_text and "µ" in result.full_text and "±" in result.full_text


@pytest.mark.asyncio
async def test_unicode_text():
    processor = OCRProcessor(provider=OCRProvider.MOCK)
    text = "患者は安静"  # Japanese
    result = await processor.process_text(text)
    assert "患者" in result.full_text
