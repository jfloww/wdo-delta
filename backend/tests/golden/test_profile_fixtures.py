"""Golden tests for the two reference profiles.

Milestone 2's definition of done: both profiles are representable and serialise
to stable fixture JSON.

The point is regression detection. Any change to a figure or to the shape of a
profile shows up here as a reviewable diff rather than as a quietly different
comparison result three milestones later. Regenerate deliberately with:

    uv run pytest tests/golden --update-golden
"""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path

import pytest

from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.categories import CalculatorName
from tests.fixtures.profiles import ComparisonSide, auburn_current, new_jersey_candidate

GOLDEN_DIR = Path(__file__).parent / "expected"


def _plain(value: object) -> object:
    """Render a domain object as JSON-safe data.

    Money becomes a string with its currency, never a float — the same rule the
    API boundary enforces, applied here so the golden files themselves cannot
    normalise away a precision bug.
    """
    if isinstance(value, Money):
        return {"amount": str(value.amount), "currency": value.currency}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


def _serialise(side: ComparisonSide) -> str:
    return json.dumps(_plain(side), indent=2, sort_keys=True) + "\n"


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("auburn_current", auburn_current),
        ("new_jersey_candidate", new_jersey_candidate),
    ],
)
def test_profile_matches_its_golden_file(name: str, build: object, update_golden: bool) -> None:
    assert callable(build)
    serialised = _serialise(build())
    path = GOLDEN_DIR / f"{name}.json"

    if update_golden or not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialised, encoding="utf-8")

    assert serialised == path.read_text(encoding="utf-8"), (
        f"{name} no longer matches its golden file. If the change is intended, "
        f"rerun with --update-golden and review the diff."
    )


def _float_paths(value: object, path: str = "$") -> list[str]:
    """Every location in a decoded JSON document holding a float."""
    if isinstance(value, float):
        return [path]
    if isinstance(value, dict):
        return [p for k, v in value.items() for p in _float_paths(v, f"{path}.{k}")]
    if isinstance(value, list):
        return [p for i, v in enumerate(value) for p in _float_paths(v, f"{path}[{i}]")]
    return []


def test_no_money_value_serialises_as_a_number() -> None:
    # The same guarantee the API contract test enforces, applied to fixtures:
    # a float anywhere in a profile is a defect regardless of where it appears.
    for build in (auburn_current, new_jersey_candidate):
        floats = _float_paths(json.loads(_serialise(build())))
        assert floats == [], f"floats found in {build.__name__}: {floats}"


def test_the_auburn_profile_is_single_jurisdiction() -> None:
    assert auburn_current().employment.is_multi_jurisdiction is False


def test_the_candidate_profile_spans_two_jurisdictions() -> None:
    # Living in New Jersey and working in New York. No reciprocal agreement
    # exists, so this is the profile that forces the nonresident-credit path.
    candidate = new_jersey_candidate().employment
    assert candidate.is_multi_jurisdiction is True
    assert candidate.residence.jurisdiction_code == "US-NJ"
    assert candidate.work_location.jurisdiction_code == "US-NY"


def test_the_auburn_override_is_active_against_its_own_profile() -> None:
    side = auburn_current()
    assert side.employment.require_active_override() is side.employment.net_pay_override


def test_every_fixture_cost_routes_to_exactly_one_calculator() -> None:
    for build in (auburn_current, new_jersey_candidate):
        costs = build().costs
        routed = [item for name in CalculatorName for item in costs.items_for(name)]
        assert len(routed) == len(costs.items)


def test_every_fixture_figure_is_marked_as_an_assumption() -> None:
    # Until real numbers replace them. If this test starts failing because a
    # value became USER_CONFIRMED, that is the point — update it deliberately.
    for build in (auburn_current, new_jersey_candidate):
        assert build().costs.has_assumptions() is True
