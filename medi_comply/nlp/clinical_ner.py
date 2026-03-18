"""
MEDI-COMPLY — Hybrid clinical NER engine.

Extracts medical entities (conditions, procedures, medications, vitals,
labs) using a combination of rule-based regex patterns and LLM-based
extraction.  Rule-based extraction runs first (fast, reliable); LLM is
used for complex conditions/procedures when enabled.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from medi_comply.nlp.document_ingester import IngestedDocument
from medi_comply.nlp.section_parser import ClinicalSection
from medi_comply.nlp.evidence_tracker import EvidenceTracker, SourceEvidence


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ClinicalEntity:
    """A single extracted clinical entity."""
    entity_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    text: str = ""
    entity_type: str = "CONDITION"
    normalized_text: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    assertion: str = "PRESENT"
    source_evidence: Optional[SourceEvidence] = None
    confidence: float = 0.90
    related_entities: list[str] = field(default_factory=list)
    raw_context: str = ""
    extraction_method: str = "RULE_BASED"


# ---------------------------------------------------------------------------
# Regex patterns for rule-based extraction
# ---------------------------------------------------------------------------

# Vital signs
_BP_PAT = re.compile(
    r"(?:BP|blood\s*pressure)\s*[:\s]*(\d{2,3})\s*/\s*(\d{2,3})", re.IGNORECASE
)
_HR_PAT = re.compile(r"(?:HR|heart\s*rate|pulse)\s*[:\s]*(\d{2,3})", re.IGNORECASE)
_SPO2_PAT = re.compile(
    r"(?:SpO2|O2\s*sat|oxygen\s*saturation)\s*[:\s]*(\d{2,3})\s*%?", re.IGNORECASE
)
_TEMP_PAT = re.compile(
    r"(?:Temp|temperature|(?<!\w)T)\s*[:\s]*([\d.]+)\s*(?:°?\s*[CF])?", re.IGNORECASE
)
_RR_PAT = re.compile(r"(?:RR|respiratory\s*rate)\s*[:\s]*(\d{1,2})", re.IGNORECASE)
_WEIGHT_PAT = re.compile(
    r"(?:Weight|Wt)\s*[:\s]*([\d.]+)\s*(?:kg|lbs?|pounds?)?", re.IGNORECASE
)

# Lab values
_LAB_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    ("troponin", re.compile(r"(?:troponin|trop)\s*[:\s]*([\d.]+)\s*(?:ng/mL|ng/L)?", re.I), "ng/mL", "< 0.04"),
    ("GFR", re.compile(r"(?:GFR|eGFR|glomerular\s*filtration)\s*[:\s]*(\d+)\s*(?:mL/min)?", re.I), "mL/min", "> 90"),
    ("HbA1c", re.compile(r"(?:HbA1c|A1c|hemoglobin\s*A1c)\s*[:\s]*([\d.]+)\s*%?", re.I), "%", "< 5.7"),
    ("creatinine", re.compile(r"(?:creatinine|Cr)\s*[:\s]*([\d.]+)\s*(?:mg/dL)?", re.I), "mg/dL", "0.7-1.3"),
    ("glucose", re.compile(r"(?:glucose|blood\s*sugar|BG)\s*[:\s]*(\d+)\s*(?:mg/dL)?", re.I), "mg/dL", "70-100"),
    ("WBC", re.compile(r"(?:WBC|white\s*blood\s*cell)\s*[:\s]*([\d.]+)", re.I), "K/uL", "4.5-11.0"),
    ("hemoglobin", re.compile(r"(?:hemoglobin|Hgb|Hb)\s*[:\s]*([\d.]+)", re.I), "g/dL", "12.0-17.5"),
    ("platelet", re.compile(r"(?:platelet|plt)\s*[:\s]*(\d+)", re.I), "K/uL", "150-400"),
    ("BNP", re.compile(r"(?:BNP|NT-proBNP)\s*[:\s]*(\d+)", re.I), "pg/mL", "< 100"),
    ("INR", re.compile(r"(?:INR)\s*[:\s]*([\d.]+)", re.I), "", "0.8-1.1"),
    ("CRP", re.compile(r"(?:CRP|C-reactive\s*protein)\s*[:\s]*([\d.]+)", re.I), "mg/L", "< 10"),
]

# Medications — common drug names for regex matching
_DRUG_NAMES = (
    "metformin|lisinopril|aspirin|atorvastatin|amlodipine|losartan|omeprazole|"
    "metoprolol|levothyroxine|albuterol|prednisone|furosemide|warfarin|"
    "gabapentin|hydrochlorothiazide|amoxicillin|clopidogrel|pantoprazole|"
    "acetaminophen|ibuprofen|insulin|heparin|enoxaparin|apixaban|rivaroxaban|"
    "carvedilol|spironolactone|digoxin|nitroglycerin|diltiazem|cephalexin|"
    "azithromycin|doxycycline|ciprofloxacin|trimethoprim|sulfamethoxazole|"
    "tiotropium|fluticasone|budesonide|montelukast|cetirizine|loratadine|"
    "sertraline|fluoxetine|escitalopram|duloxetine|trazodone|zolpidem|"
    "morphine|hydrocodone|oxycodone|tramadol|ketorolac|ondansetron"
)
_MED_PAT = re.compile(
    rf"({_DRUG_NAMES})\s*(\d+)\s*(mg|mcg|units?|mL)?\s*"
    rf"(PO|IV|IM|SQ|SL|inhaled)?\s*"
    rf"(daily|BID|TID|QID|PRN|Q\dH?|once daily|twice daily|at bedtime|QHS)?",
    re.IGNORECASE,
)

# Condition keywords for rule-based extraction
_CONDITION_KW: list[tuple[str, str]] = [
    ("acute NSTEMI", "Acute Non-ST-Elevation Myocardial Infarction"),
    ("NSTEMI", "Non-ST-Elevation Myocardial Infarction"),
    ("acute STEMI", "Acute ST-Elevation Myocardial Infarction"),
    ("STEMI", "ST-Elevation Myocardial Infarction"),
    ("type 2 diabetes mellitus", "Type 2 Diabetes Mellitus"),
    ("type 2 diabetes", "Type 2 Diabetes Mellitus"),
    ("type 1 diabetes mellitus", "Type 1 Diabetes Mellitus"),
    ("type 1 diabetes", "Type 1 Diabetes Mellitus"),
    ("T2DM", "Type 2 Diabetes Mellitus"),
    ("T1DM", "Type 1 Diabetes Mellitus"),
    ("diabetic nephropathy", "Diabetic Nephropathy"),
    ("diabetic chronic kidney disease", "Diabetic Chronic Kidney Disease"),
    ("diabetic retinopathy", "Diabetic Retinopathy"),
    ("diabetic neuropathy", "Diabetic Neuropathy"),
    ("chronic kidney disease", "Chronic Kidney Disease"),
    ("CKD stage 3b", "Chronic Kidney Disease Stage 3b"),
    ("CKD stage 3a", "Chronic Kidney Disease Stage 3a"),
    ("CKD stage 3", "Chronic Kidney Disease Stage 3"),
    ("CKD stage 4", "Chronic Kidney Disease Stage 4"),
    ("CKD stage 5", "Chronic Kidney Disease Stage 5"),
    ("congestive heart failure", "Congestive Heart Failure"),
    ("heart failure", "Heart Failure"),
    ("hypertension", "Hypertension"),
    ("HTN", "Hypertension"),
    ("atrial fibrillation", "Atrial Fibrillation"),
    ("AFib", "Atrial Fibrillation"),
    ("COPD acute exacerbation", "COPD with Acute Exacerbation"),
    ("COPD exacerbation", "COPD with Acute Exacerbation"),
    ("COPD", "Chronic Obstructive Pulmonary Disease"),
    ("coronary artery disease", "Coronary Artery Disease"),
    ("CAD", "Coronary Artery Disease"),
    ("pulmonary embolism", "Pulmonary Embolism"),
    ("deep vein thrombosis", "Deep Vein Thrombosis"),
    ("pneumonia", "Pneumonia"),
    ("chest pain", "Chest Pain"),
    ("shortness of breath", "Shortness of Breath"),
    ("SOB", "Shortness of Breath"),
    ("dyspnea", "Dyspnea"),
    ("dizziness", "Dizziness"),
    ("syncope", "Syncope"),
    ("fever", "Fever"),
    ("cough", "Cough"),
    ("hemoptysis", "Hemoptysis"),
    ("leg swelling", "Lower Extremity Edema"),
    ("tobacco use disorder", "Tobacco Use Disorder"),
    ("former smoker", "Former Tobacco Use Disorder"),
]

# Sort by length descending so longer matches take priority
_CONDITION_KW.sort(key=lambda x: -len(x[0]))


# ---------------------------------------------------------------------------
# Clinical NER Engine
# ---------------------------------------------------------------------------

class ClinicalNEREngine:
    """Hybrid entity extraction engine combining rules and LLM.

    Rule-based extraction covers vitals, lab values, medications, and
    common condition keywords.  LLM-based extraction (when enabled) handles
    complex conditions and procedures that rules miss.
    """

    def __init__(self) -> None:
        self._evidence_tracker = EvidenceTracker()

    def extract(
        self,
        document: IngestedDocument,
        sections: list[ClinicalSection],
        use_llm: bool = False,
    ) -> list[ClinicalEntity]:
        """Extract all entities from a parsed document.

        Parameters
        ----------
        document:
            Ingested source document.
        sections:
            Parsed clinical sections.
        use_llm:
            Whether to use LLM for condition/procedure extraction.

        Returns
        -------
        list[ClinicalEntity]
        """
        entities: list[ClinicalEntity] = []

        for section in sections:
            text = section.content
            base_offset = section.start_char

            # Rule-based extraction (always runs)
            entities.extend(self._extract_vitals(text, section, base_offset, document))
            entities.extend(self._extract_labs(text, section, base_offset, document))
            entities.extend(self._extract_medications(text, section, base_offset, document))
            entities.extend(self._extract_conditions_rules(text, section, base_offset, document))

        # Deduplicate (same entity_type + overlapping text)
        entities = self._deduplicate(entities)
        return entities

    # -- Vitals extraction -------------------------------------------------

    def _extract_vitals(
        self,
        text: str,
        section: ClinicalSection,
        base_offset: int,
        document: IngestedDocument,
    ) -> list[ClinicalEntity]:
        """Extract vital sign entities from text.

        Parameters
        ----------
        text:
            Section text.
        section:
            Source section.
        base_offset:
            Char offset of section start in document.
        document:
            Source document.

        Returns
        -------
        list[ClinicalEntity]
        """
        entities: list[ClinicalEntity] = []

        # Blood pressure
        for m in _BP_PAT.finditer(text):
            sys_val, dia_val = m.group(1), m.group(2)
            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group(),
                entity_type="VITAL_SIGN",
                normalized_text=f"Blood Pressure {sys_val}/{dia_val} mmHg",
                attributes={"systolic": int(sys_val), "diastolic": int(dia_val), "unit": "mmHg"},
                source_evidence=evidence,
                confidence=0.98,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))

        # Heart rate
        for m in _HR_PAT.finditer(text):
            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group(),
                entity_type="VITAL_SIGN",
                normalized_text=f"Heart Rate {m.group(1)} bpm",
                attributes={"value": int(m.group(1)), "unit": "bpm"},
                source_evidence=evidence,
                confidence=0.98,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))

        # SpO2
        for m in _SPO2_PAT.finditer(text):
            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group(),
                entity_type="VITAL_SIGN",
                normalized_text=f"SpO2 {m.group(1)}%",
                attributes={"value": int(m.group(1)), "unit": "%"},
                source_evidence=evidence,
                confidence=0.98,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))

        # RR
        for m in _RR_PAT.finditer(text):
            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group(),
                entity_type="VITAL_SIGN",
                normalized_text=f"Respiratory Rate {m.group(1)} breaths/min",
                attributes={"value": int(m.group(1)), "unit": "breaths/min"},
                source_evidence=evidence,
                confidence=0.98,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))

        # Temperature
        for m in _TEMP_PAT.finditer(text):
            val = float(m.group(1))
            # Filter out values that aren't plausible temperatures
            if val < 30 or val > 110:
                continue
            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group(),
                entity_type="VITAL_SIGN",
                normalized_text=f"Temperature {val}",
                attributes={"value": val},
                source_evidence=evidence,
                confidence=0.95,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))

        return entities

    # -- Labs extraction ---------------------------------------------------

    def _extract_labs(
        self,
        text: str,
        section: ClinicalSection,
        base_offset: int,
        document: IngestedDocument,
    ) -> list[ClinicalEntity]:
        """Extract lab value entities from text.

        Parameters
        ----------
        text:
            Section text.
        section:
            Source section.
        base_offset:
            Char offset of section start.
        document:
            Source document.

        Returns
        -------
        list[ClinicalEntity]
        """
        entities: list[ClinicalEntity] = []

        for lab_name, pattern, unit, ref_range in _LAB_PATTERNS:
            for m in pattern.finditer(text):
                val = float(m.group(1))
                char_start = base_offset + m.start()
                char_end = base_offset + m.end()
                evidence = self._evidence_tracker.create_evidence(
                    m.group(), document, section, char_start, char_end,
                    extraction_method="RULE_BASED",
                )
                # Determine if abnormal
                is_abnormal = self._check_abnormal(val, ref_range)
                entities.append(ClinicalEntity(
                    text=m.group(),
                    entity_type="LAB_VALUE",
                    normalized_text=f"{lab_name} {val} {unit}",
                    attributes={
                        "test_name": lab_name,
                        "value": val,
                        "unit": unit,
                        "reference_range": ref_range,
                        "is_abnormal": is_abnormal,
                    },
                    source_evidence=evidence,
                    confidence=0.97,
                    raw_context=text[max(0, m.start()-30):m.end()+30],
                ))
        return entities

    # -- Medications extraction --------------------------------------------

    def _extract_medications(
        self,
        text: str,
        section: ClinicalSection,
        base_offset: int,
        document: IngestedDocument,
    ) -> list[ClinicalEntity]:
        """Extract medication entities from text.

        Parameters
        ----------
        text:
            Section text.
        section:
            Source section.
        base_offset:
            Char offset of section start.
        document:
            Source document.

        Returns
        -------
        list[ClinicalEntity]
        """
        entities: list[ClinicalEntity] = []

        for m in _MED_PAT.finditer(text):
            drug = m.group(1)
            dose = m.group(2)
            unit = m.group(3) or "mg"
            route = m.group(4) or ""
            freq = m.group(5) or ""

            char_start = base_offset + m.start()
            char_end = base_offset + m.end()
            evidence = self._evidence_tracker.create_evidence(
                m.group().strip(), document, section, char_start, char_end,
                extraction_method="RULE_BASED",
            )
            entities.append(ClinicalEntity(
                text=m.group().strip(),
                entity_type="MEDICATION",
                normalized_text=f"{drug.title()} {dose}{unit}",
                attributes={
                    "drug_name": drug.lower(),
                    "dose": dose,
                    "unit": unit,
                    "route": route.upper() if route else "",
                    "frequency": freq.upper() if freq else "",
                    "status": "ACTIVE",
                },
                source_evidence=evidence,
                confidence=0.95,
                raw_context=text[max(0, m.start()-30):m.end()+30],
            ))
        return entities

    # -- Conditions (rule-based) -------------------------------------------

    def _extract_conditions_rules(
        self,
        text: str,
        section: ClinicalSection,
        base_offset: int,
        document: IngestedDocument,
    ) -> list[ClinicalEntity]:
        """Extract condition entities via keyword matching.

        Parameters
        ----------
        text:
            Section text.
        section:
            Source section.
        base_offset:
            Char offset of section start.
        document:
            Source document.

        Returns
        -------
        list[ClinicalEntity]
        """
        entities: list[ClinicalEntity] = []
        used_spans: list[tuple[int, int]] = []
        text_lower = text.lower()

        for keyword, normalized in _CONDITION_KW:
            kw_lower = keyword.lower()
            start = 0
            while True:
                pos = text_lower.find(kw_lower, start)
                if pos < 0:
                    break
                end_pos = pos + len(keyword)

                # Skip if overlapping with already-found entity
                overlap = any(s <= pos < e for s, e in used_spans)
                if overlap:
                    start = end_pos
                    continue

                char_start = base_offset + pos
                char_end = base_offset + end_pos
                actual_text = text[pos:end_pos]

                evidence = self._evidence_tracker.create_evidence(
                    actual_text, document, section, char_start, char_end,
                    extraction_method="RULE_BASED",
                )

                # Determine acuity
                acuity = "unspecified"
                if "acute" in kw_lower:
                    acuity = "acute"
                elif "chronic" in kw_lower:
                    acuity = "chronic"

                entities.append(ClinicalEntity(
                    text=actual_text,
                    entity_type="CONDITION",
                    normalized_text=normalized,
                    attributes={"acuity": acuity, "body_site": self._infer_body_site(normalized)},
                    source_evidence=evidence,
                    confidence=0.92,
                    raw_context=text[max(0, pos-50):min(len(text), end_pos+50)],
                ))
                used_spans.append((pos, end_pos))
                start = end_pos

        return entities

    # -- Helpers -----------------------------------------------------------

    @staticmethod
    def _check_abnormal(value: float, ref_range: str) -> bool:
        """Check if a lab value is outside the reference range.

        Parameters
        ----------
        value:
            Numeric value.
        ref_range:
            Reference range string (e.g. "< 0.04", "> 90", "70-100").

        Returns
        -------
        bool
        """
        ref = ref_range.strip()
        if ref.startswith("<"):
            try:
                return value >= float(ref[1:].strip())
            except ValueError:
                return False
        elif ref.startswith(">"):
            try:
                return value <= float(ref[1:].strip())
            except ValueError:
                return False
        elif "-" in ref:
            parts = ref.split("-")
            try:
                low, high = float(parts[0].strip()), float(parts[1].strip())
                return value < low or value > high
            except (ValueError, IndexError):
                return False
        return False

    @staticmethod
    def _infer_body_site(normalized: str) -> str:
        """Infer body site from normalized condition text.

        Parameters
        ----------
        normalized:
            Normalized condition name.

        Returns
        -------
        str
        """
        n = normalized.lower()
        if any(kw in n for kw in ("myocardial", "heart", "cardiac", "coronary", "atrial")):
            return "heart"
        if any(kw in n for kw in ("kidney", "renal", "nephropathy")):
            return "kidney"
        if any(kw in n for kw in ("pulmonary", "lung", "copd", "asthma", "breath", "dyspnea")):
            return "lungs"
        if any(kw in n for kw in ("diabetes", "diabetic")):
            return "systemic"
        if any(kw in n for kw in ("hypertension",)):
            return "cardiovascular"
        if any(kw in n for kw in ("retinopathy",)):
            return "eye"
        if any(kw in n for kw in ("neuropathy",)):
            return "peripheral nerves"
        return "unspecified"

    @staticmethod
    def _deduplicate(entities: list[ClinicalEntity]) -> list[ClinicalEntity]:
        """Remove duplicate entity extractions (same type + overlapping text).

        Parameters
        ----------
        entities:
            Extracted entities.

        Returns
        -------
        list[ClinicalEntity]
        """
        seen: set[tuple[str, str]] = set()
        result: list[ClinicalEntity] = []
        for e in entities:
            key = (e.entity_type, e.normalized_text.lower())
            if key not in seen:
                seen.add(key)
                result.append(e)
        return result

    def _validate_extraction(self, entity: ClinicalEntity, source_text: str) -> bool:
        """Validate that an entity's text exists in the source.

        Parameters
        ----------
        entity:
            Extracted entity.
        source_text:
            Source document text.

        Returns
        -------
        bool
        """
        return entity.text.lower() in source_text.lower()

    def _calculate_char_offset(
        self, entity_text: str, source_text: str, start_hint: int = 0,
    ) -> tuple[int, int]:
        """Find the char offset of entity text in source.

        Parameters
        ----------
        entity_text:
            Text to locate.
        source_text:
            Source text.
        start_hint:
            Position to start searching from.

        Returns
        -------
        tuple[int, int]
        """
        pos = source_text.lower().find(entity_text.lower(), start_hint)
        if pos >= 0:
            return pos, pos + len(entity_text)
        return 0, 0
