"""Previewing an import before committing to it.

Nothing here writes anything. The point is to show what *would* be imported so a
person can look at it first, because the cheapest moment to catch a
day-first/month-first mistake is before four hundred rows carry it.

Three rules govern the output.

**Nothing is silently dropped.** Every source row becomes either a parsed row or
a row error, and the preview asserts that the two counts add up to the file's
length. A row that fails validation is reported with its line number and reason,
never skipped.

**Originals are preserved.** Each parsed row keeps the raw cell values it came
from, so any figure can be traced back to the text that produced it without
re-reading the file.

**Duplicates are reported, not removed.** Two identical coffees on one day are a
legitimate pair, and an importer that silently deduplicated them would delete
real money. The preview groups rows sharing a fingerprint and leaves the
decision to a person.
"""

from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.transactions.parsing import normalise_description, parse_amount
from offerdelta.ingest.dates import DateOrder, detect_date_order, parse_date
from offerdelta.ingest.mapping import ColumnMapping, MappingDetection, detect_mapping

#: How many values per column to sample when detecting the mapping and the date
#: order. Enough to find a decisive day-above-twelve without reading a whole
#: year of statements.
SAMPLE_SIZE: Final = 200


@dataclass(frozen=True)
class ParsedRow:
    """One source row, normalised, with its original still attached."""

    line: int
    posted_on: date
    description: str
    normalised_merchant: str

    #: Signed: negative is money out, whichever shape the file used.
    amount: Money

    #: Every original cell, so a figure can be traced to the text behind it.
    raw: dict[str, str]

    @property
    def fingerprint(self) -> str:
        """A stable identity for duplicate detection.

        Built from the normalised content rather than the row's position, so
        re-importing the same file — or the same transaction from an overlapping
        date range — produces the same value. Deliberately does not include the
        line number: a duplicate that moved position is still a duplicate.
        """
        payload = "\x1f".join(
            (
                self.posted_on.isoformat(),
                self.normalised_merchant,
                str(self.amount.amount),
                self.amount.currency,
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class RowError:
    """A source row that could not be parsed, and why."""

    line: int
    reason: str
    raw: dict[str, str]


@dataclass(frozen=True)
class ImportPreview:
    """What an import would produce, before anything is written."""

    path: str
    headers: tuple[str, ...]
    detection: MappingDetection
    mapping: ColumnMapping | None
    date_order: DateOrder
    total_rows: int
    rows: tuple[ParsedRow, ...] = ()
    errors: tuple[RowError, ...] = ()

    def __post_init__(self) -> None:
        if self.mapping is not None and len(self.rows) + len(self.errors) != self.total_rows:
            raise ValidationError(
                f"{len(self.rows)} parsed and {len(self.errors)} failed do not "
                f"account for {self.total_rows} source rows; an importer that "
                f"loses rows cannot be trusted with money"
            )

    @property
    def importable(self) -> bool:
        return self.mapping is not None and bool(self.rows)

    @property
    def duplicate_groups(self) -> list[tuple[str, list[ParsedRow]]]:
        """Rows sharing a fingerprint, reported rather than removed.

        Two identical coffees on one day are a real pair. Silently collapsing
        them would delete money that was actually spent.
        """
        grouped: dict[str, list[ParsedRow]] = defaultdict(list)
        for row in self.rows:
            grouped[row.fingerprint].append(row)
        return sorted(
            ((key, rows) for key, rows in grouped.items() if len(rows) > 1),
            key=lambda item: item[1][0].line,
        )

    def render(self, limit: int = 10) -> str:
        lines = [f"{self.path}: {self.total_rows} rows", ""]
        lines.append(self.detection.render())
        lines.append(f"  date order   {self.date_order}")

        if self.date_order is DateOrder.AMBIGUOUS and self.mapping is not None:
            lines.append(
                "  the date column reads both ways; state the order explicitly "
                "rather than risk an eleven-month error"
            )

        if self.mapping is None:
            lines.append("\nnothing parsed: supply an explicit mapping")
            return "\n".join(lines)

        lines.append("")
        lines.append(f"parsed {len(self.rows)}, failed {len(self.errors)}")

        if self.rows:
            lines.append("")
            lines.append(f"  {'line':>5}  {'date':<12}{'merchant':<26}{'amount':>12}")
            for row in self.rows[:limit]:
                lines.append(
                    f"  {row.line:>5}  {row.posted_on.isoformat():<12}"
                    f"{row.normalised_merchant[:24]:<26}{row.amount.amount:>12}"
                )
            if len(self.rows) > limit:
                lines.append(f"  ... {len(self.rows) - limit} more")

        if self.errors:
            lines.append("")
            lines.append(f"errors ({len(self.errors)})")
            for error in self.errors[:limit]:
                lines.append(f"  line {error.line}: {error.reason}")
            if len(self.errors) > limit:
                lines.append(f"  ... {len(self.errors) - limit} more")

        if groups := self.duplicate_groups:
            lines.append("")
            lines.append(f"possible duplicates ({len(groups)} groups)")
            for _, rows in groups[:limit]:
                positions = ", ".join(str(row.line) for row in rows)
                lines.append(
                    f"  lines {positions}: {rows[0].normalised_merchant} "
                    f"{rows[0].amount.amount} on {rows[0].posted_on}"
                )
            lines.append(
                "  reported, not removed - two identical charges on one day can both be real"
            )

        return "\n".join(lines)


def preview_csv(
    path: Path | str,
    *,
    mapping: ColumnMapping | None = None,
    date_order: DateOrder | None = None,
) -> ImportPreview:
    """Parse a file and report what an import would produce.

    Writes nothing. Supply `mapping` when detection is uncertain, and
    `date_order` when the column reads both ways.
    """
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"no file at {path}")

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = tuple(reader.fieldnames or ())
        rows = list(reader)

    if not headers:
        raise ValidationError(f"{path.name} has no header row")

    sample = {header: [(row.get(header) or "") for row in rows[:SAMPLE_SIZE]] for header in headers}

    detection = detect_mapping(list(headers), sample)
    resolved = mapping or detection.mapping

    if resolved is None:
        return ImportPreview(
            path=str(path),
            headers=headers,
            detection=detection,
            mapping=None,
            date_order=DateOrder.AMBIGUOUS,
            total_rows=len(rows),
        )

    missing = [c for c in resolved.source_columns() if c not in headers]
    if missing:
        raise ValidationError(
            f"the mapping names columns that are not in {path.name}: {', '.join(missing)}"
        )

    order = date_order or detect_date_order(sample.get(resolved.date, []))

    parsed: list[ParsedRow] = []
    errors: list[RowError] = []
    for line, row in enumerate(rows, start=2):
        try:
            parsed.append(_to_row(row, line, resolved, order))
        except ValidationError as error:
            errors.append(RowError(line=line, reason=str(error), raw=dict(row)))

    return ImportPreview(
        path=str(path),
        headers=headers,
        detection=detection,
        mapping=resolved,
        date_order=order,
        total_rows=len(rows),
        rows=tuple(parsed),
        errors=tuple(errors),
    )


def _to_row(row: dict[str, str], line: int, mapping: ColumnMapping, order: DateOrder) -> ParsedRow:
    posted_on = parse_date(row.get(mapping.date) or "", order)

    description = (row.get(mapping.description) or "").strip()
    if not description and mapping.merchant:
        description = (row.get(mapping.merchant) or "").strip()
    if not description:
        raise ValidationError("description is blank")

    amount = _amount(row, mapping)

    return ParsedRow(
        line=line,
        posted_on=posted_on,
        description=description,
        normalised_merchant=normalise_description(description),
        amount=amount,
        raw=dict(row),
    )


def _amount(row: dict[str, str], mapping: ColumnMapping) -> Money:
    """Normalise either shape to one signed amount.

    A signed `amount` column is taken as-is. Split `debit`/`credit` columns hold
    magnitudes, so the debit is negated — the convention everywhere else in this
    codebase is that money out is negative.
    """
    if mapping.amount:
        return parse_amount((row.get(mapping.amount) or "").strip())

    debit_text = (row.get(mapping.debit) or "").strip() if mapping.debit else ""
    credit_text = (row.get(mapping.credit) or "").strip() if mapping.credit else ""

    if debit_text and credit_text:
        raise ValidationError(
            "both debit and credit are filled in; one row cannot be money out and money in at once"
        )
    if not debit_text and not credit_text:
        raise ValidationError("neither debit nor credit is filled in")

    if debit_text:
        magnitude = parse_amount(debit_text)
        # Some exports already sign the debit column. Negating a negative would
        # turn a payment into income.
        return magnitude if magnitude.amount < 0 else -magnitude
    return parse_amount(credit_text)
