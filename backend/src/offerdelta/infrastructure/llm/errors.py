"""What can go wrong in a model call, split by whether retrying could help.

The split is the entire point. A 429 and a 400 are both "the request failed",
and treating them alike produces one of two bugs: retrying a malformed request
four times and paying for four identical rejections, or abandoning a rate limit
that would have cleared in two seconds.

So every failure is one of two kinds and the retry loop never has to guess.

**Retryable** - the request was fine and the world was busy. Rate limits,
overload, 5xx, timeouts, dropped connections. The same bytes sent again later
may well succeed.

**Fatal** - the request itself is wrong, or we are not allowed to make it. A
bad API key, a malformed body, a model name that does not exist. Retrying
re-sends the same broken request and fails the same way.

One case sits deliberately on the fatal side despite looking transient:
`MalformedResponseError`, meaning the call succeeded but the body was not
something we could read. That is a contract mismatch between this client and
the API, not weather, and hammering the endpoint will not fix it.

Nothing here ever puts the API key in a message. These errors are logged and
raised into stack traces; a key that reaches a traceback reaches a log
aggregator, and rotating it is then someone's afternoon.
"""

from __future__ import annotations

import datetime as dt
import email.utils
from typing import Final

#: Anthropic returns 529 when the model is temporarily oversubscribed. It is
#: not in the standard status registry, and a client that only knows about 5xx
#: treats it as fatal - which turns a wait-and-retry into a failed batch.
OVERLOADED_STATUS: Final = 529

_TOO_MANY_REQUESTS: Final = 429
_SERVER_ERROR_FLOOR: Final = 500
_CLIENT_ERROR_FLOOR: Final = 400

_UNAUTHORISED: Final = 401
_FORBIDDEN: Final = 403
_NOT_FOUND: Final = 404
_PAYLOAD_TOO_LARGE: Final = 413


class LLMError(Exception):
    """Anything that stopped a model call from returning an answer."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class RetryableError(LLMError):
    """The request was fine; the world was busy. Sending it again may work."""


class FatalError(LLMError):
    """The request will fail identically no matter how often it is sent."""


class RateLimitError(RetryableError):
    """429. The server may have said when to come back."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message, status=_TOO_MANY_REQUESTS)

        #: Seconds to wait, when the response said so. Honoured in preference
        #: to our own backoff: the server knows when it will be ready and we
        #: are guessing.
        self.retry_after = retry_after


class OverloadedError(RetryableError):
    """529. The model is oversubscribed right now."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status=OVERLOADED_STATUS)


class ServiceUnavailableError(RetryableError):
    """5xx. Something broke on their side."""


class TransportError(RetryableError):
    """The request never completed: timeout, DNS failure, connection reset.

    Retryable because a classification call has no side effect beyond its cost.
    Nothing is created, so a duplicate delivery cannot corrupt anything - the
    worst case is paying twice for one answer. A client that wrote records would
    need an idempotency key here instead.
    """


class AuthenticationError(FatalError):
    """401. The key is missing, malformed, or revoked."""


class PermissionDeniedError(FatalError):
    """403. The key is valid but not allowed to do this."""


class ModelNotFoundError(FatalError):
    """404. Usually a typo in the model name, or a model since retired."""


class RequestTooLargeError(FatalError):
    """413. The payload exceeded the limit; the same payload always will."""


class InvalidRequestError(FatalError):
    """400 or 422. The body is wrong in a way the server can name."""


class MalformedResponseError(FatalError):
    """The call succeeded but the body was not what the contract promised.

    Fatal rather than retryable: this is a mismatch between this client and the
    API, or a model that ignored its schema. Neither is fixed by asking again,
    and retrying would bill for every attempt.
    """


class RetryBudgetExhaustedError(LLMError):
    """Retryable failures kept happening until the budget ran out.

    Carries the final underlying error so a caller can see what was actually
    going wrong, rather than only that we gave up.
    """

    def __init__(self, message: str, *, attempts: int, last_error: LLMError) -> None:
        super().__init__(message, status=last_error.status)
        self.attempts = attempts
        self.last_error = last_error


def parse_retry_after(value: str | None) -> float | None:
    """Read a `Retry-After` header, in either shape the RFC allows.

    It is legal as a number of seconds (``30``) or as an HTTP date
    (``Wed, 21 Aug 2026 07:28:00 GMT``). A client that handles only the integer
    form falls back to its own backoff exactly when the server was being most
    helpful.

    Returns `None` for anything unparseable rather than raising: a malformed
    hint is a reason to use our own backoff, not a reason to fail the call.
    """
    if value is None:
        return None

    text = value.strip()
    if not text:
        return None

    try:
        seconds = float(text)
    except ValueError:
        pass
    else:
        # A negative value is a broken header, not an instruction to hurry.
        return seconds if seconds >= 0 else None

    try:
        when = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None

    now = dt.datetime.now(tz=when.tzinfo or dt.UTC)
    return max(0.0, (when - now).total_seconds())


def classify_status(status: int, *, body: str, retry_after: str | None = None) -> LLMError:
    """Turn an HTTP status into the right kind of error.

    `body` is included in the message because the API names what it disliked,
    and a 400 without that text costs an hour of guessing. Request bodies are
    never echoed back - only the server's own description of the problem.
    """
    detail = _summarise(body)

    # Rate limiting is separate because it is the only case that carries timing
    # from the server rather than only a description.
    if status == _TOO_MANY_REQUESTS:
        return RateLimitError(
            f"rate limited by the API: {detail}",
            retry_after=parse_retry_after(retry_after),
        )
    if status == OVERLOADED_STATUS:
        return OverloadedError(f"the model is overloaded: {detail}")

    if fatal := _FATAL_BY_STATUS.get(status):
        factory, description = fatal
        return factory(f"{description}: {detail}", status=status)

    if status >= _SERVER_ERROR_FLOOR:
        return ServiceUnavailableError(f"server error {status}: {detail}", status=status)
    if status >= _CLIENT_ERROR_FLOOR:
        return InvalidRequestError(
            f"the API rejected the request ({status}): {detail}", status=status
        )

    return MalformedResponseError(f"unexpected status {status}: {detail}", status=status)


#: Statuses that mean the request itself is wrong. Retrying any of these
#: re-sends the same broken request, so they map straight to a fatal type.
_FATAL_BY_STATUS: Final[dict[int, tuple[type[FatalError], str]]] = {
    _UNAUTHORISED: (AuthenticationError, "the API key was rejected"),
    _FORBIDDEN: (PermissionDeniedError, "this key may not do that"),
    _NOT_FOUND: (ModelNotFoundError, "no such endpoint or model"),
    _PAYLOAD_TOO_LARGE: (RequestTooLargeError, "the request is too large"),
}


#: Long error bodies are usually an HTML page from something sitting in front
#: of the API. The first part identifies it; the rest is noise in a log.
_DETAIL_LIMIT: Final = 400


def _summarise(body: str) -> str:
    text = " ".join(body.split())
    if not text:
        return "(no detail in the response)"
    if len(text) <= _DETAIL_LIMIT:
        return text
    return f"{text[:_DETAIL_LIMIT]}... ({len(text)} chars)"
