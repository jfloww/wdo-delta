# Transaction Import Data-Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the transaction import path safe to point at real bank statements by giving it a canonical account identity, a reproducible versioned fingerprint, declared import semantics, and a write path that cannot be triggered by accident.

**Architecture:** A thin application service (`application/transactions/import_transactions.py`) owns orchestration; CSV ingest becomes one input adapter that produces `TransactionRecord` values; the PostgreSQL repository accepts those records and no longer imports anything from `offerdelta.ingest`. The fingerprint moves into the domain layer so both sides can compute it from persisted fields alone.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0, Alembic, PostgreSQL 16, pytest, Hypothesis, ruff, mypy (strict), import-linter, uv.

**Spec:** [`docs/superpowers/specs/2026-08-21-transaction-import-data-safety-design.md`](../specs/2026-08-21-transaction-import-data-safety-design.md)

## Global Constraints

- Work on branch `fix/import-data-integrity`. **Blocked at time of writing** — sandbox-left Deny ACEs on `.git/HEAD` prevent branch switching. Resolve before Task 1 (see Preflight).
- **Do not import real financial data.** Every fixture is synthetic.
- **Do not implement async LLM transport.** Out of scope entirely.
- Domain layer (`offerdelta.domain`) stays standard-library only — enforced by an existing import-linter contract.
- mypy runs `strict = true` with `disallow_any_explicit = true` outside `api`/`config`. No bare `Any`.
- ruff line-length 100.
- Money is `Decimal` via `Money`, never float. Persistence quantises to `CURRENCY_DISPLAY` (2dp).
- Every task ends green on `make check` (lint, types, arch, test) unless the task explicitly states a required RED step.
- Commit after every task.

---

## Preflight (do this first, once)

- [ ] **Clear the ACLs blocking git**

Run in PowerShell as Administrator:

```powershell
cd "c:\Users\JJ\Desktop\Jaehoon\Projects\wdo"
icacls ".git" /T /C `
  /remove:d "*S-1-5-21-3114834670-1257490681-952037396-3407646853" `
  /remove:d "*S-1-5-21-3727157759-2440595167-3518582032-3120531983"
```

- [ ] **Switch to the working branch**

```bash
git switch fix/import-data-integrity
git branch --show-current   # must print fix/import-data-integrity
```

Do not start Task 1 until this prints the branch name. If it still fails, stop and report — every task below commits, and committing to `main` is not what was asked for.

---

## File Structure

**Created:**

| Path | Responsibility |
|---|---|
| `backend/src/offerdelta/domain/transactions/fingerprint.py` | The versioned fingerprint function. Pure, stdlib-only. |
| `backend/src/offerdelta/domain/transactions/accounts.py` | `canonical_account_key` — the one place account text is normalised. |
| `backend/src/offerdelta/persistence_records.py` → actually `backend/src/offerdelta/infrastructure/postgres/records.py` | `TransactionRecord`, `Provenance` — the repository's input shape. |
| `backend/src/offerdelta/application/transactions/__init__.py` | Package marker. |
| `backend/src/offerdelta/application/transactions/import_transactions.py` | Orchestration: resolve account, open batch, build records, write, report. |
| `backend/transactions.py` | The CLI. Replaces `preview_import.py`. |
| `backend/migrations/versions/<rev>_rebuild_transaction_storage.py` | Accounts, import batches, transactions rebuild. |
| `backend/tests/integration/conftest_migrations.py` → fixtures go in `backend/tests/integration/conftest.py` | Scratch-schema fixture. |
| `backend/tests/unit/domain/transactions/test_fingerprint.py` | Fingerprint properties. |
| `backend/tests/unit/domain/transactions/test_accounts.py` | Key canonicalisation. |
| `backend/tests/unit/ingest/test_ragged_rows.py` | Ragged-row rejection, physical line numbers. |
| `backend/tests/unit/test_transactions_cli.py` | argparse behaviour, commit gate. |
| `backend/tests/integration/test_migration_rebuild.py` | Migration on real PostgreSQL, including the abort guard. |
| `backend/tests/integration/test_account_repository.py` | Registration and lookup. |
| `backend/tests/integration/test_snapshot_import.py` | Snapshot semantics. |
| `backend/tests/integration/test_incremental_import.py` | Incremental semantics. |

**Modified:**

| Path | Change |
|---|---|
| `backend/src/offerdelta/ingest/preview.py` | Ragged-row rejection; `reader.line_num`; `ParsedRow.fingerprint` property removed. |
| `backend/src/offerdelta/ingest/mapping.py` | `ColumnMapping.external_id` field. |
| `backend/src/offerdelta/ingest/commit.py` | `plan_import` returns records; mode and window validation. |
| `backend/src/offerdelta/infrastructure/postgres/models.py` | `AccountRow`, `ImportBatchRow`, rebuilt `TransactionRow`. |
| `backend/src/offerdelta/infrastructure/postgres/repositories.py` | `AccountRepository`; `add_many`; `count` via `func.count`; `recent` deleted; `ImportPlan` import deleted. |
| `backend/pyproject.toml` | New import-linter contract. |
| `.github/workflows/ci.yml` | PostgreSQL 16 service container. |
| `docs/status/2026-08-21.md` | Correct the false "not committed" claim. |
| `README.md` | Import section: modes, windows, account registration. |
| `backend/preview_import.py` | Deleted (replaced by `transactions.py`). |

---

## Task 1: Real PostgreSQL in CI and a scratch-schema harness

Everything downstream is tested against real PostgreSQL. This task makes that possible before anything depends on it.

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `backend/tests/integration/conftest.py`
- Test: `backend/tests/integration/test_database_harness.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: pytest fixtures `engine`, `connection`, `session` (existing), plus new `scratch_schema() -> Iterator[str]` yielding a schema name that is dropped afterwards.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_database_harness.py`:

```python
"""The harness itself is load-bearing, so it gets its own test."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from tests.integration.conftest import requires_database

pytestmark = requires_database


def test_scratch_schema_exists_during_the_test(engine: Engine, scratch_schema: str) -> None:
    with engine.connect() as conn:
        found = conn.execute(
            sa.text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :n"),
            {"n": scratch_schema},
        ).scalar_one_or_none()
    assert found == 1


def test_scratch_schema_is_isolated(engine: Engine, scratch_schema: str) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(f'CREATE TABLE "{scratch_schema}".probe (id integer)'))
        conn.execute(sa.text(f'INSERT INTO "{scratch_schema}".probe VALUES (1)'))
        count = conn.execute(sa.text(f'SELECT count(*) FROM "{scratch_schema}".probe')).scalar_one()
    assert count == 1
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_database_harness.py -v`
Expected: FAIL — `fixture 'scratch_schema' not found`.

- [ ] **Step 3: Add the fixture**

Append to `backend/tests/integration/conftest.py`:

```python
@pytest.fixture
def scratch_schema(engine: Engine) -> Iterator[str]:
    """An empty schema that exists only for this test.

    Migrations need a database at a known revision, which the shared one is
    not. Creating a throwaway schema gives each migration test a pristine
    namespace without a container, and works identically against Neon and the
    CI service.
    """
    name = f"scratch_{uuid.uuid4().hex[:12]}"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{name}"'))
    try:
        yield name
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
```

Add to that file's imports: `import uuid` and `from sqlalchemy import text`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/integration/test_database_harness.py -v`
Expected: PASS (2 passed), assuming `CONNECTION_STRING` is set locally.

- [ ] **Step 5: Add PostgreSQL to CI**

In `.github/workflows/ci.yml`, under `jobs.backend`, add `services` and `env` alongside the existing `runs-on`:

```yaml
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres
          POSTGRES_DB: offerdelta_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    env:
      CONNECTION_STRING: postgresql://postgres:postgres@localhost:5432/offerdelta_test
```

- [ ] **Step 6: Verify the whole suite still passes**

Run: `cd backend && uv run pytest -q`
Expected: all pass. Integration tests now run locally instead of skipping.

- [ ] **Step 7: Commit**

```bash
git add .github/workflows/ci.yml backend/tests/integration/conftest.py backend/tests/integration/test_database_harness.py
git commit -m "Test harness: real PostgreSQL in CI and a scratch-schema fixture"
```

---

## Task 2: A quantised, versioned, reproducible fingerprint

The core defect. Pure function, no database, fastest feedback.

**Files:**
- Create: `backend/src/offerdelta/domain/transactions/fingerprint.py`
- Test: `backend/tests/unit/domain/transactions/test_fingerprint.py`

**Interfaces:**
- Consumes: `Money` from `offerdelta.domain.common.money`, `CURRENCY_DISPLAY` from `offerdelta.domain.common.rounding`.
- Produces:
  - `FINGERPRINT_VERSION: Final[int] = 1`
  - `compute_fingerprint(*, account_id: uuid.UUID, posted_on: date, normalised_merchant: str, amount: Money) -> str` — 32 hex chars.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/domain/transactions/test_fingerprint.py`:

```python
from __future__ import annotations

import uuid
from datetime import date

import pytest

from offerdelta.domain.common.money import Money
from offerdelta.domain.transactions.fingerprint import FINGERPRINT_VERSION, compute_fingerprint

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _fp(**overrides: object) -> str:
    kwargs: dict[str, object] = {
        "account_id": ACCOUNT,
        "posted_on": date(2026, 8, 17),
        "normalised_merchant": "BLUE BOTTLE",
        "amount": Money.parse("-4.50"),
    }
    kwargs.update(overrides)
    return compute_fingerprint(**kwargs)  # type: ignore[arg-type]


def test_trailing_zeros_do_not_change_the_fingerprint() -> None:
    """The defect this module exists to fix: -4.50 and -4.5 are one charge."""
    assert _fp(amount=Money.parse("-4.50")) == _fp(amount=Money.parse("-4.5"))


def test_sub_cent_differences_collapse_after_quantisation() -> None:
    assert _fp(amount=Money.parse("-4.504")) == _fp(amount=Money.parse("-4.501"))


def test_it_is_32_hex_characters() -> None:
    value = _fp()
    assert len(value) == 32
    assert all(c in "0123456789abcdef" for c in value)


@pytest.mark.parametrize(
    "field,value",
    [
        ("account_id", OTHER),
        ("posted_on", date(2026, 8, 18)),
        ("normalised_merchant", "BLUE BOTTLE COFFEE"),
        ("amount", Money.parse("-4.51")),
    ],
)
def test_every_input_changes_the_fingerprint(field: str, value: object) -> None:
    assert _fp(**{field: value}) != _fp()


def test_the_delimiter_cannot_be_forged() -> None:
    """A merchant containing the separator must not collide with a real split."""
    left = _fp(normalised_merchant="A\x1f2026-08-17")
    right = _fp(normalised_merchant="A")
    assert left != right


def test_version_is_exported() -> None:
    assert FINGERPRINT_VERSION == 1
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/unit/domain/transactions/test_fingerprint.py -v`
Expected: FAIL — `ModuleNotFoundError: offerdelta.domain.transactions.fingerprint`.

- [ ] **Step 3: Write the implementation**

Create `backend/src/offerdelta/domain/transactions/fingerprint.py`:

```python
"""The identity that decides what is a duplicate and what is new money.

Two rules make this value trustworthy, and both were violated by the first
implementation.

**Every input is a persisted column.** Nothing derived-and-discarded enters the
hash, so a stored fingerprint can always be recomputed from its own row. A
fingerprint you cannot reproduce is one you can never rebuild or audit.

**The amount is quantised before hashing.** Persistence rounds to two places,
so hashing the unrounded value produced a fingerprint that disagreed with the
row it was stored beside: `-4.50` and `-4.5` hashed differently while landing
on the same stored amount, and the same charge was written twice.

`normalised_merchant` is the output of an evolving heuristic, so the version is
stamped on every row. Changing `normalise_description` without bumping
`FINGERPRINT_VERSION` silently re-imports every affected transaction.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date
from typing import Final

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY

#: Bump whenever any input to the payload changes meaning — including a change
#: to `normalise_description`, whose output is hashed here.
FINGERPRINT_VERSION: Final[int] = 1

#: ASCII unit separator. Vanishingly rare in bank descriptions, and joining on
#: it stops "A" + "2026-08-17" from colliding with a merchant literally named
#: "A\x1f2026-08-17".
_DELIMITER: Final = "\x1f"

_HEX_LENGTH: Final = 32


def compute_fingerprint(
    *,
    account_id: uuid.UUID,
    posted_on: date,
    normalised_merchant: str,
    amount: Money,
) -> str:
    """A stable identity for one transaction within one account.

    Deliberately excludes the line number: a duplicate that moved position in
    the file is still a duplicate.
    """
    quantised = amount.quantize(CURRENCY_DISPLAY)
    payload = _DELIMITER.join(
        (
            str(account_id),
            posted_on.isoformat(),
            normalised_merchant,
            f"{quantised.amount:.2f}",
            quantised.currency,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_HEX_LENGTH]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/domain/transactions/test_fingerprint.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Verify the domain stays stdlib-only**

Run: `cd backend && uv run lint-imports`
Expected: all contracts pass — `hashlib` and `uuid` are standard library.

- [ ] **Step 6: Commit**

```bash
git add backend/src/offerdelta/domain/transactions/fingerprint.py backend/tests/unit/domain/transactions/test_fingerprint.py
git commit -m "Fingerprint: quantise before hashing, and stamp a version"
```

---

## Task 3: Canonical account keys

**Files:**
- Create: `backend/src/offerdelta/domain/transactions/accounts.py`
- Test: `backend/tests/unit/domain/transactions/test_accounts.py`

**Interfaces:**
- Produces: `canonical_account_key(raw: str) -> str`. Raises `ValidationError` on empty input.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/domain/transactions/test_accounts.py`:

```python
from __future__ import annotations

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.transactions.accounts import canonical_account_key


@pytest.mark.parametrize(
    "raw",
    ["checking", "Checking", "CHECKING", "  checking  ", "cHeCkInG"],
)
def test_case_and_padding_collapse_to_one_key(raw: str) -> None:
    """The bug: --account=Checking and --account=checking were two accounts."""
    assert canonical_account_key(raw) == "checking"


def test_words_join_with_hyphens() -> None:
    assert canonical_account_key("Chase Checking") == "chase-checking"
    assert canonical_account_key("Chase   Checking") == "chase-checking"


def test_punctuation_becomes_a_separator() -> None:
    assert canonical_account_key("Amex (Gold)") == "amex-gold"
    assert canonical_account_key("Chase - Checking") == "chase-checking"


def test_digits_survive() -> None:
    assert canonical_account_key("Checking 1234") == "checking-1234"


@pytest.mark.parametrize("raw", ["", "   ", "!!!", "---"])
def test_a_key_with_no_content_is_refused(raw: str) -> None:
    with pytest.raises(ValidationError, match="account name"):
        canonical_account_key(raw)


def test_it_is_idempotent() -> None:
    once = canonical_account_key("Chase Checking")
    assert canonical_account_key(once) == once
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/unit/domain/transactions/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

Create `backend/src/offerdelta/domain/transactions/accounts.py`:

```python
"""Account identity.

An account was previously free text compared after a bare `.strip()`, so
`--account=Checking` in August and `--account=checking` in September built two
parallel copies of the same statement and reported both as clean imports. Every
total silently doubled.

One function owns the normalisation, and the canonical key it returns is what
the unique constraint sees. The text the user typed is preserved separately for
display — normalisation is lossy, and "chase-checking" is not what anyone wants
to read on a report.
"""

from __future__ import annotations

import re
from typing import Final

from offerdelta.domain.common.errors import ValidationError

_SEPARATORS: Final = re.compile(r"[^a-z0-9]+")


def canonical_account_key(raw: str) -> str:
    """Casefold, collapse every run of punctuation or space to one hyphen.

    Idempotent: applying it to its own output changes nothing.
    """
    key = _SEPARATORS.sub("-", raw.strip().casefold()).strip("-")
    if not key:
        raise ValidationError(
            f"an account name needs at least one letter or digit, got {raw!r}"
        )
    return key
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/domain/transactions/test_accounts.py -v`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/src/offerdelta/domain/transactions/accounts.py backend/tests/unit/domain/transactions/test_accounts.py
git commit -m "Accounts: one canonical key, so case cannot fork an account"
```

---

## Task 4: Reject ragged rows and record physical line numbers

**Files:**
- Modify: `backend/src/offerdelta/ingest/preview.py`
- Test: `backend/tests/unit/ingest/test_ragged_rows.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces: `preview_csv` unchanged in signature. `ParsedRow.line` and `RowError.line` are now physical file lines (the first line of the record). `ParsedRow.fingerprint` is **removed** — the fingerprint needs an `account_id`, which ingest does not have.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/ingest/test_ragged_rows.py`:

```python
from __future__ import annotations

from pathlib import Path

from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.preview import preview_csv

HEADER = "Date,Description,Amount\n"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "statement.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_a_row_with_surplus_cells_is_an_error_not_a_parsed_row(tmp_path: Path) -> None:
    path = _write(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50,EXTRA1,EXTRA2\n")
    preview = preview_csv(path, date_order=DateOrder.ISO)

    assert len(preview.rows) == 0
    assert len(preview.errors) == 1
    assert "extra" in preview.errors[0].reason.lower()


def test_a_short_row_is_an_error_not_a_parsed_row(tmp_path: Path) -> None:
    path = _write(tmp_path, "2026-08-17,BLUE BOTTLE\n")
    preview = preview_csv(path, date_order=DateOrder.ISO)

    assert len(preview.rows) == 0
    assert len(preview.errors) == 1
    assert "missing" in preview.errors[0].reason.lower()


def test_no_row_is_lost_when_ragged(tmp_path: Path) -> None:
    """The preview's central promise: parsed + failed == source rows."""
    path = _write(
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n"
        "2026-08-18,RAGGED,-1.00,EXTRA\n"
        "2026-08-19,SHORT\n",
    )
    preview = preview_csv(path, date_order=DateOrder.ISO)

    assert preview.total_rows == 3
    assert len(preview.rows) + len(preview.errors) == 3


def test_an_embedded_newline_does_not_desynchronise_the_line_number(tmp_path: Path) -> None:
    """A quoted newline consumes two physical lines; the next row must know."""
    path = _write(
        tmp_path,
        '2026-08-17,"MEMO\nSECOND LINE",-4.50\n'
        "2026-08-18,BLUE BOTTLE,-3.00\n",
    )
    preview = preview_csv(path, date_order=DateOrder.ISO)

    assert len(preview.rows) == 2
    # header is line 1; first record starts at line 2 and spans lines 2-3
    assert preview.rows[0].line == 2
    # so the second record starts at line 4, not line 3
    assert preview.rows[1].line == 4


def test_line_numbers_are_physical_for_ordinary_files(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "2026-08-17,A,-1.00\n2026-08-18,B,-2.00\n2026-08-19,C,-3.00\n",
    )
    preview = preview_csv(path, date_order=DateOrder.ISO)

    assert [row.line for row in preview.rows] == [2, 3, 4]
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/unit/ingest/test_ragged_rows.py -v`
Expected: FAIL — ragged rows currently parse successfully, and the embedded-newline test reports line 3 for the second record.

- [ ] **Step 3: Add the sentinels and physical line tracking**

In `backend/src/offerdelta/ingest/preview.py`, add near `SAMPLE_SIZE`:

```python
#: csv.DictReader puts surplus cells under a key and missing cells under a
#: value. Both defaults are `None`, which violates `dict[str, str]` and
#: serialises to a JSON key of "null". Explicit sentinels make a ragged row
#: detectable instead of silently corrupting the stored provenance.
_RESTKEY: Final = "__surplus__"
_RESTVAL: Final = "\x00__missing__"
```

Replace the file-reading block in `preview_csv`:

```python
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, restkey=_RESTKEY, restval=_RESTVAL)
        headers = tuple(reader.fieldnames or ())
        # `fieldnames` forces the header read, so line_num now points at the
        # last physical line the header occupied — usually 1.
        previous_line = reader.line_num
        numbered: list[tuple[int, dict[str, str]]] = []
        for record in reader:
            numbered.append((previous_line + 1, record))
            previous_line = reader.line_num

    rows = [record for _, record in numbered]
```

Replace the parse loop:

```python
    parsed: list[ParsedRow] = []
    errors: list[RowError] = []
    for line, row in numbered:
        try:
            _reject_ragged(row)
            parsed.append(_to_row(row, line, resolved, order))
        except ValidationError as error:
            errors.append(RowError(line=line, reason=str(error), raw=_displayable(row)))
```

Add the two helpers:

```python
def _reject_ragged(row: dict[str, str]) -> None:
    """A row that does not match the header is refused, not repaired.

    Guessing which column a surplus cell belongs to is exactly the kind of
    silent decision that puts a wrong number in front of someone.
    """
    if _RESTKEY in row:
        surplus = row[_RESTKEY]
        count = len(surplus) if isinstance(surplus, list) else 1
        raise ValidationError(
            f"the row has {count} extra cell(s) beyond the header; "
            f"the file does not match its own columns"
        )
    missing = [key for key, value in row.items() if value == _RESTVAL]
    if missing:
        raise ValidationError(
            f"the row is missing cell(s) for: {', '.join(sorted(missing))}"
        )


def _displayable(row: dict[str, str]) -> dict[str, str]:
    """Raw cells with the sentinels made readable for the error report."""
    out: dict[str, str] = {}
    for key, value in row.items():
        if key == _RESTKEY:
            out[key] = ", ".join(value) if isinstance(value, list) else str(value)
        else:
            out[key] = "" if value == _RESTVAL else value
    return out
```

- [ ] **Step 4: Remove the fingerprint property from `ParsedRow`**

Delete the entire `fingerprint` property from `ParsedRow` (`preview.py`, the `@property def fingerprint` block). It hashed an unquantised amount and had no account. Then update `ImportPreview.duplicate_groups` to group on the content directly:

```python
    @property
    def duplicate_groups(self) -> list[tuple[str, list[ParsedRow]]]:
        """Rows that look identical, reported rather than removed.

        Grouped on the visible content rather than a stored fingerprint: this
        is a preview, and it has no account to compute a real identity against.
        """
        grouped: dict[str, list[ParsedRow]] = defaultdict(list)
        for row in self.rows:
            key = f"{row.posted_on.isoformat()}|{row.normalised_merchant}|{row.amount.amount:.2f}"
            grouped[key].append(row)
        return sorted(
            ((key, rows) for key, rows in grouped.items() if len(rows) > 1),
            key=lambda item: item[1][0].line,
        )
```

Add `from typing import Final` to the imports if not already present.

- [ ] **Step 5: Run the new tests**

Run: `cd backend && uv run pytest tests/unit/ingest/ -v`
Expected: PASS. Existing preview tests that referenced `row.fingerprint` will fail — fix them to use `duplicate_groups` or delete the fingerprint assertions.

- [ ] **Step 6: Run the whole suite**

Run: `cd backend && uv run pytest -q`
Expected: failures only in `tests/unit/ingest/test_commit.py` and the transaction repository tests, which Tasks 6-9 replace. Note them; do not fix them here.

- [ ] **Step 7: Commit**

```bash
git add backend/src/offerdelta/ingest/preview.py backend/tests/unit/ingest/
git commit -m "Preview: refuse ragged rows, and count physical lines"
```

---

## Task 5: The schema rebuild migration

**Files:**
- Modify: `backend/src/offerdelta/infrastructure/postgres/models.py`
- Create: `backend/migrations/versions/<rev>_rebuild_transaction_storage.py`
- Test: `backend/tests/integration/test_migration_rebuild.py` (create)

**Interfaces:**
- Produces: `AccountRow`, `ImportBatchRow`, rebuilt `TransactionRow`, and a migration whose `upgrade()` aborts on a non-empty `transactions` table.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_migration_rebuild.py`:

```python
"""The migration is destructive by design, so its guard gets a real test."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from tests.integration.conftest import requires_database

pytestmark = requires_database

REVISION = "rebuild_txn_storage"  # replace with the generated revision id


def _alembic_config(schema: str, engine: Engine) -> object:
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", str(engine.url.render_as_string(hide_password=False)))
    cfg.attributes["target_schema"] = schema
    return cfg


def test_upgrade_creates_all_three_tables(engine: Engine, scratch_schema: str) -> None:
    from alembic import command

    with engine.begin() as conn:
        conn.execute(sa.text(f'SET search_path TO "{scratch_schema}"'))
        command.upgrade(_alembic_config(scratch_schema, engine), "head")

    inspector = sa.inspect(engine)
    tables = set(inspector.get_table_names(schema=scratch_schema))
    assert {"accounts", "import_batches", "transactions"} <= tables


def test_raw_cells_is_jsonb_not_json(engine: Engine, scratch_schema: str) -> None:
    """json has no equality operator, so DISTINCT over transactions errors."""
    from alembic import command

    with engine.begin() as conn:
        conn.execute(sa.text(f'SET search_path TO "{scratch_schema}"'))
        command.upgrade(_alembic_config(scratch_schema, engine), "head")
        kind = conn.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'transactions' "
                "AND column_name = 'raw_cells'"
            ),
            {"s": scratch_schema},
        ).scalar_one()
    assert kind == "jsonb"


def test_upgrade_aborts_when_transactions_holds_rows(
    engine: Engine, scratch_schema: str
) -> None:
    """A destructive migration must fail loudly, not quietly delete money."""
    from alembic import command

    with engine.begin() as conn:
        conn.execute(sa.text(f'SET search_path TO "{scratch_schema}"'))
        command.upgrade(_alembic_config(scratch_schema, engine), "a6128d6e4f20")
        conn.execute(
            sa.text(
                "INSERT INTO transactions (id, imported_at, account, posted_on, "
                "description, normalised_merchant, currency, amount, fingerprint, "
                "occurrence, source_file, source_line, raw_cells) VALUES "
                "(:id, now(), 'checking', '2026-08-17', 'BLUE BOTTLE', 'BLUE BOTTLE', "
                "'USD', -4.50, 'abc', 1, 'aug.csv', 2, '{}')"
            ),
            {"id": str(uuid.uuid4())},
        )

    with pytest.raises(RuntimeError, match="holds 1 row"):
        with engine.begin() as conn:
            conn.execute(sa.text(f'SET search_path TO "{scratch_schema}"'))
            command.upgrade(_alembic_config(scratch_schema, engine), "head")
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_migration_rebuild.py -v`
Expected: FAIL — the revision does not exist.

- [ ] **Step 3: Rewrite the models**

In `backend/src/offerdelta/infrastructure/postgres/models.py`, add `JSONB` to the SQLAlchemy imports (`from sqlalchemy.dialects.postgresql import JSONB`), then replace the whole `TransactionRow` class with:

```python
class AccountRow(Base):
    """An account the user has deliberately registered.

    The canonical `key` is what every constraint sees; `display_name` is what a
    person reads. Keeping both means normalisation can be strict without
    turning "Chase Checking" into "chase-checking" on a report.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ImportBatchRow(Base):
    """One import of one file.

    `source_sha256` is what makes a byte-identical re-import a provable no-op
    rather than an inference. The declared window is what makes "this is a
    complete snapshot" a checkable claim.
    """

    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    source_file: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(16))
    window_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    window_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    row_count: Mapped[int] = mapped_column(Integer)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "mode IN ('snapshot', 'incremental')",
            name="ck_import_batches_mode",
        ),
        CheckConstraint(
            "mode <> 'snapshot' OR "
            "(window_start IS NOT NULL AND window_end IS NOT NULL)",
            name="ck_import_batches_snapshot_window",
        ),
        CheckConstraint(
            "window_start IS NULL OR window_start <= window_end",
            name="ck_import_batches_window_ordered",
        ),
        UniqueConstraint(
            "account_id",
            "source_sha256",
            name="uq_import_batches_account_checksum",
        ),
    )


class TransactionRow(Base):
    """One imported bank row."""

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("import_batches.id"), nullable=True
    )

    posted_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    normalised_merchant: Mapped[str] = mapped_column(Text)

    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(MONEY)

    #: The bank's own id when the export carries one. Authoritative for dedupe.
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)

    fingerprint: Mapped[str] = mapped_column(String(32))
    fingerprint_version: Mapped[int] = mapped_column(SmallInteger)
    occurrence: Mapped[int] = mapped_column(Integer)

    #: Absent for manual entry, which has no file behind it.
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    raw_cells: Mapped[dict[str, str] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        CheckConstraint("occurrence > 0", name="ck_transactions_occurrence_positive"),
        CheckConstraint(
            "source_line IS NULL OR source_line > 1",
            name="ck_transactions_source_line_after_header",
        ),
        UniqueConstraint(
            "account_id",
            "fingerprint",
            "occurrence",
            name="uq_transactions_account_fingerprint_occurrence",
        ),
        Index(
            "uq_transactions_account_external_id",
            "account_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index("ix_transactions_account_posted_on", "account_id", "posted_on"),
    )
```

Add `SmallInteger` and `text` to the `sqlalchemy` imports. Remove `JSON` from them if now unused.

- [ ] **Step 4: Generate and write the migration**

```bash
cd backend && uv run alembic revision -m "rebuild transaction storage"
```

Fill the generated file's `upgrade()`:

```python
def upgrade() -> None:
    """Rebuild transaction storage on a corrected identity model.

    Destructive by design: stored fingerprints predate versioning, hashed an
    unquantised amount, and keyed on free-text accounts, so they cannot be
    recomputed or migrated. Rebuilding is the honest option — but a migration
    written for an empty table has to say so when the table is not empty,
    because whoever runs it months from now will not remember that assumption.
    """
    bind = op.get_bind()
    existing = bind.execute(sa.text("SELECT count(*) FROM transactions")).scalar_one()
    if existing:
        raise RuntimeError(
            f"transactions holds {existing} row(s). This revision rebuilds the "
            f"table and cannot preserve them: stored fingerprints predate "
            f"versioning and cannot be recomputed from their own rows. Back up, "
            f"TRUNCATE deliberately, then re-run."
        )

    op.drop_table("transactions")

    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("key", name="uq_accounts_key"),
    )

    op.create_table(
        "import_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("mode IN ('snapshot', 'incremental')", name="ck_import_batches_mode"),
        sa.CheckConstraint(
            "mode <> 'snapshot' OR (window_start IS NOT NULL AND window_end IS NOT NULL)",
            name="ck_import_batches_snapshot_window",
        ),
        sa.CheckConstraint(
            "window_start IS NULL OR window_start <= window_end",
            name="ck_import_batches_window_ordered",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_import_batches_account"),
        sa.PrimaryKeyConstraint("id", name="pk_import_batches"),
        sa.UniqueConstraint(
            "account_id", "source_sha256", name="uq_import_batches_account_checksum"
        ),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=True),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("normalised_merchant", sa.Text(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("fingerprint_version", sa.SmallInteger(), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("source_file", sa.String(length=255), nullable=True),
        sa.Column("source_line", sa.Integer(), nullable=True),
        sa.Column("raw_cells", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("occurrence > 0", name="ck_transactions_occurrence_positive"),
        sa.CheckConstraint(
            "source_line IS NULL OR source_line > 1",
            name="ck_transactions_source_line_after_header",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name="fk_transactions_account"),
        sa.ForeignKeyConstraint(["batch_id"], ["import_batches.id"], name="fk_transactions_batch"),
        sa.PrimaryKeyConstraint("id", name="pk_transactions"),
        sa.UniqueConstraint(
            "account_id",
            "fingerprint",
            "occurrence",
            name="uq_transactions_account_fingerprint_occurrence",
        ),
    )
    op.create_index(
        "uq_transactions_account_external_id",
        "transactions",
        ["account_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )
    op.create_index(
        "ix_transactions_account_posted_on", "transactions", ["account_id", "posted_on"]
    )
```

Add `from sqlalchemy.dialects import postgresql` to the migration imports.

Write `downgrade()` as an explicit refusal rather than a fake inverse:

```python
def downgrade() -> None:
    """Deliberately irreversible.

    A truthful downgrade would have to reconstruct v0 fingerprints, which is
    the exact thing that cannot be done: they hashed an unquantised amount
    against a free-text account, so they are not recomputable from any row this
    schema stores. Recreating the old tables empty would leave a database that
    looks downgraded and has silently lost every transaction. Refusing is the
    honest option.
    """
    raise NotImplementedError(
        "a6128d6e4f20 -> this revision is one-way: v0 fingerprints cannot be "
        "reconstructed. Restore from a backup instead."
    )
```

- [ ] **Step 5: Run the migration tests**

Run: `cd backend && uv run pytest tests/integration/test_migration_rebuild.py -v`
Expected: PASS (3 passed). Update `REVISION` in the test to the real generated id.

- [ ] **Step 6: Commit**

```bash
git add backend/src/offerdelta/infrastructure/postgres/models.py backend/migrations/versions/ backend/tests/integration/test_migration_rebuild.py
git commit -m "Schema: accounts, import batches, and a transactions rebuild that refuses to destroy rows"
```

---

## Task 6: Decouple persistence from ingest, proven red-green

This is the task that carries the import-linter demonstration. **It contains a required RED step.**

**Files:**
- Create: `backend/src/offerdelta/infrastructure/postgres/records.py`
- Modify: `backend/src/offerdelta/infrastructure/postgres/repositories.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/integration/test_account_repository.py` (create)

**Interfaces:**
- Consumes: `compute_fingerprint`, `FINGERPRINT_VERSION` (Task 2); `canonical_account_key` (Task 3); `AccountRow`, `ImportBatchRow`, `TransactionRow` (Task 5).
- Produces:
  - `Provenance(source_file: str, source_line: int, raw_cells: dict[str, str])`
  - `TransactionRecord(account_id, posted_on, description, normalised_merchant, amount, external_id, occurrence, provenance)`
  - `AccountRepository.register(display_name: str) -> StoredAccount`, `.by_key(key: str) -> StoredAccount | None`, `.all() -> list[StoredAccount]`
  - `TransactionRepository.add_many(records: Sequence[TransactionRecord], *, batch_id: uuid.UUID | None, now: datetime | None = None) -> TransactionImportResult`
  - `TransactionRepository.count(*, account_id: uuid.UUID | None = None) -> int`

- [ ] **Step 1: Add the contract and watch it go RED**

Add to `backend/pyproject.toml`:

```toml
[[tool.importlinter.contracts]]
name = "Persistence does not depend on CSV ingest"
type = "forbidden"
source_modules = ["offerdelta.infrastructure"]
forbidden_modules = ["offerdelta.ingest"]
```

- [ ] **Step 2: Run it and CAPTURE THE FAILURE**

Run: `cd backend && uv run lint-imports`
Expected: **FAIL** — `offerdelta.infrastructure.postgres.repositories -> offerdelta.ingest.commit`.

Paste the failing output into the eventual commit message. A contract that was green the moment it was written would not have caught the original violation, so this red output is the evidence that it works. **Do not proceed until you have seen it fail.**

- [ ] **Step 3: Write the records module**

Create `backend/src/offerdelta/infrastructure/postgres/records.py`:

```python
"""What the repository accepts.

Deliberately not the domain `Transaction`: that entity refuses to exist without
a `kind`, and a SPENDING one without a category, because an uncategorised
outflow vanishes from every total. An imported bank row has neither until
something classifies it. Loosening a real safety invariant to make persistence
tidier would be the wrong trade, so imported rows are persistence records until
they are classified.

Deliberately not the ingest `ImportPlan` either: a manually entered transaction
has no CSV, no parsed row, and no source line, and should not have to fabricate
a preview to reach storage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from offerdelta.domain.common.money import Money


@dataclass(frozen=True)
class Provenance:
    """Where a stored transaction came from. Absent for manual entry."""

    source_file: str
    source_line: int
    raw_cells: dict[str, str]


@dataclass(frozen=True)
class TransactionRecord:
    """One transaction, ready to persist, from any source.

    The fingerprint is deliberately absent: the repository derives it from
    these fields, which is what keeps a stored fingerprint reproducible from
    its own row rather than dependent on whoever built the record.
    """

    account_id: uuid.UUID
    posted_on: date
    description: str
    normalised_merchant: str
    amount: Money
    external_id: str | None
    occurrence: int
    provenance: Provenance | None
```

- [ ] **Step 4: Rewrite the repository**

In `backend/src/offerdelta/infrastructure/postgres/repositories.py`:

Delete `from offerdelta.ingest.commit import ImportPlan`. Add:

```python
from sqlalchemy import func, select

from offerdelta.domain.transactions.accounts import canonical_account_key
from offerdelta.domain.transactions.fingerprint import FINGERPRINT_VERSION, compute_fingerprint
from offerdelta.infrastructure.postgres.models import AccountRow, ImportBatchRow
from offerdelta.infrastructure.postgres.records import TransactionRecord
```

Add the account DTO and repository:

```python
@dataclass(frozen=True)
class StoredAccount:
    """A registered account."""

    id: uuid.UUID
    key: str
    display_name: str
    created_at: datetime


class AccountRepository:
    """Accounts exist because somebody registered them, never by accident.

    An import against an unknown account is refused rather than auto-creating
    one: auto-creation relocates the original bug instead of fixing it, since a
    typo still silently produces a second parallel account.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def register(self, display_name: str, *, now: datetime | None = None) -> StoredAccount:
        key = canonical_account_key(display_name)
        if self.by_key(key) is not None:
            raise ValidationError(f"account {key!r} is already registered")
        row = AccountRow(
            id=uuid.uuid4(),
            key=key,
            display_name=display_name.strip(),
            created_at=now or datetime.now(UTC),
        )
        self._session.add(row)
        try:
            self._session.flush()
        except IntegrityError as error:
            raise ValidationError(f"account {key!r} is already registered") from error
        return _to_stored_account(row)

    def by_key(self, key: str) -> StoredAccount | None:
        row = self._session.scalars(
            select(AccountRow).where(AccountRow.key == canonical_account_key(key))
        ).one_or_none()
        return None if row is None else _to_stored_account(row)

    def all(self) -> list[StoredAccount]:
        rows = self._session.scalars(select(AccountRow).order_by(AccountRow.key)).all()
        return [_to_stored_account(row) for row in rows]


def _to_stored_account(row: AccountRow) -> StoredAccount:
    return StoredAccount(
        id=row.id, key=row.key, display_name=row.display_name, created_at=row.created_at
    )
```

Replace `import_plan` with `add_many`:

```python
    def add_many(
        self,
        records: Sequence[TransactionRecord],
        *,
        batch_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> TransactionImportResult:
        """Write new identities and report every stored match.

        The read-first is what turns an ordinary re-import into a useful report
        instead of an exception; the unique constraint remains the final
        concurrency guard.
        """
        if not records:
            return TransactionImportResult(attempted_count=0, imported_ids=(), already_stored=())

        account_id = records[0].account_id
        fingerprints = {self._fingerprint(record) for record in records}
        existing = set(
            self._session.execute(
                select(TransactionRow.fingerprint, TransactionRow.occurrence).where(
                    TransactionRow.account_id == account_id,
                    TransactionRow.fingerprint.in_(fingerprints),
                )
            ).all()
        )
        existing_external = set(
            self._session.scalars(
                select(TransactionRow.external_id).where(
                    TransactionRow.account_id == account_id,
                    TransactionRow.external_id.is_not(None),
                )
            ).all()
        )

        imported_ids: list[uuid.UUID] = []
        already_stored: list[AlreadyStoredTransaction] = []
        imported_at = now or datetime.now(UTC)

        for record in records:
            fingerprint = self._fingerprint(record)
            if record.external_id is not None:
                seen = record.external_id in existing_external
            else:
                seen = (fingerprint, record.occurrence) in existing

            if seen:
                already_stored.append(
                    AlreadyStoredTransaction(
                        source_line=record.provenance.source_line if record.provenance else 0,
                        fingerprint=fingerprint,
                        occurrence=record.occurrence,
                    )
                )
                continue

            identifier = uuid.uuid4()
            imported_ids.append(identifier)
            quantised = _quantised(record.amount)
            self._session.add(
                TransactionRow(
                    id=identifier,
                    imported_at=imported_at,
                    account_id=record.account_id,
                    batch_id=batch_id,
                    posted_on=record.posted_on,
                    description=record.description,
                    normalised_merchant=record.normalised_merchant,
                    currency=quantised.currency,
                    amount=quantised.amount,
                    external_id=record.external_id,
                    fingerprint=fingerprint,
                    fingerprint_version=FINGERPRINT_VERSION,
                    occurrence=record.occurrence,
                    source_file=record.provenance.source_file if record.provenance else None,
                    source_line=record.provenance.source_line if record.provenance else None,
                    raw_cells=dict(record.provenance.raw_cells) if record.provenance else None,
                )
            )

        if imported_ids:
            try:
                self._session.flush()
            except IntegrityError as error:
                raise ValidationError(
                    "transaction import conflicted with another import; retry so stored "
                    "duplicates can be reported safely"
                ) from error

        return TransactionImportResult(
            attempted_count=len(records),
            imported_ids=tuple(imported_ids),
            already_stored=tuple(already_stored),
        )

    @staticmethod
    def _fingerprint(record: TransactionRecord) -> str:
        return compute_fingerprint(
            account_id=record.account_id,
            posted_on=record.posted_on,
            normalised_merchant=record.normalised_merchant,
            amount=record.amount,
        )
```

Replace `count` and delete `recent` entirely:

```python
    def count(self, *, account_id: uuid.UUID | None = None) -> int:
        statement = select(func.count()).select_from(TransactionRow)
        if account_id is not None:
            statement = statement.where(TransactionRow.account_id == account_id)
        return self._session.scalars(statement).one()
```

Replace `StoredTransaction` and `_to_stored_transaction` with:

```python
@dataclass(frozen=True)
class StoredTransaction:
    """An imported bank row as it came back from storage."""

    id: uuid.UUID
    imported_at: datetime
    account_id: uuid.UUID
    batch_id: uuid.UUID | None
    posted_on: date
    description: str
    normalised_merchant: str
    amount: Money
    external_id: str | None
    fingerprint: str
    fingerprint_version: int
    occurrence: int
    source_file: str | None
    source_line: int | None
    raw_cells: dict[str, str] | None


def _to_stored_transaction(row: TransactionRow) -> StoredTransaction:
    return StoredTransaction(
        id=row.id,
        imported_at=row.imported_at,
        account_id=row.account_id,
        batch_id=row.batch_id,
        posted_on=row.posted_on,
        description=row.description,
        normalised_merchant=row.normalised_merchant,
        amount=Money(row.amount, row.currency),
        external_id=row.external_id,
        fingerprint=row.fingerprint,
        fingerprint_version=row.fingerprint_version,
        occurrence=row.occurrence,
        source_file=row.source_file,
        source_line=row.source_line,
        raw_cells=dict(row.raw_cells) if row.raw_cells is not None else None,
    )
```

Add `from collections.abc import Sequence` to the imports.

- [ ] **Step 5: Run the contract and watch it go GREEN**

Run: `cd backend && uv run lint-imports`
Expected: **PASS** — all contracts green. This is the second half of the demonstration.

- [ ] **Step 6: Write the account repository test**

Create `backend/tests/integration/test_account_repository.py`:

```python
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.postgres.repositories import AccountRepository
from tests.integration.conftest import requires_database

pytestmark = requires_database


def test_registering_returns_a_canonical_key(session: Session) -> None:
    account = AccountRepository(session).register("Chase Checking")
    assert account.key == "chase-checking"
    assert account.display_name == "Chase Checking"


def test_case_variants_resolve_to_the_same_account(session: Session) -> None:
    """The original bug: Checking and checking were two accounts."""
    repo = AccountRepository(session)
    registered = repo.register("Checking")

    for spelling in ("checking", "Checking", "CHECKING", "  Checking  "):
        found = repo.by_key(spelling)
        assert found is not None
        assert found.id == registered.id


def test_registering_the_same_account_twice_is_refused(session: Session) -> None:
    repo = AccountRepository(session)
    repo.register("Checking")
    with pytest.raises(ValidationError, match="already registered"):
        repo.register("checking")


def test_an_unregistered_account_is_not_found(session: Session) -> None:
    assert AccountRepository(session).by_key("nonexistent") is None
```

- [ ] **Step 7: Run it**

Run: `cd backend && uv run pytest tests/integration/test_account_repository.py -v`
Expected: PASS (4 passed).

- [ ] **Step 8: Commit with the red-green evidence**

```bash
git add backend/src/offerdelta/infrastructure/postgres/ backend/pyproject.toml backend/tests/integration/test_account_repository.py
git commit -m "Persistence: accept records, not an ingest plan

The repository imported offerdelta.ingest.commit to accept an ImportPlan,
so a manually entered transaction could not reach storage without
fabricating a preview.

The new import-linter contract was added first and observed failing:

  Persistence does not depend on CSV ingest BROKEN
  offerdelta.infrastructure.postgres.repositories -> offerdelta.ingest.commit

It passes only after this change. A contract that was green when written
would not have caught the violation it exists to prevent."
```

---

## Task 7: Import batches and checksum idempotency

**Files:**
- Modify: `backend/src/offerdelta/infrastructure/postgres/repositories.py`
- Test: `backend/tests/integration/test_import_batches.py` (create)

**Interfaces:**
- Produces:
  - `StoredBatch(id, account_id, source_file, source_sha256, mode, window_start, window_end, row_count, imported_at)`
  - `ImportBatchRepository.open(account_id, *, source_file, source_sha256, mode, window_start, window_end, row_count, now=None) -> tuple[StoredBatch, bool]` — the bool is `True` when newly created, `False` when an identical file was already imported.
  - `file_sha256(path: Path) -> str` in `backend/src/offerdelta/ingest/checksum.py`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_import_batches.py`:

```python
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from offerdelta.infrastructure.postgres.repositories import (
    AccountRepository,
    ImportBatchRepository,
)
from tests.integration.conftest import requires_database

pytestmark = requires_database

CHECKSUM = "a" * 64


def _open(session: Session, account_id, checksum: str = CHECKSUM):
    return ImportBatchRepository(session).open(
        account_id,
        source_file="aug.csv",
        source_sha256=checksum,
        mode="snapshot",
        window_start=date(2026, 8, 1),
        window_end=date(2026, 8, 31),
        row_count=400,
    )


def test_opening_a_new_batch_reports_it_as_created(session: Session) -> None:
    account = AccountRepository(session).register("Checking")
    _batch, created = _open(session, account.id)
    assert created is True


def test_an_identical_file_returns_the_original_batch(session: Session) -> None:
    """The one unambiguous form of 'already imported'."""
    account = AccountRepository(session).register("Checking")
    first, created_first = _open(session, account.id)
    second, created_second = _open(session, account.id)

    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    assert second.imported_at == first.imported_at


def test_a_different_file_opens_a_new_batch(session: Session) -> None:
    account = AccountRepository(session).register("Checking")
    first, _ = _open(session, account.id)
    second, created = _open(session, account.id, checksum="b" * 64)

    assert created is True
    assert second.id != first.id


def test_the_same_file_in_another_account_is_a_new_batch(session: Session) -> None:
    repo = AccountRepository(session)
    checking = repo.register("Checking")
    savings = repo.register("Savings")

    _first, _ = _open(session, checking.id)
    _second, created = _open(session, savings.id)
    assert created is True
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_import_batches.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImportBatchRepository'`.

- [ ] **Step 3: Write the checksum helper**

Create `backend/src/offerdelta/ingest/checksum.py`:

```python
"""File identity.

A byte-identical re-import is the one case where "already imported" is a fact
rather than an inference from content, so it is worth recording exactly.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

_CHUNK: Final = 1 << 20


def file_sha256(path: Path) -> str:
    """Hex digest of the file's bytes, read in chunks so size does not matter."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Write the batch repository**

Add to `repositories.py`:

```python
@dataclass(frozen=True)
class StoredBatch:
    """One recorded import of one file."""

    id: uuid.UUID
    account_id: uuid.UUID
    source_file: str
    source_sha256: str
    mode: str
    window_start: date | None
    window_end: date | None
    row_count: int
    imported_at: datetime


class ImportBatchRepository:
    """Batches make a re-import of the same bytes provably a no-op."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def open(
        self,
        account_id: uuid.UUID,
        *,
        source_file: str,
        source_sha256: str,
        mode: str,
        window_start: date | None,
        window_end: date | None,
        row_count: int,
        now: datetime | None = None,
    ) -> tuple[StoredBatch, bool]:
        """Return the batch and whether it was newly created."""
        existing = self._session.scalars(
            select(ImportBatchRow).where(
                ImportBatchRow.account_id == account_id,
                ImportBatchRow.source_sha256 == source_sha256,
            )
        ).one_or_none()
        if existing is not None:
            return _to_stored_batch(existing), False

        row = ImportBatchRow(
            id=uuid.uuid4(),
            account_id=account_id,
            source_file=source_file,
            source_sha256=source_sha256,
            mode=mode,
            window_start=window_start,
            window_end=window_end,
            row_count=row_count,
            imported_at=now or datetime.now(UTC),
        )
        self._session.add(row)
        self._session.flush()
        return _to_stored_batch(row), True


def _to_stored_batch(row: ImportBatchRow) -> StoredBatch:
    return StoredBatch(
        id=row.id,
        account_id=row.account_id,
        source_file=row.source_file,
        source_sha256=row.source_sha256,
        mode=row.mode,
        window_start=row.window_start,
        window_end=row.window_end,
        row_count=row.row_count,
        imported_at=row.imported_at,
    )
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/integration/test_import_batches.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/src/offerdelta/ingest/checksum.py backend/src/offerdelta/infrastructure/postgres/repositories.py backend/tests/integration/test_import_batches.py
git commit -m "Import batches: a source checksum makes an identical re-import a no-op"
```

---

## Task 8: Snapshot mode with a declared window

**Files:**
- Modify: `backend/src/offerdelta/ingest/mapping.py`
- Modify: `backend/src/offerdelta/ingest/commit.py`
- Test: `backend/tests/unit/ingest/test_commit.py` (rewrite)
- Test: `backend/tests/integration/test_snapshot_import.py` (create)

**Interfaces:**
- Consumes: `TransactionRecord`, `Provenance` (Task 6).
- Produces:
  - `ImportMode` StrEnum: `SNAPSHOT`, `INCREMENTAL`.
  - `ImportWindow(start: date, end: date)`.
  - `plan_records(preview, *, account_id, mode, window) -> tuple[TransactionRecord, ...]`.
  - `ColumnMapping.external_id: str | None = None`.

- [ ] **Step 1: Write the failing unit test**

Rewrite `backend/tests/unit/ingest/test_commit.py`:

```python
from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.ingest.commit import ImportMode, ImportWindow, plan_records
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.preview import preview_csv

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
AUGUST = ImportWindow(start=date(2026, 8, 1), end=date(2026, 8, 31))
HEADER = "Date,Description,Amount\n"


def _preview(tmp_path: Path, body: str):
    path = tmp_path / "aug.csv"
    path.write_text(HEADER + body, encoding="utf-8")
    return preview_csv(path, date_order=DateOrder.ISO)


def test_snapshot_numbers_repeats_within_the_file(tmp_path: Path) -> None:
    preview = _preview(
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-08-17,BLUE BOTTLE,-4.50\n",
    )
    records = plan_records(
        preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST
    )
    assert [r.occurrence for r in records] == [1, 2]


def test_snapshot_requires_a_window(tmp_path: Path) -> None:
    preview = _preview(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")
    with pytest.raises(ValidationError, match="window"):
        plan_records(preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=None)


def test_a_row_outside_the_window_is_refused(tmp_path: Path) -> None:
    """A file wider than the declared range is not the snapshot you said."""
    preview = _preview(
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-09-02,LATE CHARGE,-9.00\n",
    )
    with pytest.raises(ValidationError, match="outside the declared window"):
        plan_records(preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST)


def test_the_offending_line_is_named(tmp_path: Path) -> None:
    preview = _preview(
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-09-02,LATE CHARGE,-9.00\n",
    )
    with pytest.raises(ValidationError, match="line 3"):
        plan_records(preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST)


def test_incremental_without_an_external_id_is_refused(tmp_path: Path) -> None:
    """The ambiguity is unresolvable without a stable id, so refuse it."""
    preview = _preview(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")
    with pytest.raises(ValidationError, match="transaction id"):
        plan_records(
            preview, account_id=ACCOUNT, mode=ImportMode.INCREMENTAL, window=None
        )


def test_records_carry_provenance(tmp_path: Path) -> None:
    preview = _preview(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")
    records = plan_records(
        preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST
    )
    assert records[0].provenance is not None
    assert records[0].provenance.source_file == "aug.csv"
    assert records[0].provenance.source_line == 2


def test_a_preview_with_errors_is_refused(tmp_path: Path) -> None:
    preview = _preview(tmp_path, "not-a-date,BLUE BOTTLE,-4.50\n")
    with pytest.raises(ValidationError, match="have errors"):
        plan_records(
            preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST
        )


def test_an_empty_preview_is_refused(tmp_path: Path) -> None:
    preview = _preview(tmp_path, "")
    with pytest.raises(ValidationError, match="no parsed rows"):
        plan_records(
            preview, account_id=ACCOUNT, mode=ImportMode.SNAPSHOT, window=AUGUST
        )
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/unit/ingest/test_commit.py -v`
Expected: FAIL — `ImportError: cannot import name 'ImportMode'`.

- [ ] **Step 3: Add `external_id` to the mapping**

In `backend/src/offerdelta/ingest/mapping.py`, add the field to `ColumnMapping` after `merchant`:

```python
    #: The bank's own transaction id, when the export carries one. Its presence
    #: is what makes an incremental import possible at all.
    external_id: str | None = None
```

Add it to `source_columns()`:

```python
    def source_columns(self) -> tuple[str, ...]:
        named = (
            self.date,
            self.description,
            self.merchant,
            self.external_id,
            self.amount,
            self.debit,
            self.credit,
        )
        return tuple(column for column in named if column)
```

- [ ] **Step 4: Rewrite `commit.py`**

Replace the whole of `backend/src/offerdelta/ingest/commit.py`:

```python
"""Turning an inspected preview into records that are safe to write.

Planning stays separate from writing: the preview remains a pure, read-only
description of the file, and only a caller that explicitly asks gets records
back.

## The ambiguity this module refuses to guess at

A fingerprint plus a per-file occurrence number cannot distinguish a genuine
third identical charge from one already stored. Suppose two `BLUE BOTTLE -4.50`
charges on 2026-08-17 are already persisted, and a new file contains exactly
one. Two readings are equally consistent with the file:

1. It is a **full-window snapshot** overlapping August, so that charge is
   occurrence 1 and is already stored. Writing it duplicates real money.
2. It is an **append-only incremental export**, so that charge is a genuine
   third coffee. Refusing it loses real money.

The deciding information is not in the file — it is a fact about how the file
was produced. So the mode is declared by the caller, never inferred, and
incremental mode refuses to run without a stable bank transaction id rather
than silently picking a reading.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path

from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.postgres.records import Provenance, TransactionRecord
from offerdelta.ingest.preview import ImportPreview


class ImportMode(StrEnum):
    """How the file was produced. Declared, never detected."""

    #: A complete window. Occurrences are numbered per file.
    SNAPSHOT = "snapshot"

    #: Only activity not previously exported. Requires an external id.
    INCREMENTAL = "incremental"


@dataclass(frozen=True)
class ImportWindow:
    """The date range a snapshot claims to cover completely."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValidationError(
                f"an import window starts before it ends; got {self.start} to {self.end}"
            )

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


def plan_records(
    preview: ImportPreview,
    *,
    account_id: uuid.UUID,
    mode: ImportMode,
    window: ImportWindow | None,
) -> tuple[TransactionRecord, ...]:
    """Validate a preview and turn it into records ready to persist.

    A preview with even one bad row is refused outright. Committing only the
    valid subset would break the preview's central promise that nothing
    disappears silently.
    """
    if preview.mapping is None:
        raise ValidationError("cannot commit an import whose column mapping is unresolved")
    if preview.errors:
        raise ValidationError(
            f"cannot commit while {len(preview.errors)} source row(s) have errors; "
            "fix them and preview the file again"
        )
    if not preview.rows:
        raise ValidationError("cannot commit an import with no parsed rows")

    has_external_id = bool(preview.mapping.external_id)

    if mode is ImportMode.INCREMENTAL and not has_external_id:
        raise ValidationError(
            "an incremental import cannot tell a new repeat charge from one "
            "already stored, because the deciding fact is not in the file. "
            "Supply the bank's transaction id column with --map=external_id:<column>, "
            "or re-export a full window and use --mode=snapshot."
        )

    if mode is ImportMode.SNAPSHOT:
        if window is None:
            raise ValidationError(
                "a snapshot import needs a declared window; pass --from and --to"
            )
        outside = [row.line for row in preview.rows if not window.covers(row.posted_on)]
        if outside:
            shown = ", ".join(f"line {line}" for line in outside[:10])
            more = "" if len(outside) <= 10 else f" and {len(outside) - 10} more"
            raise ValidationError(
                f"{len(outside)} row(s) fall outside the declared window "
                f"{window.start} to {window.end}: {shown}{more}. The file is not "
                f"the snapshot it was declared to be."
            )

    source_file = Path(preview.path).name
    seen: dict[tuple[date, str, str], int] = defaultdict(int)
    records: list[TransactionRecord] = []

    for row in preview.rows:
        key = (row.posted_on, row.normalised_merchant, f"{row.amount.amount:.2f}")
        seen[key] += 1
        external_id = None
        if preview.mapping.external_id:
            external_id = (row.raw.get(preview.mapping.external_id) or "").strip() or None
        records.append(
            TransactionRecord(
                account_id=account_id,
                posted_on=row.posted_on,
                description=row.description,
                normalised_merchant=row.normalised_merchant,
                amount=row.amount,
                external_id=external_id,
                occurrence=seen[key],
                provenance=Provenance(
                    source_file=source_file,
                    source_line=row.line,
                    raw_cells=dict(row.raw),
                ),
            )
        )

    return tuple(records)
```

Note: `ingest` importing from `infrastructure.postgres.records` is permitted — the forbidden direction is infrastructure → ingest. Verify with `lint-imports` in Step 6.

- [ ] **Step 5: Run the unit tests**

Run: `cd backend && uv run pytest tests/unit/ingest/test_commit.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Verify the contract still holds**

Run: `cd backend && uv run lint-imports`
Expected: PASS. If the layers contract objects to ingest importing infrastructure, move `records.py` to `offerdelta/persistence/records.py` (a neutral package both may import) and update both imports.

- [ ] **Step 7: Write the snapshot integration test**

Create `backend/tests/integration/test_snapshot_import.py`:

```python
"""Snapshot semantics, asserted on real PostgreSQL."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from offerdelta.ingest.commit import ImportMode, ImportWindow, plan_records
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.preview import preview_csv
from offerdelta.infrastructure.postgres.repositories import (
    AccountRepository,
    TransactionRepository,
)
from tests.integration.conftest import requires_database

pytestmark = requires_database

HEADER = "Date,Description,Amount\n"
AUGUST = ImportWindow(start=date(2026, 8, 1), end=date(2026, 8, 31))


def _import(session: Session, account_id, tmp_path: Path, body: str, name: str = "aug.csv"):
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    preview = preview_csv(path, date_order=DateOrder.ISO)
    records = plan_records(
        preview, account_id=account_id, mode=ImportMode.SNAPSHOT, window=AUGUST
    )
    return TransactionRepository(session).add_many(records)


def test_the_same_charge_written_two_ways_is_one_transaction(
    session: Session, tmp_path: Path
) -> None:
    """-4.50 and -4.5 are the same coffee. This was the headline bug."""
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)

    _import(session, account.id, tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n", "a.csv")
    second = _import(session, account.id, tmp_path, "2026-08-17,BLUE BOTTLE,-4.5\n", "b.csv")

    assert second.imported_count == 0
    assert second.already_stored_count == 1
    assert repo.count(account_id=account.id) == 1


def test_re_importing_an_identical_window_writes_nothing(
    session: Session, tmp_path: Path
) -> None:
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)
    body = "2026-08-17,BLUE BOTTLE,-4.50\n2026-08-18,TRANSIT,-2.75\n"

    _import(session, account.id, tmp_path, body, "a.csv")
    second = _import(session, account.id, tmp_path, body, "b.csv")

    assert second.imported_count == 0
    assert repo.count(account_id=account.id) == 2


def test_two_identical_charges_on_one_day_both_persist(
    session: Session, tmp_path: Path
) -> None:
    """Two coffees are two coffees. Deduplicating them deletes real money."""
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)

    _import(
        session,
        account.id,
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-08-17,BLUE BOTTLE,-4.50\n",
    )
    assert repo.count(account_id=account.id) == 2


def test_a_later_window_containing_a_third_repeat_adds_exactly_one(
    session: Session, tmp_path: Path
) -> None:
    """The case an unconditional max-occurrence offset would have doubled."""
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)

    _import(
        session,
        account.id,
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-08-17,BLUE BOTTLE,-4.50\n",
        "first.csv",
    )
    result = _import(
        session,
        account.id,
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n"
        "2026-08-17,BLUE BOTTLE,-4.50\n"
        "2026-08-17,BLUE BOTTLE,-4.50\n",
        "second.csv",
    )

    assert result.imported_count == 1
    assert result.already_stored_count == 2
    assert repo.count(account_id=account.id) == 3


def test_two_accounts_do_not_share_identities(session: Session, tmp_path: Path) -> None:
    repo = AccountRepository(session)
    checking = repo.register("Checking")
    savings = repo.register("Savings")
    body = "2026-08-17,BLUE BOTTLE,-4.50\n"

    _import(session, checking.id, tmp_path, body, "a.csv")
    result = _import(session, savings.id, tmp_path, body, "b.csv")

    assert result.imported_count == 1


def test_every_stored_fingerprint_recomputes_from_its_own_row(
    session: Session, tmp_path: Path
) -> None:
    """Reproducibility is a property the suite proves, not a claim."""
    from sqlalchemy import select

    from offerdelta.domain.common.money import Money
    from offerdelta.domain.transactions.fingerprint import compute_fingerprint
    from offerdelta.infrastructure.postgres.models import TransactionRow

    account = AccountRepository(session).register("Checking")
    _import(
        session,
        account.id,
        tmp_path,
        "2026-08-17,BLUE BOTTLE,-4.50\n2026-08-18,TRANSIT,-2.75\n",
    )

    for row in session.scalars(select(TransactionRow)).all():
        recomputed = compute_fingerprint(
            account_id=row.account_id,
            posted_on=row.posted_on,
            normalised_merchant=row.normalised_merchant,
            amount=Money(row.amount, row.currency),
        )
        assert recomputed == row.fingerprint
```

- [ ] **Step 8: Run it**

Run: `cd backend && uv run pytest tests/integration/test_snapshot_import.py -v`
Expected: PASS (6 passed).

- [ ] **Step 9: Commit**

```bash
git add backend/src/offerdelta/ingest/ backend/tests/unit/ingest/test_commit.py backend/tests/integration/test_snapshot_import.py
git commit -m "Snapshot mode: a declared window, validated against every row"
```

---

## Task 9: Incremental mode driven by the bank's transaction id

**Files:**
- Test: `backend/tests/integration/test_incremental_import.py` (create)
- Modify: `backend/src/offerdelta/ingest/commit.py` (occurrence offset for incremental)

**Interfaces:**
- Consumes: everything from Task 8.
- Produces: no new public names. Incremental records carry `external_id` and an occurrence offset supplied by the repository.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_incremental_import.py`:

```python
"""Incremental semantics, which exist only when the bank supplies an id."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from offerdelta.domain.common.errors import ValidationError
from offerdelta.ingest.commit import ImportMode, plan_records
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.mapping import ColumnMapping
from offerdelta.ingest.preview import preview_csv
from offerdelta.infrastructure.postgres.repositories import (
    AccountRepository,
    TransactionRepository,
)
from tests.integration.conftest import requires_database

pytestmark = requires_database

HEADER = "TxnId,Date,Description,Amount\n"
MAPPING = ColumnMapping(
    date="Date", description="Description", amount="Amount", external_id="TxnId"
)

#: Planning validates the mode before touching the database, so the refusal
#: test needs an account id but never a real account.
ANY_ACCOUNT = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _import(session: Session, account_id, tmp_path: Path, body: str, name: str):
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    preview = preview_csv(path, mapping=MAPPING, date_order=DateOrder.ISO)
    records = plan_records(
        preview, account_id=account_id, mode=ImportMode.INCREMENTAL, window=None
    )
    return TransactionRepository(session).add_many(records)


def test_a_genuine_third_repeat_is_stored(session: Session, tmp_path: Path) -> None:
    """The case that silently lost money under file-local numbering."""
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)

    _import(
        session,
        account.id,
        tmp_path,
        "T1,2026-08-17,BLUE BOTTLE,-4.50\nT2,2026-08-17,BLUE BOTTLE,-4.50\n",
        "first.csv",
    )
    result = _import(
        session, account.id, tmp_path, "T3,2026-08-17,BLUE BOTTLE,-4.50\n", "second.csv"
    )

    assert result.imported_count == 1
    assert repo.count(account_id=account.id) == 3


def test_a_re_sent_id_is_skipped(session: Session, tmp_path: Path) -> None:
    account = AccountRepository(session).register("Checking")
    repo = TransactionRepository(session)

    _import(session, account.id, tmp_path, "T1,2026-08-17,BLUE BOTTLE,-4.50\n", "a.csv")
    result = _import(
        session,
        account.id,
        tmp_path,
        "T1,2026-08-17,BLUE BOTTLE,-4.50\nT2,2026-08-18,TRANSIT,-2.75\n",
        "b.csv",
    )

    assert result.imported_count == 1
    assert result.already_stored_count == 1
    assert repo.count(account_id=account.id) == 2


def test_incremental_is_refused_without_an_id_column(tmp_path: Path) -> None:
    """No database needed: the refusal happens during planning."""
    path = tmp_path / "no-id.csv"
    path.write_text("Date,Description,Amount\n2026-08-17,BLUE BOTTLE,-4.50\n", encoding="utf-8")
    preview = preview_csv(path, date_order=DateOrder.ISO)

    with pytest.raises(ValidationError, match="transaction id"):
        plan_records(
            preview,
            account_id=ANY_ACCOUNT,
            mode=ImportMode.INCREMENTAL,
            window=None,
        )
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_incremental_import.py -v`
Expected: FAIL on `test_a_genuine_third_repeat_is_stored` — the third charge is numbered occurrence 1 and collides with the stored occurrence 1 on the unique constraint.

- [ ] **Step 3: Offset occurrences in incremental mode**

In `TransactionRepository.add_many`, before the loop, compute the per-fingerprint offset when records carry external ids:

```python
        offsets: dict[str, int] = {}
        if any(record.external_id is not None for record in records):
            rows = self._session.execute(
                select(
                    TransactionRow.fingerprint,
                    func.max(TransactionRow.occurrence),
                )
                .where(
                    TransactionRow.account_id == account_id,
                    TransactionRow.fingerprint.in_(fingerprints),
                )
                .group_by(TransactionRow.fingerprint)
            ).all()
            offsets = {fingerprint: highest for fingerprint, highest in rows}
```

Then inside the loop, after the `seen` check:

```python
            occurrence = record.occurrence + offsets.get(fingerprint, 0)
```

and use `occurrence` in place of `record.occurrence` when constructing `TransactionRow`. The offset is safe here precisely because `external_id` is doing the deduplication — the occurrence only has to satisfy the unique constraint, not carry identity.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/integration/test_incremental_import.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Confirm snapshot mode is unaffected**

Run: `cd backend && uv run pytest tests/integration/test_snapshot_import.py -v`
Expected: PASS (6 passed). Snapshot records have `external_id is None`, so no offset applies.

- [ ] **Step 6: Commit**

```bash
git add backend/src/offerdelta/infrastructure/postgres/repositories.py backend/tests/integration/test_incremental_import.py
git commit -m "Incremental mode: the bank's id carries identity, so occurrence can offset safely"
```

---

## Task 10: The application service

**Files:**
- Create: `backend/src/offerdelta/application/transactions/__init__.py`
- Create: `backend/src/offerdelta/application/transactions/import_transactions.py`
- Test: `backend/tests/integration/test_import_transactions_service.py` (create)

**Interfaces:**
- Produces:
  - `ImportRequest(path: Path, account_key: str, mode: ImportMode, window: ImportWindow | None, mapping: ColumnMapping | None, date_order: DateOrder | None)`
  - `ImportOutcome(batch: StoredBatch, created: bool, result: TransactionImportResult)`
  - `import_csv(session: Session, request: ImportRequest) -> ImportOutcome`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_import_transactions_service.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from offerdelta.application.transactions.import_transactions import (
    ImportRequest,
    import_csv,
)
from offerdelta.domain.common.errors import ValidationError
from offerdelta.ingest.commit import ImportMode, ImportWindow
from offerdelta.ingest.dates import DateOrder
from offerdelta.infrastructure.postgres.repositories import AccountRepository
from tests.integration.conftest import requires_database

pytestmark = requires_database

HEADER = "Date,Description,Amount\n"
AUGUST = ImportWindow(start=date(2026, 8, 1), end=date(2026, 8, 31))


def _request(path: Path, key: str = "checking") -> ImportRequest:
    return ImportRequest(
        path=path,
        account_key=key,
        mode=ImportMode.SNAPSHOT,
        window=AUGUST,
        mapping=None,
        date_order=DateOrder.ISO,
    )


def _file(tmp_path: Path, body: str, name: str = "aug.csv") -> Path:
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def test_an_unregistered_account_is_refused(session: Session, tmp_path: Path) -> None:
    path = _file(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")
    with pytest.raises(ValidationError, match="no account"):
        import_csv(session, _request(path))


def test_the_error_lists_known_accounts(session: Session, tmp_path: Path) -> None:
    AccountRepository(session).register("Chase Checking")
    path = _file(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")
    with pytest.raises(ValidationError, match="chase-checking"):
        import_csv(session, _request(path, key="checking"))


def test_a_successful_import_reports_the_batch(session: Session, tmp_path: Path) -> None:
    AccountRepository(session).register("Checking")
    path = _file(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")

    outcome = import_csv(session, _request(path))

    assert outcome.created is True
    assert outcome.result.imported_count == 1
    assert outcome.batch.mode == "snapshot"
    assert outcome.batch.window_start == date(2026, 8, 1)


def test_the_identical_file_is_a_batch_level_no_op(session: Session, tmp_path: Path) -> None:
    AccountRepository(session).register("Checking")
    path = _file(tmp_path, "2026-08-17,BLUE BOTTLE,-4.50\n")

    first = import_csv(session, _request(path))
    second = import_csv(session, _request(path))

    assert first.created is True
    assert second.created is False
    assert second.result.imported_count == 0
    assert second.batch.id == first.batch.id
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/integration/test_import_transactions_service.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write the service**

Create `backend/src/offerdelta/application/transactions/__init__.py` (empty), then `import_transactions.py`:

```python
"""Importing transactions, orchestrated in one place.

CSV ingest is one input adapter. Manual entry will be another, and it needs the
same sequence — resolve the account, open a batch, build records, write, report
— minus the parsing. Putting that sequence here is what stops it being written
twice, or a form reaching into the CSV pipeline for something it does not need.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.postgres.repositories import (
    AccountRepository,
    ImportBatchRepository,
    StoredBatch,
    TransactionImportResult,
    TransactionRepository,
)
from offerdelta.ingest.checksum import file_sha256
from offerdelta.ingest.commit import ImportMode, ImportWindow, plan_records
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.mapping import ColumnMapping
from offerdelta.ingest.preview import preview_csv


@dataclass(frozen=True)
class ImportRequest:
    """Everything one import needs, stated explicitly."""

    path: Path
    account_key: str
    mode: ImportMode
    window: ImportWindow | None
    mapping: ColumnMapping | None = None
    date_order: DateOrder | None = None


@dataclass(frozen=True)
class ImportOutcome:
    """What happened, in full."""

    batch: StoredBatch
    created: bool
    result: TransactionImportResult


def import_csv(session: Session, request: ImportRequest) -> ImportOutcome:
    """Resolve, plan, and write one CSV import."""
    accounts = AccountRepository(session)
    account = accounts.by_key(request.account_key)
    if account is None:
        known = ", ".join(a.key for a in accounts.all()) or "none registered yet"
        raise ValidationError(
            f"no account {request.account_key!r}. Known accounts: {known}. "
            f"Register one with: transactions.py accounts add <display name>"
        )

    preview = preview_csv(request.path, mapping=request.mapping, date_order=request.date_order)
    records = plan_records(
        preview, account_id=account.id, mode=request.mode, window=request.window
    )

    batch, created = ImportBatchRepository(session).open(
        account.id,
        source_file=request.path.name,
        source_sha256=file_sha256(request.path),
        mode=str(request.mode),
        window_start=request.window.start if request.window else None,
        window_end=request.window.end if request.window else None,
        row_count=len(records),
    )
    if not created:
        # Byte-identical file. The one unambiguous "already imported".
        return ImportOutcome(
            batch=batch,
            created=False,
            result=TransactionImportResult(
                attempted_count=len(records), imported_ids=(), already_stored=()
            ),
        )

    result = TransactionRepository(session).add_many(records, batch_id=batch.id)
    return ImportOutcome(batch=batch, created=True, result=result)
```

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/integration/test_import_transactions_service.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Verify layering**

Run: `cd backend && uv run lint-imports`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/offerdelta/application/transactions/ backend/tests/integration/test_import_transactions_service.py
git commit -m "Application: one place that orchestrates an import"
```

---

## Task 11: The CLI, with argparse and a real commit gate

**Files:**
- Create: `backend/transactions.py`
- Delete: `backend/preview_import.py`
- Test: `backend/tests/unit/test_transactions_cli.py` (create)

**Interfaces:**
- Produces: `build_parser() -> argparse.ArgumentParser`, `main(argv: list[str]) -> int`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/unit/test_transactions_cli.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest

from transactions import build_parser, main

HEADER = "Date,Description,Amount\n"


def _file(tmp_path: Path) -> Path:
    path = tmp_path / "aug.csv"
    path.write_text(HEADER + "2026-08-17,BLUE BOTTLE,-4.50\n", encoding="utf-8")
    return path


def test_an_unknown_flag_is_an_error(tmp_path: Path) -> None:
    """The bug: --dayfirst was silently discarded and dates committed wrong."""
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["preview", str(_file(tmp_path)), "--dayfirst"])


def test_a_misspelled_map_flag_is_an_error(tmp_path: Path) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["preview", str(_file(tmp_path)), "--mapping=Date:Date"])


def test_commit_requires_a_mode(tmp_path: Path) -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["commit", str(_file(tmp_path)), "--account=checking", "--yes"])


def test_snapshot_commit_requires_a_window(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        ["commit", str(_file(tmp_path)), "--account=checking", "--mode=snapshot", "--yes"]
    )
    assert args.window_start is None  # the service refuses; parser does not guess


def test_preview_never_writes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Preview is read-only. It has no --yes and no commit path."""
    code = main(["preview", str(_file(tmp_path)), "--dates=ISO"])
    out = capsys.readouterr().out
    assert code == 0
    assert "committed" not in out.lower()


def test_commit_without_yes_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old CLI rendered a preview and committed in the same breath."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(
        [
            "commit",
            str(_file(tmp_path)),
            "--account=checking",
            "--mode=snapshot",
            "--from=2026-08-01",
            "--to=2026-08-31",
        ]
    )
    out = capsys.readouterr().out
    assert code != 0
    assert "--yes" in out


def test_commit_does_not_render_the_preview_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    main(
        [
            "commit",
            str(_file(tmp_path)),
            "--account=checking",
            "--mode=snapshot",
            "--from=2026-08-01",
            "--to=2026-08-31",
        ]
    )
    out = capsys.readouterr().out
    assert "merchant" not in out  # the preview table header
```

- [ ] **Step 2: Run it and verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_transactions_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: transactions`.

- [ ] **Step 3: Write the CLI**

Create `backend/transactions.py`:

```python
"""Preview, commit, and account registration.

Two properties this script is responsible for, both of which the previous one
got wrong.

**Unknown arguments are an error.** The old hand-rolled scan discarded anything
it did not recognise, so `--dayfirst` (a missing hyphen) vanished and four
hundred rows committed with an eleven-month date error. argparse rejects
unknown arguments by default, and nothing here uses `parse_known_args`.

**Preview never falls through into a write.** The old script rendered ten of
four hundred rows and then committed in the same non-interactive invocation,
which made the preview decorative. `commit` is a separate subcommand that
prints a summary, not a preview, and requires `--yes` or an interactive
confirmation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from offerdelta.application.transactions.import_transactions import ImportRequest, import_csv
from offerdelta.domain.common.errors import ValidationError
from offerdelta.infrastructure.postgres.engine import get_engine
from offerdelta.infrastructure.postgres.repositories import AccountRepository
from offerdelta.ingest.commit import ImportMode, ImportWindow
from offerdelta.ingest.dates import DateOrder
from offerdelta.ingest.mapping import ColumnMapping
from offerdelta.ingest.preview import preview_csv


def _mapping(raw: str | None) -> ColumnMapping | None:
    if not raw:
        return None
    fields: dict[str, str] = {}
    for pair in raw.split(","):
        name, _, column = pair.partition(":")
        if not name or not column:
            raise argparse.ArgumentTypeError(
                f"--map entries look like field:Column, got {pair!r}"
            )
        fields[name.strip()] = column.strip()
    return ColumnMapping(**fields)  # type: ignore[arg-type]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="transactions.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preview = sub.add_parser("preview", help="show what an import would do; writes nothing")
    preview.add_argument("file", type=Path)
    preview.add_argument("--map", dest="mapping", default=None)
    preview.add_argument("--dates", dest="dates", choices=[o.value for o in DateOrder], default=None)

    commit = sub.add_parser("commit", help="write an import; requires --yes")
    commit.add_argument("file", type=Path)
    commit.add_argument("--account", required=True)
    commit.add_argument("--mode", required=True, choices=[m.value for m in ImportMode])
    commit.add_argument("--from", dest="window_start", type=date.fromisoformat, default=None)
    commit.add_argument("--to", dest="window_end", type=date.fromisoformat, default=None)
    commit.add_argument("--map", dest="mapping", default=None)
    commit.add_argument("--dates", dest="dates", choices=[o.value for o in DateOrder], default=None)
    commit.add_argument("--yes", action="store_true", help="confirm the write")

    accounts = sub.add_parser("accounts", help="register and list accounts")
    accounts_sub = accounts.add_subparsers(dest="accounts_command", required=True)
    add = accounts_sub.add_parser("add")
    add.add_argument("display_name")
    accounts_sub.add_parser("list")

    return parser


def _confirm(summary: str) -> bool:
    print(summary)
    if not sys.stdin.isatty():
        print("refusing to write without --yes (stdin is not a terminal)")
        return False
    return input("type 'yes' to write: ").strip().lower() == "yes"


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "preview":
            preview = preview_csv(
                args.file,
                mapping=_mapping(args.mapping),
                date_order=DateOrder(args.dates) if args.dates else None,
            )
            print(preview.render())
            return 0 if preview.importable else 1

        if args.command == "accounts":
            with Session(get_engine()) as session:
                repo = AccountRepository(session)
                if args.accounts_command == "add":
                    account = repo.register(args.display_name)
                    session.commit()
                    print(f"created {account.key}  ({account.display_name})")
                else:
                    for account in repo.all():
                        print(f"{account.key:<24}{account.display_name}")
                return 0

        window = None
        if args.window_start is not None and args.window_end is not None:
            window = ImportWindow(start=args.window_start, end=args.window_end)

        summary = (
            f"{args.file.name} -> account {args.account}, mode {args.mode}"
            + (f", window {args.window_start} to {args.window_end}" if window else "")
        )
        if not args.yes and not _confirm(summary):
            return 2

        with Session(get_engine()) as session:
            outcome = import_csv(
                session,
                ImportRequest(
                    path=args.file,
                    account_key=args.account,
                    mode=ImportMode(args.mode),
                    window=window,
                    mapping=_mapping(args.mapping),
                    date_order=DateOrder(args.dates) if args.dates else None,
                ),
            )
            session.commit()

        if not outcome.created:
            print(f"already imported: identical file, batch {outcome.batch.id}")
            return 0
        print(f"committed {outcome.result.imported_count} of {outcome.result.attempted_count} rows")
        if outcome.result.already_stored_count:
            lines = ", ".join(str(a.source_line) for a in outcome.result.already_stored[:20])
            print(f"already stored {outcome.result.already_stored_count}: source lines {lines}")
        return 0

    except ValidationError as error:
        print(error)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Delete the old script**

```bash
git rm backend/preview_import.py
```

- [ ] **Step 5: Run the tests**

Run: `cd backend && uv run pytest tests/unit/test_transactions_cli.py -v`
Expected: PASS (7 passed).

- [ ] **Step 6: Run everything**

Run: `cd backend && make -C .. check`
Expected: lint, types, arch, and tests all green.

- [ ] **Step 7: Commit**

```bash
git add backend/transactions.py backend/tests/unit/test_transactions_cli.py
git commit -m "CLI: argparse, and a commit that cannot happen by accident"
```

---

## Task 12: Documentation

**Files:**
- Modify: `docs/status/2026-08-21.md`
- Modify: `README.md`

- [ ] **Step 1: Correct the status note**

In `docs/status/2026-08-21.md`, replace the false claim on line 7. It currently reads:

> Transaction persistence is implemented in the working tree and its migration is applied to the configured PostgreSQL database; the application changes have not been committed or deployed.

Replace with:

```markdown
Transaction persistence is committed and merged to `main` (`e51562c`, PR #1). It is not
deployed. A review of that commit found the import identity model unsafe for real statements,
and the corrective pass is specified in
`docs/superpowers/specs/2026-08-21-transaction-import-data-safety-design.md`.
```

Also fix line 114 ("Migration applied; code awaits commit and deployment") the same way.

- [ ] **Step 2: Add a "Known issues, now fixed" section**

Append to `docs/status/2026-08-21.md`:

```markdown
## The data-safety pass

A review of `e51562c` found the fingerprint built from three unnormalised inputs, each of
which broke deduplication on its own: an un-quantised amount scale, a case-sensitive
free-text account, and an unversioned merchant heuristic. Two were confirmed empirically —
`-4.50` and `-4.5` stored twice, and `--account=Checking` versus `--account=checking` built
two parallel copies of a statement.

The corrective pass canonicalises account identity behind a registry, makes the fingerprint
quantised and versioned and reproducible from persisted fields, replaces the hand-rolled CLI
parsing with argparse behind an explicit `--yes` gate, and separates snapshot from incremental
import semantics rather than guessing between them.

The ambiguity at the centre is worth stating plainly: a fingerprint plus a per-file occurrence
number cannot distinguish a genuine third identical charge from one already stored, because
the deciding fact is not in the file. So the mode is declared, and incremental mode refuses to
run without a bank transaction id rather than pick a reading silently.
```

- [ ] **Step 3: Update the README import section**

Document the three commands, both modes, the window requirement, and that incremental needs an
id column. Include the worked example:

````markdown
### Importing transactions

Register the account once:

```bash
uv run python transactions.py accounts add "Chase Checking"
```

Preview before you write — this never touches the database:

```bash
uv run python transactions.py preview statement.csv --dates=ISO
```

Commit a full-window export. Both `--from` and `--to` are required, and every row must fall
inside them:

```bash
uv run python transactions.py commit statement.csv \
  --account=chase-checking --mode=snapshot \
  --from=2026-08-01 --to=2026-08-31 --yes
```

Incremental exports need the bank's transaction id column, because without one there is no way
to tell a genuine repeat charge from one already stored:

```bash
uv run python transactions.py commit new-activity.csv \
  --account=chase-checking --mode=incremental \
  --map=external_id:TransactionID --yes
```

Chase exports carry no stable id, so Chase files use snapshot mode with an explicit window.
````

- [ ] **Step 4: Verify**

Run: `make check`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add docs/status/2026-08-21.md README.md
git commit -m "Docs: correct the status note and document import modes"
```

---

## Self-Review

**Spec coverage.** Every numbered success criterion maps to a task: (1) Tasks 3, 6; (2) Tasks 2, 8 step 7; (3) Tasks 5, 6; (4) Task 7; (5) Tasks 8, 9; (6) Task 8; (7) Task 5; (8) Task 11; (9) Task 4 step 3, Task 5; (10) Task 6 steps 1-2, 5; (11) Tasks 1, 5; (12) Task 11 step 6.

**Known open questions for the executor:**

1. **`ingest` importing `infrastructure.postgres.records`** (Task 8) reverses the dependency rather than removing it. The forbidden contract only covers infrastructure → ingest, so it passes — but if the `Layers` contract is later made exhaustive it will not. Task 8 Step 6 names the fix: move `records.py` to a neutral `offerdelta/persistence/` package both may import. Prefer doing that immediately if `lint-imports` complains.

2. **Alembic scratch-schema wiring** (Task 5) depends on how `migrations/env.py` resolves its connection — the one thing in this plan I did not read before writing it. Read that file first; if it does not honour `cfg.attributes["target_schema"]`, set the search path through the engine URL instead: `?options=-csearch_path%3D<schema>`. If neither works, fall back to a dedicated throwaway *database* rather than a schema, and say so in the commit.

3. **The migration's abort test asserts on a `RuntimeError` escaping Alembic** (Task 5, step 1). Alembic wraps some exceptions; if the `pytest.raises(RuntimeError)` does not match, assert on `alembic.util.exc.CommandError` with the message text instead. The guard itself is what matters, not which exception class carries it.
