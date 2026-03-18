"""
MEDI-COMPLY — Prompt templates for the Medical Coding Agent.

These templates enforce strict constraint-following. The LLM is 
commanded never to hallucinate codes, but only to select from 
the verified Candidate Codes list injected into the prompt.
"""

SYSTEM_PROMPT = """
You are an AAPC-certified medical coder (CPC, CCS) with 15 years of experience.
You assign ICD-10-CM diagnosis codes and CPT procedure codes to clinical encounters
based on clinical documentation and Official Coding Guidelines.

ABSOLUTE RULES — VIOLATIONS WILL CAUSE SYSTEM FAILURE:
1. You may ONLY select codes from the CANDIDATE LIST provided. 
   Do NOT generate any code not in the candidate list.
2. Every code you assign MUST be supported by specific text in the clinical documentation.
3. You MUST cite the exact source text that supports each code.
4. Code to the HIGHEST SPECIFICITY supported by documentation.
5. Follow Official Coding Guidelines for sequencing.
6. For OUTPATIENT encounters: Do NOT code "suspected", "probable", "rule out" 
   conditions. Code symptoms only. (OCG Section IV.D)
7. For INPATIENT encounters: Code "suspected", "probable", "rule out" conditions 
   AS IF they are confirmed. (OCG Section II.H)
8. When a combination code exists (e.g., DM + complication), use the combination 
   code instead of separate codes.
9. Follow "Use Additional Code" and "Code First" instructions.
10. If you are uncertain about ANY code (confidence < 85%), you MUST flag it 
    for human review.

OUTPUT FORMAT: You must respond with valid JSON matching the provided schema.
No markdown, no explanation outside the JSON structure.
"""


CODE_SELECTION_PROMPT = """
CLINICAL ENCOUNTER:
{encounter_summary}

ENCOUNTER TYPE: {encounter_type}
PATIENT: {age}-year-old {gender}

CONDITION TO CODE:
Entity: {condition_text}
Assertion: {assertion}  
Acuity: {acuity}
Section found in: {section}
Surrounding text: "{surrounding_text}"

CANDIDATE CODES (you MUST select from these only):
{candidates_formatted}

RELEVANT CODING GUIDELINES:
{guidelines_formatted}

EXCLUDES WARNINGS:
{excludes_warnings}

USE ADDITIONAL INSTRUCTIONS:
{use_additional_instructions}

CODE FIRST INSTRUCTIONS:
{code_first_instructions}

ALREADY ASSIGNED CODES (for this encounter):
{already_assigned_codes}

TASK: Select the BEST code from the candidate list for this condition.

Respond with this exact JSON structure:
{{
  "selected_code": "the code you select",
  "reasoning_steps": [
    {{
      "step_number": 1,
      "action": "what you did",
      "detail": "why",
      "evidence_ref": "quoted text from documentation",
      "guideline_ref": "guideline ID if applicable"
    }}
  ],
  "alternatives_considered": [
    {{
      "code": "alternative code",
      "reason_rejected": "why not selected"
    }}
  ],
  "confidence_score": 0.95,
  "confidence_factors": [
    {{
      "factor": "EVIDENCE_STRENGTH",
      "impact": "POSITIVE",
      "detail": "explanation"
    }}
  ],
  "requires_human_review": false,
  "review_reason": null,
  "additional_codes_needed": ["N18.32"],
  "additional_codes_reason": "Per Use Additional instruction at E11.22",
  "combination_code_note": "Combination code for T2DM + diabetic CKD"
}}
"""


SEQUENCING_PROMPT = """
You are sequencing ICD-10-CM diagnosis codes for a {encounter_type} encounter.

PATIENT: {age}-year-old {gender}
CHIEF COMPLAINT: {chief_complaint}
ENCOUNTER REASON: {encounter_reason}

ASSIGNED DIAGNOSIS CODES:
{codes_with_descriptions}

RELEVANT SEQUENCING GUIDELINES:
- OCG Section II.A (Inpatient): "The principal diagnosis is the condition established 
  after study to be chiefly responsible for occasioning the admission of the patient."
- OCG Section IV.A (Outpatient): "List first the ICD-10-CM code for the diagnosis, 
  condition, problem, or other reason for encounter/visit shown in the medical record 
  to be chiefly responsible for the services provided."
- OCG I.C.4.a: For diabetes, sequence based on reason for encounter.
- OCG I.C.9.e: For AMI, the AMI code should be sequenced as primary when the MI 
  is the reason for the encounter.

TASK: Determine the correct sequencing order (primary diagnosis first).

Respond with:
{{
  "sequenced_codes": [
    {{
      "code": "I21.4",
      "position": "PRIMARY",
      "sequence_number": 1,
      "sequencing_rationale": "Acute NSTEMI is the reason for admission per OCG I.C.9.e"
    }},
    {{
      "code": "E11.22", 
      "position": "SECONDARY",
      "sequence_number": 2,
      "sequencing_rationale": "Active comorbidity affecting care"
    }}
  ],
  "sequencing_guidelines_applied": ["OCG-II-A", "OCG-I-C-9-e"],
  "confidence_score": 0.95
}}
"""


RETRY_PROMPT = """
Your previous coding attempt was REJECTED by the compliance validator.

PREVIOUS ATTEMPT:
{previous_codes}

COMPLIANCE FEEDBACK:
{compliance_feedback}

SPECIFIC ISSUES:
{issues_list}

Please RE-EVALUATE your coding decisions considering this feedback.
You still must select from the SAME candidate list.
Address each issue specifically in your reasoning.

{original_prompt_context}
"""


CPT_SELECTION_PROMPT = """
PROCEDURE TO CODE:
Procedure: {procedure_text}
Body site: {body_site}
Laterality: {laterality}

CANDIDATE CPT CODES (select from these only):
{candidates_formatted}

NCCI EDIT WARNINGS (conflicts with other procedures):
{ncci_warnings}

MEDICAL NECESSITY:
{medical_necessity_info}

MODIFIER SUGGESTIONS:
{modifier_suggestions}

ALREADY ASSIGNED CPT CODES:
{already_assigned_cpt}

TASK: Select the best CPT code and applicable modifiers.

Respond with:
{{
  "selected_code": "93306",
  "selected_modifiers": ["26"],
  "reasoning_steps": [
      {{ "step_number": 1, "action": "Selected CPT code", "detail": "Best match for Echocardiogram" }}
  ],
  "alternatives_considered": [],
  "confidence_score": 0.92,
  "ncci_conflicts_resolved": "No conflicts with current code set",
  "medical_necessity_confirmed": true,
  "requires_human_review": false
}}
"""


CLINICAL_SUMMARY_PROMPT = """
Given the following coding decisions for a clinical encounter, generate a 
concise human-readable coding summary (2-3 sentences).

ENCOUNTER: {encounter_type} | {age}yo {gender}
DIAGNOSIS CODES: {dx_codes_formatted}
PROCEDURE CODES: {cpt_codes_formatted}

Generate a summary like:
"62-year-old male admitted for acute NSTEMI (I21.4). Comorbidities coded include 
type 2 diabetes with diabetic CKD (E11.22) with CKD stage 3b (N18.32), and 
essential hypertension (I10). [4 ICD-10 codes assigned, confidence 96%]"
"""
