"""Persisting comparison runs, against a live PostgreSQL.

Two claims are checked here that no unit test can make, because both are
properties of the database rather than of the code:

- money survives a round trip exactly, because the column is NUMERIC;
- a completed run cannot be overwritten, because the primary key says so.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from offerdelta.application.queries.get_demo_comparison import get_demo_comparison
from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY
from offerdelta.infrastructure.postgres.repositories import ComparisonRunRepository
from tests.integration.conftest import requires_database

pytestmark = requires_database

ENGINE_VERSION = "0.1.0-test"


@pytest.fixture(scope="module")
def view() -> object:
    # The engine run is the slow part; one is enough for every test here.
    return get_demo_comparison(horizon_months=12)


def _save(session: Session, view: object, run_id: uuid.UUID | None = None) -> uuid.UUID:
    repo = ComparisonRunRepository(session)
    return repo.save(
        view,  # type: ignore[arg-type]
        engine_version=ENGINE_VERSION,
        request_fingerprint="fingerprint-under-test",
        run_id=run_id,
    )


def test_a_run_can_be_saved_and_read_back(session: Session, view: object) -> None:
    run_id = _save(session, view)
    stored = ComparisonRunRepository(session).get(run_id)

    assert stored is not None
    assert stored.id == run_id
    assert stored.engine_version == ENGINE_VERSION


def test_money_survives_the_round_trip_exactly(session: Session, view: object) -> None:
    # The reason the column is NUMERIC. Through a float column this assertion
    # would pass most of the time and fail on the values that matter.
    run_id = _save(session, view)
    stored = ComparisonRunRepository(session).get(run_id)
    assert stored is not None

    expected = view.comparison.cash_delta.quantize(CURRENCY_DISPLAY)  # type: ignore[attr-defined]
    assert stored.cash_delta == expected


def test_the_rounding_policy_is_recorded(session: Session, view: object) -> None:
    # Persistence rounds, so the stored row says which rule did it. A figure
    # without its policy cannot be reproduced.
    run_id = _save(session, view)
    stored = ComparisonRunRepository(session).get(run_id)
    assert stored is not None
    assert stored.rounding_policy == "CURRENCY_DISPLAY"


def test_a_completed_run_cannot_be_overwritten(session: Session, view: object) -> None:
    # An audit record you can quietly replace is not an audit record.
    run_id = uuid.uuid4()
    _save(session, view, run_id)
    with pytest.raises(ValidationError, match="immutable"):
        _save(session, view, run_id)


def test_the_breakdown_is_stored_with_the_run(session: Session, view: object) -> None:
    run_id = _save(session, view)
    stored = ComparisonRunRepository(session).get(run_id)
    assert stored is not None
    assert stored.component_count == len(view.comparison.component_deltas)  # type: ignore[attr-defined]


def test_component_order_is_preserved(session: Session, view: object) -> None:
    # The engine orders by size of impact. Without an explicit position column
    # the rows would come back in whatever order the database chose.
    run_id = _save(session, view)
    session.flush()
    rows = (
        session.execute(
            sa.text("select code from result_components where run_id = :id order by position"),
            {"id": run_id},
        )
        .scalars()
        .all()
    )
    assert rows == [c.code for c in view.comparison.component_deltas]  # type: ignore[attr-defined]


def test_a_reconciled_run_records_that_it_reconciled(session: Session, view: object) -> None:
    run_id = _save(session, view)
    stored = ComparisonRunRepository(session).get(run_id)
    assert stored is not None
    assert stored.reconciled is True


def test_recent_runs_come_back_newest_first(session: Session, view: object) -> None:
    _save(session, view)
    _save(session, view)
    session.flush()
    recent = ComparisonRunRepository(session).recent(limit=5)
    timestamps = [run.created_at for run in recent]
    assert timestamps == sorted(timestamps, reverse=True)


def test_an_unknown_run_returns_none(session: Session) -> None:
    assert ComparisonRunRepository(session).get(uuid.uuid4()) is None


def test_the_database_stores_decimals_not_floats(session: Session) -> None:
    # The property the whole design rests on, asserted against the real engine
    # rather than assumed: 0.1 + 0.2 = 0.3 is true for NUMERIC and false for
    # double precision.
    exact = session.execute(
        sa.text("select (cast(0.1 as numeric) + cast(0.2 as numeric)) = cast(0.3 as numeric)")
    ).scalar_one()
    approximate = session.execute(
        sa.text("select (cast(0.1 as float8) + cast(0.2 as float8)) = cast(0.3 as float8)")
    ).scalar_one()
    assert exact is True
    assert approximate is False


def test_no_amount_column_is_a_floating_point_type(session: Session) -> None:
    floats = (
        session.execute(
            sa.text(
                "select table_name || '.' || column_name from information_schema.columns "
                "where table_schema = 'public' and data_type in ('double precision','real')"
            )
        )
        .scalars()
        .all()
    )
    assert list(floats) == []


def test_a_stored_amount_keeps_its_scale(session: Session, view: object) -> None:
    run_id = _save(session, view)
    session.flush()
    raw = session.execute(
        sa.text("select cash_delta from comparison_runs where id = :id"), {"id": run_id}
    ).scalar_one()
    assert isinstance(raw, Decimal)
    assert raw == Money(raw, "USD").amount
