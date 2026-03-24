"""Audit trail and compliance routes for MEDI-COMPLY."""
from __future__ import annotations
import hashlib
import json
import logging
import random
import uuid
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field
logger = logging.getLogger(__name__)
# Routers
audit_router = APIRouter(prefix="/api/v1/audit", tags=["Audit Trail"])
compliance_router = APIRouter(prefix="/api/v1/compliance", tags=["Compliance"])
# Enums
class WorkflowType(str, Enum):
    MEDICAL_CODING = "MEDICAL_CODING"
    CLAIMS_ADJUDICATION = "CLAIMS_ADJUDICATION"
    PRIOR_AUTHORIZATION = "PRIOR_AUTHORIZATION"
class RiskLevel(str, Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
 
class AuditRecordResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "audit_id": "AUD-2025-01-15-A8F3C2E1",
                "workflow_type": "MEDICAL_CODING",
                "timestamp": "2025-01-15T14:23:01.234Z",
                "processing_time_ms": 3421,
                "status": "COMPLETED",
            }
        }
    )
    audit_id: str
    workflow_type: str
    timestamp: str
    processing_time_ms: int = 0
    knowledge_base_version: str = "KB-2025-Q1"
    model_versions: Dict[str, str] = Field(
        default_factory=lambda: {
            "clinical_nlp": "medcoder-v2.1",
            "coding_agent": "gpt-4o-2025-01-01",
            "compliance_agent": "claude-3.5-sonnet",
        }
    )
    input_reference: Dict[str, Any] = Field(default_factory=dict)
    extraction_results: Dict[str, Any] = Field(default_factory=dict)
    decisions: List[Dict[str, Any]] = Field(default_factory=list)
    compliance_checks: List[Dict[str, Any]] = Field(default_factory=list)
    overall_risk_score: float = 0.0
    risk_level: str = "LOW"
    escalation_triggered: bool = False
    human_review_required: bool = False
    agent_interaction_log: List[Dict[str, Any]] = Field(default_factory=list)
    digital_signature: str = ""
    status: str = "COMPLETED"
class AuditExplanationResponse(BaseModel):
    audit_id: str
    workflow_type: str
    timestamp: str
    encounter_info: Dict[str, str] = Field(default_factory=dict)
    summary: str = ""
    primary_decision: Optional[Dict[str, Any]] = None
    secondary_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    why_this_decision: List[str] = Field(default_factory=list)
    why_not_alternatives: List[str] = Field(default_factory=list)
    evidence_citations: List[Dict[str, str]] = Field(default_factory=list)
    guidelines_applied: List[str] = Field(default_factory=list)
    compliance_summary: str = ""
    risk_assessment: str = ""
    formatted_explanation: str = ""
class AuditSearchRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {"workflow_type": "MEDICAL_CODING", "risk_level": "HIGH", "limit": 20}
        }
    )
    workflow_type: Optional[WorkflowType] = None
    date_from: Optional[str] = Field(None, description="Start date (YYYY-MM-DD)")
    date_to: Optional[str] = Field(None, description="End date (YYYY-MM-DD)")
    risk_level: Optional[RiskLevel] = None
    min_risk_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_risk_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    status: Optional[str] = None
    escalation_triggered: Optional[bool] = None
    human_review_required: Optional[bool] = None
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    sort_by: str = Field(default="timestamp", description="Sort field")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")
class AuditSearchResponse(BaseModel):
    total_count: int = 0
    returned_count: int = 0
    offset: int = 0
    limit: int = 50
    has_more: bool = False
    results: List[AuditRecordResponse] = Field(default_factory=list)
    filters_applied: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
class ComplianceDashboardResponse(BaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    period: str = "last_30_days"
    total_decisions: int = 0
    compliant_decisions: int = 0
    non_compliant_decisions: int = 0
    needs_review_decisions: int = 0
    compliance_rate: float = 0.0
    workflow_breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    risk_distribution: Dict[str, int] = Field(
        default_factory=lambda: {"LOW": 0, "MODERATE": 0, "HIGH": 0, "CRITICAL": 0}
    )
    guardrail_stats: Dict[str, Any] = Field(default_factory=dict)
    top_compliance_issues: List[Dict[str, Any]] = Field(default_factory=list)
    escalation_rate: float = 0.0
    total_escalations: int = 0
    compliance_trend: List[Dict[str, Any]] = Field(default_factory=list)
    active_alerts: List[str] = Field(default_factory=list)
class ComplianceReportResponse(BaseModel):
    report_id: str = Field(default_factory=lambda: f"RPT-{uuid.uuid4().hex[:8].upper()}")
    report_type: str = "monthly"
    period_start: str = ""
    period_end: str = ""
    generated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    generated_by: str = "MEDI-COMPLY Compliance Engine"
    executive_summary: str = ""
    total_transactions: int = 0
    coding_accuracy_rate: float = 0.0
    compliance_rate: float = 0.0
    escalation_rate: float = 0.0
    average_confidence: float = 0.0
    average_processing_time_ms: int = 0
    workflow_details: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    guardrail_summary: List[Dict[str, Any]] = Field(default_factory=list)
    top_denial_reasons: List[Dict[str, Any]] = Field(default_factory=list)
    top_compliance_failures: List[Dict[str, Any]] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)
    formatted_report: str = ""
    report_hash: str = ""
# Store and helpers
class AuditComplianceStore:
    def __init__(self) -> None:
        self._audit_records: Dict[str, AuditRecordResponse] = {}
        self._rand = random.Random(42)
        self._seed_sample_data()
    # --------------------------- seeding ---------------------------
    def _seed_sample_data(self) -> None:
        now = datetime.utcnow()
        total = self._rand.randint(22, 30)
        for _ in range(total):
            record = self._build_sample_record(now)
            self.add_record(record)
    def _build_sample_record(self, now: datetime) -> AuditRecordResponse:
        days_ago = self._rand.randint(0, 29)
        ts = now - timedelta(days=days_ago, hours=self._rand.randint(0, 23), minutes=self._rand.randint(0, 59))
        workflow = self._choose_workflow()
        risk_score = round(self._rand.uniform(0.02, 0.45), 3)
        risk_level = self._risk_from_score(risk_score)
        escalation = self._rand.random() < 0.1
        human_review = self._rand.random() < 0.15
        processing_time_ms = self._rand.randint(1000, 8000)
        status = self._rand.choice(["COMPLETED", "COMPLETED", "COMPLETED", "ESCALATED"])
        audit_id = f"AUD-{ts.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
        input_reference = {
            "document_hash": self._random_hash(),
            "source_system": self._rand.choice(["EHR", "PMS", "Clearinghouse"]),
            "encounter_id": f"ENC-{uuid.uuid4().hex[:10].upper()}",
        }
        decisions = self._build_decisions(workflow)
        compliance_checks = self._build_compliance_checks(workflow)
        agent_log = self._build_agent_log(workflow)
        record = AuditRecordResponse(
            audit_id=audit_id,
            workflow_type=workflow.value,
            timestamp=ts.isoformat() + "Z",
            processing_time_ms=processing_time_ms,
            knowledge_base_version=self._rand.choice(["KB-2025-Q1", "KB-2025-Q2", "KB-2024-Q4"]),
            model_versions=self._model_versions(workflow),
            input_reference=input_reference,
            extraction_results=self._build_extraction_results(workflow),
            decisions=decisions,
            compliance_checks=compliance_checks,
            overall_risk_score=risk_score,
            risk_level=risk_level.value,
            escalation_triggered=escalation,
            human_review_required=human_review,
            agent_interaction_log=agent_log,
            status=status,
        )
        record.digital_signature = self._sign_record(record)
        return record
    def _model_versions(self, workflow: WorkflowType) -> Dict[str, str]:
        base = {
            "clinical_nlp": "medcoder-v2.1",
            "coding_agent": "gpt-4o-2025-01-01",
            "compliance_agent": "claude-3.5-sonnet",
        }
        if workflow == WorkflowType.CLAIMS_ADJUDICATION:
            base.update({"claims_engine": "claims-bolt-1.3"})
        if workflow == WorkflowType.PRIOR_AUTHORIZATION:
            base.update({"auth_agent": "prior-auth-pro-0.9"})
        return base
    def _build_decisions(self, workflow: WorkflowType) -> List[Dict[str, Any]]:
        if workflow == WorkflowType.MEDICAL_CODING:
            return [
                {
                    "code": self._rand.choice(["E11.22", "I10", "J44.9", "M54.50", "K21.9", "E78.5"]),
                    "code_type": "ICD-10-CM",
                    "description": self._rand.choice([
                        "Type 2 diabetes with chronic kidney disease",
                        "Hypertension, unspecified",
                        "COPD, unspecified",
                        "Low back pain",
                        "GERD without esophagitis",
                        "Hyperlipidemia, unspecified",
                    ]),
                    "confidence": round(self._rand.uniform(0.86, 0.98), 3),
                    "reasoning_chain": [
                        {"step_number": 1, "action": "extract", "detail": "Identified condition in assessment"},
                        {"step_number": 2, "action": "map", "detail": "Mapped to ICD-10-CM"},
                        {"step_number": 3, "action": "validate", "detail": "Specificity verified"},
                    ],
                    "clinical_evidence": [
                        {
                            "section": "Assessment",
                            "source_text": "Type 2 diabetes with CKD stage 3b",
                            "relevance": "high",
                        }
                    ],
                    "alternatives_considered": [
                        {"code": "E11.9", "reason_rejected": "CKD documented"},
                        {"code": "E13.9", "reason_rejected": "No secondary diabetes"},
                    ],
                }
            ]
        if workflow == WorkflowType.CLAIMS_ADJUDICATION:
            return [
                {
                    "claim_id": f"CLM-{uuid.uuid4().hex[:8].upper()}",
                    "status": self._rand.choice(["APPROVED", "DENIED", "PENDED"]),
                    "amount": round(self._rand.uniform(120.0, 2450.0), 2),
                    "reasoning_chain": [
                        {"step_number": 1, "action": "benefit_check", "detail": "Member active"},
                        {"step_number": 2, "action": "policy_match", "detail": "Meets coverage"},
                        {"step_number": 3, "action": "payment_calc", "detail": "Allowed amount determined"},
                    ],
                }
            ]
        return [
            {
                "auth_id": f"AUTH-{uuid.uuid4().hex[:8].upper()}",
                "status": self._rand.choice(["APPROVED", "PENDING_INFO", "DENIED"]),
                "criteria_match": [
                    {"criterion": "Conservative therapy", "status": self._rand.choice(["MET", "UNCLEAR"])},
                    {"criterion": "Clinical findings", "status": self._rand.choice(["MET", "UNCLEAR"])},
                    {"criterion": "Imaging precedent", "status": self._rand.choice(["MET", "NOT_MET", "UNCLEAR"])},
                ],
                "confidence": round(self._rand.uniform(0.7, 0.96), 3),
            }
        ]
    def _build_compliance_checks(self, workflow: WorkflowType) -> List[Dict[str, Any]]:
        base_checks = [
            {"check_name": "CODE_EXISTS", "result": "PASS"},
            {"check_name": "DOCUMENTATION_PRESENT", "result": self._rand.choice(["PASS", "PASS", "SOFT_FAIL"])},
            {"check_name": "BILLING_RULES", "result": self._rand.choice(["PASS", "PASS", "PASS", "HARD_FAIL"])},
        ]
        if workflow == WorkflowType.CLAIMS_ADJUDICATION:
            base_checks.append({"check_name": "ELIGIBILITY_CONFIRMED", "result": "PASS"})
            base_checks.append({"check_name": "DUPLICATE_CHECK", "result": self._rand.choice(["PASS", "SOFT_FAIL"])})
        if workflow == WorkflowType.PRIOR_AUTHORIZATION:
            base_checks.append({"check_name": "CRITERIA_COMPLETENESS", "result": self._rand.choice(["PASS", "SOFT_FAIL"])})
            base_checks.append({"check_name": "TURNAROUND_TARGET", "result": "PASS"})
        return base_checks
    def _build_agent_log(self, workflow: WorkflowType) -> List[Dict[str, Any]]:
        steps = [
            {"agent": "nlp", "action": "parse", "detail": "Extracted entities"},
            {"agent": "reasoner", "action": "evaluate", "detail": "Applied policy rules"},
        ]
        if workflow == WorkflowType.CLAIMS_ADJUDICATION:
            steps.append({"agent": "payment", "action": "calculate", "detail": "Determined allowed amount"})
        if workflow == WorkflowType.PRIOR_AUTHORIZATION:
            steps.append({"agent": "auth", "action": "score", "detail": "Calculated criteria match"})
        return steps
    def _build_extraction_results(self, workflow: WorkflowType) -> Dict[str, Any]:
        if workflow == WorkflowType.MEDICAL_CODING:
            return {"problems": ["diabetes", "ckd"], "procedures": ["labs"], "sections": ["assessment"]}
        if workflow == WorkflowType.CLAIMS_ADJUDICATION:
            return {"claim_lines": self._rand.randint(1, 6), "units": self._rand.randint(1, 6), "provider_npi": "1234567890"}
        return {"indication": "knee pain", "imaging": "MRI knee", "policy": "POL-BCBS-MSK-MRI"}
    def _choose_workflow(self) -> WorkflowType:
        roll = self._rand.random()
        if roll < 0.6:
            return WorkflowType.MEDICAL_CODING
        if roll < 0.85:
            return WorkflowType.CLAIMS_ADJUDICATION
        return WorkflowType.PRIOR_AUTHORIZATION
    def _risk_from_score(self, score: float) -> RiskLevel:
        if score < 0.15:
            return RiskLevel.LOW
        if score < 0.3:
            return RiskLevel.MODERATE
        if score < 0.5:
            return RiskLevel.HIGH
        return RiskLevel.CRITICAL
    def _random_hash(self) -> str:
        return hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()
    def _sign_record(self, record: AuditRecordResponse) -> str:
        payload = record.model_dump()
        payload.pop("digital_signature", None)
        serialized = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(serialized.encode()).hexdigest()
    # --------------------------- search helpers ---------------------------
    def _filter_records(self, filters: AuditSearchRequest) -> List[AuditRecordResponse]:
        items = list(self._audit_records.values())
        results: List[AuditRecordResponse] = []
        for rec in items:
            if filters.workflow_type and rec.workflow_type != filters.workflow_type.value:
                continue
            if filters.risk_level and rec.risk_level != filters.risk_level.value:
                continue
            if filters.status and rec.status != filters.status:
                continue
            if filters.escalation_triggered is not None and rec.escalation_triggered != filters.escalation_triggered:
                continue
            if filters.human_review_required is not None and rec.human_review_required != filters.human_review_required:
                continue
            if filters.min_risk_score is not None and rec.overall_risk_score < filters.min_risk_score:
                continue
            if filters.max_risk_score is not None and rec.overall_risk_score > filters.max_risk_score:
                continue
            if filters.date_from:
                try:
                    if self._parse_ts(rec.timestamp) < self._parse_date(filters.date_from):
                        continue
                except Exception:
                    pass
            if filters.date_to:
                try:
                    if self._parse_ts(rec.timestamp) > self._parse_date(filters.date_to, end=True):
                        continue
                except Exception:
                    pass
            results.append(rec)
        return results
    def _sort_records(self, records: List[AuditRecordResponse], sort_by: str, sort_order: str) -> List[AuditRecordResponse]:
        reverse = sort_order.lower() == "desc"
        try:
            return sorted(records, key=lambda r: getattr(r, sort_by), reverse=reverse)
        except Exception:
            return sorted(records, key=lambda r: r.timestamp, reverse=reverse)
    # --------------------------- public methods ---------------------------
    def get_record(self, audit_id: str) -> Optional[AuditRecordResponse]:
        return self._audit_records.get(audit_id)
    def search_records(self, filters: AuditSearchRequest) -> AuditSearchResponse:
        matched = self._filter_records(filters)
        sorted_items = self._sort_records(matched, filters.sort_by, filters.sort_order)
        paged = sorted_items[filters.offset : filters.offset + filters.limit]
        response = AuditSearchResponse(
            total_count=len(matched),
            returned_count=len(paged),
            offset=filters.offset,
            limit=filters.limit,
            has_more=filters.offset + filters.limit < len(matched),
            results=paged,
            filters_applied=filters.model_dump(exclude_none=True),
        )
        return response
    def generate_explanation(self, audit_id: str) -> Optional[AuditExplanationResponse]:
        record = self.get_record(audit_id)
        if not record:
            return None
        primary = record.decisions[0] if record.decisions else None
        secondary = record.decisions[1:] if len(record.decisions) > 1 else []
        summary = self._build_summary(record)
        why_this = self._build_why_this(primary)
        why_not = self._build_why_not(primary)
        evidence = self._build_evidence(primary)
        compliance_summary = self._build_compliance_summary(record)
        risk_text = self._build_risk_text(record)
        formatted = self._format_explanation(record, primary, why_this, why_not, compliance_summary, risk_text)
        return AuditExplanationResponse(
            audit_id=record.audit_id,
            workflow_type=record.workflow_type,
            timestamp=record.timestamp,
            encounter_info={"encounter_id": record.input_reference.get("encounter_id", "")},
            summary=summary,
            primary_decision=primary,
            secondary_decisions=secondary,
            why_this_decision=why_this,
            why_not_alternatives=why_not,
            evidence_citations=evidence,
            guidelines_applied=["OCG 2025", "LCD L12345"],
            compliance_summary=compliance_summary,
            risk_assessment=risk_text,
            formatted_explanation=formatted,
        )
    def generate_dashboard(self, period_days: int = 30) -> ComplianceDashboardResponse:
        cutoff = datetime.utcnow() - timedelta(days=period_days)
        recent = [r for r in self._audit_records.values() if self._parse_ts(r.timestamp) >= cutoff]
        total = len(recent)
        compliant = sum(1 for r in recent if self._all_checks_pass(r))
        non_compliant = sum(1 for r in recent if self._any_hard_fail(r))
        needs_review = sum(1 for r in recent if self._any_soft_fail(r))
        escalation_count = sum(1 for r in recent if r.escalation_triggered)
        trend = self._build_trend(period_days)
        workflow_breakdown = self._workflow_breakdown(recent)
        guardrail_stats, top_issues = self._guardrail_stats(recent)
        risk_distribution = self._risk_distribution(recent)
        active_alerts = self._active_alerts(recent, escalation_count, risk_distribution)
        compliance_rate = round((compliant / total) * 100, 2) if total else 0.0
        escalation_rate = round((escalation_count / total) * 100, 2) if total else 0.0
        return ComplianceDashboardResponse(
            period=f"last_{period_days}_days",
            total_decisions=total,
            compliant_decisions=compliant,
            non_compliant_decisions=non_compliant,
            needs_review_decisions=needs_review,
            compliance_rate=compliance_rate,
            workflow_breakdown=workflow_breakdown,
            risk_distribution=risk_distribution,
            guardrail_stats=guardrail_stats,
            top_compliance_issues=top_issues,
            escalation_rate=escalation_rate,
            total_escalations=escalation_count,
            compliance_trend=trend,
            active_alerts=active_alerts,
        )
    def generate_report(
        self, period: str = "monthly", period_start: Optional[str] = None, period_end: Optional[str] = None
    ) -> ComplianceReportResponse:
        start, end = self._derive_period(period, period_start, period_end)
        records = self._records_in_range(start, end)
        total = len(records)
        avg_confidence = self._avg_confidence(records)
        compliance_rate = self._rate(records, predicate=self._all_checks_pass)
        escalation_rate = self._rate(records, predicate=lambda r: r.escalation_triggered)
        avg_pt = int(sum(r.processing_time_ms for r in records) / total) if total else 0
        workflow_details = self._workflow_details(records)
        guardrail_summary = self._guardrail_summary(records)
        top_failures = self._top_failures(records)
        top_denials = self._top_denials(records)
        summary_text = self._executive_summary(start, end, total, compliance_rate, avg_confidence, escalation_rate)
        recommendations = self._recommendations(records, escalation_rate, avg_confidence, top_failures)
        formatted_report = self._format_report(
            start,
            end,
            total,
            compliance_rate,
            escalation_rate,
            avg_confidence,
            avg_pt,
            workflow_details,
            guardrail_summary,
            recommendations,
        )
        report = ComplianceReportResponse(
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            report_type=period,
            total_transactions=total,
            compliance_rate=compliance_rate,
            escalation_rate=escalation_rate,
            average_confidence=avg_confidence,
            average_processing_time_ms=avg_pt,
            workflow_details=workflow_details,
            guardrail_summary=guardrail_summary,
            top_denial_reasons=top_denials,
            top_compliance_failures=top_failures,
            executive_summary=summary_text,
            recommendations=recommendations,
            formatted_report=formatted_report,
        )
        report.report_hash = hashlib.sha256(json.dumps(report.model_dump(), sort_keys=True).encode()).hexdigest()
        return report
    def add_record(self, record: AuditRecordResponse) -> None:
        if not record.digital_signature:
            record.digital_signature = self._sign_record(record)
        self._audit_records[record.audit_id] = record
    # --------------------------- explanation helpers ---------------------------
    def _build_summary(self, record: AuditRecordResponse) -> str:
        decisions_count = len(record.decisions)
        checks_count = len(record.compliance_checks)
        return (
            f"This {record.workflow_type} decision was processed on {record.timestamp} with a risk "
            f"score of {record.overall_risk_score} ({record.risk_level}). "
            f"{decisions_count} decisions were made, all passing {checks_count} compliance checks."
        )
    def _build_why_this(self, primary: Optional[Dict[str, Any]]) -> List[str]:
        if not primary:
            return []
        reasons = []
        if "reasoning_chain" in primary:
            for step in primary["reasoning_chain"]:
                reasons.append(f"Step {step.get('step_number', '?')}: {step.get('detail', '')}")
        if "confidence" in primary:
            reasons.append(f"Confidence {round(primary.get('confidence', 0)*100, 1)}% meets threshold")
        return reasons
    def _build_why_not(self, primary: Optional[Dict[str, Any]]) -> List[str]:
        if not primary:
            return []
        alts = primary.get("alternatives_considered", [])
        return [f"{alt.get('code', 'N/A')} rejected: {alt.get('reason_rejected', 'Not specified')}" for alt in alts]
    def _build_evidence(self, primary: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not primary:
            return []
        evidence = []
        for ev in primary.get("clinical_evidence", []):
            evidence.append(
                {
                    "section": ev.get("section", ""),
                    "source_text": ev.get("source_text", ""),
                    "relevance": ev.get("relevance", ""),
                }
            )
        return evidence
    def _build_compliance_summary(self, record: AuditRecordResponse) -> str:
        total = len(record.compliance_checks)
        fails = sum(1 for c in record.compliance_checks if c.get("result") == "HARD_FAIL")
        soft = sum(1 for c in record.compliance_checks if c.get("result") == "SOFT_FAIL")
        if fails:
            return f"Warning: {fails} hard failures and {soft} soft failures across {total} checks."
        if soft:
            return f"Notice: {soft} soft failures across {total} checks."
        return f"All {total} compliance checks passed."
    def _build_risk_text(self, record: AuditRecordResponse) -> str:
        level = record.risk_level
        score = record.overall_risk_score
        if level == RiskLevel.CRITICAL.value:
            return f"Risk Level: {level}. Immediate review required (score {score})."
        if level == RiskLevel.HIGH.value:
            return f"Risk Level: {level}. Elevated risk; prioritize review (score {score})."
        if level == RiskLevel.MODERATE.value:
            return f"Risk Level: {level}. Monitor trends (score {score})."
        return f"Risk Level: {level}. Routine monitoring (score {score})."
    def _format_explanation(
        self,
        record: AuditRecordResponse,
        primary: Optional[Dict[str, Any]],
        why_this: List[str],
        why_not: List[str],
        compliance_summary: str,
        risk_text: str,
    ) -> str:
        primary_line = ""
        if primary:
            code = primary.get("code") or primary.get("auth_id") or primary.get("claim_id") or "N/A"
            desc = primary.get("description") or primary.get("status") or "Decision"
            primary_line = f"{code} — {desc}"
        reasons_text = "\n".join([f"✅ {r}" for r in why_this]) or "✅ Reasoning unavailable"
        alt_text = "\n".join([f"❌ {r}" for r in why_not]) or "❌ No alternatives considered"
        confidence = ""
        if primary and "confidence" in primary:
            confidence = f"CONFIDENCE: {round(primary.get('confidence', 0)*100, 1)}%"
        elif primary and "status" in primary:
            confidence = f"STATUS: {primary.get('status')}"
        else:
            confidence = "CONFIDENCE: N/A"
        return (
            "═══════════════════════════════════════════\n"
            f"DECISION EXPLANATION — {record.audit_id}\n"
            "═══════════════════════════════════════════\n\n"
            f"WORKFLOW: {record.workflow_type}\n"
            f"DATE: {record.timestamp}\n\n"
            "────────────────────────────────────────\n\n"
            "📋 PRIMARY DECISION\n"
            f"{primary_line}\n\n"
            "WHY THIS DECISION:\n"
            f"{reasons_text}\n\n"
            "WHY NOT ALTERNATIVES:\n"
            f"{alt_text}\n\n"
            f"{confidence}\n\n"
            "────────────────────────────────────────\n\n"
            f"COMPLIANCE: {compliance_summary}\n"
            f"RISK LEVEL: {risk_text}\n\n"
            "═══════════════════════════════════════════"
        )
    # --------------------------- dashboard helpers ---------------------------
    def _parse_ts(self, ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", ""))
    def _parse_date(self, ds: str, end: bool = False) -> datetime:
        base = datetime.strptime(ds, "%Y-%m-%d")
        return base + timedelta(days=1) - timedelta(seconds=1) if end else base
    def _all_checks_pass(self, record: AuditRecordResponse) -> bool:
        return all(c.get("result") in {"PASS", "SOFT_FAIL"} for c in record.compliance_checks)
    def _any_hard_fail(self, record: AuditRecordResponse) -> bool:
        return any(c.get("result") == "HARD_FAIL" for c in record.compliance_checks)
    def _any_soft_fail(self, record: AuditRecordResponse) -> bool:
        return any(c.get("result") == "SOFT_FAIL" for c in record.compliance_checks)
    def _build_trend(self, period_days: int) -> List[Dict[str, Any]]:
        today = datetime.utcnow().date()
        trend = []
        for i in range(7):
            day = today - timedelta(days=i)
            records = [r for r in self._audit_records.values() if self._parse_ts(r.timestamp).date() == day]
            total = len(records)
            compliant = sum(1 for r in records if self._all_checks_pass(r))
            rate = round((compliant / total) * 100, 2) if total else 0.0
            trend.append({"date": day.isoformat(), "compliance_rate": rate, "total_decisions": total})
        return list(reversed(trend))
    def _workflow_breakdown(self, records: List[AuditRecordResponse]) -> Dict[str, Dict[str, Any]]:
        breakdown: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            wf = rec.workflow_type
            if wf not in breakdown:
                breakdown[wf] = {
                    "total": 0,
                    "compliant": 0,
                    "average_confidence": 0.0,
                    "average_processing_time_ms": 0.0,
                    "escalation_count": 0,
                }
            info = breakdown[wf]
            info["total"] += 1
            info["compliant"] += 1 if self._all_checks_pass(rec) else 0
            info["average_confidence"] += rec.overall_risk_score
            info["average_processing_time_ms"] += rec.processing_time_ms
            info["escalation_count"] += 1 if rec.escalation_triggered else 0
        for wf, info in breakdown.items():
            if info["total"]:
                info["average_confidence"] = round(info["average_confidence"] / info["total"], 3)
                info["average_processing_time_ms"] = int(info["average_processing_time_ms"] / info["total"])
        return breakdown
    def _guardrail_stats(self, records: List[AuditRecordResponse]) -> (Dict[str, Any], List[Dict[str, Any]]):
        totals: Dict[str, Dict[str, int]] = {}
        for rec in records:
            for check in rec.compliance_checks:
                name = check.get("check_name", "UNKNOWN")
                result = check.get("result", "PASS")
                entry = totals.setdefault(name, {"PASS": 0, "SOFT_FAIL": 0, "HARD_FAIL": 0})
                entry[result] = entry.get(result, 0) + 1
        total_checks = sum(sum(v.values()) for v in totals.values())
        pass_count = sum(v.get("PASS", 0) for v in totals.values())
        pass_rate = round((pass_count / total_checks) * 100, 2) if total_checks else 0.0
        top_failures = sorted(
            (
                {"check_name": name, "fail_count": vals.get("HARD_FAIL", 0) + vals.get("SOFT_FAIL", 0)}
                for name, vals in totals.items()
            ),
            key=lambda x: x["fail_count"],
            reverse=True,
        )
        guardrail_stats = {
            "total_checks_run": total_checks,
            "pass_rate": pass_rate,
            "top_failures": top_failures[:5],
        }
        return guardrail_stats, top_failures[:5]
    def _risk_distribution(self, records: List[AuditRecordResponse]) -> Dict[str, int]:
        dist = {level.value: 0 for level in RiskLevel}
        for rec in records:
            dist[rec.risk_level] = dist.get(rec.risk_level, 0) + 1
        return dist
    def _active_alerts(
        self, records: List[AuditRecordResponse], escalation_count: int, risk_distribution: Dict[str, int]
    ) -> List[str]:
        alerts = []
        total = len(records)
        if total and escalation_count / total > 0.1:
            alerts.append("Escalation rate above 10% threshold")
        if risk_distribution.get(RiskLevel.HIGH.value, 0) >= 2:
            alerts.append("High risk score detected in multiple recent decisions")
        if risk_distribution.get(RiskLevel.CRITICAL.value, 0) >= 1:
            alerts.append("Critical risk decision present; immediate review recommended")
        return alerts
    # --------------------------- report helpers ---------------------------
    def _derive_period(
        self, period: str, start: Optional[str], end: Optional[str]
    ) -> (datetime, datetime):
        now = datetime.utcnow()
        if start and end:
            return self._parse_date(start), self._parse_date(end, end=True)
        if period == "weekly":
            return now - timedelta(days=7), now
        if period == "quarterly":
            return now - timedelta(days=90), now
        return now - timedelta(days=30), now
    def _records_in_range(self, start: datetime, end: datetime) -> List[AuditRecordResponse]:
        return [r for r in self._audit_records.values() if start <= self._parse_ts(r.timestamp) <= end]
    def _avg_confidence(self, records: List[AuditRecordResponse]) -> float:
        if not records:
            return 0.0
        return round(sum(r.overall_risk_score for r in records) / len(records), 3)
    def _rate(self, records: List[AuditRecordResponse], predicate) -> float:
        if not records:
            return 0.0
        count = sum(1 for r in records if predicate(r))
        return round((count / len(records)) * 100, 2)
    def _workflow_details(self, records: List[AuditRecordResponse]) -> Dict[str, Dict[str, Any]]:
        details: Dict[str, Dict[str, Any]] = {}
        for rec in records:
            wf = rec.workflow_type
            entry = details.setdefault(
                wf,
                {
                    "total": 0,
                    "avg_confidence": 0.0,
                    "avg_processing_time_ms": 0.0,
                    "escalations": 0,
                    "hard_fails": 0,
                    "soft_fails": 0,
                },
            )
            entry["total"] += 1
            entry["avg_confidence"] += rec.overall_risk_score
            entry["avg_processing_time_ms"] += rec.processing_time_ms
            entry["escalations"] += 1 if rec.escalation_triggered else 0
            entry["hard_fails"] += 1 if self._any_hard_fail(rec) else 0
            entry["soft_fails"] += 1 if self._any_soft_fail(rec) else 0
        for wf, entry in details.items():
            if entry["total"]:
                entry["avg_confidence"] = round(entry["avg_confidence"] / entry["total"], 3)
                entry["avg_processing_time_ms"] = int(entry["avg_processing_time_ms"] / entry["total"])
        return details
    def _guardrail_summary(self, records: List[AuditRecordResponse]) -> List[Dict[str, Any]]:
        summary: Dict[str, Dict[str, int]] = {}
        for rec in records:
            for chk in rec.compliance_checks:
                name = chk.get("check_name", "UNKNOWN")
                result = chk.get("result", "PASS")
                entry = summary.setdefault(name, {"total_evaluated": 0, "pass_count": 0, "fail_count": 0})
                entry["total_evaluated"] += 1
                if result == "PASS":
                    entry["pass_count"] += 1
                else:
                    entry["fail_count"] += 1
        report = []
        for name, entry in summary.items():
            total = entry["total_evaluated"]
            pass_rate = round((entry["pass_count"] / total) * 100, 2) if total else 0.0
            report.append({"check_name": name, **entry, "pass_rate": pass_rate})
        return sorted(report, key=lambda x: x["fail_count"], reverse=True)
    def _top_failures(self, records: List[AuditRecordResponse]) -> List[Dict[str, Any]]:
        failures: Dict[str, int] = {}
        for rec in records:
            for chk in rec.compliance_checks:
                if chk.get("result") in {"HARD_FAIL", "SOFT_FAIL"}:
                    name = chk.get("check_name", "UNKNOWN")
                    failures[name] = failures.get(name, 0) + 1
        return sorted(
            ({"check_name": name, "fail_count": count} for name, count in failures.items()),
            key=lambda x: x["fail_count"],
            reverse=True,
        )
    def _top_denials(self, records: List[AuditRecordResponse]) -> List[Dict[str, Any]]:
        reasons: Dict[str, int] = {}
        for rec in records:
            if rec.workflow_type == WorkflowType.CLAIMS_ADJUDICATION.value:
                for decision in rec.decisions:
                    if decision.get("status") == "DENIED":
                        reasons["CLAIM_DENIED"] = reasons.get("CLAIM_DENIED", 0) + 1
            if rec.workflow_type == WorkflowType.PRIOR_AUTHORIZATION.value:
                for decision in rec.decisions:
                    if decision.get("status") == "DENIED":
                        reasons["AUTH_DENIED"] = reasons.get("AUTH_DENIED", 0) + 1
        return sorted(
            ({"reason": name, "count": count} for name, count in reasons.items()),
            key=lambda x: x["count"],
            reverse=True,
        )
    def _executive_summary(
        self,
        start: datetime,
        end: datetime,
        total: int,
        compliance_rate: float,
        avg_confidence: float,
        escalation_rate: float,
    ) -> str:
        return (
            f"During the period {start.date()} to {end.date()}, MEDI-COMPLY processed {total} healthcare operations "
            f"transactions. The overall compliance rate was {compliance_rate}% with an average confidence score of "
            f"{avg_confidence}. {int(round(escalation_rate/100*total)) if total else 0} decisions required human "
            f"escalation ({escalation_rate}%)."
        )
    def _recommendations(
        self,
        records: List[AuditRecordResponse],
        escalation_rate: float,
        avg_confidence: float,
        top_failures: List[Dict[str, Any]],
    ) -> List[str]:
        recs = []
        if escalation_rate > 10:
            recs.append("Review escalation threshold settings to reduce manual interventions.")
        if avg_confidence < 0.9:
            recs.append("Consider additional training data for low-confidence areas.")
        if top_failures:
            recs.append(f"Address recurring {top_failures[0]['check_name']} failures.")
        recs.append("Continue monitoring compliance trends.")
        recs.append("Schedule quarterly audit review meeting.")
        return recs
    def _format_report(
        self,
        start: datetime,
        end: datetime,
        total: int,
        compliance_rate: float,
        escalation_rate: float,
        avg_confidence: float,
        avg_pt: int,
        workflow_details: Dict[str, Dict[str, Any]],
        guardrail_summary: List[Dict[str, Any]],
        recommendations: List[str],
    ) -> str:
        wf_lines = []
        for wf, details in workflow_details.items():
            wf_lines.append(
                f"- {wf}: total={details['total']}, avg_confidence={details['avg_confidence']}, "
                f"avg_processing_ms={details['avg_processing_time_ms']}, escalations={details['escalations']}"
            )
        guard_lines = [
            f"- {g['check_name']}: pass_rate={g['pass_rate']}%, total={g['total_evaluated']}, fails={g['fail_count']}"
            for g in guardrail_summary
        ]
        rec_lines = [f"- {rec}" for rec in recommendations]
        return (
            f"COMPLIANCE REPORT {start.date()} to {end.date()}\n"
            f"Total: {total} decisions\n"
            f"Compliance Rate: {compliance_rate}% | Escalation Rate: {escalation_rate}% | "
            f"Avg Confidence: {avg_confidence} | Avg Processing: {avg_pt} ms\n\n"
            "Workflow Details:\n" + "\n".join(wf_lines) + "\n\n"
            "Guardrail Summary:\n" + ("\n".join(guard_lines) or "- None") + "\n\n"
            "Recommendations:\n" + "\n".join(rec_lines)
        )
# Module store
_store = AuditComplianceStore()
# Routes - Audit
@audit_router.get(
    "/{audit_id}",
    response_model=AuditRecordResponse,
    summary="Retrieve audit record",
    description="Returns the complete audit trail for a specific decision.",
)
async def get_audit_record(audit_id: str = Path(..., description="Audit trail identifier")):
    record = _store.get_record(audit_id)
    if not record:
        raise HTTPException(404, f"Audit record '{audit_id}' not found")
    return record
@audit_router.get(
    "/{audit_id}/explain",
    response_model=AuditExplanationResponse,
    summary="Get human-readable explanation",
    description="Returns a human-readable explanation of a decision with evidence citations and reasoning.",
)
async def explain_audit_record(audit_id: str = Path(...)):
    explanation = _store.generate_explanation(audit_id)
    if not explanation:
        raise HTTPException(404, f"Audit record '{audit_id}' not found")
    return explanation
@audit_router.post(
    "/search",
    response_model=AuditSearchResponse,
    summary="Search audit records",
    description="Search and filter audit records by workflow type, date range, risk level, and more.",
)
async def search_audit_records_post(request: AuditSearchRequest):
    return _store.search_records(request)
@audit_router.get(
    "/",
    response_model=AuditSearchResponse,
    summary="Search audit records",
    description="Search and filter audit records by workflow type, date range, risk level, and more.",
)
async def search_audit_records_get(
    workflow_type: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None),
    min_risk_score: Optional[float] = Query(None, ge=0, le=1),
    max_risk_score: Optional[float] = Query(None, ge=0, le=1),
    escalation_triggered: Optional[bool] = Query(None),
    human_review_required: Optional[bool] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query("timestamp"),
    sort_order: str = Query("desc"),
):
    request = AuditSearchRequest(
        workflow_type=WorkflowType(workflow_type) if workflow_type else None,
        date_from=date_from,
        date_to=date_to,
        risk_level=RiskLevel(risk_level) if risk_level else None,
        min_risk_score=min_risk_score,
        max_risk_score=max_risk_score,
        escalation_triggered=escalation_triggered,
        human_review_required=human_review_required,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return _store.search_records(request)
# Routes - Compliance
@compliance_router.get(
    "/dashboard",
    response_model=ComplianceDashboardResponse,
    summary="Compliance dashboard",
    description="Returns real-time compliance metrics, risk distribution, and trend data.",
)
async def get_compliance_dashboard(
    period_days: int = Query(30, ge=1, le=365, description="Number of days to include")
):
    return _store.generate_dashboard(period_days)
@compliance_router.get(
    "/report",
    response_model=ComplianceReportResponse,
    summary="Generate compliance report",
    description="Generates a detailed compliance report for a specified period.",
)
async def get_compliance_report(
    period: str = Query("monthly", description="Report period: weekly, monthly, quarterly"),
    period_start: Optional[str] = Query(None, description="Custom start date (YYYY-MM-DD)"),
    period_end: Optional[str] = Query(None, description="Custom end date (YYYY-MM-DD)"),
):
    return _store.generate_report(period, period_start, period_end)
