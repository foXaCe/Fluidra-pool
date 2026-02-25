"""API resilience patterns and exceptions for Fluidra Pool integration.

Provides circuit breaker, rate limiting, and structured exception hierarchy
for robust API communication.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import logging
import time
from typing import Final

# Circuit breaker configuration
CIRCUIT_BREAKER_FAILURES: Final = 5
CIRCUIT_BREAKER_TIMEOUT: Final = 300  # 5 minutes recovery time

# Rate limiting configuration
RATE_LIMIT_REQUESTS: Final = 30
RATE_LIMIT_WINDOW: Final = 60  # Per 60 seconds

# Retry configuration
MAX_RETRIES: Final = 3
INITIAL_BACKOFF: Final = 1.0
MAX_BACKOFF: Final = 30.0
BACKOFF_MULTIPLIER: Final = 2.0

_LOGGER = logging.getLogger(__name__)


# --- Exceptions ---


class FluidraError(Exception):
    """Base exception for Fluidra errors."""


class FluidraAuthError(FluidraError):
    """Exception for authentication errors."""


class FluidraConnectionError(FluidraError):
    """Exception for connection errors."""


class FluidraRateLimitError(FluidraError):
    """Exception for rate limit errors."""


class FluidraCircuitBreakerError(FluidraError):
    """Exception when circuit breaker is open."""


# --- Circuit Breaker ---


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    """Circuit breaker implementation for API resilience.

    Prevents cascade failures by stopping requests
    when the API is consistently failing.
    """

    failure_threshold: int = CIRCUIT_BREAKER_FAILURES
    recovery_timeout: float = CIRCUIT_BREAKER_TIMEOUT
    state: CircuitState = field(default=CircuitState.CLOSED)
    failure_count: int = field(default=0)
    last_failure_time: float = field(default=0.0)
    success_count: int = field(default=0)

    def record_success(self) -> None:
        """Record a successful request."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 2:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                _LOGGER.info("Circuit breaker closed after recovery")
        elif self.state == CircuitState.CLOSED:
            self.failure_count = 0

    def record_failure(self) -> None:
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.monotonic()
        self.success_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            _LOGGER.warning("Circuit breaker re-opened after failed recovery attempt")
        elif self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            _LOGGER.warning(
                "Circuit breaker opened after %d failures",
                self.failure_count,
            )

    def can_execute(self) -> bool:
        """Check if request can be executed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if time.monotonic() - self.last_failure_time >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                _LOGGER.info("Circuit breaker half-open, testing recovery")
                return True
            return False

        return True


# --- Rate Limiter ---


@dataclass
class RateLimiter:
    """Sliding window rate limiter.

    Prevents API abuse and ensures fair usage.
    """

    max_requests: int = RATE_LIMIT_REQUESTS
    window_seconds: float = RATE_LIMIT_WINDOW
    _timestamps: deque = field(default_factory=deque)

    def can_execute(self) -> bool:
        """Check if request can be executed within rate limits."""
        now = time.monotonic()
        while self._timestamps and now - self._timestamps[0] > self.window_seconds:
            self._timestamps.popleft()

        return len(self._timestamps) < self.max_requests

    def record_request(self) -> None:
        """Record a request timestamp."""
        self._timestamps.append(time.monotonic())

    def wait_time(self) -> float:
        """Return time to wait before next request is allowed."""
        if self.can_execute():
            return 0.0
        now = time.monotonic()
        oldest = self._timestamps[0]
        return max(0.0, self.window_seconds - (now - oldest))
