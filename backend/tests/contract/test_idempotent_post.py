"""The idempotency contract, over HTTP.

The unit tests prove the rule; these prove the wiring. A contract that holds in
the service and leaks at the route is not a contract anyone can rely on.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from offerdelta.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _key() -> str:
    """A fresh key per test — the store is process-local and shared."""
    return str(uuid.uuid4())


def test_a_comparison_can_be_run_without_a_key(client: TestClient) -> None:
    response = client.post("/v1/comparisons", json={})
    assert response.status_code == 201
    assert response.json()["reconciled"] is True


def test_the_horizon_is_honoured(client: TestClient) -> None:
    body = client.post("/v1/comparisons", json={"horizon_months": 6}).json()
    assert body["horizon_months"] == 6
    assert len(body["cumulative_cash_delta"]) == 6


def test_a_first_keyed_request_is_created(client: TestClient) -> None:
    response = client.post("/v1/comparisons", json={}, headers={"Idempotency-Key": _key()})
    assert response.status_code == 201
    assert "Idempotent-Replay" not in response.headers


def test_a_retry_replays_the_original_response(client: TestClient) -> None:
    key = _key()
    first = client.post("/v1/comparisons", json={}, headers={"Idempotency-Key": key})
    second = client.post("/v1/comparisons", json={}, headers={"Idempotency-Key": key})

    assert second.status_code == 200
    assert second.headers["Idempotent-Replay"] == "true"
    assert second.text == first.text


def test_a_replay_is_byte_identical(client: TestClient) -> None:
    # Not merely equivalent. A client that retries must not have to reconcile
    # two differently-serialised answers.
    key = _key()
    first = client.post(
        "/v1/comparisons", json={"horizon_months": 9}, headers={"Idempotency-Key": key}
    )
    second = client.post(
        "/v1/comparisons", json={"horizon_months": 9}, headers={"Idempotency-Key": key}
    )
    assert second.content == first.content


def test_the_same_key_with_a_different_body_conflicts(client: TestClient) -> None:
    key = _key()
    client.post("/v1/comparisons", json={"horizon_months": 12}, headers={"Idempotency-Key": key})
    clash = client.post(
        "/v1/comparisons", json={"horizon_months": 6}, headers={"Idempotency-Key": key}
    )
    assert clash.status_code == 409
    assert "different request body" in clash.json()["detail"]


def test_different_keys_run_independently(client: TestClient) -> None:
    first = client.post("/v1/comparisons", json={}, headers={"Idempotency-Key": _key()})
    second = client.post("/v1/comparisons", json={}, headers={"Idempotency-Key": _key()})
    assert first.status_code == 201
    assert second.status_code == 201


def test_a_rejected_request_does_not_hold_its_key(client: TestClient) -> None:
    # The caller's reason for retrying is that the first attempt failed. If the
    # key stayed locked, a corrected retry would be impossible for 24 hours.
    key = _key()
    bad = client.post(
        "/v1/comparisons", json={"horizon_months": 0}, headers={"Idempotency-Key": key}
    )
    assert bad.status_code == 422

    good = client.post(
        "/v1/comparisons", json={"horizon_months": 12}, headers={"Idempotency-Key": key}
    )
    assert good.status_code == 201


def test_an_unknown_field_is_rejected(client: TestClient) -> None:
    # extra="forbid": a typo in a field name must not be silently ignored and
    # then quietly change the fingerprint.
    assert client.post("/v1/comparisons", json={"horizonMonths": 6}).status_code == 422


def test_no_float_appears_in_a_posted_comparison(client: TestClient) -> None:
    import json  # noqa: PLC0415

    def floats(value: object, path: str = "$") -> list[str]:
        if isinstance(value, float):
            return [path]
        if isinstance(value, dict):
            return [p for k, v in value.items() for p in floats(v, f"{path}.{k}")]
        if isinstance(value, list):
            return [p for i, v in enumerate(value) for p in floats(v, f"{path}[{i}]")]
        return []

    body = json.loads(client.post("/v1/comparisons", json={}).text)
    assert floats(body) == []


def test_running_without_a_move_date_reproduces_the_old_behaviour(
    client: TestClient,
) -> None:
    # The pre-move gap is reproducible rather than only described: without a
    # move date the candidate pays nothing until it moves, and the cash delta
    # is correspondingly overstated.
    with_move = client.post("/v1/comparisons", json={}).json()
    without = client.post("/v1/comparisons", json={"move_date": None}).json()
    assert without["cash_delta"] != with_move["cash_delta"]
