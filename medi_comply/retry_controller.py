"""Retry controller for coordinating coding ⇄ compliance loops."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from medi_comply.agents.compliance_guard_agent import ComplianceGuardAgent
from medi_comply.agents.medical_coding_agent import MedicalCodingAgent
from medi_comply.core.message_bus import AsyncMessageBus
from medi_comply.core.message_models import AgentMessage
from medi_comply.guardrails.compliance_report import ComplianceReport
from medi_comply.pipeline import PipelineContext
from medi_comply.audit.audit_models import RetryRecord
from medi_comply.schemas.coding_result import CodingResult


class RetryController:
    """Controls retry attempts between coding and compliance agents."""

    def __init__(self, max_retries: int = 3) -> None:
        self.max_retries = max_retries

    async def execute_with_retry(
        self,
        coding_agent: MedicalCodingAgent,
        compliance_agent: ComplianceGuardAgent,
        context: PipelineContext,
        message_bus: Optional[AsyncMessageBus] = None,
    ) -> tuple[CodingResult, ComplianceReport, list[RetryRecord]]:
        attempt = 1
        feedback_bundle: Optional[List[str]] = None
        retry_history: list[RetryRecord] = []
        pending_diff_source: Optional[CodingResult] = None
        latest_report: Optional[ComplianceReport] = None
        latest_coding_result: Optional[CodingResult] = None

        while attempt <= self.max_retries + 1:
            context.current_attempt = attempt
            coding_payload = {
                "scr": self._to_wire(context.scr),
                "context": context.retrieval_context.model_dump() if context.retrieval_context else {},
                "attempt": attempt,
                "feedback": feedback_bundle or [],
            }
            coding_message = AgentMessage(
                from_agent="OrchestratorAgent",
                to_agent="MedicalCodingAgent",
                action="RECODE" if attempt > 1 else "CODE",
                payload=coding_payload,
                trace_id=context.trace_id,
            )
            coding_response = await coding_agent.process(coding_message)
            wire_result = coding_response.payload or {}
            if not wire_result:
                raise RuntimeError("MedicalCodingAgent returned an empty payload")
            coding_result = CodingResult.model_validate(wire_result)
            context.coding_result = coding_result
            latest_coding_result = coding_result

            if pending_diff_source and retry_history:
                retry_history[-1].codes_changed = self._extract_code_changes(pending_diff_source, coding_result)
                pending_diff_source = None

            compliance_message = AgentMessage(
                from_agent="OrchestratorAgent",
                to_agent="ComplianceGuardAgent",
                action="VALIDATE",
                payload={
                    "coding_result": coding_result,
                    "scr": context.scr,
                    "retrieval_context": context.retrieval_context,
                    "attempt": attempt,
                    "max_retries": self.max_retries,
                },
                trace_id=context.trace_id,
            )
            compliance_response = await compliance_agent.process(compliance_message)
            report = self._extract_report(compliance_response.payload)
            if report is None:
                raise RuntimeError("ComplianceGuardAgent returned no report payload")
            compliance_report = report
            latest_report = compliance_report

            if retry_history:
                retry_history[-1].compliance_result_after = compliance_report.overall_decision

            decision = compliance_report.overall_decision
            if decision in {"PASS", "BLOCK", "ESCALATE"}:
                return coding_result, compliance_report, retry_history

            feedback_bundle = self._extract_feedback(compliance_report)
            if attempt > self.max_retries:
                compliance_report.overall_decision = "ESCALATE"
                return coding_result, compliance_report, retry_history

            if self._is_same_failure(retry_history, feedback_bundle):
                compliance_report.overall_decision = "ESCALATE"
                return coding_result, compliance_report, retry_history

            retry_record = RetryController._build_retry_record(attempt, feedback_bundle)
            retry_history.append(retry_record)
            pending_diff_source = coding_result
            attempt += 1

        if latest_report is None or latest_coding_result is None:
            raise RuntimeError("Retry controller exited without a compliance report")
        latest_report.overall_decision = "ESCALATE"
        return latest_coding_result, latest_report, retry_history

    @staticmethod
    def _build_retry_record(attempt: int, feedback: Optional[Sequence[str]]):
        return RetryRecord(
            attempt_number=attempt,
            triggered_at=datetime.now(timezone.utc),
            trigger_reason="COMPLIANCE_RETRY",
            feedback_provided=list(feedback or []),
            codes_changed=[],
            compliance_result_after="PENDING",
        )

    @staticmethod
    def _extract_feedback(report: ComplianceReport) -> list[str]:
        if not report.feedback or not report.feedback.feedback_items:
            return []
        return [item.action_required or item.issue for item in report.feedback.feedback_items]

    @staticmethod
    def _extract_report(payload: dict) -> Optional[ComplianceReport]:
        report_obj = payload.get("compliance_report") if payload else None
        if report_obj is None:
            return None
        if isinstance(report_obj, ComplianceReport):
            return report_obj
        return ComplianceReport.model_validate(report_obj)

    @staticmethod
    def _to_wire(scr) -> dict:
        if scr is None:
            return {}
        if is_dataclass(scr) and not isinstance(scr, type):
            return asdict(scr)
        if hasattr(scr, "model_dump"):
            return scr.model_dump()  # type: ignore[return-value]
        if isinstance(scr, dict):
            return dict(scr)
        return {}

    @staticmethod
    def _is_same_failure(retry_history: list, current_feedback: Sequence[str]) -> bool:
        if not retry_history:
            return False
        last_feedback = retry_history[-1].feedback_provided
        return sorted(last_feedback) == sorted(current_feedback)

    @staticmethod
    def _extract_code_changes(previous: CodingResult, new_result: CodingResult) -> list[dict]:
        def flatten(result: CodingResult) -> set[str]:
            pairs = {f"{code.code_type}:{code.code}" for code in result.diagnosis_codes}
            pairs.update({f"{code.code_type}:{code.code}" for code in result.procedure_codes})
            return pairs

        prev_codes = flatten(previous)
        new_codes = flatten(new_result)
        removed = prev_codes - new_codes
        added = new_codes - prev_codes
        changes = []
        for code in sorted(removed):
            changes.append({"change": "REMOVED", "code": code})
        for code in sorted(added):
            changes.append({"change": "ADDED", "code": code})
        return changes
