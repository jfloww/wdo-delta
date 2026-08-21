"""The transport, including the standard-library one, actually executed.

Every other test in this package substitutes the transport, which leaves the
real adapter as the one component that could be broken without the suite
noticing. So these drive `UrllibTransport` against a genuine HTTP server on
localhost.

The path most likely to be wrong is the error path: `urllib` raises on a 4xx
instead of returning it, and the response body - the part that says *why* the
API refused - is readable only from the exception object. A client that forgets
that turns "max_tokens must be greater than 0" into a bare "400".
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from offerdelta.infrastructure.llm.errors import TransportError
from offerdelta.infrastructure.llm.transport import (
    FakeTransport,
    Headers,
    HttpRequest,
    HttpResponse,
    UrllibTransport,
    json_response,
)

# --- Case-insensitive headers ----------------------------------------------


def test_headers_are_looked_up_regardless_of_case() -> None:
    # HTTP says field names are case-insensitive and servers vary; a miss here
    # silently discards a server's Retry-After instruction.
    headers = Headers({"Retry-After": "30", "Content-Type": "application/json"})

    assert headers["retry-after"] == "30"
    assert headers["RETRY-AFTER"] == "30"
    assert headers.get("Retry-After") == "30"


def test_a_missing_header_is_none_rather_than_an_error() -> None:
    assert Headers({"a": "1"}).get("Retry-After") is None


def test_headers_preserve_what_was_actually_sent() -> None:
    assert list(Headers({"X-Odd-Case": "1"})) == ["X-Odd-Case"]


# --- The fake --------------------------------------------------------------


def test_the_fake_transport_reports_running_past_its_script() -> None:
    """An overrun means the client retried more than the test expected.

    Silently returning something would hide exactly the bug worth catching.
    """
    transport = FakeTransport(responses=[json_response(200, "{}")])
    request = HttpRequest(url="http://example.invalid", body=b"{}")

    transport.send(request)

    with pytest.raises(AssertionError, match="ran out of scripted responses"):
        transport.send(request)


def test_an_undecodable_body_does_not_raise_while_reporting_a_failure() -> None:
    # A proxy error page need not be valid UTF-8, and a decode error raised
    # while explaining someone else's failure replaces a useful message.
    response = HttpResponse(status=500, body=b"\xff\xfe not utf-8")

    assert "not utf-8" in response.text()


# --- A real server ---------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Replies according to the path, so one server covers every case."""

    # The capitalised name is fixed by BaseHTTPRequestHandler.
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        received = self.rfile.read(length)

        if self.path == "/ratelimit":
            self.send_response(429)
            self.send_header("Retry-After", "11")
            self.send_header("Content-Type", "application/json")
            body = b'{"error":"slow down"}'
        elif self.path == "/bad":
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            body = b'{"error":{"message":"max_tokens must be greater than 0"}}'
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-Echo-Length", str(len(received)))
            body = received

        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        """Silence the default stderr logging."""


@pytest.fixture
def server() -> Iterator[str]:
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_the_real_transport_sends_a_body_and_reads_a_response(server: str) -> None:
    transport = UrllibTransport()

    response = transport.send(
        HttpRequest(
            url=f"{server}/ok",
            body=b'{"hello":"world"}',
            headers={"Content-Type": "application/json"},
        )
    )

    assert response.status == 200
    assert response.body == b'{"hello":"world"}'
    assert response.headers["x-echo-length"] == "17"


def test_an_error_status_is_returned_rather_than_raised(server: str) -> None:
    # urllib raises on 4xx; the transport's contract is to hand it back so the
    # caller can decide whether it is retryable.
    transport = UrllibTransport()

    response = transport.send(HttpRequest(url=f"{server}/ratelimit", body=b"{}"))

    assert response.status == 429
    assert response.headers["Retry-After"] == "11"


def test_the_error_body_survives_the_exception_path(server: str) -> None:
    """The detail is readable only off the exception object.

    Losing it turns a fixable 400 into an hour of guessing.
    """
    transport = UrllibTransport()

    response = transport.send(HttpRequest(url=f"{server}/bad", body=b"{}"))

    assert response.status == 400
    assert "max_tokens must be greater than 0" in response.text()


def test_an_unreachable_host_becomes_a_retryable_transport_error() -> None:
    transport = UrllibTransport()

    with pytest.raises(TransportError):
        transport.send(HttpRequest(url="http://127.0.0.1:1/unreachable", body=b"{}", timeout_s=2.0))
