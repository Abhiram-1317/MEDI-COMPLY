"""Tests for Layer 1 model guardrails (foundation checks)."""

import pytest

from medi_comply.guardrails.layer1_model import (
    CalibrationChecker,
    Environment,
    FineTuningSpec,
    Layer1ModelGuard,
    ModelConfig,
    ModelRegistry,
    Provider,
    UseCase,
)


def test_approved_model_passes():
    registry = ModelRegistry()
    guard = Layer1ModelGuard(registry)
    cfg = registry.get("gpt-4o")
    assert cfg is not None
    result = guard.run_checks(cfg, Environment.PRODUCTION, use_case=UseCase.MEDICAL_CODING)
    assert result.passed
    assert all(c.passed for c in result.checks)


def test_unapproved_model_fails():
    guard = Layer1ModelGuard()
    cfg = ModelConfig(
        name="unknown-model",
        provider=Provider.MOCK,
        medical_domain_score=0.9,
        hipaa_compliant=True,
        max_context_window=32_000,
        supports_json_mode=True,
    )
    result = guard.run_checks(cfg, Environment.PRODUCTION, use_case=UseCase.MEDICAL_CODING)
    assert not result.passed
    assert any(c.check_id == "REGISTRY" and not c.passed for c in result.checks)


def test_mock_model_passes_in_dev():
    registry = ModelRegistry()
    guard = Layer1ModelGuard(registry)
    cfg = registry.get("mock-med")
    assert cfg is not None

    dev_result = guard.run_checks(cfg, Environment.DEVELOPMENT, use_case=UseCase.MEDICAL_CODING)
    assert dev_result.passed  # relaxed in dev

    prod_result = guard.run_checks(cfg, Environment.PRODUCTION, use_case=UseCase.MEDICAL_CODING)
    assert not prod_result.passed  # fails HIPAA/domain in prod


def test_non_hipaa_model_fails_production():
    guard = Layer1ModelGuard()
    cfg = ModelConfig(
        name="non-hipaa",
        provider=Provider.OPENAI,
        medical_domain_score=0.9,
        hipaa_compliant=False,
        max_context_window=128_000,
        supports_json_mode=True,
    )
    result = guard.run_checks(cfg, Environment.PRODUCTION, use_case=UseCase.MEDICAL_CODING)
    assert not result.passed
    assert any(c.check_id == "HIPAA" and not c.passed for c in result.checks)


def test_low_domain_score_fails():
    guard = Layer1ModelGuard()
    cfg = ModelConfig(
        name="low-domain",
        provider=Provider.OPENAI,
        medical_domain_score=0.6,
        hipaa_compliant=True,
        max_context_window=64_000,
        supports_json_mode=True,
    )
    result = guard.run_checks(cfg, Environment.PRODUCTION, use_case=UseCase.MEDICAL_CODING)
    assert not result.passed
    assert any(c.check_id == "DOMAIN_SCORE" and not c.passed for c in result.checks)


def test_calibration_checker_good():
    checker = CalibrationChecker(num_bins=5)
    report = checker.check_calibration([0.99, 0.98, 0.97], [1, 1, 1])
    assert report.calibrated
    assert report.ece_score < 0.05


def test_calibration_checker_bad():
    checker = CalibrationChecker(num_bins=5)
    report = checker.check_calibration([0.9, 0.1, 0.1, 0.9], [1, 0, 1, 0])
    assert not report.calibrated
    assert report.ece_score > 0.05


def test_model_registry_lookup():
    registry = ModelRegistry()
    best = registry.best_for_use_case(UseCase.MEDICAL_CODING)
    assert best is not None
    assert best.supports_json_mode
    assert best.medical_domain_score == max(cfg.medical_domain_score for cfg in registry.registry.values() if cfg.supports_json_mode)


def test_fine_tuning_spec_safety():
    spec = FineTuningSpec()
    assert spec.never_trained_on_real_phi is True
