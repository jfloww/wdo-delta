"""The money boundary.

Phase-1 gate item: no monetary value crosses the API as a JSON number.

This is enforced by walking the raw response body rather than the parsed model,
because the parsed model would hide exactly the defect being tested — a float
that survived serialisation still deserialises into something that looks fine.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from offerdelta.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _walk(value: object, path: str = "$") -> list[tuple[str, object]]:
    """Yield every (path, value) pair in a decoded JSON document."""
    found: list[tuple[str, object]] = [(path, value)]
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_walk(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_walk(item, f"{path}[{index}]"))
    return found


def test_the_demo_derivation_is_served(client: TestClient) -> None:
    assert client.get("/v1/demo/derivation").status_code == 200


def test_no_amount_is_serialised_as_a_json_number(client: TestClient) -> None:
    body = json.loads(client.get("/v1/demo/derivation").text)
    offenders = [
        path
        for path, value in _walk(body)
        if path.endswith(".amount") and not isinstance(value, str)
    ]
    assert offenders == [], f"amounts must be strings, these were not: {offenders}"


def test_no_float_appears_anywhere_in_the_response(client: TestClient) -> None:
    # Broader than the amount check: nothing in a financial payload should be a
    # float, whatever its field name.
    body = json.loads(client.get("/v1/demo/derivation").text)
    floats = [(path, value) for path, value in _walk(body) if isinstance(value, float)]
    assert floats == [], f"floats found in response: {floats}"


def test_amounts_keep_their_exact_decimal_text(client: TestClient) -> None:
    body = client.get("/v1/demo/derivation").json()
    assert body["amount"] == "2335.00"


def test_costs_are_signed_negative_in_the_payload(client: TestClient) -> None:
    body = client.get("/v1/demo/derivation").json()
    housing = next(child for child in body["children"] if child["code"] == "housing")
    assert housing["amount"].startswith("-")


def test_every_node_reports_its_currency(client: TestClient) -> None:
    body = json.loads(client.get("/v1/demo/derivation").text)
    nodes = [value for path, value in _walk(body) if isinstance(value, dict) and "code" in value]
    assert nodes
    assert all(node["currency"] == "USD" for node in nodes)


def test_every_node_reports_its_provenance(client: TestClient) -> None:
    body = json.loads(client.get("/v1/demo/derivation").text)
    nodes = [value for path, value in _walk(body) if isinstance(value, dict) and "code" in value]
    allowed = {"SOURCED", "USER_CONFIRMED", "ASSUMED", "DERIVED"}
    assert all(node["evidence"] in allowed for node in nodes)


def test_liveness_and_readiness_are_served(client: TestClient) -> None:
    assert client.get("/v1/health/live").json() == {"status": "live"}
    assert client.get("/v1/health/ready").json() == {"status": "ready"}


def test_version_reports_the_engine(client: TestClient) -> None:
    body = client.get("/v1/version").json()
    assert body["engine"] == "0.1.0-skeleton"


def test_the_page_is_served_at_the_root(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_page_never_parses_money_as_a_number(client: TestClient) -> None:
    # The formatter must stay pure string manipulation. Number() or parseFloat()
    # on an amount would silently reintroduce binary floating point.
    #
    # Only the executable script is inspected; the footer prose deliberately
    # mentions Number() while explaining why the page avoids it.
    page = client.get("/").text
    scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", page, flags=re.DOTALL)
    assert scripts, "expected the page to contain a script block"
    for script in scripts:
        assert "Number(" not in script
        assert "parseFloat" not in script
        assert "parseInt" not in script
