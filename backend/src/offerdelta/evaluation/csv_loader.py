"""Loading the labelled dataset from CSV.

The annotation file is the one artefact a human maintains by hand, so the
columns are exactly the eight that annotation actually produces. Everything
else the schema wants is either derived or defaulted:

- `normalised_merchant` is derived from the description with the same
  normaliser the categorisers use. Deriving it rather than asking for it means
  the split key can never drift from the key the systems see, and an annotator
  never has to think about it.
- `posted_on`, `account_type`, `source` and `bank_format` are read when present
  and defaulted when not, so a minimal file works and a richer one is not
  rejected.

**Ambiguity is explicit.** A row is ambiguous exactly when `acceptable_labels`
is non-empty — authored during adjudication, never inferred from the annotators
disagreeing. That distinction matters: disagreement usually means one annotator
was wrong, and treating every disagreement as legitimate ambiguity would
quietly inflate every system's score.

`final_label` carries the adjudicated answer. Left blank, annotator A's label
stands as gold.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.transactions.parsing import normalise_description, parse_amount
from offerdelta.evaluation.dataset import LabelledDataset, LabelledTransaction

#: Exactly what annotation produces. A missing one is an error, because a
#: silently absent column would read as a column of blanks.
REQUIRED_COLUMNS: Final = (
    "transaction_id",
    "description",
    "amount",
    "annotator_a_label",
    "annotator_b_label",
    "final_label",
    "acceptable_labels",
    "ambiguity_note",
)

#: Recognised when present, defaulted when not.
OPTIONAL_COLUMNS: Final = ("posted_on", "account_type", "source", "bank_format")

#: Separator inside the acceptable_labels cell. A comma would need quoting in a
#: CSV, and a quoted comma inside a quoted field is where hand-edited files go
#: wrong.
LABEL_SEPARATOR: Final = "|"

_DEFAULT_DATE: Final = date(1970, 1, 1)


def _split_labels(cell: str) -> frozenset[str]:
    if not cell or not cell.strip():
        return frozenset()
    return frozenset(part.strip() for part in cell.split(LABEL_SEPARATOR) if part.strip())


def _blank_to_none(cell: str | None) -> str | None:
    if cell is None:
        return None
    stripped = cell.strip()
    return stripped or None


def load_labelled_csv(path: Path | str, *, dataset_version: str) -> LabelledDataset:
    """Read an annotation file into a frozen dataset.

    Raises on the first structurally broken row. Use `validate_labelled_csv`
    first for a full report — one exception at a time is a poor way to fix a
    file with thirty typos in it.
    """
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"no annotation file at {path}")

    # utf-8-sig: spreadsheets routinely write a BOM, and an unstripped one
    # turns the first header into "﻿transaction_id".
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            raise ValidationError(f"{path.name} is missing required columns: {', '.join(missing)}")
        rows = list(reader)

    if not rows:
        raise ValidationError(f"{path.name} contains no rows")

    records = tuple(_to_record(row, index) for index, row in enumerate(rows, start=2))
    return LabelledDataset(dataset_version=dataset_version, records=records)


def _to_record(row: dict[str, str], line: int) -> LabelledTransaction:
    transaction_id = (row.get("transaction_id") or "").strip()
    if not transaction_id:
        raise ValidationError(f"line {line}: transaction_id is blank")

    description = (row.get("description") or "").strip()
    if not description:
        raise ValidationError(f"line {line}: description is blank")

    raw_amount = (row.get("amount") or "").strip()
    try:
        amount = parse_amount(raw_amount)
    except ValidationError as error:
        raise ValidationError(f"line {line}: {error}") from error

    primary = _blank_to_none(row.get("annotator_a_label"))
    if primary is None:
        raise ValidationError(f"line {line}: annotator_a_label is blank")

    acceptable = _split_labels(row.get("acceptable_labels", ""))
    note = _blank_to_none(row.get("ambiguity_note"))

    # Ambiguity is exactly the presence of an authored acceptable set — never
    # inferred from the annotators having disagreed.
    ambiguous = bool(acceptable)
    if ambiguous and note is None:
        raise ValidationError(
            f"line {line}: acceptable_labels was authored without an "
            f"ambiguity_note; an ambiguity claim nobody explained cannot be "
            f"reviewed later"
        )

    posted = _blank_to_none(row.get("posted_on"))
    try:
        posted_on = date.fromisoformat(posted) if posted else _DEFAULT_DATE
    except ValueError as error:
        raise ValidationError(f"line {line}: posted_on {posted!r} is not an ISO date") from error

    return LabelledTransaction(
        transaction_id=transaction_id,
        posted_on=posted_on,
        raw_description=description,
        # Derived with the same normaliser the categorisers use, so the split
        # key can never drift from the key the systems actually see.
        normalised_merchant=normalise_description(description),
        amount=amount,
        account_type=_blank_to_none(row.get("account_type")) or "unknown",
        source=_blank_to_none(row.get("source")) or "manual_annotation",
        bank_format=_blank_to_none(row.get("bank_format")) or "unspecified",
        primary_label=primary,
        secondary_label=_blank_to_none(row.get("annotator_b_label")),
        adjudicated_label=_blank_to_none(row.get("final_label")),
        acceptable_labels=acceptable,
        ambiguous=ambiguous,
        notes=note,
    )
