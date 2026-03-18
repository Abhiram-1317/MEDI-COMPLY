"""
MEDI-COMPLY — Medical Coding Agent

The ultimate decision-making brain. Validates the CodeRetrievalContext and SCR,
applies deterministic constraints ensuring the LLM never hallucinates, and
produces the final compliant CodingResult.
"""

from __future__ import annotations

import collections
import copy
from typing import Any, Optional

from medi_comply.core.agent_base import AgentState, AgentType, BaseAgent
from medi_comply.core.config import Settings
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.retrieval import CodeRetrievalContext, ConditionCodeCandidates

from .coding_decision_engine import CodingDecisionEngine


class MedicalCodingAgent(BaseAgent):
    """
    The core coding agent that makes ICD-10/CPT coding decisions.
    
    Receives CodeRetrievalContext + SCR.
    Runs constraint filters and applies CodeDecisionEngine.
    Returns CodingResult payload.
    """

    def __init__(
        self,
        knowledge_manager: KnowledgeManager,
        config: Settings,
        llm_client: Any = None
    ) -> None:
        super().__init__(
            agent_name="MedicalCodingAgent",
            agent_type=AgentType.DOMAIN_EXPERT
        )
        self.km = knowledge_manager
        self.config = config
        self.llm_client = llm_client
        self.decision_engine = CodingDecisionEngine(knowledge_manager)
        self.max_retries = config.guardrail.max_retries if hasattr(config, "guardrail") else 3

    async def process(self, message: AgentMessage) -> AgentResponse:
        """Main entry point for processing SCR and returning CodingResult."""
        
        self.transition_state(AgentState.THINKING)
        
        payload = message.payload
        scr = StructuredClinicalRepresentation(**payload.get("scr", {}))
        context = CodeRetrievalContext(**payload.get("context", {}))
        
        self._logger.info(f"{self.agent_name}: Filtering assertion contexts")
        
        encounter_type = scr.patient_context.get("encounter_type", "INPATIENT") if scr.patient_context else "INPATIENT"

        filtered_context = self._filter_by_assertion(context, encounter_type, scr)

        self.transition_state(AgentState.PROPOSING)

        result = await self.decision_engine.make_decisions(
            context=filtered_context,
            scr=scr,
            llm_client=self.llm_client,
            attempt=1,
            previous_feedback=None
        )

        self.transition_state(AgentState.VALIDATING)

        if result.requires_human_review:
             self.transition_state(AgentState.ESCALATED)
        else:
             self.transition_state(AgentState.APPROVED)
             self.transition_state(AgentState.COMPLETED)

        return AgentResponse(
            agent_id=self.agent_id,
            original_message_id=message.message_id,
            from_agent=self.agent_name,
            status="SUCCESS",
            payload=result.model_dump()
        )

    async def process_with_retry(
        self,
        message: AgentMessage,
        compliance_feedback: list[str],
        attempt: int = 1
    ) -> AgentResponse:
        """Process with retry support."""
        if self.state in [AgentState.IDLE, AgentState.RETRY]:
             self.transition_state(AgentState.THINKING)

        if attempt > self.max_retries:
             self.transition_state(AgentState.ERROR)
             self.transition_state(AgentState.ESCALATED)
             return AgentResponse(
                 agent_id=self.agent_id,
                 original_message_id=message.message_id,
                 from_agent=self.agent_name,
                 status="FAILURE",
                 payload={"error": "Max retries exceeded", "feedback": compliance_feedback}
             )

        payload = message.payload
        scr = StructuredClinicalRepresentation(**payload.get("scr", {}))
        context = CodeRetrievalContext(**payload.get("context", {}))
        
        encounter_type = scr.patient_context.get("encounter_type", "INPATIENT") if scr.patient_context else "INPATIENT"
        filtered_context = self._filter_by_assertion(context, encounter_type, scr)

        self.transition_state(AgentState.PROPOSING)
        result = await self.decision_engine.make_decisions(
             context=filtered_context,
             scr=scr,
             llm_client=self.llm_client,
             attempt=attempt,
             previous_feedback=compliance_feedback
        )
        self.transition_state(AgentState.VALIDATING)

        if result.requires_human_review:
             self.transition_state(AgentState.ESCALATED)
        else:
             self.transition_state(AgentState.APPROVED)
             self.transition_state(AgentState.COMPLETED)
        
        return AgentResponse(
             agent_id=self.agent_id,
             original_message_id=message.message_id,
             from_agent=self.agent_name,
             status="SUCCESS",
             payload=result.model_dump()
        )

    def _filter_by_assertion(
        self,
        context: CodeRetrievalContext,
        encounter_type: str,
        scr: StructuredClinicalRepresentation
    ) -> CodeRetrievalContext:
        """Filter out ABSENT conditions and handle POSSIBLE vs OUTPATIENT."""
        
        c = copy.deepcopy(context)
        kept_conditions = []
        
        for cond in c.condition_candidates:
             if not hasattr(cond, "assertion") or not cond.assertion: 
                 kept_conditions.append(cond)
                 continue
                 
             if cond.assertion == "ABSENT":
                  continue # Exclude
                  
             if cond.assertion == "POSSIBLE":
                  if encounter_type.upper() == "OUTPATIENT":
                       symp_cond = self._handle_possible_outpatient(cond, scr)
                       if symp_cond:
                           kept_conditions.append(symp_cond)
                       continue # Skip the suspected condition itself
                  
             kept_conditions.append(cond)
             
        c.condition_candidates = kept_conditions
        return c

    def _handle_possible_outpatient(
        self,
        condition: ConditionCodeCandidates,
        scr: StructuredClinicalRepresentation
    ) -> Optional[ConditionCodeCandidates]:
        """Convert 'Possible X' into presenting symptoms for outpatient."""
        # For the hackathon, returning None effectively strips the diagnosis leaving
        # the symptoms intact if they correctly exist as independent entities in the SCR.
        return None
