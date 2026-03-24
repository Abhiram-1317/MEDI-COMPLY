"""
Coreference resolution for MEDI-COMPLY clinical NLP.

Resolves pronouns, abbreviations, and anaphoric references back to canonical
clinical entities to support accurate medical coding.

Rule-based resolution is the default; optional LLM hook can handle ambiguous
cases without blocking deterministic flows.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations and models
# ---------------------------------------------------------------------------


class MentionType(str, Enum):
    PROPER_NOUN = "PROPER_NOUN"
    PRONOUN = "PRONOUN"
    ABBREVIATED = "ABBREVIATED"
    DEFINITE_NP = "DEFINITE_NP"
    RELATIVE = "RELATIVE"
    ZERO_ANAPHORA = "ZERO_ANAPHORA"
    POSSESSIVE = "POSSESSIVE"


# Clinical pronoun and reference patterns (regex, case-insensitive)
CLINICAL_PRONOUN_PATTERNS: Dict[MentionType, str] = {
    MentionType.PRONOUN: r"\b(he|she|they|them|it|this|that|these|those|him|her|his|hers|their|theirs)\b",
    MentionType.DEFINITE_NP: r"\b(the patient|patient|pt|the condition|the disease|the disorder|the diagnosis|the medication|the drug|the medicine|the procedure|the surgery|the operation|this condition|this medication|the med|the medication|the drug|the medicine|the hypertension|the diabetes)\b|\b(the (left|right|bilateral)\s+\w+|the affected (area|site|limb|joint))\b",
    MentionType.RELATIVE: r"\b(the same|the above|the aforementioned|said|the following)\b",
    MentionType.POSSESSIVE: r"\b(his|her|patient's|pt's)\b",
    MentionType.ZERO_ANAPHORA: r"\b(was treated with|started on|continued on)\b",
}


class Mention(BaseModel):
    """Span-level mention of an entity in the document."""

    mention_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    mention_type: MentionType
    start_offset: int
    end_offset: int
    sentence_index: int
    section: Optional[str] = None
    entity_type: Optional[str] = None  # CONDITION, PROCEDURE, MEDICATION, PATIENT, BODY_SITE, PROVIDER

    model_config = ConfigDict(use_enum_values=True)


class CoreferenceChain(BaseModel):
    """Represents a linked set of mentions that refer to the same entity."""

    chain_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    canonical_mention: Mention
    mentions: List[Mention]
    entity_type: str
    canonical_text: str
    confidence: float


class CoreferenceResult(BaseModel):
    """Aggregate output of the resolver."""

    document_id: Optional[str] = None
    chains: List[CoreferenceChain]
    resolved_text: str
    resolved_abbreviations: List[Tuple[str, str]] = Field(default_factory=list)
    mention_count: int
    chain_count: int
    unresolved_mentions: List[Mention] = Field(default_factory=list)
    processing_time_ms: float = 0.0
    warnings: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Abbreviation resolver
# ---------------------------------------------------------------------------


class MedicalAbbreviationResolver:
    """Lightweight resolver for common clinical abbreviations."""

    def __init__(self) -> None:
        # Minimum of 50 frequent clinical abbreviations
        self._abbr_map: Dict[str, str] = {
            "DM": "diabetes mellitus",
            "T2DM": "type 2 diabetes mellitus",
            "T1DM": "type 1 diabetes mellitus",
            "HTN": "hypertension",
            "CKD": "chronic kidney disease",
            "CHF": "congestive heart failure",
            "COPD": "chronic obstructive pulmonary disease",
            "MI": "myocardial infarction",
            "NSTEMI": "non-ST-elevation myocardial infarction",
            "STEMI": "ST-elevation myocardial infarction",
            "DVT": "deep vein thrombosis",
            "PE": "pulmonary embolism",
            "UTI": "urinary tract infection",
            "CAD": "coronary artery disease",
            "GERD": "gastroesophageal reflux disease",
            "RA": "rheumatoid arthritis",
            "OA": "osteoarthritis",
            "BPH": "benign prostatic hyperplasia",
            "ESRD": "end-stage renal disease",
            "AKI": "acute kidney injury",
            "TIA": "transient ischemic attack",
            "CVA": "cerebrovascular accident",
            "AF": "atrial fibrillation",
            "AFIB": "atrial fibrillation",
            "PNA": "pneumonia",
            "SOB": "shortness of breath",
            "CP": "chest pain",
            "HA": "headache",
            "N/V": "nausea and vomiting",
            "NV": "nausea and vomiting",
            "ABX": "antibiotics",
            "BID": "twice daily",
            "TID": "three times daily",
            "QD": "once daily",
            "QHS": "at bedtime",
            "PRN": "as needed",
            "PO": "by mouth",
            "IM": "intramuscular",
            "IV": "intravenous",
            "SC": "subcutaneous",
            "SQ": "subcutaneous",
            "OD": "right eye",
            "OS": "left eye",
            "OU": "both eyes",
            "AD": "right ear",
            "AS": "left ear",
            "AU": "both ears",
            "LFTs": "liver function tests",
            "BMP": "basic metabolic panel",
            "CMP": "comprehensive metabolic panel",
            "CBC": "complete blood count",
            "CXR": "chest x-ray",
            "EKG": "electrocardiogram",
            "ECG": "electrocardiogram",
            "PT": "physical therapy",
            "OT": "occupational therapy",
            "PTA": "prior to arrival",
            "F/U": "follow up",
            "FU": "follow up",
            "ED": "emergency department",
            "OR": "operating room",
            "PACU": "post-anesthesia care unit",
            "ICU": "intensive care unit",
            "NICU": "neonatal intensive care unit",
            "MICU": "medical intensive care unit",
            "SICU": "surgical intensive care unit",
            "CVICU": "cardiovascular intensive care unit",
        }
        # Precompile keys for faster regex; use real word boundaries
        self._abbr_pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, self._abbr_map.keys()), key=len, reverse=True)) + r")\b", re.IGNORECASE)

    def resolve(self, abbreviation: str) -> Optional[str]:
        return self._abbr_map.get(abbreviation.upper())

    def is_abbreviation(self, text: str) -> bool:
        return text.upper() in self._abbr_map

    def find_abbreviations(self, text: str) -> List[Tuple[str, str, int, int]]:
        results: List[Tuple[str, str, int, int]] = []
        for match in self._abbr_pattern.finditer(text):
            abbr = match.group(0)
            full = self.resolve_with_context(abbr, text[max(0, match.start() - 40) : match.end() + 40])
            if full:
                results.append((abbr, full, match.start(), match.end()))
        return results

    def resolve_with_context(self, abbreviation: str, surrounding_text: str) -> Optional[str]:
        base = self.resolve(abbreviation)
        if abbreviation.upper() == "PE":
            if re.search(r"physical exam|inspection|percussion|auscultation|\bexam\b", surrounding_text, re.IGNORECASE):
                return "physical exam"
            return "pulmonary embolism"
        return base


# ---------------------------------------------------------------------------
# Pronoun resolver
# ---------------------------------------------------------------------------


class PronounResolver:
    """Rule-based pronoun linking with clinical heuristics."""

    def resolve_pronouns(self, sentences: List[str], entities: List[Mention]) -> List[Tuple[Mention, Mention]]:
        pronoun_mentions = [m for m in entities if m.mention_type in {MentionType.PRONOUN, MentionType.DEFINITE_NP, MentionType.RELATIVE, MentionType.POSSESSIVE}]
        candidates = [m for m in entities if m.mention_type == MentionType.PROPER_NOUN or m.mention_type == MentionType.ABBREVIATED]
        pairs: List[Tuple[Mention, Mention]] = []
        for pronoun in pronoun_mentions:
            antecedent = self._find_antecedent(pronoun, candidates)
            if antecedent:
                pairs.append((pronoun, antecedent))
        return pairs

    def _find_antecedent(self, pronoun: Mention, candidates: List[Mention]) -> Optional[Mention]:
        scoring: List[Tuple[float, Mention]] = []
        for cand in candidates:
            if cand.start_offset >= pronoun.start_offset:
                continue
            score = 0.0
            # Recency bias
            distance = pronoun.start_offset - cand.end_offset
            score -= distance / 500.0
            # Entity type alignment heuristics
            lower = pronoun.text.lower()
            if lower in {"he", "him", "his"} and cand.entity_type in {"PATIENT", "PROVIDER"}:
                score += 2.0
            if lower in {"she", "her", "hers"} and cand.entity_type in {"PATIENT", "PROVIDER"}:
                score += 2.0
            if lower in {"it", "this", "that", "the condition", "the disease"} and cand.entity_type in {"CONDITION", "PROCEDURE", "MEDICATION"}:
                score += 2.0
            if lower in {"they", "them"}:
                score += 1.0
            if "patient" in lower and cand.entity_type == "PATIENT":
                score += 3.0
            if "procedure" in lower or "surgery" in lower:
                if cand.entity_type == "PROCEDURE":
                    score += 2.0
            if "medication" in lower or "drug" in lower:
                if cand.entity_type == "MEDICATION":
                    score += 2.0
            # Section proximity bonus
            if pronoun.section and cand.section and pronoun.section == cand.section:
                score += 0.5
            scoring.append((score, cand))
        if not scoring:
            return None
        scoring.sort(key=lambda x: x[0], reverse=True)
        top_score, top_cand = scoring[0]
        return top_cand if top_score > -1.0 else None


# ---------------------------------------------------------------------------
# Section-aware resolver
# ---------------------------------------------------------------------------


class SectionAwareResolver:
    """Adjusts antecedent choices based on clinical sections."""

    def resolve_cross_section(self, mention: Mention, candidates: List[Mention], sections: Mapping[object, str]) -> Optional[Mention]:
        if not sections:
            return None
        scoring: List[Tuple[float, Mention]] = []
        for cand in candidates:
            if cand.start_offset >= mention.start_offset:
                continue
            score = 0.0
            if mention.section and cand.section and mention.section == cand.section:
                score += 1.5
            if mention.section == "Assessment" and cand.section in {"HPI", "ROS"}:
                score += 1.0
            if mention.section == "Plan" and cand.section in {"Assessment", "HPI"}:
                score += 0.8
            if "above" in mention.text.lower() and cand.section in {"HPI", "ROS"}:
                score += 0.7
            score -= (mention.start_offset - cand.end_offset) / 800.0
            scoring.append((score, cand))
        if not scoring:
            return None
        scoring.sort(key=lambda x: x[0], reverse=True)
        best_score, best_cand = scoring[0]
        return best_cand if best_score > -0.5 else None


# ---------------------------------------------------------------------------
# Coreference resolver
# ---------------------------------------------------------------------------


@dataclass
class _ChainBuilder:
    """Utility to group mentions into chains."""

    parents: Dict[str, str]

    def find(self, x: str) -> str:
        if self.parents[x] != x:
            self.parents[x] = self.find(self.parents[x])
        return self.parents[x]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parents[rb] = ra


class CoreferenceResolver:
    """Main orchestrator for coreference resolution."""

    def __init__(self, use_llm: bool = False, llm_client: Any = None) -> None:
        self.use_llm = use_llm
        self.llm_client = llm_client
        self.abbrev_resolver = MedicalAbbreviationResolver()
        self.pronoun_resolver = PronounResolver()
        self.section_resolver = SectionAwareResolver()

    def resolve(self, text: str, entities: Optional[List[Dict[str, Any]]] = None, sections: Optional[Mapping[object, str]] = None, document_id: Optional[str] = None) -> CoreferenceResult:
        """Resolve coreference chains for a clinical document.

        Pipeline (rule-first, optional LLM fallback):
        1) Sentence split
        2) Mention detection (pronouns, definites, relatives, possessives)
        3) Incorporate provided entity mentions (NER output)
        4) Abbreviation resolution
        5) Pronoun/NP resolution (rule-based)
        6) Section-aware adjustments
        7) Optional LLM disambiguation for unresolved pronouns
        8) Chain construction and canonical selection
        9) Resolved text generation with inline annotations
        10) Aggregate CoreferenceResult
        """
        t0 = time.perf_counter()
        sentences = self._split_sentences(text)
        mention_list = self._find_mentions(text)
        provided_entities = self._convert_entities(entities or [], sentences, sections)
        mention_list.extend(provided_entities)

        # Abbreviation resolution
        abbrev_pairs: List[Tuple[Mention, Mention]] = []
        for abbr, full, start, end in self.abbrev_resolver.find_abbreviations(text):
            mention = Mention(
                text=abbr,
                mention_type=MentionType.ABBREVIATED,
                start_offset=start,
                end_offset=end,
                sentence_index=self._sentence_for_offset(sentences, start),
                section=sections.get(start, None) if sections else None,
                entity_type="CONDITION",
            )
            mention_list.append(mention)
            canonical = Mention(
                text=full,
                mention_type=MentionType.PROPER_NOUN,
                start_offset=start,
                end_offset=end,
                sentence_index=mention.sentence_index,
                section=mention.section,
                entity_type="CONDITION",
            )
            mention_list.append(canonical)
            abbrev_pairs.append((mention, canonical))

        # Pronoun resolution
        pronoun_pairs = self.pronoun_resolver.resolve_pronouns(sentences, mention_list)

        # Section-aware adjustments (optional)
        if sections:
            adjusted_pairs: List[Tuple[Mention, Mention]] = []
            for pronoun, antecedent in pronoun_pairs:
                cross = self.section_resolver.resolve_cross_section(pronoun, [a for _, a in pronoun_pairs] + [a for _, a in abbrev_pairs], sections)
                adjusted_pairs.append((pronoun, cross or antecedent))
            pronoun_pairs = adjusted_pairs

        # LLM fallback for ambiguous/unresolved pronouns (optional)
        if self.use_llm and self.llm_client:
            resolved_pronouns = {p.mention_id for p, _ in pronoun_pairs}
            unresolved_pronouns = [m for m in mention_list if m.mention_type in {MentionType.PRONOUN, MentionType.DEFINITE_NP, MentionType.RELATIVE, MentionType.POSSESSIVE} and m.mention_id not in resolved_pronouns]
            llm_pairs = self._llm_disambiguate(text, sentences, unresolved_pronouns, mention_list)
            pronoun_pairs.extend(llm_pairs)

        all_pairs = abbrev_pairs + pronoun_pairs
        chains = self._build_chains(all_pairs, mention_list)
        resolved_text = self._generate_resolved_text(text, chains)
        resolved_ids = {m.mention_id for chain in chains for m in chain.mentions}
        unresolved = [m for m in mention_list if m.mention_id not in resolved_ids]

        result = CoreferenceResult(
            document_id=document_id,
            chains=chains,
            resolved_text=resolved_text,
            resolved_abbreviations=[(m.text, c.text) for m, c in abbrev_pairs],
            mention_count=len(mention_list),
            chain_count=len(chains),
            unresolved_mentions=unresolved,
            processing_time_ms=(time.perf_counter() - t0) * 1000,
            warnings=[],
        )
        return result

    # --------------------------- helpers ---------------------------------

    def _split_sentences(self, text: str) -> List[str]:
        raw = re.split(r"(?<=[.!?])\s+", text)
        return [s for s in raw if s]

    def _sentence_for_offset(self, sentences: List[str], offset: int) -> int:
        cursor = 0
        for idx, sent in enumerate(sentences):
            next_cursor = cursor + len(sent) + 1
            if cursor <= offset < next_cursor:
                return idx
            cursor = next_cursor
        return max(0, len(sentences) - 1)

    def _find_mentions(self, text: str) -> List[Mention]:
        mentions: List[Mention] = []
        patterns = CLINICAL_PRONOUN_PATTERNS
        sentences = self._split_sentences(text)
        for mtype, pattern in patterns.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                start, end = match.start(), match.end()
                sentence_index = self._sentence_for_offset(sentences, start)
                mentions.append(
                    Mention(
                        text=match.group(0),
                        mention_type=mtype,
                        start_offset=start,
                        end_offset=end,
                        sentence_index=sentence_index,
                        entity_type=self._infer_entity_type_from_phrase(match.group(0)),
                    )
                )
        return mentions

    def _convert_entities(self, entities: List[Dict[str, Any]], sentences: List[str], sections: Optional[Mapping[object, str]]) -> List[Mention]:
        results: List[Mention] = []
        for ent in entities:
            results.append(
                Mention(
                    text=ent.get("text", ""),
                    mention_type=MentionType.PROPER_NOUN,
                    start_offset=ent.get("start_offset", 0),
                    end_offset=ent.get("end_offset", 0),
                    sentence_index=ent.get("sentence_index", self._sentence_for_offset(sentences, ent.get("start_offset", 0))),
                    section=ent.get("section") if ent.get("section") else (sections.get(ent.get("start_offset"), None) if sections else None),
                    entity_type=ent.get("entity_type"),
                )
            )
        return results

    def _build_chains(self, pairs: List[Tuple[Mention, Mention]], mentions: List[Mention]) -> List[CoreferenceChain]:
        if not pairs:
            return []
        parent = _ChainBuilder(parents={m.mention_id: m.mention_id for m in mentions})
        for child, parent_m in pairs:
            parent.union(child.mention_id, parent_m.mention_id)
        groups: Dict[str, List[Mention]] = {}
        for m in mentions:
            root = parent.find(m.mention_id)
            groups.setdefault(root, []).append(m)
        chains: List[CoreferenceChain] = []
        for group_mentions in groups.values():
            canonical = self._select_canonical(group_mentions)
            confidence = self._calculate_chain_confidence(group_mentions, canonical)
            chains.append(
                CoreferenceChain(
                    canonical_mention=canonical,
                    mentions=group_mentions,
                    entity_type=canonical.entity_type or "UNKNOWN",
                    canonical_text=canonical.text,
                    confidence=confidence,
                )
            )
        return chains

    def _select_canonical(self, mentions: List[Mention]) -> Mention:
        proper = [m for m in mentions if m.mention_type == MentionType.PROPER_NOUN]
        if proper:
            proper.sort(key=lambda m: (m.start_offset, -len(m.text)))
            return proper[0]
        mentions.sort(key=lambda m: (m.start_offset, -len(m.text)))
        return mentions[0]

    def _calculate_chain_confidence(self, mentions: List[Mention], canonical: Mention) -> float:
        score = 0.5
        for m in mentions:
            if m.mention_type == MentionType.PROPER_NOUN:
                score += 0.2
            if m.section == canonical.section:
                score += 0.05
            distance = abs(m.start_offset - canonical.start_offset)
            score -= min(distance / 5000.0, 0.3)
        return max(0.0, min(1.0, score))

    def _generate_resolved_text(self, text: str, chains: List[CoreferenceChain]) -> str:
        if not chains:
            return text
        replacements: List[Tuple[int, int, str]] = []
        for chain in chains:
            for m in chain.mentions:
                if m.mention_type in {MentionType.PRONOUN, MentionType.ABBREVIATED, MentionType.DEFINITE_NP, MentionType.RELATIVE, MentionType.POSSESSIVE}:
                    new_text = f"{m.text} [{chain.canonical_text}]"
                    replacements.append((m.start_offset, m.end_offset, new_text))
        replacements.sort(key=lambda x: x[0])
        resolved_parts: List[str] = []
        cursor = 0
        for start, end, repl in replacements:
            if start < cursor:
                continue
            resolved_parts.append(text[cursor:start])
            resolved_parts.append(repl)
            cursor = end
        resolved_parts.append(text[cursor:])
        return "".join(resolved_parts)

    def _llm_disambiguate(self, text: str, sentences: List[str], pronouns: List[Mention], candidates: List[Mention]) -> List[Tuple[Mention, Mention]]:
        """Optional LLM-based disambiguation for unresolved pronouns.

        Falls back silently if llm_client is not available or returns nothing.
        """
        if not pronouns or not self.llm_client:
            return []
        pairs: List[Tuple[Mention, Mention]] = []
        for pronoun in pronouns:
            try:
                prompt = (
                    "Resolve this clinical pronoun to the best antecedent. "
                    f"Pronoun: '{pronoun.text}' in sentence index {pronoun.sentence_index}. "
                    f"Candidates: {[c.text for c in candidates if c.start_offset < pronoun.start_offset]}"
                )
                antecedent_text = self.llm_client.resolve_coref(prompt)
                match = next((c for c in candidates if c.text == antecedent_text), None)
                if match:
                    pairs.append((pronoun, match))
            except Exception:
                continue
        return pairs

    def _infer_entity_type_from_phrase(self, phrase: str) -> Optional[str]:
        low = phrase.lower()
        if "patient" in low or "pt" == low:
            return "PATIENT"
        if "medication" in low or "drug" in low or "medicine" in low:
            return "MEDICATION"
        if "procedure" in low or "surgery" in low or "operation" in low:
            return "PROCEDURE"
        if "condition" in low or "disease" in low or "diagnosis" in low or "hypertension" in low or "diabetes" in low:
            return "CONDITION"
        return None


__all__ = [
    "MentionType",
    "Mention",
    "CoreferenceChain",
    "CoreferenceResult",
    "MedicalAbbreviationResolver",
    "PronounResolver",
    "SectionAwareResolver",
    "CoreferenceResolver",
]
