"""Curated demo scenarios for the Streamlit application."""

from __future__ import annotations

from medi_comply.tests.conftest import SAMPLE_NOTES


DEMO_SCENARIOS = {
    "🫀 Cardiac NSTEMI (Complex)": {
        "note": SAMPLE_NOTES["cardiac_nstemi"],
        "patient": {"age": 62, "gender": "male", "encounter_type": "INPATIENT"},
        "description": (
            "62yo male with acute NSTEMI, T2DM with diabetic CKD, HTN. Tests combination codes, "
            "Use Additional logic, and sequencing."
        ),
    },
    "🫁 COPD Exacerbation": {
        "note": SAMPLE_NOTES["pulmonary_copd"],
        "patient": {"age": 55, "gender": "female", "encounter_type": "INPATIENT"},
        "description": (
            "55yo female with COPD exacerbation. Tests negation handling and tobacco history coding."
        ),
    },
    "💊 Simple DM + HTN (Outpatient)": {
        "note": SAMPLE_NOTES["simple_dm_htn"],
        "patient": {"age": 48, "gender": "female", "encounter_type": "OUTPATIENT"},
        "description": "Routine follow-up visit validating outpatient coding rules.",
    },
    "❤️ CHF Exacerbation": {
        "note": SAMPLE_NOTES["chf_exacerbation"],
        "patient": {"age": 70, "gender": "male", "encounter_type": "INPATIENT"},
        "description": "Complex CHF with AFib, CKD, hyperkalemia. Tests multi-system coding.",
    },
    "🦠 Pneumonia with Sepsis": {
        "note": SAMPLE_NOTES["pneumonia_sepsis"],
        "patient": {"age": 68, "gender": "female", "encounter_type": "INPATIENT"},
        "description": "Severe sepsis from pneumonia with respiratory failure. High complexity.",
    },
    "🦴 Hip Fracture (Injury)": {
        "note": SAMPLE_NOTES["fracture_injury"],
        "patient": {"age": 82, "gender": "female", "encounter_type": "INPATIENT"},
        "description": "Fracture with laterality requirements and 7th character validation.",
    },
    "📝 Messy Abbreviated Note": {
        "note": SAMPLE_NOTES["messy_abbreviated"],
        "patient": {"age": 45, "gender": "male", "encounter_type": "OUTPATIENT"},
        "description": "Heavily abbreviated documentation. Tests abbreviation expansion.",
    },
    "✍️ Custom Note": {
        "note": "",
        "patient": {"age": 50, "gender": "male", "encounter_type": "INPATIENT"},
        "description": "Enter your own clinical note to test the system live.",
    },
}
