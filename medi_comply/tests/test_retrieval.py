"""
MEDI-COMPLY — Retrieval Tests (Task 4)

25+ tests for:
- Direct Mapping (ClinicalCodeMapper)
- Vector, Keyword, Hierarchy Strategies
- RRF Fusion
- Context Assembler (Excludes, NCCI, Guidelines)
- Full RAG Agent
"""

import pytest
import asyncio
from datetime import datetime, timezone
import time

from medi_comply.core.config import Settings
from medi_comply.core.agent_base import AgentMessage
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.agents.clinical_code_mapper import ClinicalCodeMapper
from medi_comply.agents.retrieval_strategies import (
    VectorRetrievalStrategy, KeywordRetrievalStrategy,
    DirectMapStrategy, HierarchyTraversalStrategy, RetrievalFusion
)
from medi_comply.agents.context_assembler import ContextAssembler
from medi_comply.agents.knowledge_retrieval_agent import KnowledgeRetrievalAgent
from medi_comply.nlp.scr_builder import (
    StructuredClinicalRepresentation, ConditionEntry, ProcedureEntry
)
from medi_comply.schemas.retrieval import ConditionCodeCandidates, ProcedureCodeCandidates, RankedCodeCandidate
from dataclasses import asdict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def km():
    """Shared knowledge manager with seed data."""
    manager = KnowledgeManager()
    manager.initialize()
    return manager

@pytest.fixture
def mapper():
    return ClinicalCodeMapper()

@pytest.fixture
def assembler():
    return ContextAssembler()

@pytest.fixture
def agent(km):
    settings = Settings(env="test")
    return KnowledgeRetrievalAgent(km, settings)

@pytest.fixture
def sample_cardiac_scr():
    return StructuredClinicalRepresentation(
        scr_id="scr_cardiac",
        document_id="doc_1",
        patient_context={"age": 62, "gender": "male", "encounter_type": "INPATIENT"},
        conditions=[
            ConditionEntry(entity_id="c1", text="acute NSTEMI", normalized_text="acute nstemi", assertion="PRESENT"),
            ConditionEntry(entity_id="c2", text="T2DM with CKD", normalized_text="type 2 diabetes with diabetic chronic kidney disease", assertion="PRESENT"),
            ConditionEntry(entity_id="c3", text="CKD stage 3b", normalized_text="ckd stage 3b", assertion="PRESENT"),
            ConditionEntry(entity_id="c4", text="HTN", normalized_text="hypertension", assertion="PRESENT"),
            ConditionEntry(entity_id="c5", text="fever", normalized_text="fever", assertion="ABSENT")
        ],
        procedures=[],
        medications=[],
        vitals=None,
        lab_results=[],
        sections_found=[],
        clinical_summary="Patient admitted for NSTEMI."
    )

@pytest.fixture
def sample_pulmonary_scr():
    return StructuredClinicalRepresentation(
        scr_id="scr_pulm",
        document_id="doc_2",
        patient_context={"age": 55, "gender": "female", "encounter_type": "OUTPATIENT"},
        conditions=[
            ConditionEntry(entity_id="p1", text="COPD exacerbation", normalized_text="copd acute exacerbation", assertion="PRESENT"),
            ConditionEntry(entity_id="p2", text="possible pneumonia", normalized_text="pneumonia", assertion="POSSIBLE")
        ],
        procedures=[
            ProcedureEntry(entity_id="pr1", text="left knee x-ray", normalized_text="x-ray left knee")
        ],
        medications=[],
        vitals=None,
        lab_results=[],
        sections_found=[],
        clinical_summary="COPD flare."
    )


# ---------------------------------------------------------------------------
# Direct Mapping Tests (8)
# ---------------------------------------------------------------------------

def test_direct_map_nstemi(mapper):
    res = mapper.lookup_condition("acute NSTEMI")
    assert any(c == "I21.4" for c, conf in res)

def test_direct_map_diabetes(mapper):
    res = mapper.lookup_condition("type 2 diabetes")
    assert any(c == "E11.9" for c, conf in res)

def test_direct_map_ckd_stage3b(mapper):
    res = mapper.lookup_condition("CKD stage 3b")
    assert any(c == "N18.32" for c, conf in res)

def test_direct_map_hypertension(mapper):
    res = mapper.lookup_condition("hypertension")
    assert any(c == "I10" for c, conf in res)

def test_direct_map_unknown(mapper):
    res = mapper.lookup_condition("rare tropical disease XYZ")
    assert len(res) == 0

def test_direct_map_fuzzy(mapper):
    res = mapper.lookup_condition("typ 2 diabetis")
    assert any(c == "E11.9" for c, conf in res)

def test_direct_map_cpt_echo(mapper):
    res = mapper.lookup_procedure("echocardiogram")
    assert any(c == "93306" for c, conf in res)

def test_direct_map_cpt_troponin(mapper):
    res = mapper.lookup_procedure("troponin")
    assert any(c == "84484" for c, conf in res)


# ---------------------------------------------------------------------------
# Vector Search tests (3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_vector_search_cardiac(km):
    strat = VectorRetrievalStrategy(km.vector_store)
    res = await strat.retrieve("chest pain with elevated troponin", "ICD10", 15)
    assert isinstance(res, list)

@pytest.mark.asyncio
async def test_vector_search_renal(km):
    strat = VectorRetrievalStrategy(km.vector_store)
    res = await strat.retrieve("kidney disease with decreased GFR", "ICD10", 15)
    assert isinstance(res, list)

@pytest.mark.asyncio
async def test_vector_search_pulmonary(km):
    strat = VectorRetrievalStrategy(km.vector_store)
    res = await strat.retrieve("worsening breathing difficulty with wheezing", "ICD10", 15)
    assert isinstance(res, list)


# ---------------------------------------------------------------------------
# Fusion Tests (3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fusion_combines_strategies(km, mapper):
    fusion = RetrievalFusion([
        DirectMapStrategy(mapper, km),
        KeywordRetrievalStrategy(km)
    ])
    res = await fusion.retrieve("type 2 diabetes", "ICD10", 10)
    assert len(res) > 0
    # verify direct_map_score and keyword_score exist collectively
    has_dm = any(r.direct_map_score is not None for r in res)
    has_kw = any(r.keyword_score is not None for r in res)
    assert has_dm or has_kw
    
@pytest.mark.asyncio
async def test_fusion_deduplicates(km, mapper):
    fusion = RetrievalFusion([
        DirectMapStrategy(mapper, km),
        KeywordRetrievalStrategy(km)
    ])
    res = await fusion.retrieve("acute NSTEMI", "ICD10", 10)
    codes = [r.code for r in res]
    assert len(codes) == len(set(codes)), "Duplicate codes found in fusion results"

@pytest.mark.asyncio
async def test_fusion_top_k(km, mapper):
    fusion = RetrievalFusion([KeywordRetrievalStrategy(km)])
    res = await fusion.retrieve("testing", "ICD10", 2)
    assert len(res) <= 2


# ---------------------------------------------------------------------------
# Context Assembly Tests (9)
# ---------------------------------------------------------------------------

def test_excludes_matrix_catches_conflict(assembler, km):
    c1 = ConditionCodeCandidates(condition_entity_id="1", condition_text="t1", normalized_text="t1", assertion="PRESENT", candidates=[
        RankedCodeCandidate(code="E10.9", description="Type 1", code_type="ICD10")
    ])
    c2 = ConditionCodeCandidates(condition_entity_id="2", condition_text="t2", normalized_text="t2", assertion="PRESENT", candidates=[
        RankedCodeCandidate(code="E11.9", description="Type 2", code_type="ICD10")
    ])
    warns = assembler._build_excludes_matrix([c1, c2], km)
    assert len(warns) > 0
    assert any(w.code1 == "E10.9" and w.code2 == "E11.9" for w in warns)

def test_excludes_matrix_no_false_positive(assembler, km):
    c1 = ConditionCodeCandidates(condition_entity_id="1", condition_text="t1", normalized_text="t1", assertion="PRESENT", candidates=[
        RankedCodeCandidate(code="E11.22", description="T2DM nephrop", code_type="ICD10")
    ])
    c2 = ConditionCodeCandidates(condition_entity_id="2", condition_text="t2", normalized_text="t2", assertion="PRESENT", candidates=[
        RankedCodeCandidate(code="N18.30", description="CKD3", code_type="ICD10")
    ])
    warns = assembler._build_excludes_matrix([c1, c2], km)
    assert len(warns) == 0

def test_ncci_matrix_catches_bundling(assembler, km):
    p1 = ProcedureCodeCandidates(procedure_entity_id="1", procedure_text="CMP", normalized_text="CMP", candidates=[
        RankedCodeCandidate(code="80053", description="CMP", code_type="CPT")
    ])
    p2 = ProcedureCodeCandidates(procedure_entity_id="2", procedure_text="BMP", normalized_text="BMP", candidates=[
        RankedCodeCandidate(code="80048", description="BMP", code_type="CPT")
    ])
    warns = assembler._build_ncci_matrix([p1, p2], km)
    assert len(warns) > 0

def test_medical_necessity_covered(assembler, km, sample_cardiac_scr):
    # Setup candidate
    p1 = ProcedureCodeCandidates(procedure_entity_id="1", procedure_text="echocardiogram", normalized_text="echo", candidates=[
        RankedCodeCandidate(code="93306", description="Echo", code_type="CPT")
    ])
    # TTE (93306) covered by heart failure (I50.20)
    assembler._enrich_procedure_candidates(p1, km, ["I50.20"], sample_cardiac_scr)
    assert len(p1.medical_necessity) > 0
    assert p1.medical_necessity[0].is_covered is True

def test_medical_necessity_not_covered(assembler, km, sample_cardiac_scr):
    p1 = ProcedureCodeCandidates(procedure_entity_id="1", procedure_text="echocardiogram", normalized_text="echo", candidates=[
        RankedCodeCandidate(code="93306", description="Echo", code_type="CPT")
    ])
    assembler._enrich_procedure_candidates(p1, km, ["R51.9"], sample_cardiac_scr)
    assert len(p1.medical_necessity) > 0
    assert p1.medical_necessity[0].is_covered is False

def test_use_additional_populated(assembler, km):
    c1 = ConditionCodeCandidates(condition_entity_id="1", condition_text="t1", normalized_text="t1", assertion="PRESENT", candidates=[
        RankedCodeCandidate(code="E11.22", description="T2DM nephrop", code_type="ICD10")
    ])
    assembler._enrich_condition_candidates(c1, km, {})
    assert isinstance(c1.use_additional_instructions, list)

def test_modifier_suggestion_laterality(assembler, sample_pulmonary_scr):
    p1 = ProcedureCodeCandidates(procedure_entity_id="pr1", procedure_text="left knee x-ray", normalized_text="x-ray left knee", candidates=[
        RankedCodeCandidate(code="73620", description="XR Knee", code_type="CPT")
    ])
    mods = assembler._suggest_modifiers(p1, sample_pulmonary_scr)
    assert any(m.modifier == "LT" for m in mods)

def test_encounter_type_guidelines(assembler, km):
    gl = assembler._get_encounter_type_guidelines("INPATIENT", km)
    assert isinstance(gl, list)

def test_context_window_limited(assembler):
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    cands = [RankedCodeCandidate(code=str(i), description="x", code_type="ICD") for i in range(20)]
    cc = ConditionCodeCandidates(condition_entity_id="c", condition_text="t", normalized_text="nt", assertion="PRESENT", candidates=cands)
    ctx = CodeRetrievalContext(scr_id="id", condition_candidates=[cc], procedure_candidates=[])
    ctx = assembler._limit_context_window(ctx, max_candidates_per_entity=5)
    assert len(ctx.condition_candidates[0].candidates) == 5


# ---------------------------------------------------------------------------
# Full Agent Tests (6)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_processes_cardiac_scr(agent, sample_cardiac_scr):
    msg = AgentMessage(
        from_agent="sys", to_agent=agent.agent_id,
        action="PROCESS", payload=asdict(sample_cardiac_scr)
    )
    res = await agent.process(msg)
    assert res.status == "SUCCESS"
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    ctx = CodeRetrievalContext(**res.payload)
    
    assert len(ctx.condition_candidates) == 4 # Fever dropped
    
    # NSTEMI (I21.4)
    c1 = next(c for c in ctx.condition_candidates if c.condition_entity_id == "c1")
    assert any(cd.code == "I21.4" for cd in c1.candidates)
    
    # T2DM with CKD (E11.22)
    c2 = next(c for c in ctx.condition_candidates if c.condition_entity_id == "c2")
    assert any(cd.code == "E11.22" for cd in c2.candidates)
    
    # HTN (I10)
    c4 = next(c for c in ctx.condition_candidates if c.condition_entity_id == "c4")
    assert any(cd.code == "I10" for cd in c4.candidates)
    
    # Cross entity DM+CKD
    assert any("Diabetes with Kidney" in cg.title for cg in ctx.cross_entity_guidelines)

@pytest.mark.asyncio
async def test_agent_processes_pulmonary_scr(agent, sample_pulmonary_scr):
    msg = AgentMessage(
        from_agent="sys", to_agent=agent.agent_id,
        action="PROCESS", payload=asdict(sample_pulmonary_scr)
    )
    res = await agent.process(msg)
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    ctx = CodeRetrievalContext(**res.payload)
    
    c1 = next(c for c in ctx.condition_candidates if c.condition_entity_id == "p1")
    assert any(cd.code == "J44.1" for cd in c1.candidates)
    
    c2 = next(c for c in ctx.condition_candidates if c.condition_entity_id == "p2")
    assert c2.assertion == "POSSIBLE"
    assert any(cd.code == "J18.9" for cd in c2.candidates)

@pytest.mark.asyncio
async def test_agent_filters_negated(agent, sample_cardiac_scr):
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload=asdict(sample_cardiac_scr))
    res = await agent.process(msg)
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    ctx = CodeRetrievalContext(**res.payload)
    # "fever" was ABSENT, so it shouldn't produce a ConditionCodeCandidates block
    assert not any(c.condition_entity_id == "c5" for c in ctx.condition_candidates)

@pytest.mark.asyncio
async def test_agent_handles_empty_scr(agent):
    empty_scr = StructuredClinicalRepresentation(
        scr_id="scr_e", document_id="doc_e", patient_context={},
        conditions=[], procedures=[], medications=[], vitals=None, lab_results=[],
        sections_found=[], clinical_summary=""
    )
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload=asdict(empty_scr))
    res = await agent.process(msg)
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    ctx = CodeRetrievalContext(**res.payload)
    assert len(ctx.condition_candidates) == 0

@pytest.mark.asyncio
async def test_agent_state_transitions(agent, sample_cardiac_scr):
    assert agent.state.name == "IDLE"
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload=asdict(sample_cardiac_scr))
    await agent.process(msg)
    assert agent.state.name == "COMPLETED"

@pytest.mark.asyncio
async def test_agent_returns_response(agent, sample_cardiac_scr):
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload=asdict(sample_cardiac_scr))
    res = await agent.process(msg)
    assert res.from_agent == agent.agent_name
    assert res.status == "SUCCESS"


# ---------------------------------------------------------------------------
# Performance Tests (1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retrieval_speed(agent, sample_cardiac_scr):
    msg = AgentMessage(from_agent="sys", to_agent=agent.agent_id, action="PROCESS", payload=asdict(sample_cardiac_scr))
    
    t0 = time.perf_counter()
    res = await agent.process(msg)
    elapsed = time.perf_counter() - t0
    
    from medi_comply.schemas.retrieval import CodeRetrievalContext
    ctx = CodeRetrievalContext(**res.payload)
    
    assert elapsed < 3.0  # Must be faster than 3 seconds
    assert ctx.retrieval_summary["retrieval_time_ms"] < 3000
