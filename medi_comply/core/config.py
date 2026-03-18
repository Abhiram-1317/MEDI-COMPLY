"""
MEDI-COMPLY — System configuration via Pydantic Settings.

All configuration is loaded from environment variables (with ``.env`` fallback).
The single ``Settings`` instance aggregates every sub-config group and is
obtained through :func:`get_settings`.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Sub-config groups
# ---------------------------------------------------------------------------


class LLMConfig(BaseSettings):
    """LLM provider configuration."""

    model_config = SettingsConfigDict(env_prefix="LLM_")

    model_name: str = Field(default="gpt-4o", description="Primary LLM model identifier")
    temperature: float = Field(default=0.1, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: int = Field(default=4096, gt=0, description="Max output tokens per call")
    api_key: str = Field(default="", description="LLM provider API key")
    timeout_seconds: int = Field(default=60, gt=0, description="HTTP timeout for LLM calls")


class GuardrailConfig(BaseSettings):
    """Safety / compliance guardrail thresholds."""

    model_config = SettingsConfigDict(env_prefix="GUARDRAIL_")

    confidence_threshold: float = Field(
        default=0.85, ge=0.0, le=1.0,
        description="Minimum confidence for auto-approval",
    )
    max_retries: int = Field(default=3, ge=0, description="Max retry attempts before escalation")
    escalation_threshold: float = Field(
        default=0.7, ge=0.0, le=1.0,
        description="Confidence below which we escalate to human review",
    )


class KnowledgeConfig(BaseSettings):
    """External knowledge-store connection settings."""

    model_config = SettingsConfigDict(env_prefix="KNOWLEDGE_")

    vector_db_url: str = Field(
        default="http://localhost:8100",
        description="URL of the vector database (ChromaDB / Pinecone)",
    )
    graph_db_url: str = Field(
        default="bolt://localhost:7687",
        description="Neo4j Bolt connection URL",
    )
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Embedding model identifier",
    )


class AuditConfig(BaseSettings):
    """Audit / traceability configuration."""

    model_config = SettingsConfigDict(env_prefix="AUDIT_")

    retention_days: int = Field(
        default=2555, gt=0,
        description="Number of days to retain audit records (~7 years for HIPAA)",
    )
    log_level: str = Field(default="INFO", description="Minimum log level for audit events")


class SecurityConfig(BaseSettings):
    """Security feature flags."""

    model_config = SettingsConfigDict(env_prefix="SECURITY_")

    enable_phi_detection: bool = Field(
        default=True,
        description="Toggle PHI (Protected Health Information) detection",
    )
    enable_prompt_injection_detection: bool = Field(
        default=True,
        description="Toggle prompt-injection detection in user inputs",
    )


# ---------------------------------------------------------------------------
# Aggregated settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Top-level application settings — aggregates all sub-configs.

    Reads from environment variables and a ``.env`` file in the working
    directory (if present).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="MEDI-COMPLY", description="Application name")
    debug: bool = Field(default=False, description="Enable debug mode")
    environment: str = Field(default="development", description="Runtime environment")
    log_level: str = Field(default="INFO", description="Root log level")

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://medi:medi@localhost:5432/medi_comply",
        description="Primary PostgreSQL connection string",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection string",
    )

    # Sub-configs — instantiated with their own env-prefix resolution
    llm: LLMConfig = Field(default_factory=LLMConfig)
    guardrail: GuardrailConfig = Field(default_factory=GuardrailConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton ``Settings`` instance."""
    return Settings()
