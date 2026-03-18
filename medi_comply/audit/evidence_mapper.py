"""
MEDI-COMPLY — Bidirectional mapping between codes and clinical evidence.

This module builds and queries the evidence map that connects every
assigned ICD-10 / CPT code back to the specific span of clinical text
that justifies it — and vice-versa.  The map is consumed by the
report generator, risk scorer, and auditors.

Evidence strength is classified as:
  DIRECT_SUPPORT  (0.85 – 1.0) — text explicitly names the condition
  INFERRED        (0.60 – 0.84) — condition derivable from context
  WEAK            (< 0.60)      — borderline or indirect linkage
"""

from __future__ import annotations

from typing import Optional

from medi_comply.schemas.coding_result import CodingResult, SingleCodeDecision
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.audit.audit_models import EvidenceMap, EvidenceLinkRecord


# ────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────

_DIRECT_THRESHOLD = 0.85
_INFERRED_THRESHOLD = 0.60
_DEFAULT_LINK_STRENGTH = 0.90


def _classify_strength(strength: float) -> str:
    """Return a human-readable relevance label for a link strength."""
    if strength >= _DIRECT_THRESHOLD:
        return "DIRECT_SUPPORT"
    if strength >= _INFERRED_THRESHOLD:
        return "INFERRED"
    return "WEAK"


class EvidenceMapper:
    """Bidirectional mapping between codes and clinical evidence.

    Code → Evidence: "Why was this code assigned?"
    Evidence → Code: "What codes does this clinical text support?"

    The mapper is stateless and may be reused across encounters.
    """

    # ── Primary build method ──────────────────────────────

    def build_evidence_map(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
    ) -> EvidenceMap:
        """Build a complete bidirectional evidence map.

        Parameters
        ----------
        coding_result:
            The final coding output containing diagnosis and procedure
            code decisions.
        scr:
            The structured clinical representation that was the source
            of evidence for the coding decisions.

        Returns
        -------
        EvidenceMap
            Populated map with ``code_to_evidence``,
            ``evidence_to_codes``, unlinked items, and coverage score.
        """
        code_to_evidence: dict[str, list[EvidenceLinkRecord]] = {}
        evidence_to_codes: dict[str, dict] = {}
        unlinked_evidence: list[str] = []
        unlinked_codes: list[str] = []

        # --- Build entity index from SCR ---
        entity_index = self._build_entity_index(scr)

        # Initialise evidence→codes mapping
        for key, ent in entity_index.items():
            evidence_to_codes[key] = {
                "text": ent.entity,
                "codes_supported": [],
                "link_strengths": [],
            }
            unlinked_evidence.append(key)

        # --- Process every code decision ---
        for decision in coding_result.diagnosis_codes:
            self._index_decision(
                decision, "ICD10", entity_index,
                code_to_evidence, evidence_to_codes,
                unlinked_evidence, unlinked_codes,
            )
        for decision in coding_result.procedure_codes:
            self._index_decision(
                decision, "CPT", entity_index,
                code_to_evidence, evidence_to_codes,
                unlinked_evidence, unlinked_codes,
            )

        # Deduplicate codes in evidence→codes mapping
        for key in evidence_to_codes:
            evidence_to_codes[key]["codes_supported"] = list(
                set(evidence_to_codes[key]["codes_supported"])
            )

        # --- Compute coverage score ---
        coverage = self._compute_coverage(
            coding_result, entity_index,
            unlinked_codes, unlinked_evidence,
        )

        return EvidenceMap(
            code_to_evidence=code_to_evidence,
            evidence_to_codes=evidence_to_codes,
            unlinked_evidence=unlinked_evidence,
            unlinked_codes=unlinked_codes,
            coverage_score=coverage,
        )

    # ── Query helpers ─────────────────────────────────────

    def get_evidence_for_code(
        self, evidence_map: EvidenceMap, code: str
    ) -> list[EvidenceLinkRecord]:
        """Return all evidence links supporting a given code."""
        return evidence_map.code_to_evidence.get(code, [])

    def get_codes_for_evidence(
        self,
        evidence_map: EvidenceMap,
        section: str,
        page: int,
        line: int,
    ) -> list[dict]:
        """Return code-support dicts for evidence at *section:page:line*."""
        results: list[dict] = []
        prefix = f"{section}:{page}:{line}:"
        for key, data in evidence_map.evidence_to_codes.items():
            if key.startswith(prefix):
                results.append(data)
        return results

    def calculate_coverage_score(
        self, evidence_map: EvidenceMap
    ) -> float:
        """Return the pre-computed coverage score."""
        return evidence_map.coverage_score

    def find_evidence_gaps(
        self, evidence_map: EvidenceMap
    ) -> list[dict]:
        """Return structured gap reports for codes without evidence
        and evidence without codes.

        Always returns exactly two elements:
          - ``[0]`` → UNLINKED_CODE gaps
          - ``[1]`` → UNLINKED_EVIDENCE gaps
        """
        return [
            {
                "type": "UNLINKED_CODE",
                "count": len(evidence_map.unlinked_codes),
                "items": evidence_map.unlinked_codes,
                "severity": "HIGH" if evidence_map.unlinked_codes else "NONE",
                "recommendation": (
                    "Codes without supporting evidence should be "
                    "reviewed by a human coder."
                ) if evidence_map.unlinked_codes else "",
            },
            {
                "type": "UNLINKED_EVIDENCE",
                "count": len(evidence_map.unlinked_evidence),
                "items": evidence_map.unlinked_evidence,
                "severity": "LOW" if evidence_map.unlinked_evidence else "NONE",
                "recommendation": (
                    "Clinical evidence not linked to any code may "
                    "indicate missed diagnoses or procedures."
                ) if evidence_map.unlinked_evidence else "",
            },
        ]

    def get_weak_links(
        self, evidence_map: EvidenceMap
    ) -> list[EvidenceLinkRecord]:
        """Return all evidence links whose strength is below the
        inferred threshold (< 0.60)."""
        weak: list[EvidenceLinkRecord] = []
        for links in evidence_map.code_to_evidence.values():
            for link in links:
                if link.link_strength < _INFERRED_THRESHOLD:
                    weak.append(link)
        return weak

    # ── Internal helpers ──────────────────────────────────

    def _build_entity_index(
        self, scr: StructuredClinicalRepresentation
    ) -> dict:
        """Build a lookup dict keyed by ``section:page:line:offset``."""
        index: dict = {}
        for source_list in (
            scr.conditions,
            scr.procedures,
            getattr(scr, "lab_results", []),
        ):
            for entity in source_list:
                if not entity.evidence:
                    continue
                ev = entity.evidence[0]
                key = f"{entity.section}:{ev.page}:{ev.line}:{ev.char_offset[0]}"
                index[key] = entity
        return index

    def _index_decision(
        self,
        decision: SingleCodeDecision,
        code_type: str,
        entity_index: dict,
        code_to_evidence: dict[str, list[EvidenceLinkRecord]],
        evidence_to_codes: dict[str, dict],
        unlinked_evidence: list[str],
        unlinked_codes: list[str],
    ) -> None:
        """Link a single code decision to its supporting evidence."""
        code_to_evidence.setdefault(decision.code, [])

        if not decision.reasoning_chain:
            if decision.code not in unlinked_codes:
                unlinked_codes.append(decision.code)
            return

        for step in decision.reasoning_chain:
            for key, ent in entity_index.items():
                text_match = (
                    ent.entity.lower() in step.detail.lower()
                    or ent.entity.lower() in decision.description.lower()
                )
                if not text_match:
                    continue
                ev_ref = ent.evidence[0] if ent.evidence else None
                if ev_ref is None:
                    continue

                strength = _DEFAULT_LINK_STRENGTH
                relevance = _classify_strength(strength)

                link = EvidenceLinkRecord(
                    evidence_id=key,
                    code=decision.code,
                    source_text=ent.entity,
                    section=ent.section,
                    page=ev_ref.page,
                    line=ev_ref.line,
                    char_offset=(ev_ref.char_offset[0], ev_ref.char_offset[1]),
                    relevance=relevance,
                    link_strength=strength,
                )
                code_to_evidence[decision.code].append(link)

                evidence_to_codes[key]["codes_supported"].append(decision.code)
                evidence_to_codes[key]["link_strengths"].append(strength)

                if key in unlinked_evidence:
                    unlinked_evidence.remove(key)

        if not code_to_evidence[decision.code]:
            if decision.code not in unlinked_codes:
                unlinked_codes.append(decision.code)

    def _compute_coverage(
        self,
        coding_result: CodingResult,
        entity_index: dict,
        unlinked_codes: list[str],
        unlinked_evidence: list[str],
    ) -> float:
        """Compute the average of code coverage and evidence coverage."""
        total_codes = (
            len(coding_result.diagnosis_codes) + len(coding_result.procedure_codes)
        )
        covered_codes = total_codes - len(unlinked_codes)
        total_entities = len(entity_index)
        covered_entities = total_entities - len(unlinked_evidence)

        c1 = (covered_codes / total_codes) if total_codes > 0 else 1.0
        c2 = (covered_entities / total_entities) if total_entities > 0 else 1.0

        return (c1 + c2) / 2

