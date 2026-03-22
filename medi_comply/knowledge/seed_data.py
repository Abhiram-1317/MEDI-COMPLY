"""
MEDI-COMPLY — Data seeding script.

Loads all medical knowledge into the KnowledgeManager:
ICD-10 (200+), CPT (100+), NCCI pairs (30+), MUE (15+),
LCD (10+), and Coding Guidelines (15+).

Run as a module::

    python -m medi_comply.knowledge.seed_data
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from medi_comply.knowledge.seed_icd10_part1 import get_cardiology_codes, get_diabetes_codes
from medi_comply.knowledge.seed_icd10_part2 import (
    get_injury_codes,
    get_nephrology_codes,
    get_pulmonology_codes,
    get_symptom_codes,
)
from medi_comply.knowledge.seed_cpt_data import get_cpt_codes, get_modifiers
from medi_comply.knowledge.seed_rules_data import (
    get_coding_guidelines,
    get_lcd_entries,
    get_mue_entries,
    get_ncci_pairs,
)

from medi_comply.knowledge.icd10_db import ICD10CodeEntry

if TYPE_CHECKING:
    from medi_comply.knowledge.knowledge_manager import KnowledgeManager


def seed_all_data(km: "KnowledgeManager") -> None:
    """Populate every knowledge store on *km* with seed data.

    Parameters
    ----------
    km:
        The :class:`KnowledgeManager` instance to populate.
    """
    # --- ICD-10 ---
    icd10_codes = (
        get_cardiology_codes()
        + get_diabetes_codes()
        + get_nephrology_codes()
        + get_pulmonology_codes()
        + get_symptom_codes()
        + get_injury_codes()
    )
    _ensure_icd10_codes(icd10_codes)
    km.icd10_db.load(icd10_codes)

    # --- CPT ---
    km.cpt_db.load(get_cpt_codes())
    km.cpt_db.load_modifiers(get_modifiers())

    # --- NCCI ---
    km.ncci_engine.load_edit_pairs(get_ncci_pairs())
    km.ncci_engine.load_mue_entries(get_mue_entries())

    # --- Medical Necessity ---
    km.med_necessity.load(get_lcd_entries())

    # --- Coding Guidelines ---
    km.guidelines.load(get_coding_guidelines())

    # --- Data integrity validation ---
    _validate_integrity(km)


def _ensure_icd10_codes(entries: list[ICD10CodeEntry]) -> None:
    """Backfill critical ICD-10 codes and relationships used in tests."""

    index = {entry.code: entry for entry in entries}

    def _merge_unique(target: list[str], additions: list[str]) -> None:
        for item in additions:
            if item and item not in target:
                target.append(item)

    # Ensure COPD Excludes1 rules stay synchronized
    for code, excludes in {"J44.0": ["J44.1"], "J44.1": ["J44.0"]}.items():
        entry = index.get(code)
        if entry:
            _merge_unique(entry.excludes1, excludes)

    supplemental_entries = [
        ICD10CodeEntry(
            code="Z87.891",
            description="Personal history of nicotine dependence",
            chapter="21",
            block="Z77-Z99",
            category="Z87",
            parent_code="Z87.89",
            is_billable=True,
            excludes1=["F17.2-"],
        ),
        ICD10CodeEntry(
            code="F17.210",
            description="Nicotine dependence, cigarettes, uncomplicated",
            chapter="5",
            block="F10-F19",
            category="F17",
            parent_code="F17.21",
            is_billable=True,
            excludes1=["Z87.891"],
        ),
        ICD10CodeEntry(
            code="R05.9",
            description="Cough, unspecified",
            chapter="18",
            block="R00-R09",
            category="R05",
            parent_code="R05",
            is_billable=True,
        ),
    ]

    for entry in supplemental_entries:
        existing = index.get(entry.code)
        if existing:
            _merge_unique(existing.excludes1, entry.excludes1)
            _merge_unique(existing.excludes2, entry.excludes2)
            if not existing.parent_code and entry.parent_code:
                existing.parent_code = entry.parent_code
            continue

        entries.append(entry)
        index[entry.code] = entry


def _validate_integrity(km: "KnowledgeManager") -> None:
    """Validate cross-references between datasets.

    Checks that NCCI edit pairs reference actual CPT codes,
    LCD entries reference actual codes, etc.

    Parameters
    ----------
    km:
        Populated KnowledgeManager.
    """
    warnings: list[str] = []

    # Check NCCI pairs reference real CPT codes
    for key in km.ncci_engine._edit_pairs:
        for code in key:
            if not km.cpt_db.code_exists(code):
                warnings.append(f"NCCI pair references unknown CPT: {code}")

    # Check LCD procedure codes
    for lcd_id, lcd in km.med_necessity._lcds.items():
        for proc in lcd.procedure_codes:
            if not km.cpt_db.code_exists(proc):
                warnings.append(f"LCD {lcd_id} references unknown CPT: {proc}")

    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}", file=sys.stderr)


def print_summary(km: "KnowledgeManager") -> None:
    """Print a human-readable summary of loaded data.

    Parameters
    ----------
    km:
        Populated KnowledgeManager.
    """
    counts = km.code_count
    print("=" * 60)
    print("  MEDI-COMPLY Knowledge Base — Seed Summary")
    print("=" * 60)
    print(f"  ICD-10-CM codes:       {counts['icd10']:>6}")
    print(f"  CPT codes:             {counts['cpt']:>6}")
    print(f"  CPT modifiers:         {km.cpt_db.modifier_count:>6}")
    print(f"  NCCI PTP edit pairs:   {counts['ncci_pairs']:>6}")
    print(f"  MUE entries:           {counts['mue']:>6}")
    print(f"  LCD entries:           {counts['lcd']:>6}")
    print(f"  Coding guidelines:     {counts['guidelines']:>6}")
    print("=" * 60)


# --- CLI entry point ---

if __name__ == "__main__":
    from medi_comply.knowledge.knowledge_manager import KnowledgeManager

    km = KnowledgeManager()
    seed_all_data(km)
    print_summary(km)
    print("\n  Seed complete. Knowledge base ready.")
