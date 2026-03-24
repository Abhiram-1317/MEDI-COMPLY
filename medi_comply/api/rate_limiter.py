"""Comprehensive in-memory rate limiter for MEDI-COMPLY APIs.
This module provides multi-strategy, multi-scope rate limiting suitable for
HIPAA-oriented APIs. It supports FastAPI middleware and dependency injection
and ships with sensible defaults aimed at preventing bulk data exfiltration.
"""
from __future__ import annotations
import hashlib
import logging
import math
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
logger = logging.getLogger(__name__)
class RateLimitStrategy(str, Enum):
    """Supported rate limiting algorithms."""
    SLIDING_WINDOW = "sliding_window"
    TOKEN_BUCKET = "token_bucket"
    FIXED_WINDOW = "fixed_window"
    LEAKY_BUCKET = "leaky_bucket"
class RateLimitScope(str, Enum):
    """Granularity of a rate limit rule."""
    GLOBAL = "global"
    PER_USER = "per_user"
    PER_IP = "per_ip"
    PER_ENDPOINT = "per_endpoint"
    PER_USER_ENDPOINT = "per_user_endpoint"
    PER_ROLE = "per_role"
    PER_API_KEY = "per_api_key"
class RateLimitAction(str, Enum):
    """Actions applied when a rule is exceeded."""
    ALLOW = "allow"
    THROTTLE = "throttle"
    REJECT = "reject"
    ALERT = "alert"
    QUEUE = "queue"
class RateLimitRule(BaseModel):
    """Configuration for a single rate limit rule."""
    rule_id: str = Field(default_factory=lambda: f"RL-{uuid.uuid4().hex[:6].upper()}")
    name: str = ""
    scope: RateLimitScope = RateLimitScope.PER_USER
    strategy: RateLimitStrategy = RateLimitStrategy.SLIDING_WINDOW
    max_requests: int = 100
    window_seconds: int = 60
    burst_limit: Optional[int] = None
    refill_rate: Optional[float] = None
    applies_to_endpoints: List[str] = Field(default_factory=list)
    applies_to_roles: List[str] = Field(default_factory=list)
    action_on_limit: RateLimitAction = RateLimitAction.REJECT
    retry_after_seconds: Optional[int] = None
    enabled: bool = True
    priority: int = 0
    description: str = ""
class RateLimitResult(BaseModel):
    """Outcome of evaluating rate limit rules."""
    allowed: bool = True
    rule_id: Optional[str] = None
    rule_name: Optional[str] = None
    action: RateLimitAction = RateLimitAction.ALLOW
    current_count: int = 0
    max_allowed: int = 0
    remaining: int = 0
    window_seconds: int = 0
    reset_at: Optional[float] = None
    retry_after: Optional[int] = None
    scope: Optional[str] = None
    key: Optional[str] = None
    message: str = ""
class RateLimitStats(BaseModel):
    """Aggregated rate limiting statistics."""
    total_requests: int = 0
    allowed_requests: int = 0
    rejected_requests: int = 0
    throttled_requests: int = 0
    alerted_requests: int = 0
    current_active_keys: int = 0
    rules_evaluated: int = 0
    top_limited_keys: List[Dict[str, Any]] = Field(default_factory=list)
    top_limited_endpoints: List[Dict[str, Any]] = Field(default_factory=list)
    window_start: str = ""
    window_end: str = ""
class RateLimitHeaders(BaseModel):
    """HTTP headers to include in rate-limited responses."""
    x_ratelimit_limit: int = 0
    x_ratelimit_remaining: int = 0
    x_ratelimit_reset: int = 0
    x_ratelimit_retry_after: Optional[int] = None
    x_ratelimit_scope: str = ""
DEFAULT_RULES: List[Dict[str, Any]] = [
    {
        "name": "Global Rate Limit",
        "scope": "global",
        "strategy": "sliding_window",
        "max_requests": 1000,
        "window_seconds": 60,
        "action_on_limit": "reject",
        "description": "Global system-wide rate limit",
        "priority": 0,
    },
    {
        "name": "Per-User General",
        "scope": "per_user",
        "strategy": "sliding_window",
        "max_requests": 100,
        "window_seconds": 60,
        "action_on_limit": "reject",
        "retry_after_seconds": 30,
        "description": "Default per-user rate limit",
        "priority": 1,
    },
    {
        "name": "Per-IP Unauthenticated",
        "scope": "per_ip",
        "strategy": "sliding_window",
        "max_requests": 30,
        "window_seconds": 60,
        "action_on_limit": "reject",
        "retry_after_seconds": 60,
        "description": "Rate limit for unauthenticated requests by IP",
        "priority": 2,
    },
    {
        "name": "Coding Endpoint Limit",
        "scope": "per_user_endpoint",
        "strategy": "sliding_window",
        "max_requests": 20,
        "window_seconds": 60,
        "applies_to_endpoints": ["/api/v1/coding/process"],
        "action_on_limit": "reject",
        "retry_after_seconds": 30,
        "description": "Limit coding requests (LLM-intensive)",
        "priority": 3,
    },
    {
        "name": "Claims Batch Limit",
        "scope": "per_user_endpoint",
        "strategy": "sliding_window",
        "max_requests": 5,
        "window_seconds": 300,
        "applies_to_endpoints": ["/api/v1/claims/batch-adjudicate"],
        "action_on_limit": "reject",
        "retry_after_seconds": 120,
        "description": "Limit batch claim processing",
        "priority": 3,
    },
    {
        "name": "Knowledge Update Limit",
        "scope": "per_user_endpoint",
        "strategy": "fixed_window",
        "max_requests": 5,
        "window_seconds": 3600,
        "applies_to_endpoints": ["/api/v1/knowledge/update"],
        "action_on_limit": "reject",
        "description": "Limit knowledge base updates",
        "priority": 3,
    },
    {
        "name": "Auth Login Limit",
        "scope": "per_ip",
        "strategy": "sliding_window",
        "max_requests": 10,
        "window_seconds": 300,
        "applies_to_endpoints": ["/api/v1/auth/login"],
        "action_on_limit": "reject",
        "retry_after_seconds": 300,
        "description": "Prevent brute force login attempts",
        "priority": 5,
    },
    {
        "name": "Audit Bulk Export Alert",
        "scope": "per_user",
        "strategy": "sliding_window",
        "max_requests": 50,
        "window_seconds": 300,
        "applies_to_endpoints": ["/api/v1/audit/search", "/api/v1/audit/"],
        "action_on_limit": "alert",
        "description": "Alert on bulk audit data access (potential exfiltration)",
        "priority": 4,
    },
    {
        "name": "CODER Role Limit",
        "scope": "per_role",
        "strategy": "sliding_window",
        "max_requests": 200,
        "window_seconds": 3600,
        "applies_to_roles": ["CODER"],
        "action_on_limit": "throttle",
        "description": "Hourly limit for CODER role",
        "priority": 1,
    },
    {
        "name": "Admin Higher Limit",
        "scope": "per_role",
        "strategy": "sliding_window",
        "max_requests": 1000,
        "window_seconds": 3600,
        "applies_to_roles": ["ADMIN", "SYSTEM"],
        "action_on_limit": "reject",
        "description": "Higher hourly limit for admins",
        "priority": 1,
    },
]
class SlidingWindowCounter:
    """Sliding window rate limit counter using timestamps."""
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._timestamps: List[float] = []
        self._lock = threading.Lock()
    def allow(self) -> Tuple[bool, int, int]:
        """Check if request is allowed. Returns (allowed, current_count, remaining)."""
        with self._lock:
            now = time.time()
            cutoff = now - self.window_seconds
            self._timestamps = [t for t in self._timestamps if t > cutoff]
            current = len(self._timestamps)
            if current < self.max_requests:
                self._timestamps.append(now)
                return True, current + 1, self.max_requests - current - 1
            return False, current, 0
    def get_reset_time(self) -> float:
        """Return timestamp when oldest entry expires."""
        if self._timestamps:
            return self._timestamps[0] + self.window_seconds
        return time.time() + self.window_seconds
    def get_count(self) -> int:
        """Return current count within window."""
        now = time.time()
        cutoff = now - self.window_seconds
        return len([t for t in self._timestamps if t > cutoff])
class TokenBucket:
    """Token bucket rate limiter."""
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self._tokens = float(capacity)
        self._last_refill = time.time()
        self._lock = threading.Lock()
    def allow(self) -> Tuple[bool, int, int]:
        """Try to consume a token. Returns (allowed, current_tokens, capacity)."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last_refill = now
            if self._tokens >= 1:
                self._tokens -= 1
                return True, int(self._tokens), self.capacity
            return False, int(self._tokens), self.capacity
    def get_wait_time(self) -> float:
        """Return seconds to wait for next token."""
        if self._tokens >= 1:
            return 0.0
        return (1.0 - self._tokens) / self.refill_rate if self.refill_rate else math.inf
class FixedWindowCounter:
    """Fixed window rate limit counter."""
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._count = 0
        self._window_start = time.time()
        self._lock = threading.Lock()
    def allow(self) -> Tuple[bool, int, int]:
        """Check if request is allowed."""
        with self._lock:
            now = time.time()
            if now - self._window_start >= self.window_seconds:
                self._count = 0
                self._window_start = now
            if self._count < self.max_requests:
                self._count += 1
                return True, self._count, self.max_requests - self._count
            return False, self._count, 0
    def get_reset_time(self) -> float:
        """Return end of current window."""
        return self._window_start + self.window_seconds
class RateLimiter:
    """Central rate limiting engine supporting multiple scopes and strategies."""
    def __init__(self, rules: Optional[List[RateLimitRule]] = None):
        self.logger = logger
        self._rules: List[RateLimitRule] = []
        self._counters: Dict[str, Any] = {}
        self._counter_meta: Dict[str, Dict[str, Any]] = {}
        self._stats = {
            "total_requests": 0,
            "allowed": 0,
            "rejected": 0,
            "throttled": 0,
            "alerted": 0,
            "rejection_details": defaultdict(int),
            "endpoint_hits": defaultdict(int),
        }
        self._alert_callbacks: List[Callable[[str, str, int], None]] = []
        self._lock = threading.Lock()
        if rules:
            for rule in rules:
                self._rules.append(rule)
        else:
            self._load_default_rules()
        self._rules.sort(key=lambda r: r.priority, reverse=True)
    def _load_default_rules(self) -> None:
        """Load baked-in defaults into the rule set."""
        for cfg in DEFAULT_RULES:
            rule = RateLimitRule(
                name=cfg.get("name", ""),
                scope=RateLimitScope(cfg.get("scope", RateLimitScope.PER_USER)),
                strategy=RateLimitStrategy(cfg.get("strategy", RateLimitStrategy.SLIDING_WINDOW)),
                max_requests=cfg.get("max_requests", 100),
                window_seconds=cfg.get("window_seconds", 60),
                burst_limit=cfg.get("burst_limit"),
                refill_rate=cfg.get("refill_rate"),
                applies_to_endpoints=cfg.get("applies_to_endpoints", []),
                applies_to_roles=cfg.get("applies_to_roles", []),
                action_on_limit=RateLimitAction(cfg.get("action_on_limit", RateLimitAction.REJECT)),
                retry_after_seconds=cfg.get("retry_after_seconds"),
                enabled=cfg.get("enabled", True),
                priority=cfg.get("priority", 0),
                description=cfg.get("description", ""),
            )
            self._rules.append(rule)
    def add_rule(self, rule: RateLimitRule) -> None:
        """Add a new rate limit rule and resort by priority."""
        with self._lock:
            self._rules.append(rule)
            self._rules.sort(key=lambda r: r.priority, reverse=True)
        self.logger.info("Added rate limit rule %s (%s)", rule.rule_id, rule.name)
    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule by ID and purge related counters."""
        removed = False
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.rule_id != rule_id]
            removed = len(self._rules) < before
            if removed:
                for key in list(self._counters.keys()):
                    if key.startswith(f"{rule_id}:"):
                        self._counters.pop(key, None)
                        self._counter_meta.pop(key, None)
        if removed:
            self.logger.info("Removed rate limit rule %s", rule_id)
        return removed
    def get_rules(self) -> List[RateLimitRule]:
        """Return current rules."""
        return list(self._rules)
    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[RateLimitRule]:
        """Update a rule's parameters and reset counters for it."""
        with self._lock:
            target = None
            for rule in self._rules:
                if rule.rule_id == rule_id:
                    target = rule
                    break
            if not target:
                return None
            for k, v in updates.items():
                if hasattr(target, k):
                    setattr(target, k, v)
            self._rules.sort(key=lambda r: r.priority, reverse=True)
            for key in list(self._counters.keys()):
                if key.startswith(f"{rule_id}:"):
                    self._counters.pop(key, None)
                    self._counter_meta.pop(key, None)
        self.logger.info("Updated rate limit rule %s", rule_id)
        return target
    def check_rate_limit(
        self,
        endpoint: str,
        user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        ip_address: Optional[str] = None,
        api_key_id: Optional[str] = None,
    ) -> RateLimitResult:
        """Evaluate all applicable rules for a request."""
        with self._lock:
            self._stats["total_requests"] += 1
        matching_rules = [
            r
            for r in self._rules
            if self._rule_matches_request(r, endpoint, user_id, user_role, ip_address, api_key_id)
        ]
        result = RateLimitResult(allowed=True, action=RateLimitAction.ALLOW)
        for rule in matching_rules:
            counter_key = self._build_counter_key(
                rule,
                endpoint=endpoint,
                user_id=user_id,
                ip_address=ip_address,
                user_role=user_role,
                api_key_id=api_key_id,
            )
            counter = self._get_or_create_counter(counter_key, rule)
            allowed, current, remaining = counter.allow()
            reset_at = None
            if isinstance(counter, SlidingWindowCounter):
                reset_at = counter.get_reset_time()
            elif isinstance(counter, FixedWindowCounter):
                reset_at = counter.get_reset_time()
            elif isinstance(counter, TokenBucket):
                wait = counter.get_wait_time()
                reset_at = time.time() + wait
            self._counter_meta[counter_key]["last_used"] = time.time()
            self._counter_meta[counter_key]["window"] = rule.window_seconds
            with self._lock:
                self._stats["rules_evaluated"] = self._stats.get("rules_evaluated", 0) + 1
            if not allowed:
                action = rule.action_on_limit
                retry_after = rule.retry_after_seconds or int(rule.window_seconds)
                message = f"Rate limit exceeded for {counter_key} ({rule.name})"
                result = RateLimitResult(
                    allowed=action in {RateLimitAction.ALERT, RateLimitAction.QUEUE},
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    action=action,
                    current_count=current,
                    max_allowed=rule.max_requests,
                    remaining=remaining,
                    window_seconds=rule.window_seconds,
                    reset_at=reset_at,
                    retry_after=retry_after,
                    scope=rule.scope.value,
                    key=counter_key,
                    message=message,
                )
                with self._lock:
                    if action == RateLimitAction.REJECT:
                        self._stats["rejected"] += 1
                        self._stats["rejection_details"][counter_key] += 1
                    elif action == RateLimitAction.THROTTLE:
                        self._stats["throttled"] += 1
                    elif action == RateLimitAction.ALERT:
                        self._stats["alerted"] += 1
                if action == RateLimitAction.ALERT:
                    self._trigger_alerts(counter_key, rule, current)
                    continue
                if action == RateLimitAction.THROTTLE:
                    return result
                if action == RateLimitAction.REJECT:
                    return result
                if action == RateLimitAction.QUEUE:
                    return result
            else:
                with self._lock:
                    self._stats["allowed"] += 1
                self.logger.debug("Rate limit allowed for %s via rule %s", counter_key, rule.name)
        with self._lock:
            self._stats["endpoint_hits"][endpoint] += 1
        return result
    def _get_or_create_counter(self, key: str, rule: RateLimitRule) -> Any:
        """Return counter for key, creating it if absent."""
        if key in self._counters:
            return self._counters[key]
        if rule.strategy == RateLimitStrategy.SLIDING_WINDOW:
            counter: Any = SlidingWindowCounter(rule.max_requests, rule.window_seconds)
        elif rule.strategy == RateLimitStrategy.TOKEN_BUCKET:
            capacity = rule.burst_limit or rule.max_requests
            refill = rule.refill_rate or (rule.max_requests / rule.window_seconds)
            counter = TokenBucket(capacity, refill)
        elif rule.strategy == RateLimitStrategy.FIXED_WINDOW:
            counter = FixedWindowCounter(rule.max_requests, rule.window_seconds)
        else:
            counter = SlidingWindowCounter(rule.max_requests, rule.window_seconds)
        self._counters[key] = counter
        self._counter_meta[key] = {"last_used": time.time(), "window": rule.window_seconds, "rule": rule.rule_id}
        return counter
    def _build_counter_key(
        self,
        rule: RateLimitRule,
        endpoint: str = "",
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        user_role: Optional[str] = None,
        api_key_id: Optional[str] = None,
    ) -> str:
        """Build a stable key used to store counters based on scope."""
        if rule.scope == RateLimitScope.GLOBAL:
            return f"{rule.rule_id}:global"
        if rule.scope == RateLimitScope.PER_USER:
            return f"{rule.rule_id}:user:{user_id or 'anon'}"
        if rule.scope == RateLimitScope.PER_IP:
            return f"{rule.rule_id}:ip:{ip_address or 'unknown'}"
        if rule.scope == RateLimitScope.PER_ENDPOINT:
            return f"{rule.rule_id}:ep:{endpoint}"
        if rule.scope == RateLimitScope.PER_USER_ENDPOINT:
            return f"{rule.rule_id}:ue:{user_id or 'anon'}:{endpoint}"
        if rule.scope == RateLimitScope.PER_ROLE:
            return f"{rule.rule_id}:role:{user_role or 'unknown'}"
        if rule.scope == RateLimitScope.PER_API_KEY:
            return f"{rule.rule_id}:key:{api_key_id or 'unknown'}"
        return f"{rule.rule_id}:misc:{endpoint}"
    def _rule_matches_request(
        self,
        rule: RateLimitRule,
        endpoint: str,
        user_id: Optional[str],
        user_role: Optional[str],
        ip_address: Optional[str],
        api_key_id: Optional[str],
    ) -> bool:
        """Check if a rule applies to this request."""
        if not rule.enabled:
            return False
        if rule.applies_to_endpoints:
            if not any(endpoint.startswith(ep) for ep in rule.applies_to_endpoints):
                return False
        if rule.applies_to_roles:
            if not user_role or user_role not in rule.applies_to_roles:
                return False
        if rule.scope == RateLimitScope.GLOBAL:
            return True
        if rule.scope == RateLimitScope.PER_USER:
            return bool(user_id)
        if rule.scope == RateLimitScope.PER_IP:
            return bool(ip_address)
        if rule.scope == RateLimitScope.PER_ENDPOINT:
            return bool(endpoint)
        if rule.scope == RateLimitScope.PER_USER_ENDPOINT:
            return bool(user_id and endpoint)
        if rule.scope == RateLimitScope.PER_ROLE:
            return bool(user_role)
        if rule.scope == RateLimitScope.PER_API_KEY:
            return bool(api_key_id)
        return False
    def get_rate_limit_headers(self, result: RateLimitResult) -> Dict[str, str]:
        """Generate HTTP headers describing the rate limit state."""
        headers = {
            "X-RateLimit-Limit": str(result.max_allowed),
            "X-RateLimit-Remaining": str(max(result.remaining, 0)),
            "X-RateLimit-Reset": str(int(result.reset_at or 0)),
        }
        if not result.allowed:
            headers["Retry-After"] = str(result.retry_after or 60)
        if result.scope:
            headers["X-RateLimit-Scope"] = result.scope
        return headers
    def get_stats(self) -> RateLimitStats:
        """Return a snapshot of rate limiter statistics."""
        with self._lock:
            total = self._stats.get("total_requests", 0)
            allowed = self._stats.get("allowed", 0)
            rejected = self._stats.get("rejected", 0)
            throttled = self._stats.get("throttled", 0)
            alerted = self._stats.get("alerted", 0)
            rules_eval = self._stats.get("rules_evaluated", 0)
            key_counts = self._stats.get("rejection_details", {})
            endpoint_hits = self._stats.get("endpoint_hits", {})
        top_keys = sorted(
            ({"key": k, "count": v} for k, v in key_counts.items()),
            key=lambda x: x["count"],
            reverse=True,
        )[:5]
        top_endpoints = sorted(
            ({"endpoint": k, "count": v} for k, v in endpoint_hits.items()),
            key=lambda x: x["count"],
            reverse=True,
        )[:5]
        now = datetime.utcnow()
        return RateLimitStats(
            total_requests=total,
            allowed_requests=allowed,
            rejected_requests=rejected,
            throttled_requests=throttled,
            alerted_requests=alerted,
            current_active_keys=len(self._counters),
            rules_evaluated=rules_eval,
            top_limited_keys=top_keys,
            top_limited_endpoints=top_endpoints,
            window_start=(now - timedelta(minutes=5)).isoformat() + "Z",
            window_end=now.isoformat() + "Z",
        )
    def reset_stats(self) -> None:
        """Reset all statistics counters."""
        with self._lock:
            self._stats = {
                "total_requests": 0,
                "allowed": 0,
                "rejected": 0,
                "throttled": 0,
                "alerted": 0,
                "rejection_details": defaultdict(int),
                "endpoint_hits": defaultdict(int),
            }
    def get_counter_info(self, key: Optional[str] = None) -> Dict[str, Any]:
        """Return debug info about counters."""
        info: Dict[str, Any] = {}
        if key:
            counter = self._counters.get(key)
            meta = self._counter_meta.get(key, {})
            if not counter:
                return {}
            details: Dict[str, Any] = {"meta": meta}
            if isinstance(counter, SlidingWindowCounter):
                details.update({"strategy": "sliding_window", "count": counter.get_count(), "window": counter.window_seconds})
            elif isinstance(counter, TokenBucket):
                details.update({"strategy": "token_bucket"})
            elif isinstance(counter, FixedWindowCounter):
                details.update({"strategy": "fixed_window", "count": counter._count, "window_start": counter._window_start})
            info[key] = details
            return info
        info["total_counters"] = len(self._counters)
        scopes = defaultdict(int)
        for ckey in self._counters:
            parts = ckey.split(":", 1)
            if len(parts) > 1:
                scopes[parts[0]] += 1
        info["by_rule"] = scopes
        return info
    def cleanup_expired_counters(self) -> int:
        """Remove counters that have not been used for 2x their window."""
        now = time.time()
        removed = 0
        for key, meta in list(self._counter_meta.items()):
            last_used = meta.get("last_used", now)
            window = meta.get("window", 60)
            if now - last_used > 2 * window:
                self._counters.pop(key, None)
                self._counter_meta.pop(key, None)
                removed += 1
        return removed
    def register_alert_callback(self, callback: Callable[[str, str, int], None]) -> None:
        """Register alert callback invoked on ALERT actions."""
        self._alert_callbacks.append(callback)
    def _trigger_alerts(self, key: str, rule: RateLimitRule, count: int) -> None:
        """Call registered alert callbacks and log warnings."""
        self.logger.warning("Rate limit alert for %s via rule %s", key, rule.name)
        for cb in self._alert_callbacks:
            try:
                cb(key, rule.name, count)
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("Alert callback failed: %s", exc)
    def is_healthy(self) -> bool:
        """Consider healthy if fewer than half of requests are rejected."""
        with self._lock:
            total = self._stats.get("total_requests", 0)
            rejected = self._stats.get("rejected", 0)
        if total == 0:
            return True
        return (rejected / total) < 0.5
    def get_user_usage(self, user_id: str) -> Dict[str, Any]:
        """Return usage summary for a given user across relevant scopes."""
        summary: Dict[str, Any] = {"user_id": user_id, "endpoints": {}, "overall": {}}
        overall_count = 0
        overall_limit = 0
        for rule in self._rules:
            if rule.scope not in {RateLimitScope.PER_USER, RateLimitScope.PER_USER_ENDPOINT}:
                continue
            for key, counter in self._counters.items():
                if not key.startswith(rule.rule_id):
                    continue
                if rule.scope == RateLimitScope.PER_USER and f":user:{user_id}" not in key:
                    continue
                if rule.scope == RateLimitScope.PER_USER_ENDPOINT and f":ue:{user_id}:" not in key:
                    continue
                count = 0
                remaining = 0
                if isinstance(counter, SlidingWindowCounter):
                    count = counter.get_count()
                    remaining = max(rule.max_requests - count, 0)
                elif isinstance(counter, FixedWindowCounter):
                    count = counter._count
                    remaining = max(rule.max_requests - count, 0)
                elif isinstance(counter, TokenBucket):
                    count = rule.max_requests - int(counter._tokens)
                    remaining = max(int(counter._tokens), 0)
                endpoint = key.split(":ue:")[-1].split(":", 1)[-1] if rule.scope == RateLimitScope.PER_USER_ENDPOINT else "*"
                summary["endpoints"].setdefault(endpoint, {"count": 0, "limit": rule.max_requests, "remaining": rule.max_requests})
                summary["endpoints"][endpoint]["count"] = count
                summary["endpoints"][endpoint]["remaining"] = remaining
                overall_count += count
                overall_limit = max(overall_limit, rule.max_requests)
        summary["overall"] = {"count": overall_count, "limit": overall_limit}
        return summary
class RateLimitMiddleware:
    """FastAPI middleware for automatic rate limiting."""
    def __init__(self, app, rate_limiter: Optional[RateLimiter] = None):
        self.app = app
        self.rate_limiter = rate_limiter or _rate_limiter
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        endpoint = request.url.path
        ip = request.client.host if request.client else "unknown"
        user_id = request.headers.get("X-User-ID")
        user_role = request.headers.get("X-User-Role")
        api_key = request.headers.get("X-API-Key")
        result = self.rate_limiter.check_rate_limit(
            endpoint=endpoint,
            user_id=user_id,
            user_role=user_role,
            ip_address=ip,
            api_key_id=api_key[:8] if api_key else None,
        )
        if not result.allowed and result.action == RateLimitAction.REJECT:
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "Rate limit exceeded",
                    "message": result.message,
                    "retry_after": result.retry_after,
                    "limit": result.max_allowed,
                    "window_seconds": result.window_seconds,
                },
                headers=self.rate_limiter.get_rate_limit_headers(result),
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
_rate_limiter = RateLimiter()
def get_rate_limiter() -> RateLimiter:
    """Return the module-level RateLimiter instance."""
    return _rate_limiter
async def check_rate_limit_dependency(
    request: Request, limiter: RateLimiter = Depends(get_rate_limiter)
) -> RateLimitResult:
    """FastAPI dependency that checks rate limits for the current request."""
    endpoint = request.url.path
    ip = request.client.host if request.client else "unknown"
    user_id = getattr(request.state, "user_id", None) or request.headers.get("X-User-ID")
    user_role = getattr(request.state, "user_role", None) or request.headers.get("X-User-Role")
    api_key = request.headers.get("X-API-Key")
    result = limiter.check_rate_limit(
        endpoint=endpoint,
        user_id=user_id,
        user_role=user_role,
        ip_address=ip,
        api_key_id=api_key[:8] if api_key else None,
    )
    if not result.allowed and result.action == RateLimitAction.REJECT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "Rate limit exceeded",
                "message": result.message,
                "retry_after": result.retry_after,
                "limit": result.max_allowed,
                "remaining": 0,
            },
            headers=limiter.get_rate_limit_headers(result),
        )
    return result
def rate_limit(max_requests: int = 100, window_seconds: int = 60, scope: str = "per_user") -> Callable:
    """Decorator-style dependency for custom endpoint-level rate limits."""
    def dependency(request: Request) -> RateLimitResult:
        endpoint = request.url.path
        ip = request.client.host if request.client else "unknown"
        user_id = getattr(request.state, "user_id", request.headers.get("X-User-ID"))
        key = f"inline:{scope}:{endpoint}:{user_id or ip}"
        if key not in _rate_limiter._counters:
            _rate_limiter._counters[key] = SlidingWindowCounter(max_requests, window_seconds)
            _rate_limiter._counter_meta[key] = {"last_used": time.time(), "window": window_seconds, "rule": "inline"}
        counter = _rate_limiter._counters[key]
        allowed, current, remaining = counter.allow()
        _rate_limiter._counter_meta[key]["last_used"] = time.time()
        result = RateLimitResult(
            allowed=allowed,
            action=RateLimitAction.ALLOW if allowed else RateLimitAction.REJECT,
            current_count=current,
            max_allowed=max_requests,
            remaining=remaining,
            window_seconds=window_seconds,
            reset_at=counter.get_reset_time(),
            retry_after=window_seconds if not allowed else None,
            scope=scope,
            key=key,
            message="" if allowed else f"Rate limit exceeded: {max_requests} requests per {window_seconds}s",
        )
        if not allowed:
            _rate_limiter._stats["rejected"] += 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"error": "Rate limit exceeded", "retry_after": window_seconds},
                headers=_rate_limiter.get_rate_limit_headers(result),
            )
        _rate_limiter._stats["allowed"] += 1
        return result
    return dependency
rate_limit_router = APIRouter(prefix="/api/v1/rate-limits", tags=["Rate Limiting"])
@rate_limit_router.get("/stats")
async def get_rate_limit_stats():
    """Return rate limiter statistics."""
    return _rate_limiter.get_stats()
@rate_limit_router.get("/rules")
async def get_rate_limit_rules():
    """Return configured rate limit rules."""
    return [rule.model_dump() for rule in _rate_limiter.get_rules()]
@rate_limit_router.get("/usage/{user_id}")
async def get_user_usage(user_id: str):
    """Return rate limit usage for a given user."""
    return _rate_limiter.get_user_usage(user_id)
@rate_limit_router.get("/health")
async def rate_limit_health():
    """Health endpoint for rate limiter."""
    return {"healthy": _rate_limiter.is_healthy(), "stats": _rate_limiter.get_stats()}
@rate_limit_router.post("/cleanup")
async def cleanup_counters():
    """Cleanup expired counters and return removal count."""
    removed = _rate_limiter.cleanup_expired_counters()
    return {"removed_counters": removed}
if __name__ == "__main__":
    limiter = RateLimiter()
    print("=== MEDI-COMPLY Rate Limiter Demo ===\n")
    print("Active Rules:")
    for rule in limiter.get_rules():
        print(
            f"  [{rule.priority}] {rule.name}: {rule.max_requests}/{rule.window_seconds}s ({rule.scope}) → {rule.action_on_limit}"
        )
    print("\n--- Simulating User Requests ---")
    user_id = "USR-CODER-001"
    endpoint = "/api/v1/coding/process"
    for i in range(25):
        result = limiter.check_rate_limit(
            endpoint=endpoint,
            user_id=user_id,
            user_role="CODER",
            ip_address="192.168.1.100",
        )
        if not result.allowed:
            print(f"  Request {i+1}: REJECTED — {result.message}")
            print(f"    Rule: {result.rule_name}")
            print(f"    Retry after: {result.retry_after}s")
            break
        elif i % 5 == 0:
            print(f"  Request {i+1}: ALLOWED (remaining: {result.remaining})")
    print("\n--- Simulating Login Brute Force ---")
    login_endpoint = "/api/v1/auth/login"
    for i in range(12):
        result = limiter.check_rate_limit(
            endpoint=login_endpoint,
            ip_address="10.0.0.1",
        )
        if not result.allowed:
            print(f"  Login attempt {i+1}: BLOCKED — {result.message}")
            break
        elif i % 3 == 0:
            print(f"  Login attempt {i+1}: Allowed (remaining: {result.remaining})")
    print("\n--- Simulating Bulk Audit Access ---")
    audit_endpoint = "/api/v1/audit/search"
    for i in range(55):
        result = limiter.check_rate_limit(
            endpoint=audit_endpoint,
            user_id="USR-AUDITOR-001",
            user_role="AUDITOR",
            ip_address="192.168.1.200",
        )
        if result.action == RateLimitAction.ALERT:
            print(f"  Request {i+1}: ALERT triggered — potential exfiltration")
            break
    print("\n--- Rate Limit Stats ---")
    stats = limiter.get_stats()
    print(f"Total requests: {stats.total_requests}")
    print(f"Allowed: {stats.allowed_requests}")
    print(f"Rejected: {stats.rejected_requests}")
    print(f"Active counters: {stats.current_active_keys}")
    print("\n--- User Usage ---")
    usage = limiter.get_user_usage(user_id)
    print(f"User {user_id}:")
    for ep, info in usage.get("endpoints", {}).items():
        print(f"  {ep}: {info}")
    print(f"\nHealthy: {limiter.is_healthy()}")
    removed = limiter.cleanup_expired_counters()
    print(f"Cleaned up {removed} expired counters")
