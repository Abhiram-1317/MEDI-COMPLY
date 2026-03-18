"""
MEDI-COMPLY — Medical abbreviation expander.

Expands 100+ medical abbreviations with context-sensitive disambiguation
for ambiguous abbreviations like PE (pulmonary embolism vs physical exam).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

@dataclass
class AbbreviationMatch:
    """A single abbreviation match in clinical text."""
    original: str
    expansion: str
    start: int
    end: int
    context_dependent: bool = False


# ---------------------------------------------------------------------------
# Abbreviation dictionary
# ---------------------------------------------------------------------------

ABBREVIATIONS: dict[str, str] = {
    # Conditions
    "DM": "diabetes mellitus",
    "T2DM": "type 2 diabetes mellitus",
    "T1DM": "type 1 diabetes mellitus",
    "HTN": "hypertension",
    "MI": "myocardial infarction",
    "STEMI": "ST-elevation myocardial infarction",
    "NSTEMI": "non-ST-elevation myocardial infarction",
    "CHF": "congestive heart failure",
    "HF": "heart failure",
    "CAD": "coronary artery disease",
    "CABG": "coronary artery bypass graft",
    "PCI": "percutaneous coronary intervention",
    "AF": "atrial fibrillation",
    "AFib": "atrial fibrillation",
    "DVT": "deep vein thrombosis",
    "CVA": "cerebrovascular accident",
    "TIA": "transient ischemic attack",
    "CKD": "chronic kidney disease",
    "AKI": "acute kidney injury",
    "ESRD": "end-stage renal disease",
    "COPD": "chronic obstructive pulmonary disease",
    "SOB": "shortness of breath",
    "DOE": "dyspnea on exertion",
    "URI": "upper respiratory infection",
    "UTI": "urinary tract infection",
    "ARDS": "acute respiratory distress syndrome",
    "OSA": "obstructive sleep apnea",
    "GERD": "gastroesophageal reflux disease",
    "PUD": "peptic ulcer disease",
    "GI": "gastrointestinal",
    "IBD": "inflammatory bowel disease",
    "RA": "rheumatoid arthritis",
    "OA": "osteoarthritis",
    "SLE": "systemic lupus erythematosus",
    "TBI": "traumatic brain injury",
    "LOC": "loss of consciousness",
    "BPH": "benign prostatic hyperplasia",
    "PSA": "prostate-specific antigen",
    # Measurements
    "BMI": "body mass index",
    "BSA": "body surface area",
    "GFR": "glomerular filtration rate",
    "BUN": "blood urea nitrogen",
    "Cr": "creatinine",
    "INR": "international normalized ratio",
    "PT": "prothrombin time",
    "PTT": "partial thromboplastin time",
    # Med admin
    "NPO": "nothing by mouth",
    "PRN": "as needed",
    "BID": "twice daily",
    "TID": "three times daily",
    "QID": "four times daily",
    "QHS": "at bedtime",
    "PO": "by mouth",
    "IV": "intravenous",
    "IM": "intramuscular",
    "SQ": "subcutaneous",
    "SL": "sublingual",
    # Labs
    "CBC": "complete blood count",
    "BMP": "basic metabolic panel",
    "CMP": "comprehensive metabolic panel",
    "LFT": "liver function tests",
    "TSH": "thyroid-stimulating hormone",
    "HbA1c": "hemoglobin A1c",
    # Imaging / Diagnostics
    "ECG": "electrocardiogram",
    "EKG": "electrocardiogram",
    "CXR": "chest X-ray",
    "CT": "computed tomography",
    "MRI": "magnetic resonance imaging",
    "US": "ultrasound",
    "TTE": "transthoracic echocardiogram",
    "TEE": "transesophageal echocardiogram",
    "EEG": "electroencephalogram",
    "EMG": "electromyography",
    # Shorthand
    "s/p": "status post",
    "h/o": "history of",
    "f/u": "follow up",
    "w/": "with",
    "w/o": "without",
    "c/o": "complains of",
    "r/o": "rule out",
    "d/t": "due to",
    # Exam findings
    "WNL": "within normal limits",
    "NAD": "no acute distress",
    "PERRLA": "pupils equal round reactive to light and accommodation",
    "RRR": "regular rate and rhythm",
    "CTA": "clear to auscultation",
    "CTAB": "clear to auscultation bilaterally",
    "NKA": "no known allergies",
    "NKDA": "no known drug allergies",
    "A&O": "alert and oriented",
    "AAOx3": "alert and oriented times three",
    # Anatomy
    "LE": "lower extremity",
    "UE": "upper extremity",
    "LLE": "left lower extremity",
    "RLE": "right lower extremity",
    "LUE": "left upper extremity",
    "RUE": "right upper extremity",
    # Locations
    "OR": "operating room",
    "ED": "emergency department",
    "ICU": "intensive care unit",
    "CCU": "cardiac care unit",
}

# Context-sensitive abbreviations
CONTEXT_SENSITIVE: dict[str, dict[str, list[str]]] = {
    "PE": {
        "pulmonary embolism": ["DVT", "anticoagul", "clot", "lung", "Wells", "embol", "VTE"],
        "physical examination": ["exam", "examination", "findings", "objective", "shows", "reveals"],
    },
    "MS": {
        "multiple sclerosis": ["demyelinat", "neurolog", "relaps", "lesion", "brain"],
        "mental status": ["alert", "oriented", "GCS", "confusion", "AAO"],
        "morphine sulfate": ["mg", "IV", "pain", "dose", "PRN", "opiate"],
    },
}


# ---------------------------------------------------------------------------
# Abbreviation Expander
# ---------------------------------------------------------------------------

class AbbreviationExpander:
    """Expands medical abbreviations in clinical text.

    Handles both unambiguous abbreviations (direct lookup) and
    context-sensitive abbreviations (disambiguation via surrounding text).

    Parameters
    ----------
    custom_abbreviations:
        Optional extra abbreviations to merge with the built-in dictionary.
    """

    def __init__(self, custom_abbreviations: Optional[dict[str, str]] = None) -> None:
        self._abbreviations: dict[str, str] = {**ABBREVIATIONS}
        if custom_abbreviations:
            self._abbreviations.update(custom_abbreviations)

        # Build case-insensitive lookup (lowercase key → original key)
        self._lower_map: dict[str, str] = {k.lower(): k for k in self._abbreviations}

        # Precompile word-boundary pattern for slash-based abbreviations
        self._slash_abbrevs = sorted(
            [k for k in self._abbreviations if "/" in k],
            key=len,
            reverse=True,
        )

    def expand(self, text: str) -> tuple[str, list[AbbreviationMatch]]:
        """Expand abbreviations in text.

        Parameters
        ----------
        text:
            Clinical text.

        Returns
        -------
        tuple[str, list[AbbreviationMatch]]
            The expanded text and a list of matches found.
        """
        matches: list[AbbreviationMatch] = []

        # First handle slash abbreviations (e.g. s/p, h/o, w/o)
        for abbrev in self._slash_abbrevs:
            pattern = re.compile(re.escape(abbrev), re.IGNORECASE)
            for m in pattern.finditer(text):
                expansion = self._abbreviations[abbrev]
                matches.append(AbbreviationMatch(
                    original=m.group(),
                    expansion=expansion,
                    start=m.start(),
                    end=m.end(),
                ))

        # Then word-boundary abbreviations
        words_pattern = re.compile(r'\b([A-Za-z&]{2,}(?:x\d)?)\b')
        for m in words_pattern.finditer(text):
            word = m.group(1)
            word_lower = word.lower()

            # Skip if it's part of a slash abbreviation already matched
            if any(m.start() >= am.start and m.end() <= am.end for am in matches):
                continue

            # Check context-sensitive abbreviations
            if word.upper() in CONTEXT_SENSITIVE:
                expansion = self._disambiguate(word.upper(), text)
                matches.append(AbbreviationMatch(
                    original=word,
                    expansion=expansion,
                    start=m.start(),
                    end=m.end(),
                    context_dependent=True,
                ))
                continue

            # Check direct lookup
            if word_lower in self._lower_map:
                orig_key = self._lower_map[word_lower]
                # Extra check: only match uppercase abbreviations (avoid expanding
                # normal English words like "or", "us", etc.)
                if word == orig_key or (len(word) <= 5 and word.isupper()):
                    matches.append(AbbreviationMatch(
                        original=word,
                        expansion=self._abbreviations[orig_key],
                        start=m.start(),
                        end=m.end(),
                    ))
            elif word in self._abbreviations:
                matches.append(AbbreviationMatch(
                    original=word,
                    expansion=self._abbreviations[word],
                    start=m.start(),
                    end=m.end(),
                ))

        # Sort by position
        matches.sort(key=lambda am: am.start)

        # Build expanded text (replace from end to preserve offsets)
        expanded = text
        for am in reversed(matches):
            expanded = expanded[:am.start] + am.expansion + expanded[am.end:]

        return expanded, matches

    def expand_entity(self, entity_text: str, context: str = "") -> str:
        """Expand abbreviations within an entity's text.

        Parameters
        ----------
        entity_text:
            The entity surface form.
        context:
            Surrounding context for disambiguation.

        Returns
        -------
        str
            Expanded entity text.
        """
        expanded, _ = self.expand(entity_text)
        return expanded

    def _disambiguate(self, abbreviation: str, context: str) -> str:
        """Resolve an ambiguous abbreviation using context clues.

        Parameters
        ----------
        abbreviation:
            Upper-case abbreviation.
        context:
            Surrounding text.

        Returns
        -------
        str
            Best-matching expansion.
        """
        clues = CONTEXT_SENSITIVE.get(abbreviation, {})
        ctx_lower = context.lower()
        best_expansion = list(clues.keys())[0] if clues else abbreviation
        best_score = 0

        for expansion, keywords in clues.items():
            score = sum(1 for kw in keywords if kw.lower() in ctx_lower)
            if score > best_score:
                best_score = score
                best_expansion = expansion

        return best_expansion

    def get_expansion(self, abbreviation: str) -> Optional[str]:
        """Get the expansion for an abbreviation.

        Parameters
        ----------
        abbreviation:
            Abbreviation text.

        Returns
        -------
        Optional[str]
        """
        if abbreviation in self._abbreviations:
            return self._abbreviations[abbreviation]
        lower = abbreviation.lower()
        if lower in self._lower_map:
            return self._abbreviations[self._lower_map[lower]]
        return None

    def _is_abbreviation_boundary(self, text: str, pos: int, length: int) -> bool:
        """Check that match is a whole word.

        Parameters
        ----------
        text:
            Full text.
        pos:
            Start position.
        length:
            Length of matched abbreviation.

        Returns
        -------
        bool
        """
        if pos > 0 and text[pos - 1].isalnum():
            return False
        end = pos + length
        if end < len(text) and text[end].isalnum():
            return False
        return True
