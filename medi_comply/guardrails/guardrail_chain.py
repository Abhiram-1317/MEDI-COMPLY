"""
MEDI-COMPLY — Orchestrator for guardrail pipeline.
"""

import time
from typing import Any

from medi_comply.schemas.coding_result import CodingResult
from medi_comply.nlp.scr_builder import StructuredClinicalRepresentation
from medi_comply.schemas.retrieval import CodeRetrievalContext
from medi_comply.knowledge.knowledge_manager import KnowledgeManager

from medi_comply.guardrails.layer3_structural import StructuralGuardrails
from medi_comply.guardrails.layer4_semantic import SemanticGuardrails
from medi_comply.guardrails.layer5_output import OutputValidator
from medi_comply.guardrails.feedback_generator import ComplianceFeedbackGenerator
from medi_comply.guardrails.compliance_report import ComplianceReportGenerator, ComplianceReport


class GuardrailChain:
    """
    Orchestrates all guardrail layers in sequence.
    
    EXECUTION ORDER:
    1. Layer 5 (Output Validation) — fast, catches format issues first
    2. Layer 3 (Structural) — deterministic, catches code-level issues
    3. Layer 4 (Semantic) — AI-powered, catches reasoning issues
    """
    
    def __init__(
        self,
        knowledge_manager: KnowledgeManager,
        llm_client: Any = None,
        config: Any = None
    ):
        self.structural = StructuralGuardrails(knowledge_manager)
        self.semantic = SemanticGuardrails(llm_client, config)
        self.output_validator = OutputValidator()
        self.feedback_gen = ComplianceFeedbackGenerator()
        self.report_gen = ComplianceReportGenerator()
    
    async def validate(
        self,
        coding_result: CodingResult,
        scr: StructuredClinicalRepresentation,
        retrieval_context: CodeRetrievalContext = None,
        raw_llm_outputs: list[str] = None,
        skip_semantic: bool = False,
        retry_count: int = 1,
        max_retries: int = 3
    ) -> ComplianceReport:
        """Run the complete guardrail chain."""
        start_time = time.time()
        
        # 1. Layer 5: Output Validation
        output_results = self.output_validator.run_all_checks(coding_result, raw_llm_outputs)
        
        if self._has_security_alert(output_results):
             return self._generate_block_report(output_results, start_time, coding_result.coding_result_id)
             
        # 2. Layer 3: Structural Checks
        structural_results = self.structural.run_all_checks(coding_result, scr)
        
        # 3. Layer 4: Semantic Checks
        if skip_semantic:
             semantic_results = []
        else:
             semantic_results = await self.semantic.run_all_checks(coding_result, scr, retrieval_context)
             
        processing_time = (time.time() - start_time) * 1000
        report = self.report_gen.generate_report(
             structural_results, semantic_results, output_results,
             coding_result.coding_result_id, processing_time
        )
        
        # Attach complete feedback with loop controls
        feedback = self.feedback_gen.generate_feedback(
             structural_results, semantic_results, output_results, 
             retry_count, max_retries
        )
        report.feedback = feedback
        report.overall_decision = feedback.overall_decision
        
        return report
        
    def _has_security_alert(self, output_results: list) -> bool:
        return any(not getattr(r, "passed", True) and "SECURITY" in getattr(r, "severity", "") for r in output_results)
        
    def _generate_block_report(self, output_results: list, start_time: float, cr_id: str) -> ComplianceReport:
        report = self.report_gen.generate_report(
             [], [], output_results, cr_id, (time.time() - start_time) * 1000
        )
        return report
