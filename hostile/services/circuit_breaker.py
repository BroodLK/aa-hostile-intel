"""
Circuit breaker implementation for external API resilience (ESI, zKillboard)
"""

# Standard Library
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

# Django
from django.core.cache import cache

# AA Hostile Intel
from hostile.app_settings import (
    HOSTILE_CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    HOSTILE_CIRCUIT_BREAKER_RECOVERY_TIMEOUT,
)

logger = logging.getLogger(__name__)


class CircuitBreakerOpenException(Exception):
    """Raised when an operation is attempted while the circuit breaker is OPEN"""

    pass


class CircuitBreaker:
    """
    Lightweight circuit breaker for protecting background tasks and web requests
    from cascading failures during external API outages.
    Uses Django cache backend for distributed state coordination across processes.
    """

    STATE_CLOSED = "CLOSED"
    STATE_OPEN = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(
        self,
        name: str,
        failure_threshold: int | None = None,
        recovery_timeout: float | None = None,
    ):
        self.name = name
        self.failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else HOSTILE_CIRCUIT_BREAKER_FAILURE_THRESHOLD
        )
        self.recovery_timeout = (
            recovery_timeout
            if recovery_timeout is not None
            else HOSTILE_CIRCUIT_BREAKER_RECOVERY_TIMEOUT
        )
        self._cache_prefix = f"hostile_circuit_{self.name}"

    def _get_key(self, suffix: str) -> str:
        return f"{self._cache_prefix}_{suffix}"

    def get_state(self) -> str:
        """Determines the current circuit breaker state"""
        state = cache.get(self._get_key("state"), self.STATE_CLOSED)
        if state == self.STATE_OPEN:
            opened_at = cache.get(self._get_key("opened_at"), 0.0)
            if time.time() - opened_at >= self.recovery_timeout:
                logger.info(
                    "Circuit breaker '%s' recovery timeout elapsed. Transitioning to HALF_OPEN probe state.",
                    self.name,
                )
                self._set_state(self.STATE_HALF_OPEN)
                return self.STATE_HALF_OPEN
        return state

    def _set_state(self, state: str) -> None:
        cache.set(self._get_key("state"), state, timeout=86400)

    def is_open(self) -> bool:
        """Returns True if the circuit is currently blocking requests"""
        return self.get_state() == self.STATE_OPEN

    def can_execute(self) -> bool:
        """Returns True if a request is permitted to proceed"""
        state = self.get_state()
        return state in (self.STATE_CLOSED, self.STATE_HALF_OPEN)

    def record_success(self) -> None:
        """Records a successful operation, resetting failure counters and closing the circuit"""
        curr_state = cache.get(self._get_key("state"), self.STATE_CLOSED)
        if curr_state != self.STATE_CLOSED:
            logger.info(
                "Circuit breaker '%s' recovered successfully. Resetting to CLOSED state.",
                self.name,
            )
        cache.delete(self._get_key("failures"))
        cache.delete(self._get_key("opened_at"))
        self._set_state(self.STATE_CLOSED)

    def record_failure(self, exception: Exception | None = None) -> None:
        """Records an operation failure, tripping to OPEN if failure threshold is met"""
        failures = cache.get(self._get_key("failures"), 0) + 1
        cache.set(self._get_key("failures"), failures, timeout=86400)
        logger.warning(
            "Circuit breaker '%s' recorded failure (%d/%d): %s",
            self.name,
            failures,
            self.failure_threshold,
            exception,
        )

        curr_state = self.get_state()
        if curr_state == self.STATE_HALF_OPEN or failures >= self.failure_threshold:
            logger.error(
                "Circuit breaker '%s' TRIPPED to OPEN state. All external requests will fail fast for %.1f seconds.",
                self.name,
                self.recovery_timeout,
            )
            self._set_state(self.STATE_OPEN)
            cache.set(self._get_key("opened_at"), time.time(), timeout=86400)

    def reset(self) -> None:
        """Manually resets the circuit breaker to CLOSED"""
        cache.delete(self._get_key("failures"))
        cache.delete(self._get_key("opened_at"))
        self._set_state(self.STATE_CLOSED)

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Executes the callable guarded by the circuit breaker"""
        if not self.can_execute():
            raise CircuitBreakerOpenException(
                f"Circuit breaker '{self.name}' is OPEN. Fast-failing external call."
            )
        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as exc:
            self.record_failure(exc)
            raise


# Pre-configured circuit breakers for external integrations
esi_circuit_breaker = CircuitBreaker("esi")
zkill_circuit_breaker = CircuitBreaker("zkill")
