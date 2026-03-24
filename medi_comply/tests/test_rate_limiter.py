import threading
import time
from typing import List

import pytest

from medi_comply.api.rate_limiter import (
    FixedWindowCounter,
    RateLimitAction,
    RateLimitResult,
    RateLimitRule,
    RateLimitScope,
    RateLimitStrategy,
    RateLimiter,
    SlidingWindowCounter,
    TokenBucket,
)


@pytest.fixture
def limiter() -> RateLimiter:
    """Fresh rate limiter with default rules."""
    return RateLimiter()


@pytest.fixture
def empty_limiter() -> RateLimiter:
    """Rate limiter with no rules."""
    rl = RateLimiter(rules=[])
    rl._rules.clear()
    rl._counters.clear()
    rl._counter_meta.clear()
    return rl


@pytest.fixture
def strict_limiter() -> RateLimiter:
    """Rate limiter with very low limits for testing."""
    rules = [
        RateLimitRule(
            name="Strict Test",
            scope=RateLimitScope.PER_USER,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
            max_requests=3,
            window_seconds=60,
            action_on_limit=RateLimitAction.REJECT,
            retry_after_seconds=10,
        )
    ]
    return RateLimiter(rules=rules)


class TestSlidingWindowCounter:
    def test_allows_under_limit(self):
        """Requests under limit pass."""
        counter = SlidingWindowCounter(3, 60)
        assert counter.allow()[0]
        assert counter.allow()[0]
        assert counter.allow()[0]

    def test_rejects_over_limit(self):
        """Requests over limit rejected."""
        counter = SlidingWindowCounter(2, 60)
        counter.allow()
        counter.allow()
        allowed, current, remaining = counter.allow()
        assert not allowed
        assert current == 2
        assert remaining == 0

    def test_returns_remaining(self):
        """Remaining count correct."""
        counter = SlidingWindowCounter(4, 60)
        counter.allow()
        allowed, current, remaining = counter.allow()
        assert allowed
        assert current == 2
        assert remaining == 2

    def test_window_expiry(self):
        """Old requests expire after window."""
        counter = SlidingWindowCounter(2, 1)
        counter.allow()
        counter.allow()
        time.sleep(1.1)
        allowed, current, remaining = counter.allow()
        assert allowed
        assert current == 1
        assert remaining == 1

    def test_reset_time(self):
        """Reset time is in the future."""
        counter = SlidingWindowCounter(2, 5)
        counter.allow()
        assert counter.get_reset_time() > time.time()

    def test_concurrent_safe(self):
        """Multiple rapid calls don't crash."""
        counter = SlidingWindowCounter(5, 60)
        results: List[bool] = []

        def worker():
            allowed, _, _ = counter.allow()
            results.append(allowed)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 10
        assert sum(results) <= 5


class TestTokenBucket:
    def test_allows_burst(self):
        """Burst up to capacity allowed."""
        bucket = TokenBucket(3, 1)
        assert bucket.allow()[0]
        assert bucket.allow()[0]
        assert bucket.allow()[0]
        assert not bucket.allow()[0]

    def test_rejects_empty(self):
        """No tokens left → rejected."""
        bucket = TokenBucket(1, 0.1)
        bucket.allow()
        allowed, tokens, capacity = bucket.allow()
        assert not allowed
        assert tokens <= capacity

    def test_refills_over_time(self):
        """Tokens refill after time passes."""
        bucket = TokenBucket(2, 2.0)
        bucket.allow()
        bucket.allow()
        time.sleep(0.6)
        assert bucket.allow()[0]

    def test_wait_time_positive(self):
        """Wait time > 0 when empty."""
        bucket = TokenBucket(1, 1.0)
        bucket.allow()
        assert bucket.get_wait_time() > 0

    def test_wait_time_zero(self):
        """Wait time = 0 when tokens available."""
        bucket = TokenBucket(2, 1.0)
        assert bucket.get_wait_time() == 0


class TestFixedWindowCounter:
    def test_allows_in_window(self):
        """Requests in window pass."""
        counter = FixedWindowCounter(2, 5)
        assert counter.allow()[0]
        assert counter.allow()[0]

    def test_rejects_over_limit(self):
        """Over limit in same window rejected."""
        counter = FixedWindowCounter(2, 5)
        counter.allow()
        counter.allow()
        allowed, current, remaining = counter.allow()
        assert not allowed
        assert current == 2
        assert remaining == 0

    def test_new_window_resets(self):
        """New window resets count."""
        counter = FixedWindowCounter(1, 1)
        counter.allow()
        assert not counter.allow()[0]
        time.sleep(1.1)
        assert counter.allow()[0]

    def test_reset_time(self):
        """Reset time calculation correct."""
        counter = FixedWindowCounter(1, 2)
        start = counter._window_start
        assert abs(counter.get_reset_time() - (start + 2)) < 0.1


class TestRateLimiterInit:
    def test_default_rules_loaded(self, limiter):
        """Default rules present."""
        assert len(limiter.get_rules()) >= 10

    def test_custom_rules(self):
        """Custom rules accepted."""
        rule = RateLimitRule(name="Custom", scope=RateLimitScope.GLOBAL)
        rl = RateLimiter(rules=[rule])
        assert rl.get_rules()[0].name == "Custom"

    def test_empty_rules(self, empty_limiter):
        """Empty rules list → no rules."""
        assert empty_limiter.get_rules() == []

    def test_rules_sorted_by_priority(self):
        """Higher priority first."""
        r1 = RateLimitRule(name="Low", priority=1)
        r2 = RateLimitRule(name="High", priority=5)
        rl = RateLimiter(rules=[r1, r2])
        assert rl.get_rules()[0].name == "High"


class TestCheckRateLimit:
    def test_allows_normal_request(self, strict_limiter):
        """Standard request passes."""
        result = strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        assert result.allowed

    def test_rejects_over_limit(self, strict_limiter):
        """Exceeding limit returns reject."""
        for _ in range(3):
            strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        result = strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        assert not result.allowed
        assert result.action == RateLimitAction.REJECT
        assert result.retry_after == 10

    def test_per_user_isolation(self, strict_limiter):
        """Different users have separate counters."""
        for _ in range(3):
            strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        result_other = strict_limiter.check_rate_limit(endpoint="/x", user_id="u2")
        assert result_other.allowed

    def test_per_ip_isolation(self):
        """Different IPs have separate counters."""
        rule = RateLimitRule(
            name="Per IP",
            scope=RateLimitScope.PER_IP,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
            max_requests=1,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/x", ip_address="1.1.1.1")
        result = rl.check_rate_limit(endpoint="/x", ip_address="2.2.2.2")
        assert result.allowed

    def test_endpoint_specific_rule(self):
        """Endpoint-specific rule enforced."""
        rule = RateLimitRule(
            name="Endpoint",
            scope=RateLimitScope.PER_USER_ENDPOINT,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
            max_requests=1,
            window_seconds=60,
            applies_to_endpoints=["/only"],
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/only", user_id="u1")
        result = rl.check_rate_limit(endpoint="/only", user_id="u1")
        assert not result.allowed
        assert result.rule_name == "Endpoint"

    def test_global_limit_applies(self):
        """Global limit checked for all."""
        rule = RateLimitRule(
            name="Global",
            scope=RateLimitScope.GLOBAL,
            strategy=RateLimitStrategy.SLIDING_WINDOW,
            max_requests=2,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        rl.check_rate_limit(endpoint="/b")
        result = rl.check_rate_limit(endpoint="/c")
        assert not result.allowed
        assert result.rule_id == rule.rule_id

    def test_result_has_remaining(self, strict_limiter):
        """Result includes remaining count."""
        result = strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        assert result.remaining >= 0

    def test_result_has_retry_after(self, strict_limiter):
        """Rejected result has retry_after."""
        for _ in range(3):
            strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        result = strict_limiter.check_rate_limit(endpoint="/x", user_id="u1")
        assert result.retry_after == 10


class TestRuleMatching:
    def test_endpoint_filter(self):
        """Rule with endpoint filter only matches that endpoint."""
        rule = RateLimitRule(
            name="EP",
            scope=RateLimitScope.PER_USER_ENDPOINT,
            applies_to_endpoints=["/only"],
            max_requests=1,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[rule])
        other = rl.check_rate_limit(endpoint="/other", user_id="u")
        assert other.allowed
        assert other.rule_name is None
        rl.check_rate_limit(endpoint="/only", user_id="u")
        reject = rl.check_rate_limit(endpoint="/only", user_id="u")
        assert not reject.allowed

    def test_role_filter(self):
        """Rule with role filter only matches that role."""
        rule = RateLimitRule(
            name="Role",
            scope=RateLimitScope.PER_ROLE,
            applies_to_roles=["ADMIN"],
            max_requests=1,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[rule])
        other = rl.check_rate_limit(endpoint="/x", user_role="CODER")
        assert other.allowed
        rl.check_rate_limit(endpoint="/x", user_role="ADMIN")
        reject = rl.check_rate_limit(endpoint="/x", user_role="ADMIN")
        assert not reject.allowed

    def test_no_filter_matches_all(self):
        """Rule without filters matches everything."""
        rule = RateLimitRule(name="Any", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=60)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        reject = rl.check_rate_limit(endpoint="/b")
        assert not reject.allowed

    def test_disabled_rule_skipped(self):
        """Disabled rule not evaluated."""
        rule = RateLimitRule(name="Off", enabled=False, scope=RateLimitScope.GLOBAL, max_requests=0)
        rl = RateLimiter(rules=[rule])
        result = rl.check_rate_limit(endpoint="/a")
        assert result.allowed
        assert result.rule_name is None

    def test_multiple_rules_evaluated(self):
        """All matching rules checked."""
        r1 = RateLimitRule(name="User", scope=RateLimitScope.PER_USER, max_requests=2, window_seconds=60)
        r2 = RateLimitRule(
            name="Endpoint",
            scope=RateLimitScope.PER_USER_ENDPOINT,
            max_requests=2,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[r1, r2])
        rl.check_rate_limit(endpoint="/a", user_id="u")
        assert len(rl._counters) == 2

    def test_highest_priority_first(self):
        """Higher priority rules checked first."""
        r1 = RateLimitRule(name="Low", priority=1, scope=RateLimitScope.PER_USER, max_requests=5, window_seconds=60)
        r2 = RateLimitRule(
            name="High",
            priority=5,
            scope=RateLimitScope.PER_USER,
            max_requests=1,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[r1, r2])
        rl.check_rate_limit(endpoint="/a", user_id="u")
        reject = rl.check_rate_limit(endpoint="/a", user_id="u")
        assert reject.rule_name == "High"


class TestRateLimitActions:
    def test_reject_action(self):
        """REJECT stops request."""
        rule = RateLimitRule(name="R", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=60)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        result = rl.check_rate_limit(endpoint="/a")
        assert not result.allowed
        assert result.action == RateLimitAction.REJECT

    def test_alert_action(self):
        """ALERT allows but logs."""
        rule = RateLimitRule(
            name="Alert",
            scope=RateLimitScope.GLOBAL,
            max_requests=1,
            window_seconds=60,
            action_on_limit=RateLimitAction.ALERT,
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        result = rl.check_rate_limit(endpoint="/a")
        assert result.allowed
        assert result.action == RateLimitAction.ALERT
        assert rl._stats["alerted"] >= 1

    def test_throttle_action(self):
        """THROTTLE marks but allows caller to delay."""
        rule = RateLimitRule(
            name="Throttle",
            scope=RateLimitScope.GLOBAL,
            max_requests=1,
            window_seconds=60,
            action_on_limit=RateLimitAction.THROTTLE,
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        result = rl.check_rate_limit(endpoint="/a")
        assert not result.allowed
        assert result.action == RateLimitAction.THROTTLE
        assert rl._stats["throttled"] >= 1

    def test_allow_action(self):
        """ALLOW passes normally."""
        rule = RateLimitRule(
            name="Allow",
            scope=RateLimitScope.GLOBAL,
            max_requests=1,
            window_seconds=60,
            action_on_limit=RateLimitAction.ALLOW,
        )
        rl = RateLimiter(rules=[rule])
        result = rl.check_rate_limit(endpoint="/a")
        assert result.allowed


class TestRateLimitHeaders:
    def test_headers_on_allow(self):
        """Headers include limit and remaining."""
        result = RateLimitResult(max_allowed=5, remaining=4, reset_at=time.time() + 60)
        headers = RateLimiter().get_rate_limit_headers(result)
        assert headers["X-RateLimit-Limit"] == "5"
        assert headers["X-RateLimit-Remaining"] == "4"

    def test_headers_on_reject(self):
        """Headers include retry-after."""
        result = RateLimitResult(max_allowed=1, remaining=0, reset_at=time.time() + 60, retry_after=30, allowed=False)
        headers = RateLimiter().get_rate_limit_headers(result)
        assert headers.get("Retry-After") == "30"

    def test_header_format(self):
        """All headers are strings."""
        result = RateLimitResult(max_allowed=2, remaining=1, reset_at=time.time() + 10)
        headers = RateLimiter().get_rate_limit_headers(result)
        assert all(isinstance(v, str) for v in headers.values())


class TestRuleManagement:
    def test_add_rule(self, empty_limiter):
        """New rule added and sorted."""
        rule = RateLimitRule(name="Add", priority=2)
        empty_limiter.add_rule(rule)
        assert empty_limiter.get_rules()[0].name == "Add"

    def test_remove_rule(self, empty_limiter):
        """Rule removed by ID."""
        rule = RateLimitRule(name="Remove")
        empty_limiter.add_rule(rule)
        assert empty_limiter.remove_rule(rule.rule_id)
        assert not empty_limiter.get_rules()

    def test_update_rule(self, empty_limiter):
        """Rule parameters updated."""
        rule = RateLimitRule(name="Update", priority=1)
        empty_limiter.add_rule(rule)
        empty_limiter.update_rule(rule.rule_id, {"priority": 5})
        assert empty_limiter.get_rules()[0].priority == 5

    def test_get_rules(self, empty_limiter):
        """All rules returned."""
        rule = RateLimitRule(name="List")
        empty_limiter.add_rule(rule)
        assert len(empty_limiter.get_rules()) == 1

    def test_remove_clears_counters(self):
        """Removing rule clears its counters."""
        rule = RateLimitRule(name="Counter", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=60)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        assert rl.remove_rule(rule.rule_id)
        assert not rl._counters


class TestStats:
    def test_stats_initial(self, limiter):
        """All zeros initially."""
        stats = limiter.get_stats()
        assert stats.total_requests == 0
        assert stats.rejected_requests == 0

    def test_stats_count_requests(self, strict_limiter):
        """Total requests counted."""
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        stats = strict_limiter.get_stats()
        assert stats.total_requests == 1

    def test_stats_count_rejections(self, strict_limiter):
        """Rejections counted."""
        for _ in range(3):
            strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        stats = strict_limiter.get_stats()
        assert stats.rejected_requests >= 1

    def test_stats_count_allows(self, strict_limiter):
        """Allows counted."""
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        stats = strict_limiter.get_stats()
        assert stats.allowed_requests >= 1

    def test_reset_stats(self, strict_limiter):
        """Reset clears all counters."""
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        strict_limiter.reset_stats()
        stats = strict_limiter.get_stats()
        assert stats.total_requests == 0
        assert stats.allowed_requests == 0


class TestUserUsage:
    def test_user_usage_empty(self, empty_limiter):
        """No usage for unknown user."""
        usage = empty_limiter.get_user_usage("none")
        assert usage["overall"]["count"] == 0

    def test_user_usage_after_requests(self, strict_limiter):
        """Usage populated after requests."""
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        usage = strict_limiter.get_user_usage("u")
        assert usage["overall"]["count"] >= 1

    def test_user_usage_per_endpoint(self):
        """Endpoint breakdown correct."""
        rule = RateLimitRule(
            name="Per UE",
            scope=RateLimitScope.PER_USER_ENDPOINT,
            max_requests=2,
            window_seconds=60,
        )
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/e", user_id="u")
        usage = rl.get_user_usage("u")
        assert "/e" in usage["endpoints"]


class TestCleanup:
    def test_cleanup_removes_expired(self):
        """Old counters removed."""
        rule = RateLimitRule(name="Clean", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=1)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        # Mark counter as stale
        for meta in rl._counter_meta.values():
            meta["last_used"] = meta["last_used"] - 5
        removed = rl.cleanup_expired_counters()
        assert removed >= 1
        assert not rl._counters

    def test_cleanup_keeps_active(self):
        """Recent counters kept."""
        rule = RateLimitRule(name="Keep", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=60)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        removed = rl.cleanup_expired_counters()
        assert removed == 0
        assert rl._counters


class TestHealthCheck:
    def test_healthy_normal(self, strict_limiter):
        """Healthy when most requests allowed."""
        strict_limiter.check_rate_limit(endpoint="/a", user_id="u")
        assert strict_limiter.is_healthy()

    def test_unhealthy_many_rejections(self):
        """Unhealthy when too many rejections."""
        rule = RateLimitRule(name="Bad", scope=RateLimitScope.GLOBAL, max_requests=1, window_seconds=60)
        rl = RateLimiter(rules=[rule])
        rl.check_rate_limit(endpoint="/a")
        rl.check_rate_limit(endpoint="/a")
        rl.check_rate_limit(endpoint="/a")
        assert not rl.is_healthy()


class TestAlertCallbacks:
    def test_alert_callback_called(self):
        """Callback invoked on alert action."""
        calls: List[str] = []

        def cb(key, rule_name, count):
            calls.append(f"{key}:{rule_name}:{count}")

        rule = RateLimitRule(
            name="Alert",
            scope=RateLimitScope.GLOBAL,
            max_requests=1,
            window_seconds=60,
            action_on_limit=RateLimitAction.ALERT,
        )
        rl = RateLimiter(rules=[rule])
        rl.register_alert_callback(cb)
        rl.check_rate_limit(endpoint="/a")
        rl.check_rate_limit(endpoint="/a")
        assert calls

    def test_multiple_callbacks(self):
        """Multiple callbacks all called."""
        hits: List[int] = []
        rule = RateLimitRule(
            name="Alert",
            scope=RateLimitScope.GLOBAL,
            max_requests=1,
            window_seconds=60,
            action_on_limit=RateLimitAction.ALERT,
        )
        rl = RateLimiter(rules=[rule])

        def cb1(key, rule_name, count):
            hits.append(1)

        def cb2(key, rule_name, count):
            hits.append(2)

        rl.register_alert_callback(cb1)
        rl.register_alert_callback(cb2)
        rl.check_rate_limit(endpoint="/a")
        rl.check_rate_limit(endpoint="/a")
        assert 1 in hits and 2 in hits


class TestCustomEndpointRateLimit:
    def test_login_brute_force_blocked(self, limiter):
        """Login endpoint blocks after 10 attempts."""
        ip = "9.9.9.9"
        for _ in range(10):
            limiter.check_rate_limit(endpoint="/api/v1/auth/login", ip_address=ip)
        result = limiter.check_rate_limit(endpoint="/api/v1/auth/login", ip_address=ip)
        assert not result.allowed
        assert result.action == RateLimitAction.REJECT

    def test_coding_endpoint_limited(self, limiter):
        """Coding endpoint has lower limit."""
        user_id = "coder"
        for _ in range(20):
            limiter.check_rate_limit(endpoint="/api/v1/coding/process", user_id=user_id, user_role="CODER")
        result = limiter.check_rate_limit(endpoint="/api/v1/coding/process", user_id=user_id, user_role="CODER")
        assert not result.allowed
        assert result.rule_name == "Coding Endpoint Limit"

    def test_batch_endpoint_limited(self, limiter):
        """Batch endpoint has strict limit."""
        user_id = "coder"
        for _ in range(5):
            limiter.check_rate_limit(endpoint="/api/v1/claims/batch-adjudicate", user_id=user_id, user_role="CODER")
        result = limiter.check_rate_limit(endpoint="/api/v1/claims/batch-adjudicate", user_id=user_id, user_role="CODER")
        assert not result.allowed
        assert result.rule_name == "Claims Batch Limit"
