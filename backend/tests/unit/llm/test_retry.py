"""The retry loop, executed rather than merely written.

These are the paths that decide whether a rate limit is a two-second pause or a
failed batch, and none of them run in normal operation. An injected clock is
what makes them cheap enough to assert precisely: the suite exercises a
sixty-second backoff sequence in microseconds, and can check the exact
durations instead of only that some sleeping happened.
"""

from __future__ import annotations

import random

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.llm.errors import (
    AuthenticationError,
    LLMError,
    RateLimitError,
    RetryBudgetExhaustedError,
    ServiceUnavailableError,
    TransportError,
)
from offerdelta.infrastructure.llm.retry import (
    MAX_HONOURED_RETRY_AFTER_S,
    FakeClock,
    RetryPolicy,
    call_with_retry,
)

NO_JITTER = RetryPolicy(max_attempts=4, base_delay_s=1.0, max_delay_s=30.0, jitter=False)


def _failing(*errors: Exception):  # type: ignore[no-untyped-def]
    """An operation that raises each error in turn, then returns 'ok'."""
    queue = list(errors)
    calls: list[int] = []

    def operation() -> str:
        calls.append(1)
        if queue:
            raise queue.pop(0)
        return "ok"

    return operation, calls


# --- What gets retried, and what does not ----------------------------------


def test_a_fatal_error_is_not_retried() -> None:
    # Re-sending a rejected key costs time and money to be told the same thing.
    operation, calls = _failing(AuthenticationError("bad key"))
    clock = FakeClock()

    with pytest.raises(AuthenticationError):
        call_with_retry(operation, policy=NO_JITTER, clock=clock)

    assert len(calls) == 1
    assert clock.sleeps == []


def test_a_retryable_error_is_retried_until_it_succeeds() -> None:
    operation, calls = _failing(ServiceUnavailableError("502"), ServiceUnavailableError("502"))
    clock = FakeClock()

    assert call_with_retry(operation, policy=NO_JITTER, clock=clock) == "ok"
    assert len(calls) == 3


def test_a_transport_failure_is_retried() -> None:
    # Nothing was created, so a second attempt cannot duplicate anything.
    operation, calls = _failing(TransportError("connection reset"))

    assert call_with_retry(operation, policy=NO_JITTER, clock=FakeClock()) == "ok"
    assert len(calls) == 2


def test_success_on_the_first_attempt_never_sleeps() -> None:
    clock = FakeClock()

    assert call_with_retry(lambda: "ok", policy=NO_JITTER, clock=clock) == "ok"
    assert clock.sleeps == []


def test_max_attempts_of_one_disables_retrying() -> None:
    operation, calls = _failing(ServiceUnavailableError("502"))
    policy = RetryPolicy(max_attempts=1)

    with pytest.raises(RetryBudgetExhaustedError):
        call_with_retry(operation, policy=policy, clock=FakeClock())

    assert len(calls) == 1


# --- The shape of the backoff ----------------------------------------------


def test_the_backoff_doubles() -> None:
    operation, _ = _failing(*[ServiceUnavailableError("502")] * 4)
    clock = FakeClock()

    with pytest.raises(RetryBudgetExhaustedError):
        call_with_retry(operation, policy=NO_JITTER, clock=clock)

    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_the_backoff_is_capped() -> None:
    operation, _ = _failing(*[ServiceUnavailableError("502")] * 6)
    policy = RetryPolicy(max_attempts=6, base_delay_s=1.0, max_delay_s=3.0, jitter=False)
    clock = FakeClock()

    with pytest.raises(RetryBudgetExhaustedError):
        call_with_retry(operation, policy=policy, clock=clock)

    assert clock.sleeps == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_jitter_spreads_retries_across_the_whole_window() -> None:
    """The property that actually drains a rate-limited queue.

    Fixed backoff makes every rate-limited client return in the same
    millisecond and rebuild the spike. Sampling from [0, cap] spreads them.
    """
    policy = RetryPolicy(max_attempts=2, base_delay_s=8.0, max_delay_s=8.0, jitter=True)
    rng = random.Random(0)

    delays = []
    for _ in range(200):
        operation, _ = _failing(ServiceUnavailableError("502"))
        clock = FakeClock()
        call_with_retry(operation, policy=policy, clock=clock, rng=rng)
        delays.extend(clock.sleeps)

    assert all(0 <= delay <= 8.0 for delay in delays)
    # Not all clustered at the cap: that is the whole difference from fixed
    # backoff, and a jitter that produced one value would be no jitter.
    assert len(set(delays)) > 100
    assert min(delays) < 2.0
    assert max(delays) > 6.0


# --- The server's own instruction ------------------------------------------


def test_retry_after_beats_our_computed_backoff() -> None:
    # The server knows when it will be ready; we are guessing.
    operation, _ = _failing(RateLimitError("slow down", retry_after=17.0))
    clock = FakeClock()

    call_with_retry(operation, policy=NO_JITTER, clock=clock)

    assert clock.sleeps == [17.0]


def test_retry_after_beats_jitter_too() -> None:
    operation, _ = _failing(RateLimitError("slow down", retry_after=5.0))
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=2, base_delay_s=1.0, jitter=True)

    call_with_retry(operation, policy=policy, clock=clock, rng=random.Random(1))

    assert clock.sleeps == [5.0]


def test_an_absurd_retry_after_is_clamped() -> None:
    # Past a point, blocking a worker is worse than failing and letting the
    # caller decide.
    operation, _ = _failing(RateLimitError("come back tomorrow", retry_after=86_400.0))
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=2, deadline_s=1_000_000, jitter=False)

    call_with_retry(operation, policy=policy, clock=clock)

    assert clock.sleeps == [MAX_HONOURED_RETRY_AFTER_S]


def test_a_rate_limit_without_a_header_uses_our_backoff() -> None:
    operation, _ = _failing(RateLimitError("slow down", retry_after=None))
    clock = FakeClock()

    call_with_retry(operation, policy=NO_JITTER, clock=clock)

    assert clock.sleeps == [1.0]


# --- The deadline ----------------------------------------------------------


def test_the_deadline_stops_the_loop_before_it_sleeps_past_it() -> None:
    """Stopping before the sleep, not after.

    Waking up only to discover the deadline passed wastes exactly the time the
    deadline existed to save.
    """
    operation, calls = _failing(*[ServiceUnavailableError("502")] * 6)
    policy = RetryPolicy(
        max_attempts=6, base_delay_s=1.0, max_delay_s=30.0, deadline_s=4.0, jitter=False
    )
    clock = FakeClock()

    with pytest.raises(RetryBudgetExhaustedError):
        call_with_retry(operation, policy=policy, clock=clock)

    # 1s then 2s puts us at 3s elapsed; the next delay of 4s would cross 4s.
    assert clock.sleeps == [1.0, 2.0]
    assert clock.now < policy.deadline_s
    assert len(calls) == 3


def test_a_deadline_shorter_than_the_first_backoff_means_one_attempt() -> None:
    operation, calls = _failing(*[ServiceUnavailableError("502")] * 3)
    policy = RetryPolicy(max_attempts=4, base_delay_s=10.0, deadline_s=5.0, jitter=False)
    clock = FakeClock()

    with pytest.raises(RetryBudgetExhaustedError):
        call_with_retry(operation, policy=policy, clock=clock)

    assert clock.sleeps == []
    assert len(calls) == 1


# --- What the caller learns when it gives up -------------------------------


def test_giving_up_preserves_the_underlying_cause() -> None:
    # "We gave up" without "because of what" is not a usable error.
    operation, _ = _failing(*[ServiceUnavailableError("upstream is down")] * 4)

    with pytest.raises(RetryBudgetExhaustedError) as caught:
        call_with_retry(operation, policy=NO_JITTER, clock=FakeClock())

    assert isinstance(caught.value.last_error, ServiceUnavailableError)
    assert "upstream is down" in str(caught.value.last_error)
    assert caught.value.attempts == 4


def test_the_retry_hook_reports_each_retry() -> None:
    operation, _ = _failing(ServiceUnavailableError("502"), ServiceUnavailableError("502"))
    seen: list[tuple[int, str, float]] = []

    def on_retry(attempt: int, error: LLMError, delay: float) -> None:
        seen.append((attempt, type(error).__name__, delay))

    call_with_retry(operation, policy=NO_JITTER, clock=FakeClock(), on_retry=on_retry)

    assert seen == [(2, "ServiceUnavailableError", 1.0), (3, "ServiceUnavailableError", 2.0)]


# --- Policy validation -----------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"base_delay_s": -1.0},
        {"deadline_s": 0},
        {"base_delay_s": 10.0, "max_delay_s": 1.0},
    ],
)
def test_an_incoherent_policy_is_rejected_at_construction(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]
