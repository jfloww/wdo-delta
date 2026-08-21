"""Whether a failure is worth retrying.

The split this file defends: a 429 and a 400 are both failures, and a client
that treats them alike either pays four times to be rejected four times, or
gives up on a rate limit that would have cleared in two seconds.
"""

from __future__ import annotations

import datetime as dt
import email.utils

import pytest

from offerdelta.infrastructure.llm.errors import (
    AuthenticationError,
    FatalError,
    InvalidRequestError,
    ModelNotFoundError,
    OverloadedError,
    PermissionDeniedError,
    RateLimitError,
    RequestTooLargeError,
    RetryableError,
    ServiceUnavailableError,
    classify_status,
    parse_retry_after,
)

# --- Retry-After -----------------------------------------------------------


def test_retry_after_reads_plain_seconds() -> None:
    assert parse_retry_after("30") == 30.0


def test_retry_after_reads_an_http_date() -> None:
    when = dt.datetime.now(tz=dt.UTC) + dt.timedelta(seconds=45)
    header = email.utils.format_datetime(when)

    delay = parse_retry_after(header)

    assert delay is not None
    # Allow for the clock moving between formatting and parsing.
    assert 40 <= delay <= 46


def test_a_retry_after_date_in_the_past_means_no_wait() -> None:
    past = email.utils.format_datetime(dt.datetime.now(tz=dt.UTC) - dt.timedelta(hours=1))

    assert parse_retry_after(past) == 0.0


@pytest.mark.parametrize("value", [None, "", "   ", "soon", "-5"])
def test_an_unusable_retry_after_falls_back_to_our_own_backoff(value: str | None) -> None:
    # None, not an exception: a malformed hint is a reason to compute our own
    # delay, never a reason to fail the call.
    assert parse_retry_after(value) is None


# --- Status classification -------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, RateLimitError),
        (529, OverloadedError),
        (500, ServiceUnavailableError),
        (502, ServiceUnavailableError),
        (503, ServiceUnavailableError),
    ],
)
def test_transient_statuses_are_retryable(status: int, expected: type) -> None:
    error = classify_status(status, body="{}")

    assert isinstance(error, expected)
    assert isinstance(error, RetryableError)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, InvalidRequestError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, ModelNotFoundError),
        (413, RequestTooLargeError),
        (422, InvalidRequestError),
    ],
)
def test_request_problems_are_fatal(status: int, expected: type) -> None:
    error = classify_status(status, body="{}")

    assert isinstance(error, expected)
    assert isinstance(error, FatalError)


def test_overload_is_retryable_even_though_529_is_not_a_standard_status() -> None:
    # A client that only knows 5xx would call this fatal and fail the batch.
    error = classify_status(529, body='{"type":"overloaded_error"}')

    assert isinstance(error, RetryableError)


def test_a_rate_limit_carries_the_servers_own_timing() -> None:
    error = classify_status(429, body="slow down", retry_after="12")

    assert isinstance(error, RateLimitError)
    assert error.retry_after == 12.0


def test_the_servers_explanation_survives_into_the_message() -> None:
    # Without this, a 400 costs an hour of guessing what it disliked.
    error = classify_status(400, body='{"error":{"message":"max_tokens must be > 0"}}')

    assert "max_tokens must be > 0" in str(error)


def test_a_giant_error_body_is_truncated_rather_than_logged_whole() -> None:
    # Usually an HTML error page from a proxy in front of the API.
    error = classify_status(502, body="<html>" + ("x" * 50_000) + "</html>")

    message = str(error)
    assert len(message) < 1_000
    assert "50" in message  # the original length is reported


def test_an_empty_error_body_still_produces_a_usable_message() -> None:
    error = classify_status(500, body="")

    assert "no detail" in str(error)
