"""The comparison endpoint contract.

The full engine exposed over HTTP. Held to the same rule as everything else on
this boundary: no monetary value is ever a JSON number.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from offerdelta.api.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _float_paths(value: object, path: str = "$") -> list[str]:
    if isinstance(value, float):
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _float_paths(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _float_paths(v, f"{path}[{i}]")]
    return []


def test_the_comparison_is_served(client: TestClient) -> None:
    assert client.get("/v1/demo/comparison").status_code == 200


def test_no_float_appears_anywhere(client: TestClient) -> None:
    body = json.loads(client.get("/v1/demo/comparison").text)
    floats = _float_paths(body)
    assert floats == [], f"floats found in comparison response: {floats}"


def test_the_headline_deltas_are_strings(client: TestClient) -> None:
    body = client.get("/v1/demo/comparison").json()
    assert isinstance(body["cash_delta"], str)
    assert isinstance(body["wealth_delta"], str)


def test_the_response_reports_that_every_month_reconciled(client: TestClient) -> None:
    # The engine refuses to return an unbalanced result, so this must be true.
    # Surfaced so a reader can see the guarantee rather than trust it.
    assert client.get("/v1/demo/comparison").json()["reconciled"] is True


def test_the_cumulative_series_covers_the_horizon(client: TestClient) -> None:
    body = client.get("/v1/demo/comparison").json()
    assert len(body["cumulative_cash_delta"]) == body["horizon_months"]


def test_break_even_reports_both_months(client: TestClient) -> None:
    body = client.get("/v1/demo/comparison").json()["break_even"]
    assert "first_crossing_month" in body
    assert "stable_break_even_month" in body
    assert body["metric"] == "CASH"


def test_the_equivalent_salary_names_its_tax_model(client: TestClient) -> None:
    # An extrapolated answer must not look like a computed one.
    solved = client.get("/v1/demo/comparison").json()["equivalent_salary"]
    assert solved["tax_model"] == "NET_PAY_OVERRIDE"
    assert "is_far_from_calibration" in solved


def test_the_negotiation_options_are_present(client: TestClient) -> None:
    negotiation = client.get("/v1/demo/comparison").json()["negotiation"]
    assert negotiation["options"]
    assert all(option["note"] for option in negotiation["options"])


def test_both_derivation_trees_are_returned(client: TestClient) -> None:
    body = client.get("/v1/demo/comparison").json()
    for side in ("current_derivation", "candidate_derivation"):
        assert body[side]["children"], f"{side} has no branches"


def test_a_derivation_branch_sums_to_its_parent(client: TestClient) -> None:
    # The invariant that makes an explanation trustworthy, checked over the
    # wire rather than only in the domain.
    from decimal import Decimal  # noqa: PLC0415

    root = client.get("/v1/demo/comparison").json()["current_derivation"]

    def check(node: dict[str, object]) -> None:
        children = node["children"]
        assert isinstance(children, list)
        if not children:
            return
        total = sum(Decimal(str(child["amount"])) for child in children)
        assert total == Decimal(str(node["amount"])), f"{node['code']} does not add up"
        for child in children:
            check(child)

    check(root)


def test_component_deltas_are_ordered_by_size(client: TestClient) -> None:
    from decimal import Decimal  # noqa: PLC0415

    components = client.get("/v1/demo/comparison").json()["component_deltas"]
    magnitudes = [abs(Decimal(component["delta"])) for component in components]
    assert magnitudes == sorted(magnitudes, reverse=True)
