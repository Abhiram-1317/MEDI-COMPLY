"""Top-level entry point for MEDI-COMPLY."""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Union

from medi_comply.agents.audit_trail_agent import AuditTrailAgent
from medi_comply.agents.orchestrator_agent import OrchestratorAgent
from medi_comply.audit.audit_models import AuditQuery, AuditQueryResult, WorkflowTrace
from medi_comply.audit.audit_store import AuditStore
from medi_comply.audit.query_engine import AuditQueryEngine
from medi_comply.core.config import Settings
from medi_comply.knowledge.knowledge_manager import KnowledgeManager
from medi_comply.result_models import MediComplyResult


class MediComplySystem:
    """High-level facade exposed to end users / API handlers."""

    def __init__(
        self,
        config: Optional[Settings] = None,
        llm_client: Any = None,
        db_path: str = "medi_comply_audit.db",
    ) -> None:
        self.config = config or Settings()
        self.llm_client = llm_client
        self.db_path = db_path

        self.knowledge_manager: Optional[KnowledgeManager] = None
        self.audit_store: Optional[AuditStore] = None
        self.audit_engine: Optional[AuditQueryEngine] = None
        self.orchestrator: Optional[OrchestratorAgent] = None
        self._initialized = False

    async def initialize(self) -> None:
        self.knowledge_manager = KnowledgeManager()
        await self._init_knowledge_manager()

        self.audit_store = AuditStore(self.db_path)
        audit_agent = AuditTrailAgent(self.audit_store, self.config)

        self.orchestrator = OrchestratorAgent(
            knowledge_manager=self.knowledge_manager,
            config=self.config,
            llm_client=self.llm_client,
            audit_agent=audit_agent,
        )
        self.audit_engine = AuditQueryEngine(self.audit_store)

        health = await self.health_check()
        if not health["is_healthy"]:
            raise SystemInitializationError(f"Health check failed: {health['issues']}")
        self._initialized = True

    async def process(
        self,
        clinical_note: Union[str, bytes, dict],
        patient_context: Optional[dict] = None,
        source_type: str = "auto",
        workflow_type: str = "MEDICAL_CODING",
    ) -> MediComplyResult:
        if not self._initialized or not self.orchestrator:
            raise SystemNotInitializedError("Call initialize() before processing")
        try:
            return await self.orchestrator.run_pipeline(
                clinical_document=clinical_note,
                source_type=source_type,
                patient_context=patient_context,
                workflow_type=workflow_type,
            )
        except Exception as exc:  # pragma: no cover - surfaced in tests
            raise ProcessingError(str(exc)) from exc

    async def process_batch(
        self,
        documents: List[dict],
        max_concurrent: int = 5,
    ) -> List[MediComplyResult]:
        if not self._initialized:
            raise SystemNotInitializedError("System not initialized")
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _process(doc: dict) -> MediComplyResult:
            async with semaphore:
                return await self.process(
                    clinical_note=doc.get("clinical_note", ""),
                    patient_context=doc.get("patient_context"),
                    source_type=doc.get("source_type", "auto"),
                    workflow_type=doc.get("workflow_type", "MEDICAL_CODING"),
                )

        tasks = [_process(doc) for doc in documents]
        return await asyncio.gather(*tasks)

    async def health_check(self) -> dict:
        checks: dict[str, dict[str, Any]] = {}
        issues: list[str] = []

        # Knowledge base readiness
        kb_check: dict[str, Any] = {"status": "OK", "icd10_codes": 0, "cpt_codes": 0}
        km = self.knowledge_manager
        if km and getattr(km, "is_initialized", False):
            counts = getattr(km, "code_count", {})
            kb_check["icd10_codes"] = counts.get("icd10", 0)
            kb_check["cpt_codes"] = counts.get("cpt", 0)
            if kb_check["icd10_codes"] == 0 or kb_check["cpt_codes"] == 0:
                kb_check["status"] = "WARNING"
                issues.append("Knowledge base counts incomplete")
        else:
            kb_check["status"] = "NOT_READY"
            issues.append("Knowledge base not initialized")
        checks["knowledge_base"] = kb_check

        # Vector store status
        vector_status = "NOT_READY"
        if km and getattr(km, "vector_store", None):
            vector_status = "OK" if km.vector_store.is_initialized else "DEGRADED"
            if vector_status != "OK":
                issues.append("Vector store unavailable")
        else:
            issues.append("Vector store not configured")
        checks["vector_store"] = {"status": vector_status}

        # Audit store readiness
        audit_status = "OK" if self.audit_store else "NOT_READY"
        if audit_status != "OK":
            issues.append("Audit store not configured")
        checks["audit_store"] = {"status": audit_status}

        # LLM client status
        checks["llm_client"] = {"status": "OK" if self.llm_client else "NOT_CONFIGURED"}

        # Agent orchestration readiness
        agents_check: dict[str, Any] = {"status": "NOT_READY", "agents_loaded": 0}
        orchestrator = self.orchestrator
        if orchestrator:
            agent_attrs = [
                "nlp_pipeline",
                "retrieval_agent",
                "coding_agent",
                "compliance_agent",
                "audit_agent",
                "escalation_agent",
            ]
            loaded = sum(1 for attr in agent_attrs if getattr(orchestrator, attr, None) is not None)
            agents_check["agents_loaded"] = loaded
            if loaded == len(agent_attrs):
                agents_check["status"] = "OK"
            elif loaded >= 4:
                agents_check["status"] = "WARNING"
                issues.append(f"Only {loaded} orchestrator agents loaded")
            else:
                issues.append("Insufficient orchestrator agents loaded")
        else:
            issues.append("Orchestrator not instantiated")
        checks["agents"] = agents_check

        acceptable_statuses = {"OK", "NOT_CONFIGURED"}
        is_healthy = all(entry.get("status") in acceptable_statuses for entry in checks.values()) and not issues
        return {
            "is_healthy": is_healthy,
            "checks": checks,
            "issues": issues,
            "system_version": "1.0.0",
        }

    async def get_audit_trail(self, trace_id: str) -> Optional[WorkflowTrace]:
        if not self.audit_store:
            raise SystemNotInitializedError("Audit store unavailable")
        return self.audit_store.retrieve(trace_id)

    async def search_audits(self, query: AuditQuery) -> AuditQueryResult:
        if not self.audit_engine:
            raise SystemNotInitializedError("Audit engine unavailable")
        return self.audit_engine.search(query)

    async def get_system_stats(self) -> dict:
        if not self.audit_store:
            raise SystemNotInitializedError("Audit store unavailable")
        return self.audit_store.get_statistics().model_dump()

    async def _init_knowledge_manager(self) -> None:
        if not self.knowledge_manager:
            return
        from medi_comply.knowledge.seed_data import seed_all_data

        seed_all_data(self.knowledge_manager)
        if self.knowledge_manager.vector_store:
            self.knowledge_manager.vector_store.initialize()
        self.knowledge_manager._initialized = True

    async def shutdown(self) -> None:
        self._initialized = False
        self.orchestrator = None
        self.audit_engine = None
        self.audit_store = None
        self.knowledge_manager = None


class SystemNotInitializedError(Exception):
    """Raised when system APIs are used before initialization."""


class SystemInitializationError(Exception):
    """Raised when initialization health checks fail."""


class ProcessingError(Exception):
    """Raised when unrecoverable errors occur during processing."""
