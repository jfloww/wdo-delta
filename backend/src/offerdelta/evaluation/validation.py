"""Validating an annotation file before it is trusted.

The loader raises on the first broken row, which is the wrong shape for fixing a
file with thirty typos in it. This reports everything at once, split into errors
that make the dataset unusable and warnings that make it weaker.

It runs in two passes for that reason. The first reads the CSV as raw text and
checks every row independently, so a misspelled label on line 40 does not hide
the four on lines 41 to 44. Only when that pass is clean does it build the
dataset and run the checks that need the whole thing in view.

The checks are chosen for what actually goes wrong when a person labels four
hundred rows by hand:

- **A misspelled label.** By far the most common, and invisible until a class
  with one member appears in the report.
- **A disagreement nobody adjudicated.** Annotator A said groceries, B said
  dining, `final_label` is blank. The dataset silently adopts A and the
  disagreement never surfaces. The most damaging error here, because nothing
  downstream can detect it.
- **Ambiguity authored without an explanation**, which cannot be reviewed later.
- **Too few double annotations**, which makes kappa too noisy to quote.
- **A class with almost no support**, whose F1 swings on a single correction.
- **A merchant that dominates**, which makes a disjoint split lopsided.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.transactions.parsing import parse_amount
from offerdelta.evaluation.csv_loader import (
    LABEL_SEPARATOR,
    REQUIRED_COLUMNS,
    load_labelled_csv,
)
from offerdelta.evaluation.dataset import LabelledDataset
from offerdelta.evaluation.labels import LABEL_SPACE

#: Below this, Cohen's kappa is computed on too little to mean anything.
MIN_DOUBLE_ANNOTATED: Final = 50

#: A class with fewer rows than this has an F1 that moves several points on a
#: single correction.
MIN_CLASS_SUPPORT: Final = 5

#: One merchant owning more than this share makes a merchant-disjoint split
#: lopsided whichever side it lands on.
MAX_MERCHANT_SHARE: Final = 0.15

#: Fewer distinct merchants than this and a disjoint split cannot leave both
#: sides representative.
MIN_MERCHANTS: Final = 10

#: Above this share of ambiguous rows, the benchmark is measurably more
#: forgiving than a strict one and the write-up has to say so.
MAX_AMBIGUOUS_SHARE: Final = 0.2

#: How many offending ids to name before summarising the rest.
MAX_LISTED_IDS: Final = 10


@dataclass
class ValidationReport:
    """What is wrong with an annotation file, and how wrong."""

    path: str
    rows: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return not self.errors

    def render(self) -> str:
        lines = [f"{self.path}: {self.rows} rows"]
        for heading, items in (
            ("ERRORS", self.errors),
            ("WARNINGS", self.warnings),
            ("NOTES", self.notes),
        ):
            if items:
                lines.append(f"\n{heading} ({len(items)})")
                lines.extend(f"  - {item}" for item in items)
        if self.usable and not self.warnings:
            lines.append("\nready to use")
        elif self.usable:
            lines.append("\nusable, with warnings above")
        else:
            lines.append("\nNOT usable until the errors are fixed")
        return "\n".join(lines)


def validate_labelled_csv(
    path: Path | str, *, dataset_version: str = "validation"
) -> ValidationReport:
    """Check an annotation file and report everything wrong with it at once."""
    report = ValidationReport(path=str(path))
    rows = _read_raw(path, report)
    if rows is None:
        return report

    report.rows = len(rows)
    _check_rows(rows, report)
    if not report.usable:
        # The whole-dataset checks need well-formed rows; running them on
        # broken input would bury the real errors under consequences of them.
        return report

    dataset = load_labelled_csv(path, dataset_version=dataset_version)
    _check_adjudication(dataset, report)
    _check_ambiguity(dataset, report)
    _check_double_annotation(dataset, report)
    _check_distribution(dataset, report)
    _check_merchants(dataset, report)
    return report


def _read_raw(path: Path | str, report: ValidationReport) -> list[dict[str, str]] | None:
    path = Path(path)
    if not path.exists():
        report.errors.append(f"no annotation file at {path}")
        return None

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        missing = [column for column in REQUIRED_COLUMNS if column not in headers]
        if missing:
            report.errors.append(f"missing required columns: {', '.join(missing)}")
            return None
        rows = list(reader)

    if not rows:
        report.errors.append("the file contains no rows")
        return None
    return rows


def _check_rows(rows: list[dict[str, str]], report: ValidationReport) -> None:
    """Per-row checks, all of them, so one bad line does not mask the next."""
    seen: set[str] = set()
    for line, row in enumerate(rows, start=2):
        _check_identity(row, line, seen, report)
        _check_row_labels(row, line, report)
        _check_row_ambiguity(row, line, report)


def _check_identity(
    row: dict[str, str], line: int, seen: set[str], report: ValidationReport
) -> None:
    txn_id = (row.get("transaction_id") or "").strip()
    if not txn_id:
        report.errors.append(f"line {line}: transaction_id is blank")
    elif txn_id in seen:
        report.errors.append(f"line {line}: duplicate transaction_id {txn_id!r}")
    else:
        seen.add(txn_id)

    if not (row.get("description") or "").strip():
        report.errors.append(f"line {line}: description is blank")

    raw_amount = (row.get("amount") or "").strip()
    try:
        parse_amount(raw_amount)
    except ValidationError:
        report.errors.append(f"line {line}: amount {raw_amount!r} could not be parsed")


def _check_row_labels(row: dict[str, str], line: int, report: ValidationReport) -> None:
    """Catch misspellings before they become a class with one member."""
    if not (row.get("annotator_a_label") or "").strip():
        report.errors.append(f"line {line}: annotator_a_label is blank")

    for column in ("annotator_a_label", "annotator_b_label", "final_label"):
        label = (row.get(column) or "").strip()
        if label and label not in LABEL_SPACE:
            report.errors.append(f"line {line}: {column} {label!r} is not a valid label")


def _check_row_ambiguity(row: dict[str, str], line: int, report: ValidationReport) -> None:
    acceptable = {
        part.strip()
        for part in (row.get("acceptable_labels") or "").split(LABEL_SEPARATOR)
        if part.strip()
    }
    if not acceptable:
        return

    for label in sorted(acceptable):
        if label not in LABEL_SPACE:
            report.errors.append(f"line {line}: acceptable label {label!r} is not a valid label")

    if not (row.get("ambiguity_note") or "").strip():
        report.errors.append(
            f"line {line}: acceptable_labels was authored without an "
            f"ambiguity_note; an ambiguity claim nobody explained cannot be "
            f"reviewed later"
        )

    if len(acceptable) == 1:
        report.errors.append(
            f"line {line}: acceptable_labels lists one label, which is just a "
            f"labelled row with an extra claim attached; use two or more, or "
            f"leave it blank"
        )

    primary = (row.get("annotator_a_label") or "").strip()
    gold = (row.get("final_label") or "").strip() or primary
    if gold and gold not in acceptable:
        report.errors.append(
            f"line {line}: the gold label {gold!r} is not in its own acceptable "
            f"set, so the row would score its own answer wrong"
        )


def _check_adjudication(dataset: LabelledDataset, report: ValidationReport) -> None:
    """The damaging one: a disagreement nobody resolved.

    Left alone, the dataset quietly adopts annotator A's label as gold and the
    disagreement disappears. Nothing downstream can detect it, which is why it
    is an error rather than a warning.
    """
    unresolved = [
        record.transaction_id
        for record in dataset.records
        if record.annotators_agree is False and record.adjudicated_label is None
    ]
    if unresolved:
        shown = ", ".join(unresolved[:MAX_LISTED_IDS])
        more = (
            ""
            if len(unresolved) <= MAX_LISTED_IDS
            else f" (and {len(unresolved) - MAX_LISTED_IDS} more)"
        )
        report.errors.append(
            f"{len(unresolved)} rows where the annotators disagree and final_label "
            f"is blank: {shown}{more}. Without adjudication the dataset silently "
            f"adopts annotator A and the disagreement vanishes."
        )

    changed = [
        record.transaction_id
        for record in dataset.records
        if record.annotators_agree is True
        and record.adjudicated_label is not None
        and record.adjudicated_label != record.primary_label
    ]
    if changed:
        report.warnings.append(
            f"{len(changed)} rows where both annotators agreed but final_label "
            f"differs from both: {', '.join(changed[:5])}. Legitimate if a "
            f"reviewer overruled them, worth confirming."
        )


def _check_ambiguity(dataset: LabelledDataset, report: ValidationReport) -> None:
    count = len(dataset.ambiguous)
    if not count:
        return

    share = count / len(dataset)
    report.notes.append(f"{count} rows ({share:.0%}) marked ambiguous and reported separately")
    if share > MAX_AMBIGUOUS_SHARE:
        report.warnings.append(
            f"{share:.0%} of rows are ambiguous. A benchmark where one row in five "
            f"accepts several answers reports a higher score than a stricter one, "
            f"so the share belongs in the write-up."
        )


def _check_double_annotation(dataset: LabelledDataset, report: ValidationReport) -> None:
    double = dataset.double_annotated
    report.notes.append(f"{len(double)} rows independently double-annotated")

    if not double:
        report.errors.append(
            "no rows carry annotator_b_label, so inter-annotator agreement and "
            "Cohen's kappa cannot be computed at all"
        )
        return

    if len(double) < MIN_DOUBLE_ANNOTATED:
        report.warnings.append(
            f"only {len(double)} rows are double-annotated; below about "
            f"{MIN_DOUBLE_ANNOTATED} the kappa estimate is too noisy to quote"
        )

    agreed = sum(1 for record in double if record.annotators_agree)
    report.notes.append(
        f"raw annotator agreement {agreed}/{len(double)} ({agreed / len(double):.0%})"
    )

    covered = {record.gold_label for record in double}
    overall = {record.gold_label for record in dataset.records}
    if len(covered) < len(overall) / 2:
        report.warnings.append(
            f"the double-annotated subset covers {len(covered)} of {len(overall)} "
            f"labels; kappa will describe agreement on that subset rather than on "
            f"the dataset"
        )


def _check_distribution(dataset: LabelledDataset, report: ValidationReport) -> None:
    counts = Counter(record.gold_label for record in dataset.records)
    thin = sorted((label, n) for label, n in counts.items() if n < MIN_CLASS_SUPPORT)
    if thin:
        listed = ", ".join(f"{label} ({n})" for label, n in thin[:8])
        report.warnings.append(
            f"{len(thin)} classes have fewer than {MIN_CLASS_SUPPORT} rows: "
            f"{listed}. Macro F1 weights these equally with the large classes, so "
            f"one correction moves the headline number several points."
        )
    report.notes.append(f"{len(counts)} distinct labels used")


def _check_merchants(dataset: LabelledDataset, report: ValidationReport) -> None:
    counts = Counter(record.normalised_merchant for record in dataset.records)
    report.notes.append(f"{len(counts)} distinct normalised merchants")

    if len(counts) < MIN_MERCHANTS:
        report.warnings.append(
            f"only {len(counts)} distinct merchants; a merchant-disjoint split "
            f"needs enough of them to leave both sides representative"
        )

    for merchant, n in counts.most_common(3):
        share = n / len(dataset)
        if share > MAX_MERCHANT_SHARE:
            report.warnings.append(
                f"merchant {merchant!r} accounts for {share:.0%} of rows; a "
                f"disjoint split will be lopsided whichever side it lands on"
            )
