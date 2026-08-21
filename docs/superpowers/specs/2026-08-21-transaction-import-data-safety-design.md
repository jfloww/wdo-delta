# Transaction import: a data-safety pass

**Date:** 2026-08-21
**Status:** Approved for planning
**Supersedes:** the storage schema introduced in `a6128d6e4f20`

## Why this exists

A code review of `e51562c` ("Persist imported transactions after preview") found that the
fingerprint — the value that decides what is a duplicate and what is new money — is built
from three unnormalised inputs. Each one independently breaks deduplication, and two of them
were confirmed empirically against the repository's own environment:

- The fingerprint hashes `str(amount.amount)` before quantisation, so `-4.50` and `-4.5`
  produce different fingerprints and the same charge stores twice. The row is then persisted
  as the *quantised* value, so no stored fingerprint can be recomputed from its own stored
  row.
- The account is free text compared after a bare `.strip()`, so `--account=Checking` and
  `--account=checking` build two parallel copies of a statement and report both as clean
  imports.
- The fingerprint embeds the output of `normalise_description`, an explicitly evolving
  regex heuristic, with no version stamp. Any change to it silently re-imports every stored
  transaction.

Alongside those, the CLI became a write path while keeping preview-era argument handling: it
renders a preview and falls straight through into a commit in the same non-interactive
invocation, and it silently discards unrecognised flags — so `--dayfirst` (a missing hyphen)
is dropped and every row commits with an eleven-month date error.

This document specifies the corrective pass. It is a data-safety change, made before any real
statement is imported, and it deliberately rebuilds storage rather than patching it.

## The unavoidable ambiguity

This is the heart of the design and the reason several decisions below look conservative.

Given a fingerprint and a per-file occurrence number, **the system cannot distinguish a
genuine third identical charge from one it has already stored.** Consider an account with two
`2026-08-17 BLUE BOTTLE -4.50` charges already persisted as occurrences 1 and 2. A new file
arrives containing exactly one such charge. There are two irreconcilable readings:

1. The file is a **full-window snapshot** that overlaps August. The single charge is
   occurrence 1, already stored. Writing it again would duplicate real money.
2. The file is an **append-only incremental export** of new activity. The single charge is a
   genuine third coffee. Refusing it loses real money.

Both readings are consistent with the file's contents. No numbering scheme resolves this,
because the information required is not in the file — it is a fact about how the file was
produced. The original implementation chose reading 1 silently; an unconditional offset by
the maximum stored occurrence chooses reading 2 silently and duplicates every repeated
transaction whenever a full statement is re-imported.

Three things follow, and they are the spine of this design:

- **The mode is declared, never inferred.** The caller states `--mode=snapshot` or
  `--mode=incremental`. There is no default that guesses.
- **Incremental mode requires a stable bank transaction id.** Without one the ambiguity is
  provably unresolvable, so incremental mode refuses to run rather than pick a reading. The
  error names the two ways forward: supply the id column, or re-export a full window and use
  snapshot mode.
- **A byte-identical file is idempotent in either mode**, via a source checksum recorded on
  the import batch. This is the one case where "already imported" is a fact rather than an
  inference.

## Scope

**In scope:** canonical account identity; a reproducible, versioned fingerprint; external
transaction ids; import batches with source checksums; declared snapshot and incremental
semantics; argparse with a hard commit gate; ragged-row rejection and true physical line
numbers; JSONB, a non-materialising `count()`, and index cleanup; decoupling persistence from
the ingest layer; real PostgreSQL in CI.

**Out of scope, explicitly:** async LLM transport; importing real financial data; transaction
categorisation; the manual-entry UI itself (this design only makes room for it).

## Architecture

### Layering

The repository currently imports `offerdelta.ingest.commit` to accept an `ImportPlan`, which
couples persistence to the CSV pipeline. A manually entered transaction has no CSV, no
`ParsedRow`, and no source line, so it cannot reach storage without fabricating a fake
preview.

The corrected shape introduces a thin application service:

```
api / cli
    └── application/transactions/import_transactions.py   ← orchestration
            ├── ingest/            (CSV → TransactionRecord)   one input adapter
            └── infrastructure/postgres/                       persistence
```

`import_transactions` resolves the account, opens or reuses an import batch, converts input
into `TransactionRecord` values, calls the repository, and reports the result. CSV ingest
becomes one adapter feeding it; manual entry will become a second, reusing the same
orchestration minus the parsing.

**Rejected alternative:** extending the domain `Transaction` with an `UNCLASSIFIED` kind so
imports could store domain entities. `entities.py` refuses to construct a `SPENDING`
transaction without a category precisely because an uncategorised outflow vanishes from every
total. Loosening a real safety invariant to serve a layering convenience is the wrong trade.
Imported rows are therefore persistence records, not domain entities, until something
classifies them.

**Enforcement:** an import-linter contract forbidding `offerdelta.infrastructure` from
importing `offerdelta.ingest`. The existing `Layers` contract omits both packages and is
`exhaustive = false`, which is why the original coupling went uncaught. The new contract must
fail against the current `main` and pass after the change.

### The persistence record

```python
@dataclass(frozen=True)
class Provenance:
    """Where a stored transaction came from. Absent for manual entry."""
    source_file: str
    source_line: int          # physical line, from csv.reader.line_num
    raw_cells: dict[str, str]

@dataclass(frozen=True)
class TransactionRecord:
    account_id: uuid.UUID
    posted_on: date
    description: str
    normalised_merchant: str
    amount: Money             # already quantised to CURRENCY_DISPLAY
    external_id: str | None
    occurrence: int
    provenance: Provenance | None
```

The fingerprint is *not* a field on this record. It is derived by the repository from the
persisted values, which is what makes reproducibility structural rather than a convention
someone must remember.

## Schema

One Alembic revision, `down_revision = "a6128d6e4f20"`. The `transactions` table holds no real
data, so the revision drops and recreates it rather than backfilling. This avoids inventing
account rows for legacy strings and avoids a population of v0 fingerprints that can never be
reproduced.

**The migration refuses to destroy data.** Before dropping anything it counts the existing
rows, and aborts with a clear error if the table is not empty:

```python
count = bind.execute(sa.text("SELECT count(*) FROM transactions")).scalar_one()
if count:
    raise RuntimeError(
        f"transactions holds {count} row(s). This revision rebuilds the table and "
        f"cannot preserve them: stored fingerprints predate versioning and cannot be "
        f"recomputed. Back up and TRUNCATE deliberately, then re-run."
    )
```

A destructive migration that is *correct* under a stated assumption still has to fail loudly
when the assumption does not hold — the operator running it months from now will not remember
that it was written for an empty table. This abort is itself tested, on real PostgreSQL,
by seeding a row and asserting the upgrade refuses.

### `accounts`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | the stable identity everything else references |
| `key` | `varchar(100)` | canonical: casefolded, whitespace-collapsed. **unique** |
| `display_name` | `varchar(200)` | as the user typed it, preserved for display |
| `created_at` | `timestamptz` | |

Accounts are registered by an explicit command. An unknown `--account` on an import is
**refused**, with the known keys listed. Auto-creation was rejected: a typo would still create
a phantom account, which is the original bug relocated rather than fixed.

### `import_batches`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` FK → `accounts.id` | |
| `source_file` | `varchar(255)` | basename only — a directory may leak a username |
| `source_sha256` | `char(64)` | checksum of the file's bytes |
| `mode` | `varchar(16)` | `snapshot` or `incremental`, CHECK-constrained |
| `window_start` | `date` | declared range start. NOT NULL when mode is `snapshot` |
| `window_end` | `date` | declared range end. NOT NULL when mode is `snapshot` |
| `row_count` | `integer` | |
| `imported_at` | `timestamptz` | |

`CHECK (mode <> 'snapshot' OR (window_start IS NOT NULL AND window_end IS NOT NULL))` — a
snapshot without a declared window is not a snapshot, it is an assumption.
`CHECK (window_start IS NULL OR window_start <= window_end)`.

**Unique `(account_id, source_sha256)`.** Re-importing a byte-identical file into the same
account is a no-op that returns the existing batch and reports it as already imported. This
holds in both modes and is the only unambiguous form of "already imported".

### `transactions`

Recreated with:

| Column | Change |
|---|---|
| `account_id` | **new** — `uuid` FK → `accounts.id`, NOT NULL. Replaces the free-text `account` |
| `batch_id` | **new** — `uuid` FK → `import_batches.id`, nullable (manual entry has no batch) |
| `external_id` | **new** — `varchar(200)`, nullable. The bank's own transaction id |
| `fingerprint_version` | **new** — `smallint`, NOT NULL |
| `raw_cells` | `sa.JSON` → **`JSONB`** |
| `source_file`, `source_line`, `raw_cells` | now nullable together — manual entry has no provenance |

Constraints and indexes:

- `UNIQUE (account_id, fingerprint, occurrence)` — retained, still the concurrency guard.
- `UNIQUE (account_id, external_id) WHERE external_id IS NOT NULL` — partial index; the
  bank's id is authoritative when present.
- `CHECK (occurrence > 0)` — retained.
- `CHECK (source_line > 1)` — retained, and now *true*, because the line is physical.
- `INDEX (account_id, posted_on)` — retained, serves `recent()` and date-range queries.
- `ix_transactions_imported_at` — **dropped.** Nothing orders or filters by it.
- `ix_transactions_fingerprint` — **dropped.** Redundant with the unique constraint, whose
  leading column is the account, for the only fingerprint query in the codebase.

`JSONB` rather than `JSON` because PostgreSQL's `json` type has no equality operator: any
`DISTINCT`, `GROUP BY`, or `UNION` over `transactions` errors outright. `JSONB` also
normalises whitespace and duplicate keys and can be indexed, which is what a provenance
column is for. SQLite treats both as text, which is why 929 tests never surfaced it.

## The fingerprint

### Definition

Version 1 of the versioned scheme. Nothing survives the rebuild, so there are no earlier rows
to straddle; the pre-rebuild scheme was never versioned and its rows are dropped.

```
payload = "\x1f".join((
    str(account_id),               # canonical account, not free text
    posted_on.isoformat(),
    normalised_merchant,
    f"{quantised_amount:.2f}",     # fixed scale: -4.5 and -4.50 agree
    currency,
))
fingerprint = sha256(payload.encode("utf-8")).hexdigest()[:32]
```

`\x1f` (ASCII unit separator) remains the field delimiter, as in the current implementation,
so a merchant name containing the delimiter cannot forge a collision.

Two rules govern it:

1. **Every input is a persisted column.** `account_id`, `posted_on`, `normalised_merchant`,
   `amount`, and `currency` are all stored. Nothing derived-but-discarded enters the hash.
2. **The amount is quantised before hashing**, to `CURRENCY_DISPLAY` (2dp) — the same policy
   under which it is persisted — and formatted at fixed scale so `-4.5` and `-4.50` render
   identically. This is the specific defect that made stored fingerprints unreproducible.

### Reproducibility as a tested property

A test loads every stored row, recomputes the fingerprint from its persisted columns alone,
and asserts equality. "Reproducible from persisted fields" becomes something the suite proves
on every run rather than a claim in a docstring. This test is what will catch a future change
to `normalise_description` that forgets to bump the version.

### Why the version column exists

`normalised_merchant` is `normalise_description` output, driven by `_PROCESSOR_PREFIX`,
`_TRAILING_STORE`, `_TRAILING_DIGITS`, and `_EMBEDDED_DATE`. That heuristic is expected to
evolve — its own docstring says recurrence detection "depends entirely on collapsing those to
a stable key". Adding `VENMO` to the prefix list changes the fingerprint of every stored
transaction carrying it.

`fingerprint_version` makes that legible instead of silent. Rows written under a superseded
version are identifiable, can be recomputed deliberately, and dedupe can refuse to compare
across versions rather than reporting a false mismatch.

## Import semantics

### Dedupe precedence

1. **Batch checksum.** A batch with the same `(account_id, source_sha256)` exists → the whole
   import is a no-op. Reported as already imported, with the original batch's timestamp.
2. **External id**, when the mapping supplies one. `(account_id, external_id)` is
   authoritative; the fingerprint is still computed and stored for recurrence work, but plays
   no part in the decision.
3. **Fingerprint + occurrence**, otherwise, under the declared mode.

### Snapshot mode

The file is a complete window, and **the window is declared explicitly** on the command line:
`--from=YYYY-MM-DD --to=YYYY-MM-DD`. Both are required in snapshot mode.

Occurrence numbers are assigned per file, starting at 1 for each fingerprint — the current
behaviour, which is correct *for this mode*. Re-importing the same or an overlapping window is
idempotent, because a genuine third repeat appears in a full window alongside the first two
and is numbered 3.

The declared window is what makes "complete" a checkable claim rather than an assumption:

- **Every parsed row must fall inside it.** A row outside the declared window means the file
  is not what the caller said it is, so the import is refused and the offending lines are
  named. This catches the common mistake of exporting a wider or narrower range than intended.
- **The window is persisted on the batch** (`window_start`, `window_end`), so the set of
  fully-covered date ranges per account is a recorded fact rather than something inferred from
  the min and max of stored rows — which cannot distinguish "no transactions that week" from
  "that week was never imported".

This is the path Chase files take, since Chase exports carry no stable transaction id.

### Incremental mode

The file contains only activity not previously exported. **Refused without an `external_id`
column**, with an error that states the ambiguity and names both remedies. With an id column,
dedupe runs entirely on `(account_id, external_id)`; occurrence is assigned by offsetting from
the maximum stored for that `(account_id, fingerprint)` and exists only to satisfy the unique
constraint, since the id is doing the real work.

### Tested separately

Snapshot and incremental each get their own test module asserting their own guarantees. The
key cases:

- snapshot: missing `--from`/`--to` → refused
- snapshot: a row dated outside the declared window → refused, offending lines named
- snapshot: re-import of an identical file → no new rows
- snapshot: re-import of an overlapping window → no new rows
- snapshot: a later full window containing a genuine third repeat → exactly one new row
- incremental without an id column → refused, with the explanatory error
- incremental with ids: a repeat charge on an already-covered date → stored, not swallowed
- incremental with ids: a re-sent row with a known id → skipped
- either mode: byte-identical file → batch-level no-op

## Ingest corrections

### Ragged rows

`csv.DictReader` puts surplus fields under the key `None` and missing fields under the value
`None`. Both violate the `dict[str, str]` contract and corrupt the JSON round trip — a `None`
key serialises to the string `"null"` with a list value.

Explicit `restkey` and `restval` sentinels are supplied, and any row carrying either becomes a
`RowError` rather than a `ParsedRow`. The preview already refuses to commit when errors exist
and asserts that parsed plus failed equals the source row count, so a ragged row is reported
with its line and reason, never dropped and never half-stored.

### Physical line numbers

`preview_csv` currently does `rows = list(reader)` and then `enumerate(rows, start=2)`, which
counts CSV *records*. One quoted embedded newline — common in memo columns — desynchronises
every subsequent number from the file. That was a cosmetic flaw in an error message; it is now
a durable provenance column carrying a CHECK constraint that asserts it is a file position.

Iteration is restructured to capture `reader.line_num` per record. A test with a quoted
embedded newline asserts the stored line points at the record's real physical position.

## CLI

`argparse`, replacing the hand-rolled `argv` scanning that silently discarded anything it did
not recognise.

The script is renamed `preview_import.py` → **`transactions.py`**. It is no longer only a
preview tool, and `import.py` — the obvious name — is not importable, since `import` is a
Python keyword and the tests reach `main` by importing it.

```
transactions.py preview  <file> [--map=…] [--dates=day-first|month-first]
transactions.py commit   <file> --account=<key>
                               --mode=snapshot --from=YYYY-MM-DD --to=YYYY-MM-DD
                               [--map=…] [--dates=…] --yes
transactions.py commit   <file> --account=<key>
                               --mode=incremental        # requires an id column
                               [--map=…] [--dates=…] --yes
transactions.py accounts add <display name>
transactions.py accounts list
```

Two properties matter:

- **Unknown or malformed arguments are a hard error.** `argparse` rejects them by default;
  the point is to stop hand-parsing, so no `parse_known_args` anywhere.
- **Preview never falls through into a write.** `commit` is a separate subcommand that does
  not render a preview. It prints a short summary — account, mode, row count, what will be
  skipped — and then requires `--yes`, or an interactive confirmation when stdin is a TTY. The
  previous design printed 10 of 400 rows and committed in the same breath, which made the
  preview decorative.

## Repository

- `import_plan(plan: ImportPlan)` is replaced by `add_many(records, *, batch)`, taking
  `TransactionRecord` values. The import of `offerdelta.ingest.commit` is deleted.
- `count()` becomes `select(func.count()).select_from(TransactionRow)` instead of
  `len(scalars(...).all())`, which currently materialises one UUID per transaction — on the
  one table in the schema that grows without bound — to produce an integer.
- `recent()` is **deleted.** It has zero callers across source, unit tests, and integration
  tests, and it tie-breaks on a `uuid4`, so same-day charges come back in arbitrary order.
  Keeping dead code alive and fixing its ordering speculatively is the wrong half of YAGNI;
  the read path can be rebuilt against a real caller's requirements when one exists.

## Testing

### Real PostgreSQL

Integration tests currently skip whenever `CONNECTION_STRING` is unset, and CI has no secret —
so they skip in CI always. The SQLite unit fixture proves less than it appears: `Numeric(18,2)`
round-trips through a float there, so an exactness assertion passes for any 2dp value.

- **CI gains a `postgres:16` service container** and sets `CONNECTION_STRING` to it.
  Integration and migration tests run on real PostgreSQL on every push, with no secret. The
  skip remains for contributors without a database.
- **Migration tests use a scratch schema**: `CREATE SCHEMA mig_test_<uuid>`, point
  `search_path` at it, run `alembic upgrade head`, assert the schema, run `downgrade`, then
  `DROP SCHEMA … CASCADE`. The same code path works against the CI container and against Neon
  locally without touching real tables — which matters, because the existing conftest
  deliberately targets a live Neon database and a test that litters is a test nobody runs
  twice.

### Coverage the review found missing

Untested branches that must gain tests: `plan_import`'s unresolved-mapping and empty-rows
paths; the `IntegrityError → ValidationError` conversion; and the full `commit` success path
including the already-stored summary line.

## Documentation

- The snapshot/incremental ambiguity is recorded in this spec, restated in the `commit` module
  docstring, and summarised in the README's import section.
- `docs/status/2026-08-21.md` is corrected in the same change. It currently claims transaction
  persistence "has not been committed or deployed" — a sentence that shipped inside the commit
  that committed it and is now on `main`. It gains a note describing this pass.

## Success criteria

1. `--account=Checking` and `--account=checking` cannot produce two accounts; an unregistered
   account is refused.
2. `-4.50` and `-4.5` produce the same fingerprint, and every stored fingerprint recomputes
   from its own persisted columns — asserted for every row by a test.
3. `fingerprint_version` is stored on every row.
4. A byte-identical re-import is a no-op in both modes.
5. Snapshot and incremental each have their own tests asserting their own guarantees;
   incremental without an id column is refused.
6. Snapshot mode requires `--from`/`--to`, persists the window on the batch, and refuses a
   file containing any row dated outside it.
7. The migration aborts with a clear error when `transactions` is non-empty, proven by a test
   on real PostgreSQL that seeds a row and asserts the refusal.
8. Unknown CLI arguments are an error; `commit` writes nothing without `--yes` or an
   interactive confirmation, and never renders a preview that falls through to a write.
9. A ragged row is a `RowError`; a quoted embedded newline does not desynchronise
   `source_line`.
10. `raw_cells` is `JSONB`; `count()` issues a `COUNT`; the two redundant indexes are gone.
11. An import-linter contract forbids `infrastructure → ingest`, demonstrated red-green: it
    fails against the current violation and passes only after the boundary is corrected.
12. Migration and integration tests run against real PostgreSQL in CI.
13. `make check` is green.
