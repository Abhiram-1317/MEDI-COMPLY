"""Layer 1 — Model foundation guardrails for MEDI-COMPLY.

This module performs foundational checks *before* any LLM call is made. It
validates model selection, HIPAA suitability, domain fitness, calibration
expectations, and alignment with the approved model registry. It is designed to
plug into the 5-layer Compliance Cage alongside structural/semantic/output
layers.

Key responsibilities:
- Registry of approved models (OpenAI GPT-4o, Anthropic Claude 3.5 Sonnet,
  Ollama Llama 3 70B, and a mock provider).
- Model selection validation per use case (medical coding, claims adjudication,
  prior auth, compliance audit) with domain score thresholds and HIPAA gates.
- Fine-tuning specification documentation for transparency and downstream audits.
- Calibration checking (Expected Calibration Error) to ensure confidence scores
  remain reliable.
- Environment-aware runner that applies stricter rules in production.

Notes:
- All Pydantic models use v2 BaseModel.
- The return shape mirrors other guardrail layers: structured result objects
  with pass/fail status and recommendations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field, model_validator


class UseCase(str, Enum):
    """Supported healthcare workflows for model selection."""

    MEDICAL_CODING = "MEDICAL_CODING"
    CLAIMS_ADJUDICATION = "CLAIMS_ADJUDICATION"
    PRIOR_AUTH = "PRIOR_AUTH"
    COMPLIANCE_AUDIT = "COMPLIANCE_AUDIT"


class Environment(str, Enum):
    """Deployment environment modes."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Provider(str, Enum):
    """LLM providers supported by the layer 1 guard."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    MOCK = "mock"


class ModelConfig(BaseModel):
    """Configuration metadata for an approved model."""

    name: str
    provider: Provider
    medical_domain_score: float = Field(ge=0.0, le=1.0)
    hipaa_compliant: bool
    max_context_window: int = Field(gt=0)
    supports_json_mode: bool
    description: str = ""


class ModelValidationResult(BaseModel):
    """Outcome of model selection validation."""

    passed: bool
    reasons: List[str] = Field(default_factory=list)
    model_name: Optional[str] = None
    provider: Optional[Provider] = None
    use_case: Optional[UseCase] = None
    severity: str = "INFO"


@dataclass
class FineTuningSpec:
    """Documented fine-tuning corpus and objectives.

    - 50,000 medical coding examples
    - 20,000 claims adjudication cases
    - 10,000 prior auth decisions
    - 5,000 edge cases (deliberate traps)
    - 5,000 refusal examples ("I don't know")

    Training objective prioritizes calibration (ECE < 0.05) in addition to
    accuracy. Dataset safety: never trained on real PHI.
    """

    medical_coding_examples: int = 50_000
    claims_adjudication_cases: int = 20_000
    prior_auth_decisions: int = 10_000
    edge_cases: int = 5_000
    refusal_examples: int = 5_000
    expected_ece_threshold: float = 0.05
    never_trained_on_real_phi: bool = True


class CalibrationReport(BaseModel):
    """Result of calibration evaluation for a model."""

    ece_score: float
    calibrated: bool
    bins_used: int
    details: str = ""


class CalibrationChecker:
    """Performs Expected Calibration Error (ECE) checks."""

    def __init__(self, num_bins: int = 10) -> None:
        self.num_bins = max(1, num_bins)

    def check_calibration(self, predictions: Iterable[float], actuals: Iterable[int]) -> CalibrationReport:
        """Compute ECE over provided predictions/labels.

        Args:
            predictions: Iterable of confidence scores (0-1).
            actuals: Iterable of binary ground-truth labels (0 or 1).
        """

        preds = list(predictions)
        trues = list(actuals)
        if not preds or not trues or len(preds) != len(trues):
            return CalibrationReport(ece_score=1.0, calibrated=False, bins_used=self.num_bins, details="Invalid inputs")

        bin_boundaries = [i / self.num_bins for i in range(self.num_bins + 1)]
        total = len(preds)
        ece = 0.0
        for i in range(self.num_bins):
            lower, upper = bin_boundaries[i], bin_boundaries[i + 1]
            bucket_indices = [j for j, p in enumerate(preds) if lower <= p < upper or (p == 1.0 and upper == 1.0)]
            if not bucket_indices:
                continue
            bucket_confs = [preds[j] for j in bucket_indices]
            bucket_trues = [trues[j] for j in bucket_indices]
            avg_conf = sum(bucket_confs) / len(bucket_confs)
            avg_acc = sum(bucket_trues) / len(bucket_trues)
            gap = abs(avg_conf - avg_acc)
            ece += (len(bucket_indices) / total) * gap

        return CalibrationReport(
            ece_score=ece,
            calibrated=self.is_calibrated(ece),
            bins_used=self.num_bins,
            details=f"ECE={ece:.4f} using {self.num_bins} bins",
        )

    @staticmethod
    def is_calibrated(ece_score: float) -> bool:
        """Return True if calibration meets the <0.05 target."""

        return ece_score < 0.05


class Layer1CheckResult(BaseModel):
    """Individual check result for layer 1."""

    check_id: str
    name: str
    passed: bool
    severity: str = "INFO"
    detail: str = ""
    recommendation: Optional[str] = None


class Layer1Result(BaseModel):
    """Aggregate result for layer 1 guardrails."""

    passed: bool
    checks: List[Layer1CheckResult]
    recommendations: List[str] = Field(default_factory=list)
    model_name: Optional[str] = None
    provider: Optional[Provider] = None
    environment: Optional[Environment] = None


class ModelRegistry:
    """Registry of approved models and helper selection routines."""

    def __init__(self) -> None:
        self._registry: Dict[str, ModelConfig] = {
            "gpt-4o": ModelConfig(
                name="gpt-4o",
                provider=Provider.OPENAI,
                medical_domain_score=0.92,
                hipaa_compliant=True,
                max_context_window=128_000,
                supports_json_mode=True,
                description="OpenAI GPT-4o tuned for medical reasoning",
            ),
            "claude-3.5-sonnet": ModelConfig(
                name="claude-3.5-sonnet",
                provider=Provider.ANTHROPIC,
                medical_domain_score=0.90,
                hipaa_compliant=True,
                max_context_window=200_000,
                supports_json_mode=True,
                description="Anthropic Claude 3.5 Sonnet for structured compliance outputs",
            ),
            "llama3-70b-ollama": ModelConfig(
                name="llama3-70b-ollama",
                provider=Provider.OLLAMA,
                medical_domain_score=0.82,
                hipaa_compliant=False,
                max_context_window=8_000,
                supports_json_mode=False,
                description="Local Llama 3 70B via Ollama (for dev/offline)",
            ),
            "mock-med": ModelConfig(
                name="mock-med",
                provider=Provider.MOCK,
                medical_domain_score=0.70,
                hipaa_compliant=False,
                max_context_window=16_000,
                supports_json_mode=True,
                description="Mock model for tests and dry-runs",
            ),
        }

    def is_approved(self, model_name: str) -> bool:
        return model_name in self._registry

    def get(self, model_name: str) -> Optional[ModelConfig]:
        return self._registry.get(model_name)

    def best_for_use_case(self, use_case: UseCase, require_json: bool = True) -> ModelConfig:
        """Return best scoring model for a use case."""

        candidates = list(self._registry.values())
        if require_json:
            candidates = [c for c in candidates if c.supports_json_mode]
        key_fn = lambda cfg: cfg.medical_domain_score
        return sorted(candidates, key=key_fn, reverse=True)[0]


class ModelSelectionGuard:
    """Validates whether a chosen model is fit for a target use case."""

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def validate_model_config(self, model_name: str, provider: Provider, use_case: UseCase) -> ModelValidationResult:
        reasons: List[str] = []
        severity = "INFO"

        if not self.registry.is_approved(model_name):
            reasons.append(f"Model {model_name} is not in the approved registry.")
            return ModelValidationResult(
                passed=False,
                reasons=reasons,
                model_name=model_name,
                provider=provider,
                use_case=use_case,
                severity="HARD_FAIL",
            )

        cfg = self.registry.get(model_name)
        if cfg is None:
            return ModelValidationResult(
                passed=False,
                reasons=["Model config could not be loaded."],
                model_name=model_name,
                provider=provider,
                use_case=use_case,
                severity="HARD_FAIL",
            )

        if cfg.provider != provider:
            reasons.append(f"Provider mismatch: expected {cfg.provider}, got {provider}.")

        if use_case == UseCase.MEDICAL_CODING and cfg.medical_domain_score < 0.80:
            reasons.append("Medical coding requires medical_domain_score >= 0.80.")
        if use_case in {UseCase.CLAIMS_ADJUDICATION, UseCase.PRIOR_AUTH, UseCase.COMPLIANCE_AUDIT} and cfg.medical_domain_score < 0.75:
            reasons.append("Healthcare workflows require medical_domain_score >= 0.75.")

        passed = len(reasons) == 0
        severity = "HARD_FAIL" if not passed else "INFO"

        return ModelValidationResult(
            passed=passed,
            reasons=reasons,
            model_name=model_name,
            provider=provider,
            use_case=use_case,
            severity=severity,
        )


class Layer1ModelGuard:
    """Executes Layer 1 checks (model foundation) before LLM calls."""

    def __init__(self, registry: Optional[ModelRegistry] = None, calibration_checker: Optional[CalibrationChecker] = None) -> None:
        self.registry = registry or ModelRegistry()
        self.selection_guard = ModelSelectionGuard(self.registry)
        self.calibration_checker = calibration_checker or CalibrationChecker()
        self.fine_tuning_spec = FineTuningSpec()

    def _hipaa_check(self, cfg: ModelConfig, env: Environment) -> Layer1CheckResult:
        if env == Environment.PRODUCTION and not cfg.hipaa_compliant:
            return Layer1CheckResult(
                check_id="HIPAA",
                name="HIPAA Compliance",
                passed=False,
                severity="HARD_FAIL",
                detail="Model not marked HIPAA-compliant for production.",
                recommendation="Choose a HIPAA-compliant model (e.g., gpt-4o, claude-3.5-sonnet).",
            )
        return Layer1CheckResult(
            check_id="HIPAA",
            name="HIPAA Compliance",
            passed=True,
            detail="HIPAA requirement satisfied for this environment.",
        )

    def _domain_score_check(self, cfg: ModelConfig, use_case: UseCase) -> Layer1CheckResult:
        threshold = 0.80 if use_case == UseCase.MEDICAL_CODING else 0.75
        if cfg.medical_domain_score < threshold:
            return Layer1CheckResult(
                check_id="DOMAIN_SCORE",
                name="Medical Domain Score",
                passed=False,
                severity="HARD_FAIL",
                detail=f"Model domain score {cfg.medical_domain_score:.2f} below threshold {threshold:.2f} for {use_case}.",
                recommendation="Select a higher-scoring model or fine-tune to improve calibration and domain fitness.",
            )
        return Layer1CheckResult(
            check_id="DOMAIN_SCORE",
            name="Medical Domain Score",
            passed=True,
            detail=f"Domain score {cfg.medical_domain_score:.2f} meets threshold for {use_case}.",
        )

    def _registry_check(self, model_name: str) -> Layer1CheckResult:
        if not self.registry.is_approved(model_name):
            return Layer1CheckResult(
                check_id="REGISTRY",
                name="Approved Model Registry",
                passed=False,
                severity="HARD_FAIL",
                detail=f"Model {model_name} is not approved.",
                recommendation="Use an approved model or add it to the registry after review.",
            )
        return Layer1CheckResult(
            check_id="REGISTRY",
            name="Approved Model Registry",
            passed=True,
            detail="Model is approved in registry.",
        )

    def _calibration_check(self, sample_predictions: Optional[Iterable[float]], sample_actuals: Optional[Iterable[int]]) -> Layer1CheckResult:
        if sample_predictions is None or sample_actuals is None:
            return Layer1CheckResult(
                check_id="CALIBRATION",
                name="Confidence Calibration",
                passed=True,
                detail="Calibration not evaluated (no samples provided); allowed in non-production.",
                severity="INFO",
                recommendation="Provide recent eval predictions to verify ECE < 0.05 before go-live.",
            )

        report = self.calibration_checker.check_calibration(sample_predictions, sample_actuals)
        if not report.calibrated:
            return Layer1CheckResult(
                check_id="CALIBRATION",
                name="Confidence Calibration",
                passed=False,
                severity="SOFT_FAIL",
                detail=f"ECE={report.ece_score:.4f} exceeds target <0.05.",
                recommendation="Apply temperature scaling or recalibrate on latest domain data.",
            )
        return Layer1CheckResult(
            check_id="CALIBRATION",
            name="Confidence Calibration",
            passed=True,
            detail=report.details,
        )

    def run_checks(
        self,
        model_config: ModelConfig,
        environment: Environment,
        use_case: UseCase = UseCase.MEDICAL_CODING,
        sample_predictions: Optional[Iterable[float]] = None,
        sample_actuals: Optional[Iterable[int]] = None,
    ) -> Layer1Result:
        """Run Layer 1 checks with environment-aware strictness."""

        checks: List[Layer1CheckResult] = []

        registry_check = self._registry_check(model_config.name)
        checks.append(registry_check)

        selection_result = self.selection_guard.validate_model_config(
            model_name=model_config.name,
            provider=model_config.provider,
            use_case=use_case,
        )
        checks.append(
            Layer1CheckResult(
                check_id="MODEL_SELECTION",
                name="Model Selection Validator",
                passed=selection_result.passed,
                severity=selection_result.severity if not selection_result.passed else "INFO",
                detail="; ".join(selection_result.reasons) if selection_result.reasons else "Model meets selection criteria.",
                recommendation="Review provider and domain score requirements." if not selection_result.passed else None,
            )
        )

        checks.append(self._hipaa_check(model_config, environment))
        checks.append(self._domain_score_check(model_config, use_case))
        checks.append(self._calibration_check(sample_predictions, sample_actuals))

        # Evaluate overall pass/fail based on environment strictness
        hard_fail = any((not c.passed) and c.severity == "HARD_FAIL" for c in checks)
        soft_fail = any((not c.passed) and c.severity == "SOFT_FAIL" for c in checks)

        if environment == Environment.PRODUCTION:
            overall_passed = not (hard_fail or soft_fail)
        elif environment == Environment.STAGING:
            overall_passed = not hard_fail
        else:  # development
            overall_passed = True  # allow relaxed checks; surfaced via recommendations

        recommendations = [c.recommendation for c in checks if c.recommendation]

        return Layer1Result(
            passed=overall_passed,
            checks=checks,
            recommendations=recommendations,
            model_name=model_config.name,
            provider=model_config.provider,
            environment=environment,
        )


__all__ = [
    "UseCase",
    "Environment",
    "Provider",
    "ModelConfig",
    "ModelValidationResult",
    "FineTuningSpec",
    "CalibrationChecker",
    "CalibrationReport",
    "Layer1CheckResult",
    "Layer1Result",
    "ModelRegistry",
    "ModelSelectionGuard",
    "Layer1ModelGuard",
]
