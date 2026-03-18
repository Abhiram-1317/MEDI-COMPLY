"""
MEDI-COMPLY — End-to-end clinical NLP pipeline.

Orchestrates: ingestion → section parsing → NER → negation →
abbreviation expansion → entity linking → evidence verification → SCR.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional, Union

from medi_comply.nlp.document_ingester import DocumentIngester, IngestedDocument
from medi_comply.nlp.section_parser import ClinicalSectionParser, ClinicalSection
from medi_comply.nlp.clinical_ner import ClinicalNEREngine, ClinicalEntity
from medi_comply.nlp.negation_detector import NegationDetector
from medi_comply.nlp.abbreviation_expander import AbbreviationExpander
from medi_comply.nlp.entity_linker import EntityLinker
from medi_comply.nlp.evidence_tracker import EvidenceTracker
from medi_comply.nlp.scr_builder import SCRBuilder, StructuredClinicalRepresentation


class ClinicalNLPPipeline:
    """End-to-end clinical NLP pipeline.

    Runs the complete extraction pipeline in order:

    1. :class:`DocumentIngester` — ingest input
    2. :class:`ClinicalSectionParser` — detect sections
    3. :class:`ClinicalNEREngine` — extract entities (rules + optional LLM)
    4. :class:`NegationDetector` — update assertions
    5. :class:`AbbreviationExpander` — normalize entity text
    6. :class:`EntityLinker` — merge duplicates + detect relationships
    7. :class:`EvidenceTracker` — verify evidence links
    8. :class:`SCRBuilder` — assemble final output

    If any step fails, the pipeline continues with available data and
    flags the failure in extraction metadata.
    """

    def __init__(self) -> None:
        self.ingester = DocumentIngester()
        self.section_parser = ClinicalSectionParser()
        self.ner_engine = ClinicalNEREngine()
        self.negation_detector = NegationDetector()
        self.abbreviation_expander = AbbreviationExpander()
        self.entity_linker = EntityLinker()
        self.evidence_tracker = EvidenceTracker()
        self.scr_builder = SCRBuilder()

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

        # Step 4: Negation detection
        t4 = time.perf_counter()
        try:
            entities = self._apply_negation(entities, document.raw_text)
        except Exception as exc:
            errors.append(f"Negation detection failed: {exc}")
        timings["negation_ms"] = (time.perf_counter() - t4) * 1000

        # Step 5: Abbreviation expansion
        t5 = time.perf_counter()
        try:
            entities = self._expand_abbreviations(entities)
        except Exception as exc:
            errors.append(f"Abbreviation expansion failed: {exc}")
        timings["abbreviation_ms"] = (time.perf_counter() - t5) * 1000

        # Step 6: Entity linking
        t6 = time.perf_counter()
        try:
            entities = self.entity_linker.link_entities(entities, document.raw_text)
        except Exception as exc:
            errors.append(f"Entity linking failed: {exc}")
        timings["linking_ms"] = (time.perf_counter() - t6) * 1000

        # Step 7: Evidence verification
        t7 = time.perf_counter()
        try:
            self._verify_evidence(entities, document)
        except Exception as exc:
            errors.append(f"Evidence verification failed: {exc}")
        timings["evidence_ms"] = (time.perf_counter() - t7) * 1000

        # Step 8: SCR assembly
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
        if errors:
            scr.extraction_metadata.low_confidence_flags.extend(errors)

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
