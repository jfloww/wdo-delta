"""Relational mapping.

Two rules govern this schema.

**Money is NUMERIC, never a floating-point type.** In this database
`0.1 + 0.2 = 0.3` is true for NUMERIC and false for `float8`; the whole product
rests on the first being the case.

**Persistence is a rounding boundary.** The engine keeps full precision through
every intermediate step, but a stored result is the figure a person was shown,
so amounts are quantised on the way in and the policy that did it is recorded
on the row. A run therefore says not just what the number was but how it was
presented.

Completed runs are immutable. The repository offers no update path, and the
primary key makes a second write of the same run fail rather than overwrite the
first.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

#: Eighteen digits with two decimal places. Comfortably beyond any salary, and
#: exact — NUMERIC stores decimal digits rather than approximating them.
MONEY = Numeric(18, 2)

#: Hours carry a finer scale than money: a commute is measured in minutes.
HOURS = Numeric(12, 4)


class Base(DeclarativeBase):
    pass


class ComparisonRunRow(Base):
    """One completed calculation, immutable once written."""

    __tablename__ = "comparison_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    #: Which version of the calculation rules produced this. A stored figure is
    #: meaningless without it: the same inputs under different rules are a
    #: different answer, and pretending otherwise is how an audit trail lies.
    engine_version: Mapped[str] = mapped_column(String(64))

    #: Names the policy that quantised every amount below.
    rounding_policy: Mapped[str] = mapped_column(String(64))

    current_label: Mapped[str] = mapped_column(String(200))
    candidate_label: Mapped[str] = mapped_column(String(200))

    horizon_months: Mapped[int] = mapped_column(Integer)
    move_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    #: Digest of the request that produced this run, so an identical request can
    #: be recognised without re-running the engine.
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    currency: Mapped[str] = mapped_column(String(3))
    cash_delta: Mapped[Decimal] = mapped_column(MONEY)
    wealth_delta: Mapped[Decimal] = mapped_column(MONEY)
    time_delta_hours: Mapped[Decimal] = mapped_column(HOURS)

    #: Whether every projected month balanced. The engine refuses to return an
    #: unbalanced result, so this is always true on write — stored because a
    #: claim nobody recorded is a claim nobody can audit later.
    reconciled: Mapped[bool] = mapped_column(Boolean)

    components: Mapped[list[ResultComponentRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="ResultComponentRow.position",
    )

    __table_args__ = (Index("ix_comparison_runs_created_at", "created_at"),)


class ResultComponentRow(Base):
    """One line of a run's breakdown."""

    __tablename__ = "result_components"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("comparison_runs.id", ondelete="CASCADE"), index=True
    )

    #: Preserves the engine's ordering, which is by size of impact. Without it a
    #: reload would come back in whatever order the database chose.
    position: Mapped[int] = mapped_column(Integer)

    code: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(200))

    currency: Mapped[str] = mapped_column(String(3))
    current_cash: Mapped[Decimal] = mapped_column(MONEY)
    candidate_cash: Mapped[Decimal] = mapped_column(MONEY)
    delta: Mapped[Decimal] = mapped_column(MONEY)

    run: Mapped[ComparisonRunRow] = relationship(back_populates="components")

    __table_args__ = (Index("ix_result_components_run_position", "run_id", "position"),)


class SchemaNote(Base):
    """A single row recording what this schema is for.

    Useful when someone opens the database cold and needs to know whether they
    are looking at demo data or something real.
    """

    __tablename__ = "schema_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note: Mapped[str] = mapped_column(Text)
