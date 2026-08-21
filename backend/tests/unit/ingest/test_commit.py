"""The explicit boundary between a read-only preview and a database write."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from preview_import import main
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.infrastructure.postgres.models import Base
from offerdelta.infrastructure.postgres.repositories import TransactionRepository
from offerdelta.ingest.commit import plan_import
from offerdelta.ingest.preview import preview_csv


def _write(tmp_path: Path, text: str, name: str = "export.csv") -> Path:
    path = tmp_path / name
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


TWO_IDENTICAL = """
Date,Description,Amount
2026-08-17,BLUE BOTTLE,-4.50
2026-08-17,BLUE BOTTLE,-4.50
2026-08-18,NETFLIX,-15.99
"""

ONE_BAD_ROW = """
Date,Description,Amount
2026-08-17,BLUE BOTTLE,-4.50
2026-08-18,NETFLIX,NOT MONEY
2026-08-19,SHELL,-52.10
2026-08-20,COSTCO,-120.00
"""


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as database_session:
        yield database_session
    engine.dispose()


def test_a_plan_assigns_occurrences_without_collapsing_duplicates(tmp_path: Path) -> None:
    plan = plan_import(preview_csv(_write(tmp_path, TWO_IDENTICAL)), account=" checking ")

    assert plan.account == "checking"
    assert plan.source_file == "export.csv"
    assert [row.occurrence for row in plan.rows] == [1, 2, 1]


def test_a_preview_with_any_bad_row_cannot_be_committed(tmp_path: Path) -> None:
    preview = preview_csv(_write(tmp_path, ONE_BAD_ROW))

    with pytest.raises(ValidationError, match=r"1 source row.*errors"):
        plan_import(preview, account="checking")


def test_an_import_needs_an_account(tmp_path: Path) -> None:
    preview = preview_csv(_write(tmp_path, TWO_IDENTICAL))

    with pytest.raises(ValidationError, match="account name"):
        plan_import(preview, account="   ")


def test_the_cli_always_previews_before_requiring_a_commit_account(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path, TWO_IDENTICAL)

    exit_code = main(["preview_import.py", str(path), "--commit"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "parsed 3" in output
    assert "not committed: --commit requires --account=NAME" in output


def test_the_cli_refuses_a_partial_import_before_opening_the_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path, ONE_BAD_ROW)

    exit_code = main(["preview_import.py", str(path), "--commit", "--account=checking"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "failed 1" in output
    assert "not committed: cannot commit while 1 source row" in output


def test_identical_real_charges_are_both_stored(session: Session, tmp_path: Path) -> None:
    plan = plan_import(preview_csv(_write(tmp_path, TWO_IDENTICAL)), account="checking")

    result = TransactionRepository(session).import_plan(plan)

    assert result.imported_count == 3
    assert result.already_stored_count == 0
    assert TransactionRepository(session).count(account="checking") == 3


def test_reimporting_the_same_file_is_a_reported_no_op(session: Session, tmp_path: Path) -> None:
    plan = plan_import(preview_csv(_write(tmp_path, TWO_IDENTICAL)), account="checking")
    repository = TransactionRepository(session)
    repository.import_plan(plan)

    repeated = repository.import_plan(plan)

    assert repeated.imported_count == 0
    assert repeated.already_stored_count == 3
    assert [row.source_line for row in repeated.already_stored] == [2, 3, 4]
    assert repository.count(account="checking") == 3


def test_an_overlapping_export_adds_only_new_occurrences(
    session: Session,
    tmp_path: Path,
) -> None:
    one = """
Date,Description,Amount
2026-08-17,BLUE BOTTLE,-4.50
"""
    two = """
Date,Description,Amount
2026-08-17,BLUE BOTTLE,-4.50
2026-08-17,BLUE BOTTLE,-4.50
"""
    repository = TransactionRepository(session)
    repository.import_plan(
        plan_import(preview_csv(_write(tmp_path, one, "one.csv")), account="checking")
    )

    overlap = repository.import_plan(
        plan_import(preview_csv(_write(tmp_path, two, "two.csv")), account="checking")
    )

    assert overlap.imported_count == 1
    assert overlap.already_stored_count == 1
    assert repository.count(account="checking") == 2


def test_deduplication_is_scoped_to_the_account(session: Session, tmp_path: Path) -> None:
    preview = preview_csv(_write(tmp_path, TWO_IDENTICAL))
    repository = TransactionRepository(session)
    repository.import_plan(plan_import(preview, account="checking"))

    savings = repository.import_plan(plan_import(preview, account="savings"))

    assert savings.imported_count == 3
    assert repository.count(account="checking") == 3
    assert repository.count(account="savings") == 3


def test_stored_rows_keep_exact_money_and_source_lineage(
    session: Session,
    tmp_path: Path,
) -> None:
    plan = plan_import(preview_csv(_write(tmp_path, TWO_IDENTICAL)), account="checking")
    repository = TransactionRepository(session)

    result = repository.import_plan(plan)
    stored = repository.get(result.imported_ids[0])

    assert stored is not None
    assert stored.amount == Money.parse("-4.50")
    assert stored.description == "BLUE BOTTLE"
    assert stored.source_file == "export.csv"
    assert stored.source_line == 2
    assert stored.raw_cells["Amount"] == "-4.50"
