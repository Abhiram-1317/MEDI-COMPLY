import asyncio
import sys
from pathlib import Path

from medi_comply.system import MediComplySystem

ROOT = Path(__file__).resolve().parent
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.append(str(TESTS_DIR))

from test_orchestrator import MockLLMClient  # type: ignore  # noqa: E402

PULMONARY_NOTE = """
CC: Worsening dyspnea
HPI: 55yo F with COPD, 3-day worsening SOB, productive cough.
Denies chest pain, hemoptysis, leg swelling. Former smoker.
PE: SpO2 88%, bilateral wheezing.
Assessment: COPD acute exacerbation. Former tobacco use.
"""


async def main() -> None:
    system = MediComplySystem(llm_client=MockLLMClient())
    await system.initialize()
    try:
        result = await system.process(
            clinical_note=PULMONARY_NOTE,
            patient_context={"age": 55, "gender": "female", "encounter_type": "INPATIENT"},
        )
        print("status:", result.status)
        print("warnings:", result.warnings)
        if result.coding_result:
            print("codes:", [dec.code for dec in result.coding_result.diagnosis_codes])
        else:
            print("No coding result")
    finally:
        await system.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
