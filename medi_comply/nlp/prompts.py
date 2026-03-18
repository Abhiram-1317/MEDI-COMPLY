"""
MEDI-COMPLY — LLM prompts for clinical extraction.

Contains structured prompts for condition, procedure, and summary extraction.
These are used by ClinicalNEREngine when LLM mode is enabled.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Condition extraction prompt
# ---------------------------------------------------------------------------

CONDITION_EXTRACTION_PROMPT = """You are a clinical NLP specialist. Extract all medical conditions, \
diagnoses, symptoms, and clinical findings from the following clinical text.

For each entity, provide:
- text: exact text as it appears in the document
- normalized_text: standardized medical term
- acuity: acute, chronic, subacute, or unspecified
- severity: mild, moderate, severe, or unspecified
- body_site: affected body part/organ
- start_char: character offset where entity starts in the input text
- end_char: character offset where entity ends

RULES:
1. Only extract entities that ACTUALLY APPEAR in the text
2. Do NOT infer conditions not mentioned
3. Include symptoms AND confirmed diagnoses
4. Capture severity and acuity when explicitly stated
5. Output valid JSON array

Example:
Input: 'Patient has severe COPD with acute exacerbation'
Output: [
  {{
    "text": "severe COPD with acute exacerbation",
    "normalized_text": "Chronic Obstructive Pulmonary Disease with Acute Exacerbation",
    "acuity": "acute",
    "severity": "severe",
    "body_site": "lungs",
    "start_char": 16,
    "end_char": 52
  }}
]

Clinical text:
{clinical_text}

Extract and return a valid JSON array:"""


# ---------------------------------------------------------------------------
# Procedure extraction prompt
# ---------------------------------------------------------------------------

PROCEDURE_EXTRACTION_PROMPT = """You are a clinical NLP specialist. Extract all medical procedures, \
surgeries, diagnostic tests, and therapeutic procedures from the following clinical text.

For each procedure, provide:
- text: exact text as it appears in the document
- normalized_text: standardized procedure name
- status: PLANNED, COMPLETED, or CANCELLED
- body_site: target body part/organ
- start_char: character offset where entity starts in the input text
- end_char: character offset where entity ends

RULES:
1. Only extract procedures that ACTUALLY APPEAR in the text
2. Include both planned and completed procedures
3. Include diagnostic tests (labs, imaging) if mentioned as ordered procedures
4. Output valid JSON array

Clinical text:
{clinical_text}

Extract and return a valid JSON array:"""


# ---------------------------------------------------------------------------
# Clinical summary prompt
# ---------------------------------------------------------------------------

CLINICAL_SUMMARY_PROMPT = """Generate a concise one-line clinical summary from the following \
structured clinical data. The summary should be in the format:
"[Age]-year-old [gender] presenting with [primary condition], with [relevant history], \
[relevant findings]."

Patient age: {age}
Patient gender: {gender}
Conditions: {conditions}
Medications: {medications}
Key lab findings: {labs}
Vitals: {vitals}

Generate a single concise clinical summary sentence:"""
