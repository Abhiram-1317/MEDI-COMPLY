"""
MEDI-COMPLY — Tests for the Medical Coding Agent

Verifies coding schemas, combinations, constraints, assertion handling (POSSIBLE vs ABSENT)
and deterministic sequencing paths.
"""

import pytest
from datetime import datetime
from medi_comply.core.config import Settings
from medi_comply.core.message_models import AgentMessage
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.coding_result import CodingResult
from medi_comply.schemas.retrieval import (
    CodeRetrievalContext,
    ConditionCodeCandidates,
    ProcedureCodeCandidates,
    RankedCodeCandidate
)
from medi_comply.agents.medical_coding_agent import MedicalCodingAgent


# ----- MOCKS -----

class MockLLMClient:
    """Returns deterministic configurations enforcing hackathon unit test paths."""
    
    def __init__(self):
         self.MOCK_RESPONSES = {
              "NSTEMI_coding": {
                  "selected_code": "I21.4",
                  "reasoning_steps": [{"step_number": 1, "action": "Mock LLM Select", "detail": "Match I21.4"}],
                  "confidence_score": 0.96,
                  "requires_human_review": False
              },
              "T2DM_nephropathy_coding": {
                  "selected_code": "E11.22",
                  "reasoning_steps": [{"step_number": 1, "action": "Mock LLM Select", "detail": "Comb"}],
                  "confidence_score": 0.95,
                  "requires_human_review": False
              },
              "CPT_Echo": {
                  "selected_code": "93306",
                  "confidence_score": 0.92,
                  "requires_human_review": False
              }
         }

    async def handle_prompt(self, type_flag, obj):
         desc = obj.normalized_text.lower()
         if "nstemi" in desc:
              return self.MOCK_RESPONSES["NSTEMI_coding"]
         elif "diabetes" in desc or "ckd" in desc or "nephropathy" in desc:
              return self.MOCK_RESPONSES["T2DM_nephropathy_coding"]
         elif "echocardiogram" in desc:
              return self.MOCK_RESPONSES["CPT_Echo"]
         return None


@pytest.fixture
def km():
    km_inst = KnowledgeManager()
    km_inst.initialize()
    return km_inst

@pytest.fixture
def config():
    return Settings()

@pytest.fixture
def agent(km, config):
    return MedicalCodingAgent(knowledge_manager=km, config=config, llm_client=MockLLMClient())

@pytest.fixture
def sample_scr():
    return {
        "scr_id": "test_scr",
        "patient_context": {
            "encounter_type": "INPATIENT",
            "chief_complaint": "Chest pain",
            "age": 62,
            "gender": "MALE"
        },
        "conditions": [],
        "procedures": []
    }

@pytest.fixture
def mock_retrieval_context():
    return {
        "context_id": "ctx-1",
        "scr_id": "test_scr",
        "created_at": datetime.now(),
        "condition_candidates": [
            {
                "condition_entity_id": "c1",
                "condition_text": "Acute NSTEMI",
                "normalized_text": "acute nstemi",
                "assertion": "PRESENT",
                "candidates": [
                    {"code": "I21.4", "code_type": "ICD10", "description": "NSTEMI", "strategy": "Direct Map", "score": 1.0}
                ]
            },
            {
                "condition_entity_id": "c2",
                "condition_text": "Type 2 diabetes with nephropathy",
                "normalized_text": "type 2 diabetes with nephropathy",
                "assertion": "PRESENT",
                "candidates": [
                    {"code": "E11.22", "code_type": "ICD10", "description": "Type 2 diabetes mellitus with diabetic chronic kidney disease", "strategy": "Graph", "score": 0.95}
                ]
            }
        ],
        "procedure_candidates": [
            {
                 "procedure_entity_id": "p1",
                 "procedure_text": "echocardiogram",
                 "normalized_text": "echocardiogram",
                 "assertion": "PRESENT",
                 "candidates": [{"code": "93306", "code_type": "CPT", "description": "Echo 2d", "strategy": "Keyword Map", "score": 0.9}]
            }
        ],
        "excludes_warnings": [],
        "ncci_warnings": [],
        "retrieval_summary": {}
    }


# ----- TEST CASES -----

@pytest.mark.asyncio
async def test_full_scenario_execution(agent, sample_scr, mock_retrieval_context):
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": mock_retrieval_context})
    res = await agent.process(msg)
    
    assert res.status == "SUCCESS"
    
    payload = res.payload
    assert payload["coding_result_id"] is not None
    assert payload["patient_age"] == 62
    
    dx = payload["diagnosis_codes"]
    cpt = payload["procedure_codes"]
    
    assert len(dx) >= 2 # I21.4 and E11.22
    assert len(cpt) == 1 # 93306
    
    # Check sequencing (I21.4 should be primary based on chief complaint)
    assert dx[0]["code"] == "I21.4"
    assert dx[0]["sequence_position"] == "PRIMARY"
    
    # Check combinations
    assert payload["has_combination_codes"] is True
    assert any(c["code"] == "E11.22" for c in dx)


@pytest.mark.asyncio
async def test_detect_dm_ckd_combination(agent, sample_scr):
    # Pass just Diabetes + CKD separately
    ctx = {
       "context_id": "ctx-x", "scr_id": "scr", "created_at": datetime.now(),
       "condition_candidates": [
           {"condition_entity_id": "c1", "condition_text": "Diabetes", "normalized_text": "diabetes", "assertion": "PRESENT", "candidates": [{"code": "E11.9", "code_type": "ICD10", "description": "DM", "strategy": "mock", "score": 1.0}]},
           {"condition_entity_id": "c2", "condition_text": "CKD stage 3", "normalized_text": "ckd", "assertion": "PRESENT", "candidates": [{"code": "N18.30", "code_type": "ICD10", "description": "CKD", "strategy": "mock", "score": 1.0}]}
       ],
       "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
    }
    
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
    res = await agent.process(msg)
    
    dx = res.payload["diagnosis_codes"]
    
    # E11.22 should be dynamically combined and appended via the Handler
    assert any(c["code"] == "E11.22" for c in dx)
    assert res.payload["has_combination_codes"] is True


@pytest.mark.asyncio
async def test_htn_heart_combination(agent, sample_scr):
     ctx = {
        "context_id": "ctx-y", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {"condition_entity_id": "c1", "condition_text": "Hypertension", "normalized_text": "hypertension", "assertion": "PRESENT", "candidates": [{"code": "I10", "code_type": "ICD10", "description": "HTN", "strategy": "mock", "score": 1.0}]},
            {"condition_entity_id": "c2", "condition_text": "Heart failure", "normalized_text": "heart failure", "assertion": "PRESENT", "candidates": [{"code": "I50.9", "code_type": "ICD10", "description": "HF", "strategy": "mock", "score": 1.0}]}
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
     }
     
     msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
     res = await agent.process(msg)
     
     dx = res.payload["diagnosis_codes"]
     assert any(c["code"] == "I11.0" for c in dx) # Hypertensive heart with heart failure


@pytest.mark.asyncio
async def test_no_false_combination(agent, sample_scr):
     ctx = {
        "context_id": "ctx-z", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {"condition_entity_id": "c1", "condition_text": "Hypertension", "normalized_text": "hypertension", "assertion": "PRESENT", "candidates": [{"code": "I10", "code_type": "ICD10", "description": "HTN", "strategy": "mock", "score": 1.0}]},
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
     }
     msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
     res = await agent.process(msg)
     assert res.payload["has_combination_codes"] is False


@pytest.mark.asyncio
async def test_negated_not_coded(agent, sample_scr):
    ctx = {
        "context_id": "ctx-2", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {
                 "condition_entity_id": "c1",
                 "condition_text": "Fever",
                 "normalized_text": "fever",
                 "assertion": "ABSENT",
                 "candidates": [{"code": "R50.9", "code_type": "ICD10", "description": "Fever", "strategy": "k", "score": 1.0}]
            }
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
    }
    
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
    res = await agent.process(msg)
    
    assert len(res.payload["diagnosis_codes"]) == 0
    assert "Fever" not in str(res.payload)


@pytest.mark.asyncio
async def test_possible_inpatient_coded(agent, sample_scr):
    # sample_scr is INPATIENT
    ctx = {
        "context_id": "ctx-3", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {
                 "condition_entity_id": "c1",
                 "condition_text": "Pneumonia",
                 "normalized_text": "pneumonia",
                 "assertion": "POSSIBLE",
                 "candidates": [{"code": "J18.9", "code_type": "ICD10", "description": "Pneumonia", "strategy": "k", "score": 1.0}]
            }
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
    }
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
    res = await agent.process(msg)
    assert len(res.payload["diagnosis_codes"]) == 1 # Inpatient -> code POSSIBLE as actual


@pytest.mark.asyncio
async def test_possible_outpatient_symptoms(agent, sample_scr):
    sample_scr["patient_context"]["encounter_type"] = "OUTPATIENT"
    # Same context as before
    ctx = {
        "context_id": "ctx-4", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {
                 "condition_entity_id": "c1",
                 "condition_text": "Pneumonia",
                 "normalized_text": "pneumonia",
                 "assertion": "POSSIBLE",
                 "candidates": [{"code": "J18.9", "code_type": "ICD10", "description": "Pneumonia", "strategy": "k", "score": 1.0}]
            }
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
    }
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
    res = await agent.process(msg)
    
    # Outpatient -> DONT code possible. So it's removed.
    assert len(res.payload["diagnosis_codes"]) == 0


@pytest.mark.asyncio
async def test_fallback_without_llm(km, config, sample_scr, mock_retrieval_context):
    no_llm_agent = MedicalCodingAgent(knowledge_manager=km, config=config, llm_client=None)
    msg = AgentMessage(from_agent="sys", to_agent=no_llm_agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": mock_retrieval_context})
    res = await no_llm_agent.process(msg)
    assert res.status == "SUCCESS"
    assert len(res.payload["diagnosis_codes"]) > 0
    # Uses fallback engine correctly and outputs results
    assert res.payload["overall_confidence"] > 0.0


@pytest.mark.asyncio
async def test_retry_with_feedback(agent, sample_scr, mock_retrieval_context):
     msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="RETRY", payload={"scr": sample_scr, "context": mock_retrieval_context})
     res = await agent.process_with_retry(msg, compliance_feedback=["Missing modifier"], attempt=2)
     assert res.status == "SUCCESS"
     assert res.payload["attempt_number"] == 2
     assert "Missing modifier" in res.payload["previous_feedback"]


@pytest.mark.asyncio
async def test_max_retries_then_escalate(agent, sample_scr, mock_retrieval_context):
     msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="RETRY", payload={"scr": sample_scr, "context": mock_retrieval_context})
     res = await agent.process_with_retry(msg, compliance_feedback=["Still broken"], attempt=4) # max config is 3
     assert res.status == "FAILURE"
     assert "Max retries exceeded" in res.payload["error"]
     assert agent.state == "ESCALATED" # transitioned state


# Extra edge-case validations inside Python
@pytest.mark.asyncio
async def test_no_conditions_in_scr(agent, sample_scr):
    # empty ctx
    ctx = {
        "context_id": "ctx-x", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
    }
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
    res = await agent.process(msg)
    assert res.status == "SUCCESS"
    assert len(res.payload["diagnosis_codes"]) == 0
    assert len(res.payload["procedure_codes"]) == 0


@pytest.mark.asyncio
async def test_single_condition(agent, sample_scr):
     ctx = {
        "context_id": "ctx-x", "scr_id": "scr", "created_at": datetime.now(),
        "condition_candidates": [
            {"condition_entity_id": "c1", "condition_text": "Hypertension", "normalized_text": "hypertension", "assertion": "PRESENT", "candidates": [{"code": "I10", "code_type": "ICD10", "description": "HTN", "strategy": "mock", "score": 1.0}]},
        ],
        "procedure_candidates": [], "excludes_warnings": [], "ncci_warnings": [], "retrieval_summary": {}
     }
     msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload={"scr": sample_scr, "context": ctx})
     res = await agent.process(msg)
     dx = res.payload["diagnosis_codes"]
     assert len(dx) == 1
     assert dx[0]["sequence_position"] == "PRIMARY" # Auto sets to primary if lone code
