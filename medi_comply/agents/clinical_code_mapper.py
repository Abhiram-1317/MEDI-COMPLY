"""
MEDI-COMPLY — Clinical Code Mapper.

Provides direct O(1) mappings from common clinical condition texts
and procedure names to their most likely ICD-10 or CPT codes.
Acts as a high-confidence, lightning-fast first pass before vector search.
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Direct Mapping Tables
# ---------------------------------------------------------------------------

# Format: {"normalized string": [("Code", confidence), ...]}
CONDITION_TO_ICD10_MAP: dict[str, list[tuple[str, float]]] = {
    # Cardiac
    "acute nstemi": [("I21.4", 0.95)],
    "non-st elevation myocardial infarction": [("I21.4", 0.95)],
    "nstemi": [("I21.4", 0.95)],
    "acute stemi": [("I21.3", 0.90)],  # needs more specificity
    "stemi anterior wall": [("I21.01", 0.95)],
    "stemi inferior wall": [("I21.19", 0.95)],
    "myocardial infarction": [("I21.9", 0.80)],  # unspecified - needs more info
    "heart failure": [("I50.9", 0.75)],  # unspecified
    "systolic heart failure": [("I50.20", 0.80)],
    "diastolic heart failure": [("I50.30", 0.80)],
    "congestive heart failure": [("I50.9", 0.75)],
    "atrial fibrillation": [("I48.91", 0.80)],
    "paroxysmal atrial fibrillation": [("I48.0", 0.90)],
    "persistent atrial fibrillation": [("I48.1", 0.90)],
    "essential hypertension": [("I10", 0.95)],
    "hypertension": [("I10", 0.90)],
    "uncontrolled hypertension": [("I10", 0.90)],
    "coronary artery disease": [("I25.10", 0.85)],
    "chest pain": [("R07.9", 0.80)],
    "substernal chest pain": [("R07.2", 0.85)],

    # Diabetes
    "type 2 diabetes mellitus": [("E11.9", 0.80)],  # unspecified - needs complication check
    "type 2 diabetes": [("E11.9", 0.80)],
    "t2dm": [("E11.9", 0.80)],
    "type 2 diabetes with nephropathy": [("E11.22", 0.90)],
    "type 2 diabetes with diabetic nephropathy": [("E11.22", 0.90)],
    "type 2 diabetes with diabetic chronic kidney disease": [("E11.22", 0.95)],
    "type 2 diabetes with retinopathy": [("E11.319", 0.85)],
    "type 2 diabetes with neuropathy": [("E11.40", 0.85)],
    "type 2 diabetes with hyperglycemia": [("E11.65", 0.90)],
    "type 1 diabetes": [("E10.9", 0.80)],
    "diabetic nephropathy": [("E11.22", 0.85)],
    "diabetic chronic kidney disease": [("E11.22", 0.90)],

    # Renal
    "chronic kidney disease": [("N18.9", 0.70)],  # needs stage
    "ckd stage 1": [("N18.1", 0.95)],
    "ckd stage 2": [("N18.2", 0.95)],
    "ckd stage 3": [("N18.30", 0.90)],
    "ckd stage 3a": [("N18.31", 0.95)],
    "ckd stage 3b": [("N18.32", 0.95)],
    "ckd stage 4": [("N18.4", 0.95)],
    "ckd stage 5": [("N18.5", 0.95)],
    "end stage renal disease": [("N18.6", 0.95)],
    "acute kidney injury": [("N17.9", 0.80)],

    # Pulmonary
    "copd": [("J44.1", 0.75)],
    "copd exacerbation": [("J44.1", 0.92)],
    "copd acute exacerbation": [("J44.1", 0.95)],
    "copd with acute exacerbation": [("J44.1", 0.95)],
    "chronic obstructive pulmonary disease": [("J44.1", 0.75)],
    "chronic obstructive pulmonary disease with acute exacerbation": [("J44.1", 0.95)],
    "copd with acute lower respiratory infection": [("J44.0", 0.92)],
    "copd with infection": [("J44.0", 0.88)],
    "pneumonia": [("J18.9", 0.75)],
    "community acquired pneumonia": [("J18.9", 0.80)],
    "asthma": [("J45.20", 0.75)],
    "acute respiratory failure": [("J96.00", 0.80)],
    "acute respiratory failure with hypoxia": [("J96.01", 0.90)],
    "covid-19": [("U07.1", 0.95)],

    # Symptoms
    "dyspnea": [("R06.00", 0.85)],
    "shortness of breath": [("R06.02", 0.85)],
    "fever": [("R50.9", 0.90)],
    "cough": [("R05.9", 0.85)],
    "headache": [("R51.9", 0.85)],
    "dizziness": [("R42", 0.85)],
    "syncope": [("R55", 0.90)],
    "tachycardia": [("R00.0", 0.90)],
    "bradycardia": [("R00.1", 0.90)],
    "nausea": [("R11.0", 0.90)],
    "vomiting": [("R11.10", 0.90)],

    # Other
    "urinary tract infection": [("N39.0", 0.90)],
    "sepsis": [("A41.9", 0.75)],
    # Tobacco / smoking history
    "former tobacco use": [("Z87.891", 0.92)],
    "former smoker": [("Z87.891", 0.92)],
    "ex smoker": [("Z87.891", 0.90)],
    "tobacco use history": [("Z87.891", 0.88)],
    "quit smoking": [("Z87.891", 0.88)],
    "previous smoker": [("Z87.891", 0.90)],
    "history of tobacco use": [("Z87.891", 0.90)],
    "history of smoking": [("Z87.891", 0.90)],
    "pack year history": [("Z87.891", 0.85)],
    "tobacco use": [("F17.210", 0.90)],
    "current smoker": [("F17.210", 0.90)],
    "current tobacco use": [("F17.210", 0.90)],
    "active smoker": [("F17.210", 0.90)],
    "tobacco use disorder": [("F17.210", 0.88)],
    "nicotine dependence": [("F17.210", 0.92)],
    "tobacco dependence": [("F17.210", 0.90)],
    "obesity": [("E66.01", 0.80)],
    "morbid obesity": [("E66.01", 0.90)],
}

PROCEDURE_TO_CPT_MAP: dict[str, list[tuple[str, float]]] = {
    "echocardiogram": [("93306", 0.85)],
    "transthoracic echocardiogram": [("93306", 0.90)],
    "tte": [("93306", 0.90)],
    "transesophageal echocardiogram": [("93312", 0.90)],
    "tee": [("93312", 0.90)],
    "ecg": [("93000", 0.90)],
    "ekg": [("93000", 0.90)],
    "electrocardiogram": [("93000", 0.90)],
    "cardiac catheterization": [("93453", 0.80)],
    "left heart catheterization": [("93452", 0.90)],
    "right heart catheterization": [("93451", 0.90)],
    "coronary angiography": [("93458", 0.85)],
    "pci": [("92920", 0.85)],
    "percutaneous coronary intervention": [("92920", 0.85)],
    "chest x-ray": [("71046", 0.90)],
    "cxr": [("71046", 0.90)],
    "ct chest": [("71250", 0.80)],
    "ct chest with contrast": [("71260", 0.90)],
    "ct abdomen pelvis": [("74177", 0.80)],
    "mri brain": [("70553", 0.80)],
    "cbc": [("85025", 0.90)],
    "complete blood count": [("85025", 0.90)],
    "basic metabolic panel": [("80048", 0.90)],
    "bmp": [("80048", 0.90)],
    "comprehensive metabolic panel": [("80053", 0.90)],
    "cmp": [("80053", 0.90)],
    "troponin": [("84484", 0.90)],
    "hba1c": [("83036", 0.90)],
    "hemoglobin a1c": [("83036", 0.90)],
    "urinalysis": [("81001", 0.90)],
    "spirometry": [("94010", 0.90)],
    "nebulizer treatment": [("94640", 0.90)],
    "lipid panel": [("80061", 0.90)],
}


# ---------------------------------------------------------------------------
# Clinical Code Mapper
# ---------------------------------------------------------------------------

class ClinicalCodeMapper:
    """Direct mapping strategy for quick and robust candidate generation.

    Before querying vector DBs or keyword indexes, checks known common
    alignments perfectly matching exact or fuzzy definitions.
    """

    def lookup_condition(self, condition_text: str) -> list[tuple[str, float]]:
        """Lookup direct condition matches.

        Parameters
        ----------
        condition_text: str
            Input text to map.

        Returns
        -------
        list[tuple[str, float]]
            A list of tuples (code, confidence). Empty list if missing.
        """
        return self._fuzzy_match(condition_text, CONDITION_TO_ICD10_MAP, threshold=0.85)

    def lookup_procedure(self, procedure_text: str) -> list[tuple[str, float]]:
        """Lookup direct procedure matches.

        Parameters
        ----------
        procedure_text: str
            Input text to map.

        Returns
        -------
        list[tuple[str, float]]
            A list of tuples (code, confidence). Empty list if missing.
        """
        return self._fuzzy_match(procedure_text, PROCEDURE_TO_CPT_MAP, threshold=0.85)

    def get_all_condition_mappings(self) -> dict[str, list[tuple[str, float]]]:
        """Get the full dictionary of direct mappings for conditions."""
        return CONDITION_TO_ICD10_MAP

    def get_all_procedure_mappings(self) -> dict[str, list[tuple[str, float]]]:
        """Get the full dictionary of direct mappings for procedures."""
        return PROCEDURE_TO_CPT_MAP

    def _normalize_text(self, text: str) -> str:
        """Lowercase, strip, and shrink whitespace.

        Parameters
        ----------
        text: str
            Raw input text.

        Returns
        -------
        str
            Normalized string.
        """
        norm = text.lower().strip()
        norm = re.sub(r"\s+", " ", norm)
        return norm

    def _fuzzy_match(
        self,
        text: str,
        candidates: dict[str, list[tuple[str, float]]],
        threshold: float = 0.85
    ) -> list[tuple[str, float]]:
        """Use difflib exact hit checking with fuzzy fallbacks on dictionary keys.

        Parameters
        ----------
        text: str
            Raw string text.
        candidates: dict
            Target mapping index.
        threshold: float
            Minimum match ratio.

        Returns
        -------
        list[tuple[str, float]]
            Matches.
        """
        import difflib

        normed = self._normalize_text(text)

        # 1. Exact direct check
        if normed in candidates:
            return candidates[normed]

        # 2. Fuzzy mapping
        closest = difflib.get_close_matches(normed, candidates.keys(), n=1, cutoff=threshold)
        if closest:
            match_key = closest[0]
            base_results = candidates[match_key]
            # Reduce confidence slightly because it's a fuzzy match (e.g., 0.95 -> 0.95 * 0.9 = 0.855)
            return [(code, conf * 0.9) for code, conf in base_results]

        return []
