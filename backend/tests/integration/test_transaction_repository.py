"""Imported transactions against real PostgreSQL.

SQLite exercises the repository quickly in the unit suite. These tests verify
the claims that belong to PostgreSQL itself: exact NUMERIC round trips, JSON
lineage, and the multiplicity-aware unique constraint created by Alembic.

The shared integration fixture wraps every test in an outer transaction and
rolls it back, so verification leaves no bank rows behind.
"""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

from offerdelta.domain.common.money import Money
from offerdelta.infrastructure.postgres.repositories import TransactionRepository
from offerdelta.ingest.commit import ImportPlan, plan_import
from offerdelta.ingest.preview import preview_csv
from tests.integration.conftest import requires_database

pytestmark = requires_database


EXPORT = """
Date,Description,Amount
2026-08-17,BLUE BOTTLE,-4.50
2026-08-17,BLUE BOTTLE,-4.50
2026-08-18,NETFLIX,-15.99
"""


def _plan(tmp_path: Path, *, account: str = "integration-checking") -> ImportPlan:
    path = tmp_path / "statement.csv"
    path.write_text(EXPORT.strip() + "\n", encoding="utf-8")
    return plan_import(preview_csv(path), account=account)


def test_a_transaction_round_trips_exactly_with_its_lineage(
    session: Session,
    tmp_path: Path,
) -> None:
    repository = TransactionRepository(session)

    result = repository.import_plan(_plan(tmp_path))
    stored = repository.get(result.imported_ids[0])

    assert stored is not None
    assert stored.amount == Money.parse("-4.50")
    assert stored.source_file == "statement.csv"
    assert stored.source_line == 2
    assert stored.raw_cells["Amount"] == "-4.50"


def test_postgres_preserves_multiplicity_and_deduplicates_a_reimport(
    session: Session,
    tmp_path: Path,
) -> None:
    repository = TransactionRepository(session)
    plan = _plan(tmp_path)

    first = repository.import_plan(plan)
    repeated = repository.import_plan(plan)

    assert first.imported_count == 3
    assert repeated.imported_count == 0
    assert repeated.already_stored_count == 3
    assert repository.count(account="integration-checking") == 3


def test_the_multiplicity_aware_identity_is_a_database_constraint(session: Session) -> None:
    exists = session.execute(
        sa.text(
            "select count(*) from pg_constraint "
            "where conname = 'uq_transactions_account_fingerprint_occurrence'"
        )
    ).scalar_one()

    assert exists == 1
