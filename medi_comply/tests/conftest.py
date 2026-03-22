"""Shared pytest fixtures and sample clinical notes for MEDI-COMPLY tests."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from typing import Any, Optional

import pytest
import pytest_asyncio


# ── async event loop ──
@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Mock LLM Client ──
class GoldenMockLLMClient:
    """Deterministic mock LLM wired for golden scenarios."""

    SCENARIO_RESPONSES: dict[str, dict[str, Any]] = {}

    def __init__(self, scenario_overrides: Optional[dict[str, dict[str, Any]]] = None):
        self.call_count = 0
        self.last_prompt: Optional[str] = None
        if scenario_overrides:
            self.SCENARIO_RESPONSES.update(scenario_overrides)

    async def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, str]:
        self.call_count += 1
        self.last_prompt = messages[-1]["content"] if messages else ""

        prompt_text = (self.last_prompt or "").lower()
        for response in self.SCENARIO_RESPONSES.values():
            triggers = response.get("trigger_keywords", [])
            if any(keyword in prompt_text for keyword in triggers):
                return {"content": json.dumps(response["llm_response"])}

        return {
            "content": json.dumps(
                {
                    "selected_code": "FALLBACK",
                    "reasoning_steps": [
                        {
                            "step_number": 1,
                            "action": "Fallback",
                            "detail": "No matching scenario",
                        }
                    ],
                    "confidence_score": 0.70,
                    "alternatives_considered": [],
                    "requires_human_review": True,
                }
            )
        }


@pytest_asyncio.fixture(scope="session")
async def initialized_system():
    """Session-scoped initialized MediComplySystem."""
    from medi_comply.system import MediComplySystem

    system = MediComplySystem(
        llm_client=GoldenMockLLMClient(),
        db_path=os.path.join(tempfile.mkdtemp(), "test_audit.db"),
    )
    await system.initialize()
    yield system
    await system.shutdown()


@pytest.fixture
def mock_llm():
    """Per-test mock LLM client."""
    return GoldenMockLLMClient()


@pytest.fixture
def temp_audit_db():
    """Temporary audit database for tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        path = handle.name
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


SAMPLE_NOTES = {
    "cardiac_nstemi": """
CHIEF COMPLAINT: Chest pain, shortness of breath

HISTORY OF PRESENT ILLNESS:
62-year-old male presents with substernal chest pain radiating to left arm,
onset 2 hours ago. History of type 2 diabetes mellitus with diabetic
nephropathy, on metformin 1000mg BID and lisinopril 20mg daily.
GFR 38 mL/min. Denies fever or cough.

PHYSICAL EXAMINATION:
Vitals: BP 160/95, HR 102, SpO2 94% on room air.
Cardiac: Tachycardic, no murmurs. Lungs: Bilateral crackles at bases.

LABS:
Troponin: 0.8 ng/mL (elevated)
BNP: 450 pg/mL

ASSESSMENT AND PLAN:
1. Acute NSTEMI — troponin elevated at 0.8 ng/mL
2. Type 2 diabetes with diabetic chronic kidney disease
3. CKD stage 3b
4. Hypertension, uncontrolled
Plan: Admit cardiac ICU. Heparin drip. Hold metformin.
""".strip(),
    "pulmonary_copd": """
CC: Worsening dyspnea

HPI: 55-year-old female with h/o COPD presents with 3-day history of
worsening SOB and productive cough with yellow sputum. Denies chest pain,
hemoptysis, or leg swelling. Former smoker, quit 5 years ago.
On tiotropium 18mcg daily and albuterol PRN.

PE: BP 138/82, HR 88, RR 24, SpO2 88% on RA
Lungs: Diffuse bilateral wheezing and rhonchi. No crackles.

Assessment:
1. COPD acute exacerbation
2. Former tobacco use disorder
Plan: Nebulizer treatments, steroids.
""".strip(),
    "simple_dm_htn": """
CHIEF COMPLAINT: Follow-up diabetes and hypertension

HPI: 48-year-old female returns for routine follow-up of type 2 diabetes
and essential hypertension. Both well controlled. Metformin 500mg BID,
lisinopril 10mg daily. No complaints.

Vitals: BP 128/78, HR 72
Labs: HbA1c 6.8%, BMP normal.

Assessment:
1. Type 2 diabetes mellitus without complications, well controlled
2. Essential hypertension, controlled
Plan: Continue meds. Follow up 3 months.
""".strip(),
    "messy_abbreviated": """
pt 45yo M PMH HTN T2DM CAD s/p PCI 2019 c/o dizziness x2d
denies CP SOB syncope n/v
BP 142/88 HR 76 SpO2 98% A&Ox3 RRR CTAB
A: dizziness likely orthostatic d/t antihypertensives. Adjust meds.
""".strip(),
    "empty_minimal": """
Patient seen today. No significant findings. Follow up as needed.
""".strip(),
    "chf_exacerbation": """
CC: Worsening leg swelling and dyspnea

HPI: 70-year-old male with history of systolic heart failure (EF 30%)
presents with 1-week progressive bilateral lower extremity edema and
worsening DOE. Weight up 8 lbs. He reports 3-pillow orthopnea.
PMH also significant for atrial fibrillation on warfarin and CKD stage 4.

PE: BP 98/62, HR 110 irregularly irregular, SpO2 91% on 2L NC
JVD to angle of jaw. Bilateral crackles halfway up. 3+ pitting edema BLE.

Labs: BNP 2400, Cr 2.8 (baseline 2.2), K 5.1

Assessment:
1. Acute decompensated systolic heart failure
2. Atrial fibrillation with RVR
3. Acute on chronic kidney disease (stage 4, baseline stage 3)
4. Hyperkalemia
Plan: IV furosemide, rate control, hold ACEi, kayexalate.
""".strip(),
    "pneumonia_sepsis": """
CC: Fever and cough x 5 days

HPI: 68-year-old female presents with 5-day history of productive cough,
fever to 102.4°F, and progressive dyspnea. Found to be hypotensive in ED.
PMH: COPD, type 2 diabetes.

PE: T 39.1°C, BP 82/54, HR 118, RR 28, SpO2 86% on 4L NC
Appears toxic. Lungs: Right lower lobe crackles with egophony.
Decreased breath sounds right base.

Labs: WBC 18.4, Lactate 4.2, Procalcitonin 8.5
CXR: Right lower lobe consolidation with small effusion.

Assessment:
1. Community-acquired pneumonia, right lower lobe
2. Sepsis secondary to pneumonia
3. Acute hypoxic respiratory failure
4. COPD (underlying)
5. Type 2 diabetes
Plan: Broad spectrum antibiotics, IV fluids, vasopressors if needed,
supplemental oxygen, possibly BiPAP.
""".strip(),
    "fracture_injury": """
CC: Left hip pain after fall

HPI: 82-year-old female brought in after mechanical fall at home,
landing on left hip. Unable to bear weight. PMH: Osteoporosis, HTN.
No LOC, no head injury.

PE: BP 148/82, HR 88
Left hip: Shortened and externally rotated. TTP over greater trochanter.
Neurovascularly intact distally.

Imaging: XR left hip shows displaced femoral neck fracture.

Assessment:
1. Left displaced femoral neck fracture, initial encounter
2. Mechanical fall
3. Osteoporosis
4. Hypertension
Plan: Ortho consult for surgical repair. DVT prophylaxis. Pain management.
""".strip(),
}
