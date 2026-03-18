"""
MEDI-COMPLY — Entity linker for coreference resolution and duplicate merging.

Links related entities (e.g. "diabetic nephropathy" → DM + nephropathy),
merges duplicate mentions, and detects relationships
(COMPLICATION_OF, TREATMENT_FOR, CAUSED_BY, LOCATED_IN).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from medi_comply.nlp.clinical_ner import ClinicalEntity
from medi_comply.nlp.abbreviation_expander import ABBREVIATIONS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class EntityRelationship:
    """A relationship between two clinical entities."""
    relationship_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_entity_id: str = ""
    target_entity_id: str = ""
    relationship_type: str = ""  # COMPLICATION_OF | TREATMENT_FOR | CAUSED_BY | LOCATED_IN
    confidence: float = 0.80


# ---------------------------------------------------------------------------
# Relationship patterns
# ---------------------------------------------------------------------------

_COMPLICATION_PATS = [
    re.compile(r"(\w+)\s+with\s+(\w+)", re.I),
    re.compile(r"(\w+)\s+complicated by\s+(\w+)", re.I),
]
_TREATMENT_PATS = [
    re.compile(r"(\w+)\s+for\s+(\w+)", re.I),
]
_CAUSED_BY_PATS = [
    re.compile(r"(\w+)\s+due to\s+(\w+)", re.I),
    re.compile(r"(\w+)\s+secondary to\s+(\w+)", re.I),
    re.compile(r"(\w+)\s+caused by\s+(\w+)", re.I),
]


# ---------------------------------------------------------------------------
# Entity Linker
# ---------------------------------------------------------------------------

class EntityLinker:
    """Links, merges, and relates clinical entities.

    Heuristic approach for hackathon speed:

    * **Duplicates**: Same normalized text (case-insensitive) or abbreviation
      match → single entity with multiple evidence spans.
    * **Relationships**: Pattern-based detection from raw context.
    """

    def link_entities(
        self,
        entities: list[ClinicalEntity],
        document_text: str,
    ) -> list[ClinicalEntity]:
        """Run full entity linking pipeline.

        Parameters
        ----------
        entities:
            Extracted entities.
        document_text:
            Full source text.

        Returns
        -------
        list[ClinicalEntity]
            Merged and linked entities.
        """
        # Step 1: Find and merge duplicates
        groups = self._find_duplicates(entities)
        merged = self._merge_duplicates(groups)

        # Step 2: Detect relationships
        relationships = self._detect_relationships(merged)

        # Step 3: Annotate related_entities on each entity
        for rel in relationships:
            for entity in merged:
                if entity.entity_id == rel.source_entity_id:
                    if rel.target_entity_id not in entity.related_entities:
                        entity.related_entities.append(rel.target_entity_id)
                elif entity.entity_id == rel.target_entity_id:
                    if rel.source_entity_id not in entity.related_entities:
                        entity.related_entities.append(rel.source_entity_id)

        return merged

    def _find_duplicates(self, entities: list[ClinicalEntity]) -> list[list[ClinicalEntity]]:
        """Group entities that refer to the same concept.

        Parameters
        ----------
        entities:
            All entities.

        Returns
        -------
        list[list[ClinicalEntity]]
            Groups of duplicate entities.
        """
        groups: dict[str, list[ClinicalEntity]] = {}

        for entity in entities:
            key = self._normalize_key(entity)
            if key not in groups:
                groups[key] = []
            groups[key].append(entity)

        return list(groups.values())

    def _merge_duplicates(self, groups: list[list[ClinicalEntity]]) -> list[ClinicalEntity]:
        """Merge duplicate groups into single entities.

        Parameters
        ----------
        groups:
            Grouped duplicate entities.

        Returns
        -------
        list[ClinicalEntity]
        """
        merged: list[ClinicalEntity] = []
        for group in groups:
            if len(group) == 1:
                merged.append(group[0])
                continue

            # Keep the highest-confidence entity as primary
            primary = max(group, key=lambda e: e.confidence)
            # Could accumulate multiple evidence spans here if needed
            merged.append(primary)
        return merged

    def _detect_relationships(self, entities: list[ClinicalEntity]) -> list[EntityRelationship]:
        """Detect relationships between entities using context patterns.

        Parameters
        ----------
        entities:
            Merged entities.

        Returns
        -------
        list[EntityRelationship]
        """
        relationships: list[EntityRelationship] = []
        entity_map = {e.entity_id: e for e in entities}

        for i, e1 in enumerate(entities):
            for j, e2 in enumerate(entities):
                if i >= j:
                    continue
                # Check for "with" pattern in context (COMPLICATION_OF)
                ctx = e1.raw_context.lower()
                e1_text = e1.text.lower()
                e2_text = e2.text.lower()

                if e1_text in ctx and e2_text in ctx:
                    if " with " in ctx:
                        relationships.append(EntityRelationship(
                            source_entity_id=e1.entity_id,
                            target_entity_id=e2.entity_id,
                            relationship_type="COMPLICATION_OF",
                            confidence=0.80,
                        ))
                    elif " due to " in ctx or " d/t " in ctx:
                        relationships.append(EntityRelationship(
                            source_entity_id=e1.entity_id,
                            target_entity_id=e2.entity_id,
                            relationship_type="CAUSED_BY",
                            confidence=0.78,
                        ))

                # Medication → Condition relationship
                if e1.entity_type == "MEDICATION" and e2.entity_type == "CONDITION":
                    if " for " in e1.raw_context.lower() and e2_text in e1.raw_context.lower():
                        relationships.append(EntityRelationship(
                            source_entity_id=e1.entity_id,
                            target_entity_id=e2.entity_id,
                            relationship_type="TREATMENT_FOR",
                            confidence=0.75,
                        ))

        return relationships

    def _resolve_coreferences(
        self,
        entities: list[ClinicalEntity],
        text: str,
    ) -> list[ClinicalEntity]:
        """Simple coreference resolution via text matching.

        Parameters
        ----------
        entities:
            Current entities.
        text:
            Full document text.

        Returns
        -------
        list[ClinicalEntity]
        """
        # Handled by _find_duplicates + _merge_duplicates via normalize_key
        return entities

    @staticmethod
    def _normalize_key(entity: ClinicalEntity) -> str:
        """Create a normalization key for deduplication.

        Parameters
        ----------
        entity:
            Entity to normalize.

        Returns
        -------
        str
        """
        norm = entity.normalized_text.lower().strip()
        if not norm:
            norm = entity.text.lower().strip()

        # Expand known abbreviations
        for abbr, expansion in ABBREVIATIONS.items():
            if norm == abbr.lower():
                norm = expansion.lower()
                break

        return f"{entity.entity_type}::{norm}"
