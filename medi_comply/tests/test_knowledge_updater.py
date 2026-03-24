import asyncio
import re
from datetime import datetime, timezone
from typing import Any

import pytest

from medi_comply.knowledge.knowledge_updater import (
    ChangesSummary,
    CodeChange,
    DiffEngine,
    FailedTestDetail,
    FeedChecker,
    KnowledgeUpdater,
    KnowledgeVersion,
    PolicyChange,
    RegressionTestResults,
    RuleChange,
    ShadowKnowledgeBase,
    UpdateFeedConfig,
    UpdateFrequency,
    UpdateNotification,
    UpdateSource,
    UpdateStatus,
    _format_version_id,
    create_initial_version,
    get_default_feed_configs,
)


class DummyKnowledgeManager:
    """Lightweight stand-in for KnowledgeManager with snapshot support."""

    def __init__(self, snapshot: dict | None = None) -> None:
        self._snapshot = snapshot or {"icd10": {}, "cpt": {}, "ncci_edits": {}, "rules": {}, "policies": {}}

    def get_snapshot(self) -> dict:
        return {k: dict(v) if isinstance(v, dict) else v for k, v in self._snapshot.items()}

    def load_snapshot(self, snapshot: dict) -> None:
        self._snapshot = {k: dict(v) if isinstance(v, dict) else v for k, v in snapshot.items()}


# ---------------------------------------------------------------------------
# KnowledgeVersion
# ---------------------------------------------------------------------------


def test_initial_version_created():
    version = create_initial_version()
    assert isinstance(version, KnowledgeVersion)
    assert version.version_number == 1
    assert version.version_id.startswith("KB-")


def test_version_id_format():
    version = create_initial_version()
    assert re.match(r"KB-\d{4}-Q[1-4]-v\d+", version.version_id)


def test_version_is_active():
    version = create_initial_version()
    assert version.is_active is True


def test_version_serialization():
    version = create_initial_version()
    dumped = version.model_dump()
    assert dumped["version_id"] == version.version_id
    assert "created_at" in dumped
    assert version.model_dump_json()


# ---------------------------------------------------------------------------
# ChangesSummary
# ---------------------------------------------------------------------------


def test_empty_changes_summary():
    summary = ChangesSummary()
    assert summary.total_additions == 0
    assert summary.total_modifications == 0
    assert summary.total_deletions == 0


def test_changes_summary_counts():
    summary = ChangesSummary(
        codes_added=[CodeChange(code="A", code_type="ICD10", change_type="ADDED", old_description=None, new_description="new", old_properties=None, new_properties={}, effective_date="", reason="add")],
        codes_modified=[CodeChange(code="B", code_type="ICD10", change_type="MODIFIED", old_description="old", new_description="new", old_properties={}, new_properties={}, effective_date="", reason="mod")],
        codes_deleted=[CodeChange(code="C", code_type="ICD10", change_type="DELETED", old_description="old", new_description=None, old_properties={}, new_properties=None, effective_date="", reason="del")],
    )
    summary.total_additions = len(summary.codes_added)
    summary.total_modifications = len(summary.codes_modified)
    summary.total_deletions = len(summary.codes_deleted)
    assert summary.total_additions == 1
    assert summary.total_modifications == 1
    assert summary.total_deletions == 1


def test_code_change_model():
    change = CodeChange(
        code="A00",
        code_type="ICD10",
        change_type="MODIFIED",
        old_description="old",
        new_description="new",
        old_properties={"desc": "old"},
        new_properties={"desc": "new"},
        effective_date="2026-01-01",
        reason="update",
    )
    assert change.old_description == "old"
    assert change.new_description == "new"
    assert change.old_properties is not None
    assert change.new_properties is not None
    assert change.old_properties["desc"] == "old"
    assert change.new_properties["desc"] == "new"


# ---------------------------------------------------------------------------
# DiffEngine
# ---------------------------------------------------------------------------


def test_diff_no_changes():
    engine = DiffEngine()
    existing = {"A00": {"description": "old"}}
    assert engine.diff_codes(existing, existing, "ICD10") == []


def test_diff_code_added():
    engine = DiffEngine()
    existing = {}
    new = {"A00": {"description": "new"}}
    changes = engine.diff_codes(existing, new, "ICD10")
    assert len(changes) == 1 and changes[0].change_type == "ADDED"


def test_diff_code_deleted():
    engine = DiffEngine()
    existing = {"A00": {"description": "old"}}
    new = {}
    changes = engine.diff_codes(existing, new, "ICD10")
    assert len(changes) == 1 and changes[0].change_type == "DELETED"


def test_diff_code_modified():
    engine = DiffEngine()
    existing = {"A00": {"description": "old"}}
    new = {"A00": {"description": "new"}}
    changes = engine.diff_codes(existing, new, "ICD10")
    assert len(changes) == 1 and changes[0].change_type == "MODIFIED"


def test_diff_multiple_changes():
    engine = DiffEngine()
    existing = {"A00": {"description": "old"}, "B00": {"description": "keep"}}
    new = {"A00": {"description": "new"}, "C00": {"description": "add"}}
    changes = engine.diff_codes(existing, new, "ICD10")
    kinds = {c.change_type for c in changes}
    assert {"ADDED", "MODIFIED", "DELETED"}.issubset(kinds)


def test_diff_rules():
    engine = DiffEngine()
    existing = {"R1": {"description": "old"}}
    new = {"R1": {"description": "new"}, "R2": {"description": "add"}}
    changes = engine.diff_rules(existing, new, "NCCI_EDIT")
    types = {c.change_type for c in changes}
    assert "ADDED" in types and "MODIFIED" in types


def test_diff_policies():
    engine = DiffEngine()
    existing = {"P1": {"policy_id": "P1", "description": "old"}}
    new = {"P1": {"policy_id": "P1", "description": "new"}, "P2": {"policy_id": "P2", "description": "add"}}
    changes = engine.diff_policies(existing, new)
    types = {c.change_type for c in changes}
    assert "ADDED" in types and "MODIFIED" in types


def test_generate_summary():
    engine = DiffEngine()
    codes = [
        CodeChange(code="A", code_type="ICD10", change_type="ADDED", old_description=None, new_description="n", old_properties=None, new_properties={}, effective_date="", reason=""),
        CodeChange(code="B", code_type="ICD10", change_type="DELETED", old_description="o", new_description=None, old_properties={}, new_properties=None, effective_date="", reason=""),
    ]
    rules = [RuleChange(rule_id="R1", rule_type="NCCI_EDIT", change_type="MODIFIED", description="d", effective_date="", old_value={}, new_value={})]
    policies = [PolicyChange(policy_id="P1", payer_id="X", change_type="ADDED", description="d", effective_date="", affected_cpt_codes=[], old_policy=None, new_policy={})]
    summary = engine.generate_summary(codes, rules, policies, "2026-01-01", "ref")
    assert summary.total_additions == 2  # code added + policy added
    assert summary.total_deletions == 1
    assert summary.total_modifications == 1


# ---------------------------------------------------------------------------
# FeedChecker
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_single_feed():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.CMS_NCCI, name="NCCI")
    result = await checker.check_feed(feed)
    assert result is not None


@pytest.mark.asyncio
async def test_check_all_feeds():
    checker = FeedChecker()
    feeds = [UpdateFeedConfig(source=UpdateSource.CMS_NCCI, name="NCCI"), UpdateFeedConfig(source=UpdateSource.AMA_CPT, name="CPT")]
    results = await checker.check_all_feeds(feeds)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_disabled_feed_skipped():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.CMS_NCCI, name="NCCI", enabled=False)
    result = await checker.check_feed(feed)
    assert result is None


@pytest.mark.asyncio
async def test_feed_returns_none_when_no_update():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.MANUAL, name="Manual")
    result = await checker.check_feed(feed)
    assert result is None


@pytest.mark.asyncio
async def test_simulated_ncci_update():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.CMS_NCCI, name="NCCI")
    result = await checker.check_feed(feed)
    assert result is not None
    assert result.get("ncci_edits")


@pytest.mark.asyncio
async def test_simulated_icd10_update():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.WHO_ICD10, name="ICD10")
    result = await checker.check_feed(feed)
    assert result is not None
    assert result.get("icd10")


@pytest.mark.asyncio
async def test_simulated_cpt_update():
    checker = FeedChecker()
    feed = UpdateFeedConfig(source=UpdateSource.AMA_CPT, name="CPT")
    result = await checker.check_feed(feed)
    assert result is not None
    assert result.get("cpt")


# ---------------------------------------------------------------------------
# ShadowKnowledgeBase
# ---------------------------------------------------------------------------


def test_create_from_production():
    km = DummyKnowledgeManager({"icd10": {"A00": {"description": "old"}}})
    shadow = ShadowKnowledgeBase.create_from_production(km)
    assert shadow.get_snapshot()["icd10"]["A00"]["description"] == "old"


def test_apply_changes():
    km = DummyKnowledgeManager({"icd10": {}})
    shadow = ShadowKnowledgeBase.create_from_production(km)
    summary = ChangesSummary(codes_added=[CodeChange(code="A00", code_type="ICD10", change_type="ADDED", old_description=None, new_description="new", old_properties=None, new_properties={"description": "new"}, effective_date="", reason="")])
    shadow.apply_changes(summary)
    assert shadow.get_snapshot()["codes"]["A00"]["description"] == "new"


def test_validate_consistency_clean():
    km = DummyKnowledgeManager({"icd10": {"A00": {"description": "d"}}, "rules": {"R1": {"codes": ["A00"]}}})
    shadow = ShadowKnowledgeBase.create_from_production(km)
    assert shadow.validate_consistency() == []


def test_validate_consistency_orphaned_code():
    km = DummyKnowledgeManager({"icd10": {}, "rules": {"R1": {"codes": ["MISSING"]}}})
    shadow = ShadowKnowledgeBase.create_from_production(km)
    issues = shadow.validate_consistency()
    assert any("missing code" in msg for msg in issues)


def test_get_snapshot():
    km = DummyKnowledgeManager({"icd10": {"A00": {"description": "d"}}})
    shadow = ShadowKnowledgeBase.create_from_production(km)
    snap = shadow.get_snapshot()
    assert snap["icd10"]["A00"]["description"] == "d"


# ---------------------------------------------------------------------------
# RegressionTestRunner
# ---------------------------------------------------------------------------


def test_default_test_cases_exist():
    from medi_comply.knowledge.knowledge_updater import RegressionTestRunner

    runner = RegressionTestRunner()
    cases = runner._get_default_test_cases()
    assert len(cases) >= 20


def test_run_regression_all_pass():
    from medi_comply.knowledge.knowledge_updater import RegressionTestRunner

    runner = RegressionTestRunner()
    shadow = ShadowKnowledgeBase({})
    results = runner.run_regression_tests(shadow)
    assert results.pass_rate == 1.0
    assert results.passed_threshold is True


def test_regression_results_structure():
    from medi_comply.knowledge.knowledge_updater import RegressionTestRunner

    runner = RegressionTestRunner()
    shadow = ShadowKnowledgeBase({})
    results = runner.run_regression_tests(shadow)
    assert results.total_tests >= 20
    assert results.tests_failed == 0
    assert isinstance(results.tested_at, datetime)


def test_failed_test_has_details(monkeypatch):
    from medi_comply.knowledge.knowledge_updater import RegressionTestRunner

    runner = RegressionTestRunner()

    def fail_once(case: dict, shadow: ShadowKnowledgeBase):
        return False, FailedTestDetail(test_id=case.get("test_id", "T"), test_name="fail", expected="x", actual="y", error_message="e")

    monkeypatch.setattr(runner, "_run_single_test", fail_once)
    results = runner.run_regression_tests(ShadowKnowledgeBase({}), test_cases=[{"test_id": "T1"}])
    assert results.tests_failed == 1
    assert results.failed_test_details


# ---------------------------------------------------------------------------
# KnowledgeUpdater protocol
# ---------------------------------------------------------------------------


@pytest.fixture
def updater() -> KnowledgeUpdater:
    km = DummyKnowledgeManager()
    return KnowledgeUpdater(km)


def test_step1_ingest(updater: KnowledgeUpdater):
    data = {"icd10": {"A00": {"description": "new"}}}
    ingested = updater._ingest_update(data, UpdateSource.WHO_ICD10)
    assert ingested["icd10"]["A00"]["description"] == "new"


def test_step2_diff(updater: KnowledgeUpdater):
    updater.knowledge_manager._snapshot = {"icd10": {"A00": {"description": "old"}}}
    ingested = {"icd10": {"A00": {"description": "new"}}, "effective_date": "2026-01-01"}
    changes = updater._diff_against_current(ingested)
    assert changes.codes_modified


def test_step3_validate_passes(updater: KnowledgeUpdater):
    updater.knowledge_manager._snapshot = {"icd10": {"A00": {"description": "d"}}}
    changes = ChangesSummary()
    issues = updater._validate_consistency(changes)
    assert issues == []


def test_step3_validate_fails(updater: KnowledgeUpdater):
    updater.knowledge_manager._snapshot = {"icd10": {}}
    ingested = {"rules": {"R1": {"codes": ["MISSING"]}}}
    changes = updater._diff_against_current(ingested)
    issues = updater._validate_consistency(changes)
    assert issues


def test_step4_stage(updater: KnowledgeUpdater):
    changes = ChangesSummary(codes_added=[CodeChange(code="A00", code_type="ICD10", change_type="ADDED", old_description=None, new_description="d", old_properties=None, new_properties={"description": "d"}, effective_date="", reason="")])
    shadow = updater._stage_in_shadow(changes)
    assert shadow.get_snapshot()["codes"]["A00"]["description"] == "d"


def test_step5_test(updater: KnowledgeUpdater):
    shadow = ShadowKnowledgeBase({})
    results = updater._run_regression_tests(shadow)
    assert results.passed_threshold


def test_step6_evaluate_auto_promote(updater: KnowledgeUpdater):
    res = RegressionTestResults(total_tests=10, tests_passed=10, tests_failed=0, pass_rate=1.0, threshold=0.995, passed_threshold=True, failed_test_details=[], execution_time_seconds=0.1, tested_at=datetime.now(timezone.utc))
    status = updater._evaluate_test_results(res)
    assert status == UpdateStatus.APPROVED


def test_step6_evaluate_human_review(updater: KnowledgeUpdater):
    res = RegressionTestResults(total_tests=10, tests_passed=5, tests_failed=5, pass_rate=0.5, threshold=0.995, passed_threshold=False, failed_test_details=[], execution_time_seconds=0.1, tested_at=datetime.now(timezone.utc))
    status = updater._evaluate_test_results(res)
    assert status == UpdateStatus.HUMAN_REVIEW


def test_step7_promote(updater: KnowledgeUpdater):
    changes = ChangesSummary(codes_added=[CodeChange(code="A00", code_type="ICD10", change_type="ADDED", old_description=None, new_description="d", old_properties=None, new_properties={"description": "d"}, effective_date="", reason="")])
    shadow = ShadowKnowledgeBase.create_from_production(updater.knowledge_manager)
    shadow.apply_changes(changes)
    version = create_initial_version()
    updater._promote_to_production(shadow, version)
    assert updater.current_version.version_id == version.version_id
    assert version.is_active


def test_step7_human_review_notification(updater: KnowledgeUpdater):
    version = create_initial_version()
    res = RegressionTestResults(total_tests=1, tests_passed=0, tests_failed=1, pass_rate=0.0, threshold=0.995, passed_threshold=False, failed_test_details=[], execution_time_seconds=0.1, tested_at=datetime.now(timezone.utc))
    updater._request_human_review(version, res)
    assert any(n.status == UpdateStatus.HUMAN_REVIEW for n in updater.notifications)


def test_step8_log(updater: KnowledgeUpdater):
    version = create_initial_version()
    updater._log_update(version)
    assert updater.notifications


@pytest.mark.asyncio
async def test_full_update_pipeline_success():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    update_data = {"icd10": {"A00": {"description": "new"}}, "effective_date": "2026-01-01", "reference": "ref", "source": UpdateSource.CMS_NCCI}
    version = await updater.process_update(update_data, UpdateSource.CMS_NCCI)
    assert version.changes_summary.total_additions >= 0
    assert updater.current_version.version_id == version.version_id or version.is_shadow


@pytest.mark.asyncio
async def test_full_update_pipeline_needs_review(monkeypatch):
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)

    def failing_tests(shadow: ShadowKnowledgeBase):
        return RegressionTestResults(total_tests=1, tests_passed=0, tests_failed=1, pass_rate=0.0, threshold=0.995, passed_threshold=False, failed_test_details=[], execution_time_seconds=0.1, tested_at=datetime.now(timezone.utc))

    monkeypatch.setattr(updater, "_run_regression_tests", failing_tests)
    update_data = {"icd10": {"A00": {"description": "new"}}, "source": UpdateSource.AMA_CPT}
    version = await updater.process_update(update_data, UpdateSource.AMA_CPT)
    assert updater._status_map[version.version_id] in {UpdateStatus.HUMAN_REVIEW, UpdateStatus.FAILED_TESTING}


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------


def test_approve_update(monkeypatch):
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    version = create_initial_version()
    updater.version_history.append(version)
    updater._set_status(version.version_id, UpdateStatus.HUMAN_REVIEW)
    success = updater.approve_update(version.version_id, "admin")
    assert success is True


def test_reject_update():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    version = create_initial_version()
    updater.version_history.append(version)
    success = updater.reject_update(version.version_id, "admin", "reason")
    assert success is True


def test_rollback_restores_previous():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    prev = updater.current_version
    new_version = create_initial_version()
    updater.version_history.append(new_version)
    updater.current_version = new_version
    success = updater.rollback(new_version.version_id, "issue")
    assert success is True
    assert updater.current_version.version_id == prev.version_id


def test_version_history():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    assert len(updater.get_version_history()) >= 1


def test_get_pending_reviews():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    version = create_initial_version()
    updater.version_history.append(version)
    updater._set_status(version.version_id, UpdateStatus.HUMAN_REVIEW)
    pending = updater.get_pending_reviews()
    assert version in pending


# ---------------------------------------------------------------------------
# Feed configuration
# ---------------------------------------------------------------------------


def test_default_feed_configs():
    feeds = get_default_feed_configs()
    sources = {f.source for f in feeds}
    assert {UpdateSource.CMS_NCCI, UpdateSource.WHO_ICD10, UpdateSource.AMA_CPT, UpdateSource.CMS_LCD_NCD, UpdateSource.PAYER_POLICY, UpdateSource.CODING_GUIDELINES}.issubset(sources)


def test_configure_feed():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    cfg = UpdateFeedConfig(source=UpdateSource.MANUAL, name="Manual", frequency=UpdateFrequency.ON_DEMAND)
    updater.configure_feed(cfg)
    assert any(f.source == UpdateSource.MANUAL for f in updater.feed_configs)


def test_get_feed_status():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    status = updater.get_feed_status()
    assert isinstance(status, list)
    assert status[0]["enabled"] in {True, False}


def test_cms_ncci_quarterly():
    feeds = get_default_feed_configs()
    cfg = next(f for f in feeds if f.source == UpdateSource.CMS_NCCI)
    assert cfg.frequency == UpdateFrequency.QUARTERLY


def test_who_icd10_annually():
    feeds = get_default_feed_configs()
    cfg = next(f for f in feeds if f.source == UpdateSource.WHO_ICD10)
    assert cfg.frequency == UpdateFrequency.ANNUALLY


def test_payer_daily():
    feeds = get_default_feed_configs()
    cfg = next(f for f in feeds if f.source == UpdateSource.PAYER_POLICY)
    assert cfg.frequency == UpdateFrequency.DAILY


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def test_notification_created_on_update(monkeypatch):
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    updater._log_update(create_initial_version())
    assert updater.notifications


def test_notification_for_human_review():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    version = create_initial_version()
    res = RegressionTestResults(total_tests=1, tests_passed=0, tests_failed=1, pass_rate=0.0, threshold=0.995, passed_threshold=False, failed_test_details=[], execution_time_seconds=0.1, tested_at=datetime.now(timezone.utc))
    updater._request_human_review(version, res)
    assert any(n.requires_human_review for n in updater.notifications)


def test_get_unread_notifications():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    updater.notifications.append(UpdateNotification(notification_id="1", version_id="v", source=UpdateSource.MANUAL, status=UpdateStatus.PENDING, summary="s", requires_human_review=False, created_at=datetime.now(timezone.utc), details={}))
    unread = updater.get_update_notifications(unread_only=True)
    assert unread


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_updates_available(monkeypatch):
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    monkeypatch.setattr(updater.feed_checker, "check_all_feeds", lambda feeds: [])
    updates = await updater.check_for_updates()
    assert updates == []


@pytest.mark.asyncio
async def test_concurrent_update_safety():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    update = {"icd10": {"A00": {"description": "new"}}}
    v1, v2 = await asyncio.gather(updater.process_update(update, UpdateSource.CMS_NCCI), updater.process_update(update, UpdateSource.CMS_LCD_NCD))
    assert v1.version_id != v2.version_id


def test_changes_since_version():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    base_version = updater.current_version
    # add another version manually
    new_version = create_initial_version()
    new_version.changes_summary.codes_added.append(CodeChange(code="A00", code_type="ICD10", change_type="ADDED", old_description=None, new_description="d", old_properties=None, new_properties={}, effective_date="", reason=""))
    updater.version_history.append(new_version)
    summary = updater.get_changes_since(base_version.version_id)
    assert summary.codes_added


@pytest.mark.asyncio
async def test_version_metadata():
    km = DummyKnowledgeManager()
    updater = KnowledgeUpdater(km)
    update = {"icd10": {"A00": {"description": "new"}}}
    version = await updater.process_update(update, UpdateSource.CMS_NCCI)
    assert version.metadata.get("version_id") == version.version_id
