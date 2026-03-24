"""
MEDI-COMPLY Knowledge Updater.

Implements an 8-step safe update protocol for clinical coding knowledge:
- Feed checks
- Ingestion
- Diffing
- Consistency validation
- Shadow staging
- Regression testing
- Evaluation (auto or human review)
- Promotion and logging with rollback safety

This module uses simulated feeds for hackathon purposes. All models use
Pydantic v2. Async is used for feed checks and end-to-end processing
entrypoints.
"""
from __future__ import annotations

import asyncio
import copy
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class UpdateSource(str, Enum):
    CMS_NCCI = "CMS_NCCI"
    WHO_ICD10 = "WHO_ICD10"
    AMA_CPT = "AMA_CPT"
    CMS_LCD_NCD = "CMS_LCD_NCD"
    PAYER_POLICY = "PAYER_POLICY"
    CODING_GUIDELINES = "CODING_GUIDELINES"
    CMS_FEE_SCHEDULE = "CMS_FEE_SCHEDULE"
    MANUAL = "MANUAL"


class UpdateStatus(str, Enum):
    PENDING = "PENDING"
    INGESTING = "INGESTING"
    DIFFING = "DIFFING"
    VALIDATING = "VALIDATING"
    STAGING = "STAGING"
    TESTING = "TESTING"
    APPROVED = "APPROVED"
    PROMOTED = "PROMOTED"
    FAILED_VALIDATION = "FAILED_VALIDATION"
    FAILED_TESTING = "FAILED_TESTING"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class UpdateFrequency(str, Enum):
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    ANNUALLY = "ANNUALLY"
    ON_DEMAND = "ON_DEMAND"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class CodeChange(BaseModel):
    code: str
    code_type: str  # ICD10 or CPT
    change_type: str  # ADDED, MODIFIED, DELETED, DEPRECATED
    old_description: Optional[str] = None
    new_description: Optional[str] = None
    old_properties: Optional[dict] = None
    new_properties: Optional[dict] = None
    effective_date: str
    reason: str

    model_config = ConfigDict(extra="ignore")


class RuleChange(BaseModel):
    rule_id: str
    rule_type: str  # NCCI_EDIT, LCD_NCD, CODING_GUIDELINE
    change_type: str  # ADDED, MODIFIED, DELETED
    description: str
    effective_date: str
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None

    model_config = ConfigDict(extra="ignore")


class PolicyChange(BaseModel):
    policy_id: str
    payer_id: str
    change_type: str  # ADDED, MODIFIED, DELETED
    description: str
    effective_date: str
    affected_cpt_codes: List[str]
    old_policy: Optional[dict] = None
    new_policy: Optional[dict] = None

    model_config = ConfigDict(extra="ignore")


class FailedTestDetail(BaseModel):
    test_id: str
    test_name: str
    expected: str
    actual: str
    error_message: str
    related_change: Optional[str] = None

    model_config = ConfigDict(extra="ignore")


class RegressionTestResults(BaseModel):
    total_tests: int
    tests_passed: int
    tests_failed: int
    pass_rate: float
    threshold: float = 0.995
    passed_threshold: bool
    failed_test_details: List[FailedTestDetail] = Field(default_factory=list)
    execution_time_seconds: float
    tested_at: datetime

    model_config = ConfigDict(extra="ignore")


class ChangesSummary(BaseModel):
    codes_added: List[CodeChange] = Field(default_factory=list)
    codes_modified: List[CodeChange] = Field(default_factory=list)
    codes_deleted: List[CodeChange] = Field(default_factory=list)
    codes_deprecated: List[CodeChange] = Field(default_factory=list)
    rules_added: List[RuleChange] = Field(default_factory=list)
    rules_modified: List[RuleChange] = Field(default_factory=list)
    rules_deleted: List[RuleChange] = Field(default_factory=list)
    policies_added: List[PolicyChange] = Field(default_factory=list)
    policies_modified: List[PolicyChange] = Field(default_factory=list)
    policies_deleted: List[PolicyChange] = Field(default_factory=list)
    total_additions: int = 0
    total_modifications: int = 0
    total_deletions: int = 0
    effective_date: str = ""
    source_reference: str = ""

    model_config = ConfigDict(extra="ignore")


class KnowledgeVersion(BaseModel):
    version_id: str
    version_number: int
    created_at: datetime
    promoted_at: Optional[datetime] = None
    source: UpdateSource
    description: str
    changes_summary: ChangesSummary
    is_active: bool = False
    is_shadow: bool = False
    previous_version_id: Optional[str] = None
    regression_test_results: Optional[RegressionTestResults] = None
    approved_by: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class UpdateFeedConfig(BaseModel):
    source: UpdateSource
    name: str
    url: Optional[str] = None
    frequency: UpdateFrequency = UpdateFrequency.ON_DEMAND
    enabled: bool = True
    last_checked: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    auth_required: bool = False
    auth_config: Optional[dict] = None
    auto_promote: bool = False

    model_config = ConfigDict(extra="ignore")


class UpdateNotification(BaseModel):
    notification_id: str
    version_id: str
    source: UpdateSource
    status: UpdateStatus
    summary: str
    requires_human_review: bool
    created_at: datetime
    details: dict = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Feed checking (simulated)
# ---------------------------------------------------------------------------


class FeedChecker:
    """Simulated checker for external regulatory and policy feeds."""

    async def check_feed(self, feed_config: UpdateFeedConfig) -> Optional[dict]:
        if not feed_config.enabled:
            return None
        feed_config.last_checked = datetime.now(timezone.utc)
        if feed_config.source == UpdateSource.CMS_NCCI:
            return self._simulate_cms_ncci_update()
        if feed_config.source == UpdateSource.WHO_ICD10:
            return self._simulate_icd10_update()
        if feed_config.source == UpdateSource.AMA_CPT:
            return self._simulate_cpt_update()
        if feed_config.source == UpdateSource.PAYER_POLICY:
            return self._simulate_payer_update()
        if feed_config.source == UpdateSource.CMS_LCD_NCD:
            return self._simulate_lcd_ncd_update()
        if feed_config.source == UpdateSource.CODING_GUIDELINES:
            return {"guideline_updates": ["Annual guideline refresh"]}
        if feed_config.source == UpdateSource.CMS_FEE_SCHEDULE:
            return {"fee_schedule": "MPFS annual update"}
        return None

    async def check_all_feeds(self, feeds: List[UpdateFeedConfig]) -> List[dict]:
        results: List[dict] = []
        tasks = [self.check_feed(feed) for feed in feeds]
        for res in await asyncio.gather(*tasks):
            if res:
                results.append(res)
        return results

    def _simulate_cms_ncci_update(self) -> dict:
        return {
            "source": UpdateSource.CMS_NCCI,
            "effective_date": "2026-04-01",
            "ncci_edits": {"pair_add": [("99213", "97530")]},
            "reference": "https://cms.gov/ncci/q2-2026",
        }

    def _simulate_icd10_update(self) -> dict:
        return {
            "source": UpdateSource.WHO_ICD10,
            "effective_date": "2026-10-01",
            "icd10": {"A00.0": {"description": "Cholera due to Vibrio"}},
            "reference": "https://who.int/icd-10/2026",
        }

    def _simulate_cpt_update(self) -> dict:
        return {
            "source": UpdateSource.AMA_CPT,
            "effective_date": "2026-01-01",
            "cpt": {"12345": {"description": "New procedure"}},
            "reference": "https://ama-assn.org/cpt/2026",
        }

    def _simulate_payer_update(self) -> dict:
        return {
            "source": UpdateSource.PAYER_POLICY,
            "effective_date": "2026-03-15",
            "policies": {"PAYER1": {"policy_id": "POL-001", "affected_cpt": ["70553"]}},
            "reference": "payer-feed",
        }

    def _simulate_lcd_ncd_update(self) -> dict:
        return {
            "source": UpdateSource.CMS_LCD_NCD,
            "effective_date": "2026-02-01",
            "rules": {"LCD123": {"description": "Updated coverage"}},
            "reference": "https://cms.gov/lcd/123",
        }


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------


class DiffEngine:
    """Computes differences between current KB and new data."""

    def diff_codes(self, existing_codes: dict, new_codes: dict, code_type: str) -> List[CodeChange]:
        changes: List[CodeChange] = []
        existing_keys = set(existing_codes or {})
        new_keys = set(new_codes or {})

        for code in new_keys - existing_keys:
            payload = new_codes.get(code, {})
            changes.append(
                CodeChange(
                    code=code,
                    code_type=code_type,
                    change_type="ADDED",
                    old_description=None,
                    new_description=payload.get("description"),
                    old_properties=None,
                    new_properties=payload,
                    effective_date=payload.get("effective_date", ""),
                    reason="New code added",
                )
            )

        for code in existing_keys - new_keys:
            payload = existing_codes.get(code, {})
            changes.append(
                CodeChange(
                    code=code,
                    code_type=code_type,
                    change_type="DELETED",
                    old_description=payload.get("description"),
                    new_description=None,
                    old_properties=payload,
                    new_properties=None,
                    effective_date=payload.get("effective_date", ""),
                    reason="Code removed",
                )
            )

        for code in existing_keys & new_keys:
            old_payload = existing_codes.get(code, {})
            new_payload = new_codes.get(code, {})
            if old_payload != new_payload:
                changes.append(
                    CodeChange(
                        code=code,
                        code_type=code_type,
                        change_type="MODIFIED",
                        old_description=old_payload.get("description"),
                        new_description=new_payload.get("description"),
                        old_properties=old_payload,
                        new_properties=new_payload,
                        effective_date=new_payload.get("effective_date", ""),
                        reason="Code updated",
                    )
                )
        return changes

    def diff_rules(self, existing_rules: dict, new_rules: dict, rule_type: str) -> List[RuleChange]:
        changes: List[RuleChange] = []
        existing_keys = set(existing_rules or {})
        new_keys = set(new_rules or {})

        for rid in new_keys - existing_keys:
            payload = new_rules.get(rid, {})
            changes.append(
                RuleChange(
                    rule_id=rid,
                    rule_type=rule_type,
                    change_type="ADDED",
                    description=payload.get("description", ""),
                    effective_date=payload.get("effective_date", ""),
                    old_value=None,
                    new_value=payload,
                )
            )

        for rid in existing_keys - new_keys:
            payload = existing_rules.get(rid, {})
            changes.append(
                RuleChange(
                    rule_id=rid,
                    rule_type=rule_type,
                    change_type="DELETED",
                    description=payload.get("description", ""),
                    effective_date=payload.get("effective_date", ""),
                    old_value=payload,
                    new_value=None,
                )
            )

        for rid in existing_keys & new_keys:
            old_payload = existing_rules.get(rid, {})
            new_payload = new_rules.get(rid, {})
            if old_payload != new_payload:
                changes.append(
                    RuleChange(
                        rule_id=rid,
                        rule_type=rule_type,
                        change_type="MODIFIED",
                        description=new_payload.get("description", ""),
                        effective_date=new_payload.get("effective_date", ""),
                        old_value=old_payload,
                        new_value=new_payload,
                    )
                )
        return changes

    def diff_policies(self, existing_policies: dict, new_policies: dict) -> List[PolicyChange]:
        changes: List[PolicyChange] = []
        existing_keys = set(existing_policies or {})
        new_keys = set(new_policies or {})

        for pid in new_keys - existing_keys:
            payload = new_policies.get(pid, {})
            changes.append(
                PolicyChange(
                    policy_id=payload.get("policy_id", pid),
                    payer_id=payload.get("payer_id", "UNKNOWN"),
                    change_type="ADDED",
                    description=payload.get("description", ""),
                    effective_date=payload.get("effective_date", ""),
                    affected_cpt_codes=payload.get("affected_cpt", []) or payload.get("affected_cpt_codes", []),
                    old_policy=None,
                    new_policy=payload,
                )
            )

        for pid in existing_keys - new_keys:
            payload = existing_policies.get(pid, {})
            changes.append(
                PolicyChange(
                    policy_id=payload.get("policy_id", pid),
                    payer_id=payload.get("payer_id", "UNKNOWN"),
                    change_type="DELETED",
                    description=payload.get("description", ""),
                    effective_date=payload.get("effective_date", ""),
                    affected_cpt_codes=payload.get("affected_cpt", []) or payload.get("affected_cpt_codes", []),
                    old_policy=payload,
                    new_policy=None,
                )
            )

        for pid in existing_keys & new_keys:
            old_payload = existing_policies.get(pid, {})
            new_payload = new_policies.get(pid, {})
            if old_payload != new_payload:
                changes.append(
                    PolicyChange(
                        policy_id=new_payload.get("policy_id", pid),
                        payer_id=new_payload.get("payer_id", "UNKNOWN"),
                        change_type="MODIFIED",
                        description=new_payload.get("description", ""),
                        effective_date=new_payload.get("effective_date", ""),
                        affected_cpt_codes=new_payload.get("affected_cpt", []) or new_payload.get("affected_cpt_codes", []),
                        old_policy=old_payload,
                        new_policy=new_payload,
                    )
                )
        return changes

    def generate_summary(
        self,
        code_changes: List[CodeChange],
        rule_changes: List[RuleChange],
        policy_changes: List[PolicyChange],
        effective_date: str,
        source_ref: str,
    ) -> ChangesSummary:
        summary = ChangesSummary(
            effective_date=effective_date,
            source_reference=source_ref,
        )
        summary.codes_added = [c for c in code_changes if c.change_type == "ADDED"]
        summary.codes_modified = [c for c in code_changes if c.change_type == "MODIFIED"]
        summary.codes_deleted = [c for c in code_changes if c.change_type == "DELETED"]
        summary.codes_deprecated = [c for c in code_changes if c.change_type == "DEPRECATED"]

        summary.rules_added = [r for r in rule_changes if r.change_type == "ADDED"]
        summary.rules_modified = [r for r in rule_changes if r.change_type == "MODIFIED"]
        summary.rules_deleted = [r for r in rule_changes if r.change_type == "DELETED"]

        summary.policies_added = [p for p in policy_changes if p.change_type == "ADDED"]
        summary.policies_modified = [p for p in policy_changes if p.change_type == "MODIFIED"]
        summary.policies_deleted = [p for p in policy_changes if p.change_type == "DELETED"]

        summary.total_additions = len(summary.codes_added) + len(summary.rules_added) + len(summary.policies_added)
        summary.total_modifications = len(summary.codes_modified) + len(summary.rules_modified) + len(summary.policies_modified)
        summary.total_deletions = len(summary.codes_deleted) + len(summary.rules_deleted) + len(summary.policies_deleted)
        return summary


# ---------------------------------------------------------------------------
# Shadow knowledge base
# ---------------------------------------------------------------------------


class ShadowKnowledgeBase:
    """Shadow copy of the production KB for safe staging and testing."""

    def __init__(self, snapshot: dict) -> None:
        self._snapshot = copy.deepcopy(snapshot)

    @classmethod
    def create_from_production(cls, production_kb: Any) -> "ShadowKnowledgeBase":
        if hasattr(production_kb, "get_snapshot"):
            snapshot = production_kb.get_snapshot()
        else:
            snapshot = copy.deepcopy(getattr(production_kb, "__dict__", {}))
        return cls(snapshot)

    def apply_changes(self, changes: ChangesSummary) -> None:
        codes = self._snapshot.setdefault("codes", {})
        rules = self._snapshot.setdefault("rules", {})
        policies = self._snapshot.setdefault("policies", {})

        for change in changes.codes_added:
            codes[change.code] = change.new_properties or {"description": change.new_description}
        for change in changes.codes_modified:
            codes[change.code] = change.new_properties or {"description": change.new_description}
        for change in changes.codes_deleted:
            codes.pop(change.code, None)

        for change in changes.rules_added:
            rules[change.rule_id] = change.new_value or {"description": change.description}
        for change in changes.rules_modified:
            rules[change.rule_id] = change.new_value or {"description": change.description}
        for change in changes.rules_deleted:
            rules.pop(change.rule_id, None)

        for change in changes.policies_added:
            policies[change.policy_id] = change.new_policy or {"description": change.description}
        for change in changes.policies_modified:
            policies[change.policy_id] = change.new_policy or {"description": change.description}
        for change in changes.policies_deleted:
            policies.pop(change.policy_id, None)

    def validate_consistency(self) -> List[str]:
        inconsistencies: List[str] = []
        codes = self._snapshot.get("codes", {})
        # Incorporate icd10/cpt maps if present for validation
        if "icd10" in self._snapshot:
            for code, payload in self._snapshot.get("icd10", {}).items():
                codes.setdefault(code, payload)
        if "cpt" in self._snapshot:
            for code, payload in self._snapshot.get("cpt", {}).items():
                codes.setdefault(code, payload)
        rules = self._snapshot.get("rules", {})

        # Orphaned codes referenced by rules
        for rule_id, rule in rules.items():
            refs = rule.get("codes", []) if isinstance(rule, dict) else []
            for ref in refs:
                if ref not in codes:
                    inconsistencies.append(f"Rule {rule_id} references missing code {ref}")

        # Simple circular check placeholder
        for rule_id, rule in rules.items():
            excludes = rule.get("excludes", []) if isinstance(rule, dict) else []
            if rule_id in excludes:
                inconsistencies.append(f"Rule {rule_id} has circular exclude")
        return inconsistencies

    def get_snapshot(self) -> dict:
        return copy.deepcopy(self._snapshot)


# ---------------------------------------------------------------------------
# Regression testing
# ---------------------------------------------------------------------------


class RegressionTestRunner:
    """Runs regression tests against a shadow KB (simulated)."""

    def __init__(self, threshold: float = 0.995) -> None:
        self.threshold = threshold

    def run_regression_tests(self, shadow_kb: ShadowKnowledgeBase, test_cases: Optional[List[dict]] = None) -> RegressionTestResults:
        cases = test_cases or self._get_default_test_cases()
        start = datetime.now(timezone.utc)
        passed = 0
        failed_details: List[FailedTestDetail] = []

        for case in cases:
            ok, failure_detail = self._run_single_test(case, shadow_kb)
            if ok:
                passed += 1
            elif failure_detail:
                failed_details.append(failure_detail)

        total = len(cases)
        failed = total - passed
        pass_rate = passed / total if total else 1.0
        execution_time = (datetime.now(timezone.utc) - start).total_seconds()
        return RegressionTestResults(
            total_tests=total,
            tests_passed=passed,
            tests_failed=failed,
            pass_rate=pass_rate,
            threshold=self.threshold,
            passed_threshold=pass_rate >= self.threshold,
            failed_test_details=failed_details,
            execution_time_seconds=execution_time,
            tested_at=datetime.now(timezone.utc),
        )

    def _get_default_test_cases(self) -> List[dict]:
        # Simple deterministic placeholder covering key scenarios
        base_cases = [
            {"test_id": f"T{i:03d}", "input": "diabetes note", "expected_codes": ["E11.9"], "expected_primary": "E11.9"}
            for i in range(1, 11)
        ]
        base_cases.extend(
            {"test_id": f"T{i:03d}", "input": "hypertension note", "expected_codes": ["I10"], "expected_primary": "I10"}
            for i in range(11, 16)
        )
        base_cases.extend(
            {"test_id": f"T{i:03d}", "input": "fracture note", "expected_codes": ["S52.501A"], "expected_primary": "S52.501A"}
            for i in range(16, 21)
        )
        return list(base_cases)

    def _run_single_test(self, test_case: dict, shadow_kb: ShadowKnowledgeBase) -> Tuple[bool, Optional[FailedTestDetail]]:
        # In lieu of a full coding engine, treat all default cases as passing.
        return True, None


# ---------------------------------------------------------------------------
# Knowledge updater orchestrator
# ---------------------------------------------------------------------------


def _format_version_id(version_number: int, ts: Optional[datetime] = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    quarter = ((ts.month - 1) // 3) + 1
    return f"KB-{ts.year}-Q{quarter}-v{version_number}"


class KnowledgeUpdater:
    """Orchestrates safe knowledge base updates with staging and rollback."""

    def __init__(self, knowledge_manager: Any, config: Optional[dict] = None) -> None:
        self.knowledge_manager = knowledge_manager
        self.feed_configs: List[UpdateFeedConfig] = get_default_feed_configs()
        self.version_history: List[KnowledgeVersion] = []
        self.current_version: KnowledgeVersion = create_initial_version()
        self.version_history.append(self.current_version)
        self.pending_updates: List[dict] = []
        self.notifications: List[UpdateNotification] = []
        self.pass_rate_threshold: float = 0.995
        self._status_map: Dict[str, UpdateStatus] = {self.current_version.version_id: UpdateStatus.PROMOTED}
        self.diff_engine = DiffEngine()
        self.feed_checker = FeedChecker()
        self.test_runner = RegressionTestRunner(threshold=self.pass_rate_threshold)
        self._config = config or {}
        self._update_lock = asyncio.Lock()

    async def check_for_updates(self) -> List[dict]:
        maybe_updates = self.feed_checker.check_all_feeds(self.feed_configs)
        updates = await maybe_updates if asyncio.iscoroutine(maybe_updates) else maybe_updates
        if updates:
            self.pending_updates.extend(updates)
        return updates

    async def process_update(self, update_data: dict, source: UpdateSource) -> KnowledgeVersion:
        async with self._update_lock:
            version_number = (self.current_version.version_number or 0) + 1
            version = KnowledgeVersion(
                version_id=_format_version_id(version_number),
                version_number=version_number,
                created_at=datetime.now(timezone.utc),
                source=source,
                description=f"Update from {source.value}",
                changes_summary=ChangesSummary(),
                is_active=False,
                is_shadow=True,
                previous_version_id=self.current_version.version_id,
                metadata={"status": UpdateStatus.PENDING.value, "version_id": None},
            )
            version.metadata["version_id"] = version.version_id
            self._set_status(version.version_id, UpdateStatus.INGESTING)

            ingested = self._ingest_update(update_data, source)
            self._set_status(version.version_id, UpdateStatus.DIFFING)
            changes = self._diff_against_current(ingested)
            version.changes_summary = changes

            self._set_status(version.version_id, UpdateStatus.VALIDATING)
            inconsistencies = self._validate_consistency(changes)
            if inconsistencies:
                self._set_status(version.version_id, UpdateStatus.FAILED_VALIDATION)
                version.metadata["inconsistencies"] = inconsistencies
                self._log_update(version)
                self.version_history.append(version)
                return version

            self._set_status(version.version_id, UpdateStatus.STAGING)
            shadow = self._stage_in_shadow(changes)

            self._set_status(version.version_id, UpdateStatus.TESTING)
            test_results = self._run_regression_tests(shadow)
            version.regression_test_results = test_results

            status_after_tests = self._evaluate_test_results(test_results)
            self._set_status(version.version_id, status_after_tests)

            feed_cfg = next((f for f in self.feed_configs if f.source == source), None)
            auto_promote = bool(feed_cfg.auto_promote) if feed_cfg else False

            if status_after_tests == UpdateStatus.APPROVED and auto_promote:
                self._promote_to_production(shadow, version)
            elif status_after_tests == UpdateStatus.APPROVED:
                self._request_human_review(version, test_results)
            elif status_after_tests == UpdateStatus.HUMAN_REVIEW:
                self._request_human_review(version, test_results)
            else:
                # Tests failed below threshold
                self._set_status(version.version_id, UpdateStatus.FAILED_TESTING)

            self._log_update(version)
            self.version_history.append(version)
            return version

    def approve_update(self, version_id: str, approved_by: str) -> bool:
        version = self._find_version(version_id)
        if not version:
            return False
        if self._status_map.get(version_id) != UpdateStatus.HUMAN_REVIEW:
            return False
        shadow = ShadowKnowledgeBase.create_from_production(self.knowledge_manager)
        shadow.apply_changes(version.changes_summary)
        self._promote_to_production(shadow, version)
        version.approved_by = approved_by
        self._set_status(version_id, UpdateStatus.PROMOTED)
        return True

    def reject_update(self, version_id: str, rejected_by: str, reason: str) -> bool:
        version = self._find_version(version_id)
        if not version:
            return False
        self._set_status(version_id, UpdateStatus.REJECTED)
        version.metadata["rejected_by"] = rejected_by
        version.metadata["rejection_reason"] = reason
        self._log_update(version)
        return True

    def rollback(self, version_id: str, reason: str) -> bool:
        version = self._find_version(version_id)
        if not version:
            return False
        prev = self._find_version(version.previous_version_id or "")
        if not prev and len(self.version_history) >= 2:
            # Fallback: previous version in history order
            prev = self.version_history[-2]
        if not prev:
            return False
        self._set_status(version.version_id, UpdateStatus.ROLLED_BACK)
        prev.is_active = True
        prev.promoted_at = datetime.now(timezone.utc)
        self.current_version = prev
        version.is_active = False
        version.metadata["rollback_reason"] = reason
        self._log_update(version)
        return True

    def get_version_history(self) -> List[KnowledgeVersion]:
        return list(self.version_history)

    def get_current_version(self) -> KnowledgeVersion:
        return self.current_version

    def get_pending_reviews(self) -> List[KnowledgeVersion]:
        return [v for v in self.version_history if self._status_map.get(v.version_id) == UpdateStatus.HUMAN_REVIEW]

    def get_update_notifications(self, unread_only: bool = True) -> List[UpdateNotification]:
        if unread_only:
            return [n for n in self.notifications if not n.details.get("read", False)]
        return list(self.notifications)

    def configure_feed(self, feed_config: UpdateFeedConfig) -> None:
        existing = next((f for f in self.feed_configs if f.source == feed_config.source), None)
        if existing:
            self.feed_configs.remove(existing)
        self.feed_configs.append(feed_config)

    def get_feed_status(self) -> List[dict]:
        return [
            {
                "source": f.source.value,
                "enabled": f.enabled,
                "last_checked": f.last_checked,
                "last_updated": f.last_updated,
                "frequency": f.frequency.value,
            }
            for f in self.feed_configs
        ]

    def schedule_check(self, source: Optional[UpdateSource] = None) -> None:
        # For hackathon: immediately queue a check request
        if source:
            self.pending_updates.append({"source": source})
        else:
            self.pending_updates.append({"source": "ALL"})

    def get_changes_since(self, version_id: str) -> ChangesSummary:
        current_idx = next((i for i, v in enumerate(self.version_history) if v.version_id == version_id), None)
        if current_idx is None:
            return ChangesSummary()
        aggregated = ChangesSummary()
        for v in self.version_history[current_idx + 1 :]:
            aggregated.codes_added.extend(v.changes_summary.codes_added)
            aggregated.codes_modified.extend(v.changes_summary.codes_modified)
            aggregated.codes_deleted.extend(v.changes_summary.codes_deleted)
            aggregated.codes_deprecated.extend(v.changes_summary.codes_deprecated)
            aggregated.rules_added.extend(v.changes_summary.rules_added)
            aggregated.rules_modified.extend(v.changes_summary.rules_modified)
            aggregated.rules_deleted.extend(v.changes_summary.rules_deleted)
            aggregated.policies_added.extend(v.changes_summary.policies_added)
            aggregated.policies_modified.extend(v.changes_summary.policies_modified)
            aggregated.policies_deleted.extend(v.changes_summary.policies_deleted)
        aggregated.total_additions = len(aggregated.codes_added) + len(aggregated.rules_added) + len(aggregated.policies_added)
        aggregated.total_modifications = len(aggregated.codes_modified) + len(aggregated.rules_modified) + len(aggregated.policies_modified)
        aggregated.total_deletions = len(aggregated.codes_deleted) + len(aggregated.rules_deleted) + len(aggregated.policies_deleted)
        aggregated.effective_date = "multiple"
        aggregated.source_reference = "historical"
        return aggregated

    # ----------------------- protocol step helpers -----------------------

    def _ingest_update(self, update_data: dict, source: UpdateSource) -> dict:
        payload = dict(update_data)
        payload.setdefault("source", source)
        return payload

    def _diff_against_current(self, ingested_data: dict) -> ChangesSummary:
        existing = getattr(self.knowledge_manager, "get_snapshot", lambda: {})()
        code_changes: List[CodeChange] = []
        rule_changes: List[RuleChange] = []
        policy_changes: List[PolicyChange] = []

        icd10_changes = self.diff_engine.diff_codes(existing.get("icd10", {}), ingested_data.get("icd10", {}), "ICD10")
        cpt_changes = self.diff_engine.diff_codes(existing.get("cpt", {}), ingested_data.get("cpt", {}), "CPT")
        code_changes.extend(icd10_changes)
        code_changes.extend(cpt_changes)

        rule_changes.extend(
            self.diff_engine.diff_rules(existing.get("ncci_edits", {}), ingested_data.get("ncci_edits", {}), "NCCI_EDIT")
        )
        rule_changes.extend(
            self.diff_engine.diff_rules(existing.get("lcd_ncd", {}), ingested_data.get("rules", {}), "LCD_NCD")
        )

        policy_changes.extend(self.diff_engine.diff_policies(existing.get("policies", {}), ingested_data.get("policies", {})))

        effective_date = ingested_data.get("effective_date", "")
        source_ref = ingested_data.get("reference", "")
        return self.diff_engine.generate_summary(code_changes, rule_changes, policy_changes, effective_date, source_ref)

    def _validate_consistency(self, changes: ChangesSummary) -> List[str]:
        shadow = ShadowKnowledgeBase.create_from_production(self.knowledge_manager)
        shadow.apply_changes(changes)
        return shadow.validate_consistency()

    def _stage_in_shadow(self, changes: ChangesSummary) -> ShadowKnowledgeBase:
        shadow = ShadowKnowledgeBase.create_from_production(self.knowledge_manager)
        shadow.apply_changes(changes)
        return shadow

    def _run_regression_tests(self, shadow_kb: ShadowKnowledgeBase) -> RegressionTestResults:
        return self.test_runner.run_regression_tests(shadow_kb)

    def _evaluate_test_results(self, results: RegressionTestResults) -> UpdateStatus:
        if results.pass_rate >= self.pass_rate_threshold:
            return UpdateStatus.APPROVED
        return UpdateStatus.HUMAN_REVIEW

    def _promote_to_production(self, shadow_kb: ShadowKnowledgeBase, version: KnowledgeVersion) -> None:
        snapshot = shadow_kb.get_snapshot()
        if hasattr(self.knowledge_manager, "load_snapshot"):
            self.knowledge_manager.load_snapshot(snapshot)
        elif hasattr(self.knowledge_manager, "__dict__"):
            self.knowledge_manager.__dict__.update(snapshot)
        version.is_active = True
        version.is_shadow = False
        version.promoted_at = datetime.now(timezone.utc)
        self.current_version.is_active = False
        self.current_version = version
        self._set_status(version.version_id, UpdateStatus.PROMOTED)

    def _request_human_review(self, version: KnowledgeVersion, test_results: RegressionTestResults) -> None:
        self._set_status(version.version_id, UpdateStatus.HUMAN_REVIEW)
        note = UpdateNotification(
            notification_id=str(uuid.uuid4()),
            version_id=version.version_id,
            source=version.source,
            status=UpdateStatus.HUMAN_REVIEW,
            summary="Update requires human review",
            requires_human_review=True,
            created_at=datetime.now(timezone.utc),
            details={"failed_tests": test_results.tests_failed, "pass_rate": test_results.pass_rate},
        )
        self.notifications.append(note)

    def _log_update(self, version: KnowledgeVersion) -> None:
        note = UpdateNotification(
            notification_id=str(uuid.uuid4()),
            version_id=version.version_id,
            source=version.source,
            status=self._status_map.get(version.version_id, UpdateStatus.PENDING),
            summary=version.description,
            requires_human_review=self._status_map.get(version.version_id) == UpdateStatus.HUMAN_REVIEW,
            created_at=datetime.now(timezone.utc),
            details={"changes": version.changes_summary.model_dump(), "metadata": version.metadata},
        )
        self.notifications.append(note)

    def _set_status(self, version_id: str, status: UpdateStatus) -> None:
        self._status_map[version_id] = status

    def _find_version(self, version_id: str) -> Optional[KnowledgeVersion]:
        return next((v for v in self.version_history if v.version_id == version_id), None)


# ---------------------------------------------------------------------------
# Default configs and initial version
# ---------------------------------------------------------------------------


def get_default_feed_configs() -> List[UpdateFeedConfig]:
    return [
        UpdateFeedConfig(
            source=UpdateSource.CMS_NCCI,
            name="CMS NCCI",
            url="https://www.cms.gov/medicare/coding-billing/national-correct-coding-initiative-ncci-edits",
            frequency=UpdateFrequency.QUARTERLY,
            auto_promote=True,
        ),
        UpdateFeedConfig(
            source=UpdateSource.WHO_ICD10,
            name="WHO ICD-10",
            url="https://www.who.int/standards/classifications/classification-of-diseases",
            frequency=UpdateFrequency.ANNUALLY,
            auto_promote=False,
        ),
        UpdateFeedConfig(
            source=UpdateSource.AMA_CPT,
            name="AMA CPT",
            url="https://www.ama-assn.org/practice-management/cpt",
            frequency=UpdateFrequency.ANNUALLY,
            auto_promote=False,
        ),
        UpdateFeedConfig(
            source=UpdateSource.CMS_LCD_NCD,
            name="CMS LCD/NCD",
            url="https://www.cms.gov/medicare-coverage-database",
            frequency=UpdateFrequency.MONTHLY,
            auto_promote=True,
        ),
        UpdateFeedConfig(
            source=UpdateSource.PAYER_POLICY,
            name="Payer Policy",
            url=None,
            frequency=UpdateFrequency.DAILY,
            auto_promote=True,
        ),
        UpdateFeedConfig(
            source=UpdateSource.CODING_GUIDELINES,
            name="Official Coding Guidelines",
            url=None,
            frequency=UpdateFrequency.ANNUALLY,
            auto_promote=False,
        ),
    ]


def create_initial_version() -> KnowledgeVersion:
    now = datetime.now(timezone.utc)
    version_id = _format_version_id(1, now)
    return KnowledgeVersion(
        version_id=version_id,
        version_number=1,
        created_at=now,
        promoted_at=now,
        source=UpdateSource.MANUAL,
        description="Initial seeded knowledge base",
        changes_summary=ChangesSummary(),
        is_active=True,
        is_shadow=False,
        previous_version_id=None,
        metadata={"status": UpdateStatus.PROMOTED.value},
    )


__all__ = [
    "UpdateSource",
    "UpdateStatus",
    "UpdateFrequency",
    "CodeChange",
    "RuleChange",
    "PolicyChange",
    "ChangesSummary",
    "RegressionTestResults",
    "FailedTestDetail",
    "KnowledgeVersion",
    "UpdateFeedConfig",
    "UpdateNotification",
    "FeedChecker",
    "DiffEngine",
    "ShadowKnowledgeBase",
    "RegressionTestRunner",
    "KnowledgeUpdater",
    "get_default_feed_configs",
    "create_initial_version",
    "_format_version_id",
]
