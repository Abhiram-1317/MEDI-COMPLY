"""Pipeline metrics aggregation utilities."""

from __future__ import annotations

import math
import statistics
import time
from typing import Dict

from medi_comply.pipeline import PipelineContext
from medi_comply.result_models import PipelineMetrics


class PipelineMetricsCollector:
    """Collects per-run and aggregate metrics for orchestrator executions."""

    def __init__(self) -> None:
        self._stage_timers: dict[str, float] = {}
        self._llm_metrics: list[dict] = []
        self._counters: dict[str, float] = {
            "total_runs": 0,
            "successful": 0,
            "escalated": 0,
            "blocked": 0,
            "errors": 0,
            "total_retries": 0,
        }
        self._latencies: list[float] = []
        self._codes_history: list[int] = []
        self._llm_token_total: int = 0
        self._llm_latency_total: float = 0.0

    def start_stage_timer(self, stage: str) -> None:
        self._stage_timers[stage] = time.perf_counter()

    def stop_stage_timer(self, stage: str) -> float:
        start = self._stage_timers.pop(stage, None)
        if start is None:
            return 0.0
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed

    def record_llm_call(
        self, model: str, prompt_tokens: int, response_tokens: int, latency_ms: float
    ) -> None:
        entry = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "response_tokens": response_tokens,
            "total_tokens": prompt_tokens + response_tokens,
            "latency_ms": latency_ms,
        }
        self._llm_metrics.append(entry)
        self._llm_token_total += entry["total_tokens"]
        self._llm_latency_total += latency_ms

    def record_pipeline_complete(self, status: str, retry_count: int) -> None:
        self._counters["total_runs"] += 1
        status_key = {
            "SUCCESS": "successful",
            "ESCALATED": "escalated",
            "BLOCKED": "blocked",
            "ERROR": "errors",
        }.get(status, "errors")
        self._counters[status_key] += 1
        self._counters["total_retries"] += retry_count

    def build_metrics(self, context: PipelineContext) -> PipelineMetrics:
        stage_times = {result.stage_name: result.processing_time_ms for result in context.stage_results}
        llm_calls = len(context.llm_interactions)
        llm_tokens = sum(i.total_tokens for i in context.llm_interactions)
        llm_latency = sum(i.latency_ms for i in context.llm_interactions)
        retrieval_candidates = 0
        if context.retrieval_context:
            retrieval_candidates += sum(len(c.candidates) for c in context.retrieval_context.condition_candidates)
            retrieval_candidates += sum(len(p.candidates) for p in context.retrieval_context.procedure_candidates)
        codes_assigned = context.coding_result.total_codes_assigned if context.coding_result else 0
        compliance_checks_run = context.compliance_report.total_checks_run if context.compliance_report else 0
        compliance_checks_passed = context.compliance_report.checks_passed if context.compliance_report else 0
        knowledge_version = "UNKNOWN"
        if context.retrieval_context and context.retrieval_context.retrieval_summary:
            knowledge_version = context.retrieval_context.retrieval_summary.get("knowledge_version", knowledge_version)
        if knowledge_version == "UNKNOWN" and getattr(context, "patient_context", None):
            knowledge_version = context.patient_context.get("knowledge_base_version", knowledge_version)
        model_versions: Dict[str, str] = {}
        if context.llm_interactions:
            last = context.llm_interactions[-1]
            model_versions = {"llm": f"{last.model_name}-{last.model_version}"}

        total_time = context.get_processing_time_ms()
        self._latencies.append(total_time)
        self._codes_history.append(codes_assigned)

        metrics = PipelineMetrics(
            total_time_ms=total_time,
            stage_times_ms=stage_times,
            llm_calls_count=llm_calls,
            llm_total_tokens=llm_tokens,
            llm_total_latency_ms=llm_latency,
            retrieval_candidates_count=retrieval_candidates,
            codes_assigned=codes_assigned,
            compliance_checks_run=compliance_checks_run,
            compliance_checks_passed=compliance_checks_passed,
            retry_count=len(context.retry_history),
            knowledge_base_version=knowledge_version,
            model_versions=model_versions,
        )
        return metrics

    def get_aggregate_stats(self) -> dict:
        runs = max(1, self._counters["total_runs"])
        latency_avg = statistics.mean(self._latencies) if self._latencies else 0.0
        latency_sorted = sorted(self._latencies)
        idx = math.floor(0.95 * (len(latency_sorted) - 1)) if latency_sorted else 0
        latency_p95 = latency_sorted[idx] if latency_sorted else 0.0
        avg_codes = statistics.mean(self._codes_history) if self._codes_history else 0.0
        llm_avg_latency = (
            self._llm_latency_total / len(self._llm_metrics)
            if self._llm_metrics
            else 0.0
        )
        return {
            "total_runs": self._counters["total_runs"],
            "success_rate": self._counters["successful"] / runs,
            "escalation_rate": self._counters["escalated"] / runs,
            "average_latency_ms": latency_avg,
            "p95_latency_ms": latency_p95,
            "average_codes_per_encounter": avg_codes,
            "average_retries": (self._counters["total_retries"] / runs),
            "llm_total_tokens": self._llm_token_total,
            "llm_average_latency_ms": llm_avg_latency,
        }
