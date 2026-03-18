"""
MEDI-COMPLY — Compliance Guard Agent
Wrapper for orchestrating guardrails autonomously.
"""

from typing import Any

from medi_comply.core.agent_base import BaseAgent
from medi_comply.core.message_models import AgentMessage, AgentResponse
from medi_comply.schemas.common import AgentState, AgentType
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.guardrails.guardrail_chain import GuardrailChain
from medi_comply.guardrails.compliance_report import ComplianceReport


class ComplianceGuardAgent(BaseAgent):
    """
    The compliance guardian agent.
    Input: AgentMessage containing CodingResult + SCR + CodeRetrievalContext
    Output: AgentResponse containing ComplianceReport
    """
    
    def __init__(
        self,
        knowledge_manager: KnowledgeManager,
        config: Any,
        llm_client: Any = None
    ):
        super().__init__(
            agent_name="ComplianceGuardAgent",
            agent_type=AgentType.VALIDATOR
        )
        self.guardrail_chain = GuardrailChain(knowledge_manager, llm_client, config)
        self.config = config
    
    async def process(self, message: AgentMessage) -> AgentResponse:
        self.transition_state(AgentState.THINKING)
        
        if "coding_result" not in message.payload:
             self.transition_state(AgentState.ERROR)
             return AgentResponse(agent_id=self.agent_id, success=False, error="Missing coding_result payload")
             
        coding_result = message.payload["coding_result"]
        scr = message.payload.get("scr")
        retrieval_context = message.payload.get("retrieval_context")
        raw_outputs = message.payload.get("raw_outputs", [])
        attempt = message.payload.get("attempt", 1)
        max_retries = message.payload.get("max_retries", 3)
        
        self.transition_state(AgentState.VALIDATING)
        
        report = await self.guardrail_chain.validate(
             coding_result=coding_result,
             scr=scr,
             retrieval_context=retrieval_context,
             raw_llm_outputs=raw_outputs,
             skip_semantic=self.llm_client is None,
             retry_count=attempt,
             max_retries=max_retries
        )
        
        decision = report.overall_decision
        if decision == "PASS":
             self.transition_state(AgentState.APPROVED)
             self.transition_state(AgentState.COMPLETED)
        elif decision == "RETRY":
             self.transition_state(AgentState.RETRY)
        elif decision == "BLOCK":
             self.transition_state(AgentState.ERROR)
        else: # ESCALATE
             self.transition_state(AgentState.ESCALATED)
             
        return AgentResponse(
             agent_id=self.agent_id,
             success=(decision == "PASS"),
             data={"compliance_report": report},
             error=None if decision == "PASS" else f"Guardrail Check Failed: {decision}"
        )
    
    def should_retry(self, report: ComplianceReport, current_attempt: int) -> bool:
        """True if decision is RETRY AND attempt < max_retries."""
        return report.overall_decision == "RETRY" and current_attempt < (report.feedback.max_retries if report.feedback else 3)
    
    def get_retry_feedback(self, report: ComplianceReport) -> list[str]:
        """Extract structured feedback for the coding agent."""
        if not report.feedback:
             return []
        fb = self.guardrail_chain.feedback_gen.format_for_retry_prompt(report.feedback)
        return [fb]
    
    def format_report_for_audit(self, report: ComplianceReport) -> dict:
        return report.model_dump()
