"""Seed script for LCD/NCD database.

Run as: `python -m medi_comply.knowledge.seed_lcd_ncd`
"""

from __future__ import annotations

from medi_comply.knowledge.lcd_ncd_engine import (
    CoverageStatus,
    LCDNCDDatabase,
    LCDNCDEngine,
    seed_lcd_ncd_data,
)


def main() -> None:
    db = LCDNCDDatabase()
    seed_lcd_ncd_data(db)
    engine = LCDNCDEngine(database=db)

    determinations = db.get_active_determinations()
    ncds = db.get_all_ncds()
    lcds = db.get_all_lcds()

    covered_cpt = set()
    covered_icd = set()
    for det in determinations:
        covered_cpt.update(det.covered_cpt_codes)
        covered_icd.update(det.covered_icd10_codes)

    print("LCD/NCD Seed Summary")
    print("--------------------")
    print(f"Total determinations loaded: {len(determinations)}")
    print(f"NCDs loaded: {len(ncds)}")
    print(f"LCDs loaded: {len(lcds)}")
    print(f"Total covered CPT codes: {len(covered_cpt)}")
    print(f"Total covered ICD-10 codes: {len(covered_icd)}")

    print("\nSample Checks:")
    covered_check = engine.check_medical_necessity("82306", ["E55.9"])
    not_covered_check = engine.check_medical_necessity("82306", ["J06.9"])

    def summarize(label: str, res) -> None:
        det_id = res.determination_used.determination_id if res.determination_used else "NONE"
        print(f"{label}: status={res.coverage_status}, det={det_id}, covered_dx={res.covered_diagnoses_found}, non_covered_dx={res.non_covered_diagnoses_found}")

    summarize("Vitamin D deficiency", covered_check)
    summarize("URI dx", not_covered_check)

    assert covered_check.coverage_status == CoverageStatus.COVERED
    assert not_covered_check.coverage_status in {CoverageStatus.NOT_COVERED, CoverageStatus.NOT_SPECIFIED}


if __name__ == "__main__":
    main()
