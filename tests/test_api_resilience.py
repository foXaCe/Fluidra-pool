"""Tests for API resilience patterns (circuit breaker, rate limiter)."""

from __future__ import annotations

import time

import pytest

from custom_components.fluidra_pool.api_resilience import (
    CIRCUIT_BREAKER_FAILURES,
    CIRCUIT_BREAKER_TIMEOUT,
    CircuitBreaker,
    CircuitState,
    FluidraAuthError,
    FluidraCircuitBreakerError,
    FluidraConnectionError,
    FluidraError,
    FluidraRateLimitError,
    RateLimiter,
)

# --- Exception hierarchy ---


class TestExceptionHierarchy:
    """Test exception class hierarchy."""

    def test_base_exception(self):
        err = FluidraError("test")
        assert isinstance(err, Exception)
        assert str(err) == "test"

    def test_auth_error_inherits_from_base(self):
        err = FluidraAuthError("auth failed")
        assert isinstance(err, FluidraError)
        assert isinstance(err, Exception)

    def test_connection_error_inherits_from_base(self):
        err = FluidraConnectionError("timeout")
        assert isinstance(err, FluidraError)

    def test_rate_limit_error_inherits_from_base(self):
        err = FluidraRateLimitError("too many requests")
        assert isinstance(err, FluidraError)

    def test_circuit_breaker_error_inherits_from_base(self):
        err = FluidraCircuitBreakerError("circuit open")
        assert isinstance(err, FluidraError)

    def test_catch_all_fluidra_errors(self):
        """All specific errors can be caught with FluidraError."""
        for exc_class in (FluidraAuthError, FluidraConnectionError, FluidraRateLimitError, FluidraCircuitBreakerError):
            with pytest.raises(FluidraError):
                raise exc_class("test")


# --- Circuit Breaker ---


class TestCircuitBreaker:
    """Test CircuitBreaker implementation."""

    def test_initial_state_is_closed(self):
        cb = CircuitBreaker()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.can_execute()

    def test_can_execute_when_closed(self):
        cb = CircuitBreaker()
        assert cb.can_execute() is True

    def test_record_success_resets_failure_count(self):
        cb = CircuitBreaker()
        cb.failure_count = 3
        cb.record_success()
        assert cb.failure_count == 0

    def test_record_failure_increments_count(self):
        cb = CircuitBreaker()
        cb.record_failure()
        assert cb.failure_count == 1
        cb.record_failure()
        assert cb.failure_count == 2

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_blocks_execution_when_open(self):
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_transitions_to_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # Simulate time passing
        cb.last_failure_time = time.monotonic() - 15.0
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_closes_after_successes(self):
        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN

        cb.record_success()
        assert cb.state == CircuitState.HALF_OPEN  # Need 2 successes

        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0
        assert cb.success_count == 0

    def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_half_open_allows_execution(self):
        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN
        assert cb.can_execute() is True

    def test_failure_resets_success_count(self):
        cb = CircuitBreaker()
        cb.state = CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.success_count == 1
        cb.record_failure()
        assert cb.success_count == 0

    def test_default_threshold_matches_constant(self):
        cb = CircuitBreaker()
        assert cb.failure_threshold == CIRCUIT_BREAKER_FAILURES

    def test_default_timeout_matches_constant(self):
        cb = CircuitBreaker()
        assert cb.recovery_timeout == CIRCUIT_BREAKER_TIMEOUT

    def test_not_yet_timed_out_stays_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=300.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # last_failure_time is very recent, should stay open
        assert cb.can_execute() is False


# --- Rate Limiter ---


class TestRateLimiter:
    """Test RateLimiter implementation."""

    def test_initial_state_allows_execution(self):
        rl = RateLimiter()
        assert rl.can_execute() is True

    def test_records_request(self):
        rl = RateLimiter()
        rl.record_request()
        assert len(rl._timestamps) == 1

    def test_blocks_after_max_requests(self):
        rl = RateLimiter(max_requests=3, window_seconds=60.0)
        rl.record_request()
        rl.record_request()
        rl.record_request()
        assert rl.can_execute() is False

    def test_allows_after_window_expires(self):
        rl = RateLimiter(max_requests=2, window_seconds=10.0)
        rl.record_request()
        rl.record_request()
        assert rl.can_execute() is False

        # Simulate old timestamps
        old_time = time.monotonic() - 15.0
        rl._timestamps.clear()
        rl._timestamps.append(old_time)
        rl._timestamps.append(old_time)
        assert rl.can_execute() is True

    def test_wait_time_zero_when_can_execute(self):
        rl = RateLimiter()
        assert rl.wait_time() == 0.0

    def test_wait_time_positive_when_rate_limited(self):
        rl = RateLimiter(max_requests=1, window_seconds=60.0)
        rl.record_request()
        wait = rl.wait_time()
        assert wait > 0.0
        assert wait <= 60.0

    def test_sliding_window_cleans_old_entries(self):
        rl = RateLimiter(max_requests=2, window_seconds=5.0)

        # Add old timestamps
        old_time = time.monotonic() - 10.0
        rl._timestamps.append(old_time)
        rl._timestamps.append(old_time)

        # Should clean old entries and allow execution
        assert rl.can_execute() is True

    def test_exact_boundary_at_max_requests(self):
        rl = RateLimiter(max_requests=5, window_seconds=60.0)
        for _ in range(4):
            rl.record_request()
        assert rl.can_execute() is True
        rl.record_request()
        assert rl.can_execute() is False
