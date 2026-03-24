"""
MEDI-COMPLY — End-to-end clinical NLP pipeline.

Orchestrates: ingestion → section parsing → NER → negation →
abbreviation expansion → entity linking → evidence verification → SCR.
"""

from __future__ import annotations

import time
import re
from pathlib import Path
from typing import Any, Optional, Union

from medi_comply.nlp.document_ingester import DocumentIngester as LegacyDocumentIngester, IngestedDocument, PageInfo
from medi_comply.nlp.section_parser import ClinicalSectionParser, ClinicalSection
from medi_comply.nlp.clinical_ner import ClinicalNEREngine, ClinicalEntity
from medi_comply.nlp.negation_detector import NegationDetector
from medi_comply.nlp.abbreviation_expander import AbbreviationExpander
from medi_comply.nlp.entity_linker import EntityLinker
from medi_comply.nlp.evidence_tracker import EvidenceTracker
from medi_comply.nlp.scr_builder import SCRBuilder, StructuredClinicalRepresentation
from medi_comply.nlp.ocr_processor import (
    DocumentIngester as OCRDocumentIngester,
    OCRResult,
    TextPositionTracker,
)
from medi_comply.nlp.coref_resolver import CoreferenceResolver


class ClinicalNLPPipeline:
    """End-to-end clinical NLP pipeline.

    Runs the complete extraction pipeline in order:

    1. :class:`DocumentIngester` — ingest input
    2. :class:`ClinicalSectionParser` — detect sections
    3. :class:`ClinicalNEREngine` — extract entities (rules + optional LLM)
    4. :class:`CoreferenceResolver` — resolve pronouns/abbreviations
    5. :class:`NegationDetector` — update assertions using resolved text
    6. :class:`AbbreviationExpander` — normalize entity text
    7. :class:`EntityLinker` — merge duplicates + detect relationships
    8. :class:`EvidenceTracker` — verify evidence links
    9. :class:`SCRBuilder` — assemble final output

    If any step fails, the pipeline continues with available data and
    flags the failure in extraction metadata.
    """

    def __init__(self) -> None:
        self.ingester = LegacyDocumentIngester()
        self.section_parser = ClinicalSectionParser()
        self.ner_engine = ClinicalNEREngine()
        self.negation_detector = NegationDetector()
        self.abbreviation_expander = AbbreviationExpander()
        self.entity_linker = EntityLinker()
        self.evidence_tracker = EvidenceTracker()
        self.scr_builder = SCRBuilder()
        self.coref_resolver = CoreferenceResolver()

    async def process(
        self,
        input_data: Union[str, bytes, dict, Path],
        source_type: str = "auto",
        patient_context: Optional[dict[str, Any]] = None,
        use_llm: bool = False,
    ) -> StructuredClinicalRepresentation:
        """Run the full NLP pipeline.

        Parameters
        ----------
        input_data:
            Raw input (text, PDF bytes, FHIR dict, file path).
        source_type:
            ``"auto"``, ``"PLAIN_TEXT"``, ``"PDF"``, ``"FHIR"``, ``"STRUCTURED"``.
        patient_context:
            Optional dict with ``age``, ``gender``, ``encounter_type``.
        use_llm:
            Whether to enable LLM-based extraction for conditions/procedures.

        Returns
        -------
        StructuredClinicalRepresentation
        """
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        errors: list[str] = []

        # Step 1: Ingest
        t1 = time.perf_counter()
        try:
            document = self.ingester.ingest(input_data, source_type)
        except Exception as exc:
            errors.append(f"Ingestion failed: {exc}")
            document = IngestedDocument(raw_text=str(input_data) if isinstance(input_data, str) else "")
        timings["ingest_ms"] = (time.perf_counter() - t1) * 1000
        return await self._run_pipeline(document, patient_context, use_llm, timings, errors, t0)

    async def process_document(
        self,
        file_path: Optional[str] = None,
        file_bytes: Optional[bytes] = None,
        format: Optional[str] = None,
        raw_text: Optional[str] = None,
        patient_context: Optional[dict[str, Any]] = None,
        use_llm: bool = False,
    ) -> dict:
        """Enhanced entry point that accepts multiple input formats.

        Priority:
        1. raw_text (direct passthrough)
        2. file_path via OCR pipeline
        3. file_bytes + format via OCR pipeline

        Returns the structured output plus OCR metadata.
        """

        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        errors: list[str] = []
        ocr_result: Optional[OCRResult] = None
        tracker: Optional[TextPositionTracker] = None

        # Ingest via OCR when file inputs are present
        if raw_text is not None:
            document = self.ingester.ingest(raw_text, source_type="PLAIN_TEXT")
            timings["ingest_ms"] = (time.perf_counter() - t0) * 1000
        else:
            ocr_ingester = OCRDocumentIngester()
            try:
                if file_path:
                    t_ingest = time.perf_counter()
                    ingestion = await ocr_ingester.ingest(file_path)
                    timings["ingest_ms"] = (time.perf_counter() - t_ingest) * 1000
                elif file_bytes is not None and format:
                    t_ingest = time.perf_counter()
                    from medi_comply.nlp.ocr_processor import DocumentFormat  # local import to avoid cycle

                    ingestion = await ocr_ingester.ingest_bytes(file_bytes, DocumentFormat(format))
                    timings["ingest_ms"] = (time.perf_counter() - t_ingest) * 1000
                else:
                    raise ValueError("No valid input provided to process_document")
            except Exception as exc:
                errors.append(f"OCR ingestion failed: {exc}")
                ingestion = None

            if ingestion:
                ocr_result = ingestion.ocr_result
                tracker = ingestion.position_tracker
                document = self._build_document_from_ocr(ocr_result, tracker)
            else:
                document = IngestedDocument(raw_text="")

        scr = await self._run_pipeline(document, patient_context, use_llm, timings, errors, t0)
        response = scr

        if ocr_result:
            self._attach_ocr_metadata(scr, ocr_result, tracker)

        return scr

    def process_sync(
        self,
        input_data: Union[str, bytes, dict, Path],
        source_type: str = "auto",
        patient_context: Optional[dict[str, Any]] = None,
        use_llm: bool = False,
    ) -> StructuredClinicalRepresentation:
        """Synchronous wrapper around :meth:`process`.

        Parameters
        ----------
        input_data:
            Raw input data.
        source_type:
            Source type hint.
        patient_context:
            Optional patient context.
        use_llm:
            Whether to use LLM extraction.

        Returns
        -------
        StructuredClinicalRepresentation
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context — can't use run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.process(input_data, source_type, patient_context, use_llm),
                )
                return future.result()
        else:
            return asyncio.run(
                self.process(input_data, source_type, patient_context, use_llm)
            )

    # -- Internal steps ----------------------------------------------------

    def _apply_negation(
        self, entities: list[ClinicalEntity], full_text: str,
    ) -> list[ClinicalEntity]:
        """Apply negation detection to all condition entities.

        Parameters
        ----------
        entities:
            Extracted entities.
        full_text:
            Full document text.

        Returns
        -------
        list[ClinicalEntity]
        """
        for entity in entities:
            if entity.entity_type in ("CONDITION", "PROCEDURE"):
                context = entity.raw_context or full_text
                result = self.negation_detector.detect(entity.text, context)
                entity.assertion = result.assertion
        return entities

    async def _run_pipeline(
        self,
        document: IngestedDocument,
        patient_context: Optional[dict[str, Any]],
        use_llm: bool,
        timings: dict[str, float],
        errors: list[str],
        t0: float,
    ) -> StructuredClinicalRepresentation:
        # Step 2: Section parsing
        t2 = time.perf_counter()
        try:
            sections = self.section_parser.parse(document)
        except Exception as exc:
            errors.append(f"Section parsing failed: {exc}")
            sections = [ClinicalSection(
                section_type="UNSTRUCTURED", header_text="", content=document.raw_text,
                start_char=0, end_char=len(document.raw_text),
            )]
        timings["section_parse_ms"] = (time.perf_counter() - t2) * 1000

        # Step 3: NER extraction
        t3 = time.perf_counter()
        try:
            entities = self.ner_engine.extract(document, sections, use_llm=use_llm)
        except Exception as exc:
            errors.append(f"NER extraction failed: {exc}")
            entities = []
        timings["ner_ms"] = (time.perf_counter() - t3) * 1000

        # Step 4: Coreference resolution
        coref_result = None
        t35 = time.perf_counter()
        try:
            sentences = self._split_sentences(document.raw_text)
            sections_map = self._build_section_map(sections)
            coref_entities = self._coref_entities_from_ner(entities, sentences, sections_map)
            coref_result = self.coref_resolver.resolve(
                document.raw_text,
                entities=coref_entities,
                sections=sections_map,
                document_id=document.document_id,
            )
            coref_text = coref_result.resolved_text or document.raw_text
        except Exception as exc:
            errors.append(f"Coreference resolution failed: {exc}")
            coref_text = document.raw_text
        timings["coref_ms"] = (time.perf_counter() - t35) * 1000

        # Step 5: Negation detection
        t4 = time.perf_counter()
        try:
            entities = self._apply_negation(entities, coref_text)
        except Exception as exc:
            errors.append(f"Negation detection failed: {exc}")
        timings["negation_ms"] = (time.perf_counter() - t4) * 1000

        # Step 6: Abbreviation expansion
        t5 = time.perf_counter()
        try:
            entities = self._expand_abbreviations(entities)
        except Exception as exc:
            errors.append(f"Abbreviation expansion failed: {exc}")
        timings["abbreviation_ms"] = (time.perf_counter() - t5) * 1000

        # Step 7: Entity linking
        t6 = time.perf_counter()
        try:
            entities = self.entity_linker.link_entities(entities, document.raw_text)
        except Exception as exc:
            errors.append(f"Entity linking failed: {exc}")
        timings["linking_ms"] = (time.perf_counter() - t6) * 1000

        # Step 8: Evidence verification
        t7 = time.perf_counter()
        try:
            self._verify_evidence(entities, document)
        except Exception as exc:
            errors.append(f"Evidence verification failed: {exc}")
        timings["evidence_ms"] = (time.perf_counter() - t7) * 1000

        # Step 9: SCR assembly
        t8 = time.perf_counter()
        total_ms = (time.perf_counter() - t0) * 1000
        try:
            scr = self.scr_builder.build(
                document, sections, entities, patient_context, total_ms,
            )
        except Exception as exc:
            errors.append(f"SCR build failed: {exc}")
            scr = StructuredClinicalRepresentation(document_id=document.document_id)
        timings["scr_build_ms"] = (time.perf_counter() - t8) * 1000

        # Record timings and errors
        scr.processing_time_ms = (time.perf_counter() - t0) * 1000
        scr.extraction_metadata.step_timings_ms = timings
        if coref_result:
            setattr(
                scr,
                "coreference",
                {
                    "coreference_chains": [chain.model_dump() for chain in coref_result.chains],
                    "resolved_abbreviations": coref_result.resolved_abbreviations,
                    "resolved_text": coref_result.resolved_text,
                    "unresolved_mentions": [m.model_dump() for m in coref_result.unresolved_mentions],
                },
            )
        if errors:
            scr.extraction_metadata.low_confidence_flags.extend(errors)

        return scr

    def _build_document_from_ocr(self, ocr_result: OCRResult, tracker: Optional[TextPositionTracker]) -> IngestedDocument:
        text = ocr_result.full_text
        pages_info = []
        char_to_page_line_map: dict[int, tuple[int, int]] = {}
        cursor = 0
        separator = "\n\f\n"
        for idx, page in enumerate(ocr_result.pages, start=1):
            page_start = cursor
            lines_offsets: list[tuple[int, int]] = []
            for line in page.lines:
                line_start = page_start + line.char_offset_start
                line_end = page_start + line.char_offset_end
                lines_offsets.append((line_start, line_end))
                for pos in range(line_start, line_end):
                    char_to_page_line_map[pos] = (page.page_number, line.line_number)
            pages_info.append(ClinicalNLPPipeline._make_pageinfo(idx, page.raw_text, lines_offsets))
            cursor += len(page.raw_text)
            if idx < len(ocr_result.pages):
                cursor += len(separator)

        doc = IngestedDocument(
            source_type=str(ocr_result.source_format),
            raw_text=text,
            pages=pages_info,
            metadata={
                "document_hash": ocr_result.document_hash,
                "ocr_provider": ocr_result.provider_used,
                "ocr_total_pages": ocr_result.total_pages,
                "ocr_confidence": ocr_result.overall_confidence,
            },
            char_to_page_line_map=char_to_page_line_map,
        )
        if tracker:
            doc.metadata["position_tracker"] = tracker
        return doc

    @staticmethod
    def _make_pageinfo(page_number: int, text: str, line_offsets: list[tuple[int, int]]) -> PageInfo:
        return PageInfo(page_number=page_number, text=text, line_offsets=line_offsets)

    def _attach_ocr_metadata(
        self,
        scr: StructuredClinicalRepresentation,
        ocr_result: OCRResult,
        tracker: Optional[TextPositionTracker],
    ) -> None:
        metadata = {
            "document_hash": ocr_result.document_hash,
            "ocr_total_pages": ocr_result.total_pages,
            "ocr_confidence": ocr_result.overall_confidence,
        }
        if tracker:
            metadata["position_tracker"] = tracker
        # Attach dynamically to SCR to avoid breaking existing schema
        setattr(scr, "ocr_metadata", metadata)

    def _expand_abbreviations(self, entities: list[ClinicalEntity]) -> list[ClinicalEntity]:
        """Expand abbreviations in entity text.

        Parameters
        ----------
        entities:
            Extracted entities.

        Returns
        -------
        list[ClinicalEntity]
        """
        for entity in entities:
            if entity.entity_type == "CONDITION" and not entity.normalized_text:
                entity.normalized_text = self.abbreviation_expander.expand_entity(
                    entity.text, entity.raw_context,
                )
        return entities

    def _verify_evidence(
        self, entities: list[ClinicalEntity], document: IngestedDocument,
    ) -> None:
        """Verify evidence for all entities against the source document.

        Parameters
        ----------
        entities:
            Extracted entities.
        document:
            Source document.
        """
        for entity in entities:
            if entity.source_evidence:
                valid = self.evidence_tracker.verify_evidence(entity.source_evidence, document)
                if not valid:
                    entity.confidence *= 0.8  # Reduce confidence for unverified evidence

    # --------------------------- helpers ---------------------------------

    def _split_sentences(self, text: str) -> list[str]:
        raw = re.split(r"(?<=[.!?])\s+", text)
        return [s for s in raw if s]

    def _sentence_for_offset(self, sentences: list[str], offset: int) -> int:
        cursor = 0
        for idx, sent in enumerate(sentences):
            next_cursor = cursor + len(sent) + 1
            if cursor <= offset < next_cursor:
                return idx
            cursor = next_cursor
        return max(0, len(sentences) - 1)

    def _build_section_map(self, sections: list[ClinicalSection]) -> dict[int, str]:
        section_map: dict[int, str] = {}
        for section in sections:
            section_map[section.start_char] = section.section_type
        return section_map

    def _coref_entities_from_ner(
        self,
        entities: list[ClinicalEntity],
        sentences: list[str],
        sections_map: dict[int, str],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for ent in entities:
            start, end = (ent.source_evidence.char_offset if ent.source_evidence else (0, 0))
            section_val = ent.source_evidence.section if ent.source_evidence else None
            if not section_val and start in sections_map:
                section_val = sections_map[start]
            results.append(
                {
                    "text": ent.text,
                    "start_offset": start,
                    "end_offset": end,
                    "sentence_index": self._sentence_for_offset(sentences, start) if sentences else 0,
                    "section": section_val,
                    "entity_type": ent.entity_type,
                }
            )
        return results
