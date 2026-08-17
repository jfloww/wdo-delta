"""Repositories.

Domain-oriented persistence, not a thin wrapper over the ORM. There is a `save`
and there are reads; there is deliberately no `update`, because a completed
comparison run is immutable. Writing the same run twice raises rather than
overwriting — an audit record you can quietly replace is not an audit record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from offerdelta.application.queries.get_demo_comparison import ComparisonView
from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY
from offerdelta.infrastructure.postgres.models import (
    ComparisonRunRow,
    ResultComponentRow,
)


@dataclass(frozen=True)
class StoredRun:
    """A run as it came back from the database."""

    id: uuid.UUID
    created_at: datetime
    engine_version: str
    rounding_policy: str
    current_label: str
    candidate_label: str
    horizon_months: int
    cash_delta: Money
    wealth_delta: Money
    reconciled: bool
    component_count: int


def _quantised(amount: Money) -> Money:
    """Persistence is a rounding boundary; the policy is recorded alongside."""
    return amount.quantize(CURRENCY_DISPLAY)


class ComparisonRunRepository:
    """Stores and retrieves completed comparison runs."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self,
        view: ComparisonView,
        *,
        engine_version: str,
        request_fingerprint: str,
        run_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> uuid.UUID:
        """Write a run and its breakdown in one transaction.

        Raises if the run already exists. Overwriting would mean a figure
        someone acted on could change afterwards without trace.
        """
        comparison = view.comparison
        reconciled = all(
            month.residual.is_zero()
            for side in (comparison.current, comparison.candidate)
            for month in side.months
        )
        if not reconciled:
            # Belt and braces: the engine already refuses to return an
            # unbalanced result, so reaching here means something upstream
            # changed. Storing it would put a number nobody should trust into
            # the permanent record.
            raise ValidationError("refusing to store a run whose months do not reconcile")

        identifier = run_id or uuid.uuid4()

        # Checked explicitly for a clear error, and the primary key still backs
        # it up: this read cannot see a row a concurrent writer has not
        # committed, so the constraint below is the guarantee and this is the
        # message.
        if self._session.get(ComparisonRunRow, identifier) is not None:
            raise ValidationError(
                f"comparison run {identifier} already exists; completed runs are immutable"
            )

        row = ComparisonRunRow(
            id=identifier,
            created_at=now or datetime.now(UTC),
            engine_version=engine_version,
            rounding_policy=CURRENCY_DISPLAY.name,
            current_label=view.current_label,
            candidate_label=view.candidate_label,
            horizon_months=view.horizon_months,
            move_date=None,
            request_fingerprint=request_fingerprint,
            currency=comparison.cash_delta.currency,
            cash_delta=_quantised(comparison.cash_delta).amount,
            wealth_delta=_quantised(comparison.wealth_delta).amount,
            time_delta_hours=comparison.time_delta_hours,
            reconciled=reconciled,
            components=[
                ResultComponentRow(
                    id=uuid.uuid4(),
                    position=position,
                    code=component.code,
                    label=component.label,
                    currency=component.delta.currency,
                    current_cash=_quantised(component.current_cash).amount,
                    candidate_cash=_quantised(component.candidate_cash).amount,
                    delta=_quantised(component.delta).amount,
                )
                for position, component in enumerate(comparison.component_deltas)
            ],
        )

        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as error:
            raise ValidationError(
                f"comparison run {identifier} already exists; completed runs are immutable"
            ) from error
        return identifier

    def get(self, run_id: uuid.UUID) -> StoredRun | None:
        row = self._session.get(ComparisonRunRow, run_id)
        return None if row is None else _to_stored(row)

    def recent(self, limit: int = 20) -> list[StoredRun]:
        rows = self._session.scalars(
            select(ComparisonRunRow).order_by(ComparisonRunRow.created_at.desc()).limit(limit)
        ).all()
        return [_to_stored(row) for row in rows]

    def count(self) -> int:
        return len(self._session.scalars(select(ComparisonRunRow.id)).all())


def _to_stored(row: ComparisonRunRow) -> StoredRun:
    return StoredRun(
        id=row.id,
        created_at=row.created_at,
        engine_version=row.engine_version,
        rounding_policy=row.rounding_policy,
        current_label=row.current_label,
        candidate_label=row.candidate_label,
        horizon_months=row.horizon_months,
        cash_delta=Money(row.cash_delta, row.currency),
        wealth_delta=Money(row.wealth_delta, row.currency),
        reconciled=row.reconciled,
        component_count=len(row.components),
    )
