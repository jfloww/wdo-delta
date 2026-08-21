"""Retrying, without turning a busy API into a stampede.

Three decisions here matter more than the loop that implements them.

**Full jitter, not fixed backoff.** When a rate limit trips, it usually trips
for many callers at once. If every one of them waits exactly two seconds, they
all return in the same millisecond and re-form the same spike that caused the
limit; doubling to four seconds just moves the collision. Sleeping a *random*
duration drawn from ``[0, cap]`` spreads the retries across the whole window,
which is the one change that actually drains the queue. The cost is that a
single caller sometimes waits longer than it strictly had to, which is a fine
trade for not being the reason the API stays down.

**The server's instruction beats our arithmetic.** A `Retry-After` header is
the API telling us when it will be ready. Overriding that with our own guess is
how a client earns a longer ban, so it wins whenever it is present.

**A deadline, not just an attempt count.** Four attempts with exponential
backoff can span a minute, and a caller that needed an answer in ten seconds
would rather fail fast than discover that later. The loop stops as soon as the
*next* sleep would cross the deadline, rather than sleeping and then noticing.

The clock is injected for one practical reason: a retry suite that really slept
would take a minute per assertion, so it would be written once, run rarely, and
rot. `FakeClock` makes the same tests finish instantly, which is why they exist
at all - and it lets a test assert on the exact sleep durations, which is the
only way to prove that jitter and `Retry-After` do what this docstring claims.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final, Protocol

from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.llm.errors import (
    LLMError,
    RateLimitError,
    RetryableError,
    RetryBudgetExhaustedError,
)

#: Even when a server asks for a very long wait, there is a point past which
#: blocking a worker is worse than failing and letting the caller decide.
MAX_HONOURED_RETRY_AFTER_S: Final = 60.0


class Clock(Protocol):
    """Time, injected so tests do not spend it."""

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    """Real time. Monotonic, so a clock adjustment cannot rewind a deadline."""

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass
class FakeClock:
    """Time that passes only when asked.

    Records every sleep, so a test can assert the actual backoff sequence
    rather than merely that some sleeping happened.
    """

    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@dataclass(frozen=True)
class RetryPolicy:
    """How hard, and how long, to keep trying."""

    #: Total attempts including the first. 1 disables retrying.
    max_attempts: int = 4

    #: First backoff, doubled per attempt before jitter.
    base_delay_s: float = 0.5

    #: Ceiling on the computed backoff, so attempt eight is not four minutes.
    max_delay_s: float = 20.0

    #: Wall-clock budget for the whole call, across every attempt and sleep.
    deadline_s: float = 60.0

    #: Off only for tests that need an exact, predictable sequence.
    jitter: bool = True

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValidationError("max_attempts must be at least 1; 1 means do not retry")
        if self.base_delay_s < 0 or self.max_delay_s < 0:
            raise ValidationError("delays cannot be negative")
        if self.max_delay_s < self.base_delay_s:
            raise ValidationError(
                "max_delay_s is below base_delay_s, which would cap the first backoff "
                "below its own starting value"
            )
        if self.deadline_s <= 0:
            raise ValidationError("deadline_s must be positive")

    def delay_for(
        self,
        attempt: int,
        *,
        retry_after: float | None = None,
        rng: random.Random | None = None,
    ) -> float:
        """How long to wait before `attempt` (1-based, so the first retry is 2).

        A `Retry-After` from the server wins outright, clamped so a pathological
        value cannot park a worker for an hour.
        """
        if retry_after is not None:
            return min(retry_after, MAX_HONOURED_RETRY_AFTER_S)

        # 2.0 rather than 2: an int base with a non-literal int exponent
        # types as Any, because the negative-exponent overload returns float.
        exponential = self.base_delay_s * (2.0 ** max(0, attempt - 2))
        capped = min(exponential, self.max_delay_s)

        if not self.jitter:
            return capped

        # Full jitter: anywhere in [0, capped]. Sleeping the *same* backoff as
        # everyone else is what rebuilds the spike we are backing off from.
        return float((rng or random).uniform(0, capped))


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    policy: RetryPolicy | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    on_retry: Callable[[int, LLMError, float], None] | None = None,
) -> T:
    """Run `operation`, retrying only the failures where retrying can help.

    `FatalError` propagates immediately and untouched: re-sending a rejected
    request wastes time and money to be told the same thing. Retryable failures
    are retried until the attempts or the deadline run out, whichever comes
    first, and then re-raised inside `RetryBudgetExhaustedError` with the last
    real error attached so the cause is never lost.

    `on_retry` receives the upcoming attempt number, the error that caused it,
    and the delay about to be slept - the hook a metric or a log line hangs on
    without this module needing to know which.
    """
    policy = policy or RetryPolicy()
    clock = clock or SystemClock()
    started = clock.monotonic()

    last_error: LLMError | None = None
    attempted = 0

    for attempt in range(1, policy.max_attempts + 1):
        attempted = attempt
        try:
            return operation()
        except RetryableError as error:
            # Bind it to an outer name deliberately: Python unbinds the `as`
            # target when the except block ends, so reading it below would be a
            # NameError.
            last_error = error
        # FatalError is not caught at all, and goes straight up.

        if attempt == policy.max_attempts:
            break

        retry_after = last_error.retry_after if isinstance(last_error, RateLimitError) else None
        delay = policy.delay_for(attempt + 1, retry_after=retry_after, rng=rng)

        elapsed = clock.monotonic() - started
        if elapsed + delay >= policy.deadline_s:
            # Stop before sleeping rather than after: waking up only to discover
            # the deadline passed wastes exactly the time we were trying to save.
            break

        if on_retry is not None:
            on_retry(attempt + 1, last_error, delay)

        clock.sleep(delay)

    if last_error is None:  # pragma: no cover - the loop cannot exit without one
        raise RetryBudgetExhaustedError(
            "the retry loop ended without a result or an error",
            attempts=attempted,
            last_error=LLMError("no error was recorded"),
        )

    raise RetryBudgetExhaustedError(
        f"giving up after {attempted} attempt(s): {last_error}",
        attempts=attempted,
        last_error=last_error,
    )
