"""
MEDI-COMPLY — Knowledge Retrieval Agent.

The RAG orchestrator linking Clinical NLP extraction (SCR) to the
Medical Knowledge Base, generating a CodeRetrievalContext.
"""

from __future__ import annotations

import time

from medi_comply.core.config import Settings
from medi_comply.core.agent_base import BaseAgent, AgentMessage, AgentResponse, AgentType
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation

from medi_comply.schemas.retrieval import (
    CodeRetrievalContext, ConditionCodeCandidates, ProcedureCodeCandidates,
    RetrievalMetadata, RankedCodeCandidate
)

from medi_comply.schemas.common import AgentState
from medi_comply.agents.clinical_code_mapper import ClinicalCodeMapper
from medi_comply.agents.retrieval_strategies import (
    RetrievalFusion, DirectMapStrategy, VectorRetrievalStrategy,
    KeywordRetrievalStrategy, HierarchyTraversalStrategy
)
from medi_comply.agents.context_assembler import ContextAssembler


class KnowledgeRetrievalAgent(BaseAgent):
    """Retrieves standard medical codes and guidelines for a given SCR."""

    def __init__(self, knowledge_manager: KnowledgeManager, config: Settings):
        super().__init__(
            agent_name="KnowledgeRetrievalAgent",
            agent_type=AgentType.RAG_SPECIALIST
        )
        self.km = knowledge_manager
        self.config = config
        
        self.mapper = ClinicalCodeMapper()
        
        # Initialize strategy bundle
        self.fusion = RetrievalFusion(strategies=[
            DirectMapStrategy(self.mapper, self.km),
            VectorRetrievalStrategy(self.km.vector_store),
            KeywordRetrievalStrategy(self.km),
            HierarchyTraversalStrategy(self.km)
        ])
        
        self.assembler = ContextAssembler()

    async def process(self, message: AgentMessage) -> AgentResponse:
        """Process an SCR to retrieve candidate codes and guidelines."""
        # 1. Transition to THINKING
        self.transition_state(AgentState.THINKING)
        self._logger.info("KnowledgeRetrievalAgent processing SCR payload")
        
        payload = message.payload
        if isinstance(payload, dict):
            # Parse dict into SCR
            scr = StructuredClinicalRepresentation(**payload)
        elif isinstance(payload, StructuredClinicalRepresentation):
            scr = payload
        else:
            self.transition_state(AgentState.ERROR)
            return AgentResponse(
                agent_id=self.agent_id,
                original_message_id=message.message_id,
                from_agent=self.agent_name,
                status="ERROR",
                message="Invalid payload type for KnowledgeRetrievalAgent."
            )
            
        t0 = time.perf_counter()
            
        # 2. Process conditions
        cond_candidates: list[ConditionCodeCandidates] = []
        for condition in scr.conditions:
            cond_assertion = condition.get("assertion", "") if isinstance(condition, dict) else getattr(condition, "assertion", "")
            if cond_assertion == "ABSENT":
                # Skip negated
                continue
            cand = await self.retrieve_for_condition(condition, scr.patient_context)
            if cand:
                cond_candidates.append(cand)
                
        # 3. Process procedures
        proc_candidates: list[ProcedureCodeCandidates] = []
        dx_codes = []
        for cc in cond_candidates:
            dx_codes.extend([c.code for c in cc.candidates])
            
        for procedure in scr.procedures:
            cand = await self.retrieve_for_procedure(procedure, dx_codes)
            if cand:
                proc_candidates.append(cand)
                
        # 4. Assemble context
        context = await self.assembler.assemble(
            scr, cond_candidates, proc_candidates, self.km
        )
        
        # Log final stats
        elapsed_ms = (time.perf_counter() - t0) * 1000
        context.retrieval_summary["retrieval_time_ms"] = elapsed_ms
        self._logger.info(f"Retrieval complete in {elapsed_ms:.1f}ms")
        
        # 5. Transition to COMPLETED
        self.transition_state(AgentState.PROPOSING)
        self.transition_state(AgentState.VALIDATING)
        self.transition_state(AgentState.APPROVED)
        self.transition_state(AgentState.COMPLETED)
        
        # 6. Return response
        return AgentResponse(
            agent_id=self.agent_id,
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status="SUCCESS",
            payload=context.model_dump() if hasattr(context, "model_dump") else getattr(context, "__dict__", {})
        )

    async def retrieve_for_condition(
        self,
        condition: Any,  # ConditionEntry from SCR
        patient_context: dict
    ) -> ConditionCodeCandidates:
        """Fetch code candidates for a condition using RAG fusion."""
        t0 = time.perf_counter()
        query = self._build_condition_query(condition)
        
        # Run fusion
        top_k = 10
        candidates = await self.fusion.retrieve(query, "ICD10", top_k=top_k)
        
        # Prepare tracking structure
        cond_entity_id = condition.get("entity_id", "") if isinstance(condition, dict) else getattr(condition, "entity_id", "")
        cond_text = condition.get("text", "") if isinstance(condition, dict) else getattr(condition, "text", "")
        cond_norm = condition.get("normalized_text", "") if isinstance(condition, dict) else getattr(condition, "normalized_text", "")
        cond_assert = condition.get("assertion", "") if isinstance(condition, dict) else getattr(condition, "assertion", "")
        cond_acuity = condition.get("acuity", "") if isinstance(condition, dict) else getattr(condition, "acuity", "")

        cc = ConditionCodeCandidates(
            condition_entity_id=cond_entity_id,
            condition_text=cond_text,
            normalized_text=cond_norm,
            assertion=cond_assert,
            acuity=cond_acuity,
            candidates=candidates
        )
        
        # Filter demographics (simplified here, deeper in assembler)
        cc = self._filter_by_assertion(cc)
        self._apply_specificity_preference(cc.candidates)
        
        # Re-sort after specificity preference apply
        cc.candidates.sort(key=lambda x: x.relevance_score, reverse=True)
        
        cc.retrieval_metadata = RetrievalMetadata(
            strategies_used=["DIRECT_MAP", "VECTOR", "KEYWORD", "HIERARCHY"],
            total_candidates_before_filter=len(candidates),
            total_candidates_after_filter=len(cc.candidates),
            retrieval_time_ms=(time.perf_counter() - t0) * 1000,
            fusion_method="RECIPROCAL_RANK_FUSION"
        )
        
        return cc

    async def retrieve_for_procedure(
        self,
        procedure: Any,  # ProcedureEntry from SCR
        diagnosis_codes: list[str]
    ) -> ProcedureCodeCandidates:
        """Fetch CPT candidates using matched diagnostic coding contexts."""
        t0 = time.perf_counter()
        query = self._build_procedure_query(procedure)
        
        top_k = 10
        candidates = await self.fusion.retrieve(query, "CPT", top_k=top_k)
        proc_eid = procedure.get("entity_id", "") if isinstance(procedure, dict) else getattr(procedure, "entity_id", "")
        proc_text = procedure.get("text", "") if isinstance(procedure, dict) else getattr(procedure, "text", "")
        proc_norm = procedure.get("normalized_text", "") if isinstance(procedure, dict) else getattr(procedure, "normalized_text", "")

        pc = ProcedureCodeCandidates(
            procedure_entity_id=proc_eid,
            procedure_text=proc_text,
            normalized_text=proc_norm,
            candidates=candidates
        )
        
        self._apply_specificity_preference(pc.candidates)
        pc.candidates.sort(key=lambda x: x.relevance_score, reverse=True)
        
        pc.retrieval_metadata = RetrievalMetadata(
            strategies_used=["DIRECT_MAP", "VECTOR", "KEYWORD", "HIERARCHY"],
            total_candidates_before_filter=len(candidates),
            total_candidates_after_filter=len(pc.candidates),
            retrieval_time_ms=(time.perf_counter() - t0) * 1000,
            fusion_method="RECIPROCAL_RANK_FUSION"
        )
        
        return pc

    def _build_condition_query(self, condition: Any) -> str:
        """Construct the search query emphasizing critical features."""
        parts = []
        cond_acuity = condition.get("acuity", "") if isinstance(condition, dict) else getattr(condition, "acuity", "")
        cond_severity = condition.get("severity", "") if isinstance(condition, dict) else getattr(condition, "severity", "")
        cond_norm = condition.get("normalized_text", "") if isinstance(condition, dict) else getattr(condition, "normalized_text", "")
        cond_text = condition.get("text", "") if isinstance(condition, dict) else getattr(condition, "text", "")
        cond_laterality = condition.get("laterality", "") if isinstance(condition, dict) else getattr(condition, "laterality", "")

        if cond_acuity and cond_acuity.lower() != "unspecified":
            parts.append(cond_acuity)
        if cond_severity:
            parts.append(cond_severity)
        if cond_norm:
            parts.append(cond_norm)
        else:
            parts.append(cond_text)
        if cond_laterality:
            parts.append(cond_laterality)
            
        return " ".join(parts).lower()

    def _build_procedure_query(self, procedure: Any) -> str:
        """Construct procedure query with anatomic context."""
        parts = []
        proc_norm = procedure.get("normalized_text", "") if isinstance(procedure, dict) else getattr(procedure, "normalized_text", "")
        proc_text = procedure.get("text", "") if isinstance(procedure, dict) else getattr(procedure, "text", "")
        proc_bs = procedure.get("body_site", "") if isinstance(procedure, dict) else getattr(procedure, "body_site", "")
        proc_lat = procedure.get("laterality", "") if isinstance(procedure, dict) else getattr(procedure, "laterality", "")

        if proc_norm:
            parts.append(proc_norm)
        else:
            parts.append(proc_text)
        if proc_bs and proc_bs.lower() != "unspecified":
            parts.append(proc_bs)
        if proc_lat:
            parts.append(proc_lat)
            
        return " ".join(parts).lower()

    def _filter_by_assertion(
        self,
        candidates_group: ConditionCodeCandidates
    ) -> ConditionCodeCandidates:
        """Modify or discard condition scores based on NLP assertion."""
        assertion = candidates_group.assertion.upper()
        
        if assertion == "ABSENT":
            candidates_group.candidates = []
        elif assertion == "POSSIBLE":
            # Possible conditions might be coded in inpatient but not out
            for cand in candidates_group.candidates:
                cand.relevance_score *= 0.8  # Slight penalty
        elif assertion in ["HISTORICAL", "FAMILY"]:
            # Drop regular codes; real logic would swap them to Z codes.
            # Simplified for hackathon structure
            pass
            
        return candidates_group

    def _apply_specificity_preference(self, candidates: list[RankedCodeCandidate]) -> list[RankedCodeCandidate]:
        """Boost codes that hold deeper granular specificity depth."""
        for c in candidates:
            if c.is_billable:
                bonus = 1.0 + (0.05 * c.specificity_level)
                c.relevance_score = min(1.0, c.relevance_score * bonus)
        return candidates
