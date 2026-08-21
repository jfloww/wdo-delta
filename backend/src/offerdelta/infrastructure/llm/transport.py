"""Sending bytes somewhere, behind a seam.

The `Transport` port exists so every interesting behaviour of this client -
backoff on a 429, giving up on a 401, surviving a truncated body, honouring a
deadline - can be tested exactly, offline, with no API key and no spend. Those
paths are the ones that actually page someone at 3am, and a client whose retry
logic has never been executed does not have retry logic, it has retry source
code.

The concrete implementation is the standard library. `urllib.request` is not
what anyone would choose for a high-throughput service, and that is a real
limitation rather than a preference: no connection pooling, no HTTP/2, and no
async, so calls cannot overlap. It is chosen here because it adds nothing to
the dependency list, and because the port means replacing it with httpx - or an
async client, when batch throughput starts to matter - is one adapter and no
change to anything above.

Header lookups are case-insensitive throughout. HTTP says field names are
case-insensitive, servers vary in what they send, and `Retry-After` arriving as
`retry-after` must not silently turn a server's instruction into a miss.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Protocol

from offerdelta.infrastructure.llm.errors import TransportError

#: Read far enough to capture an error body worth reporting, and stop. A model
#: that somehow streams megabytes at us should not exhaust memory.
_MAX_BODY_BYTES: Final = 4 * 1024 * 1024


class Headers(Mapping[str, str]):
    """Case-insensitive header lookup, preserving what was actually sent."""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values: dict[str, str] = dict(values or {})
        self._folded = {key.lower(): key for key in self._values}

    def __getitem__(self, key: str) -> str:
        return self._values[self._folded[key.lower()]]

    def __iter__(self):  # type: ignore[no-untyped-def]
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"Headers({self._values!r})"


@dataclass(frozen=True)
class HttpRequest:
    """One outbound request, fully formed."""

    url: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    method: str = "POST"

    #: Wall-clock ceiling for this single attempt. Distinct from the retry
    #: budget: one attempt may time out while the call as a whole still has
    #: time to try again.
    timeout_s: float = 30.0


@dataclass(frozen=True)
class HttpResponse:
    """What came back, unparsed."""

    status: int
    body: bytes
    headers: Headers = field(default_factory=Headers)

    def text(self) -> str:
        """Decode defensively.

        An error body from a proxy may not be valid UTF-8, and a decoding
        exception raised while reporting someone else's failure replaces a
        useful message with a useless one.
        """
        return self.body.decode("utf-8", errors="replace")


class Transport(Protocol):
    """Anything that can send a request and return a response.

    Implementations raise `TransportError` when the request never completed,
    and return the response otherwise - including 4xx and 5xx, which are the
    caller's to interpret rather than the transport's.
    """

    def send(self, request: HttpRequest) -> HttpResponse: ...


class UrllibTransport:
    """The standard library, with its failure modes mapped onto ours."""

    def send(self, request: HttpRequest) -> HttpResponse:
        raw = urllib.request.Request(
            request.url,
            data=request.body,
            headers=dict(request.headers),
            method=request.method,
        )

        try:
            with urllib.request.urlopen(raw, timeout=request.timeout_s) as response:
                return HttpResponse(
                    status=response.status,
                    body=response.read(_MAX_BODY_BYTES),
                    headers=Headers(dict(response.headers.items())),
                )
        except urllib.error.HTTPError as error:
            # An HTTP error is a real response and carries a body that usually
            # says what was wrong. Reading it is the difference between "400"
            # and "max_tokens must be greater than 0".
            return HttpResponse(
                status=error.code,
                body=error.read(_MAX_BODY_BYTES) if error.fp is not None else b"",
                headers=Headers(dict(error.headers.items()) if error.headers else {}),
            )
        except urllib.error.URLError as error:
            # DNS failure, refused connection, TLS problem, or a timeout, which
            # urllib surfaces here rather than as a distinct type.
            raise TransportError(f"could not reach the API: {error.reason}") from error
        except TimeoutError as error:
            raise TransportError(
                f"the request did not complete within {request.timeout_s}s"
            ) from error
        except OSError as error:
            # A reset mid-read lands here. Retryable for the same reason a
            # timeout is: nothing was created, so a second attempt is safe.
            raise TransportError(f"the connection failed: {error}") from error


@dataclass
class FakeTransport:
    """A scripted transport, for testing every path the real one can take.

    Each element of `responses` is either an `HttpResponse` to return or an
    exception to raise, consumed in order. Running past the end is itself a
    failure worth reporting: a test that expected three attempts and got four
    has found a bug in the retry loop.
    """

    responses: list[HttpResponse | Exception] = field(default_factory=list)

    #: Every request sent, so a test can assert what the model was actually
    #: shown - including that a retry re-sent identical bytes.
    requests: list[HttpRequest] = field(default_factory=list)

    def send(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)

        if not self.responses:
            raise AssertionError(
                f"FakeTransport ran out of scripted responses on call "
                f"{len(self.requests)}; the client made more attempts than the test expected"
            )

        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def json_response(status: int, payload: str, **headers: str) -> HttpResponse:
    """Shorthand for scripting a JSON reply in a test."""
    return HttpResponse(
        status=status,
        body=payload.encode("utf-8"),
        headers=Headers({"Content-Type": "application/json", **headers}),
    )
