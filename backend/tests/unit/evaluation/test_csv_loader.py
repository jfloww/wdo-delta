"""Loading and validating the annotation CSV.

The file a human maintains by hand, so the tests are about the ways hand-made
files go wrong: a BOM from a spreadsheet, a misspelled label, a disagreement
nobody adjudicated, an ambiguity claim with no explanation.
"""

from pathlib import Path

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.evaluation.csv_loader import REQUIRED_COLUMNS, load_labelled_csv
from offerdelta.evaluation.validation import validate_labelled_csv

GROCERY = "LIVING_GROCERY"
DINING = "LIVING_DINING"
OTHER = "LIVING_OTHER"

HEADER = ",".join(REQUIRED_COLUMNS)


def _write(tmp_path: Path, *rows: str, header: str = HEADER, bom: bool = False) -> Path:
    path = tmp_path / "transactions.csv"
    text = "\n".join([header, *rows]) + "\n"
    path.write_text(("﻿" if bom else "") + text, encoding="utf-8")
    return path


def _row(
    txn_id: str = "t1",
    description: str = "SQ *BLUE BOTTLE 4412",
    amount: str = "-4.50",
    *,
    a: str = DINING,
    b: str = "",
    final: str = "",
    acceptable: str = "",
    note: str = "",
) -> str:
    return ",".join([txn_id, description, amount, a, b, final, acceptable, note])


# --- Loading ---------------------------------------------------------------


def test_a_minimal_file_loads(tmp_path: Path) -> None:
    ds = load_labelled_csv(_write(tmp_path, _row()), dataset_version="v1")
    assert len(ds) == 1
    assert ds.records[0].primary_label == DINING


def test_the_merchant_is_derived_from_the_description(tmp_path: Path) -> None:
    # Derived rather than asked for, so the split key cannot drift from the key
    # the categorisers actually see — and the annotator never thinks about it.
    ds = load_labelled_csv(_write(tmp_path, _row()), dataset_version="v1")
    assert ds.records[0].normalised_merchant == "BLUE BOTTLE"


def test_amounts_are_parsed_into_money(tmp_path: Path) -> None:
    ds = load_labelled_csv(_write(tmp_path, _row(amount='"$1,234.56"')), dataset_version="v1")
    assert ds.records[0].amount == Money.parse("1234.56")


def test_a_spreadsheet_byte_order_mark_is_tolerated(tmp_path: Path) -> None:
    # Excel writes one by default, and an unstripped BOM turns the first header
    # into something that matches nothing.
    ds = load_labelled_csv(_write(tmp_path, _row(), bom=True), dataset_version="v1")
    assert len(ds) == 1


def test_a_second_annotation_is_read(tmp_path: Path) -> None:
    ds = load_labelled_csv(_write(tmp_path, _row(b=GROCERY, final=GROCERY)), dataset_version="v1")
    record = ds.records[0]
    assert record.secondary_label == GROCERY
    assert record.has_second_annotation is True


def test_a_blank_second_annotation_stays_absent(tmp_path: Path) -> None:
    ds = load_labelled_csv(_write(tmp_path, _row(b="")), dataset_version="v1")
    assert ds.records[0].secondary_label is None


def test_the_final_label_becomes_the_gold_label(tmp_path: Path) -> None:
    ds = load_labelled_csv(
        _write(tmp_path, _row(a=DINING, b=GROCERY, final=GROCERY)), dataset_version="v1"
    )
    assert ds.records[0].gold_label == GROCERY


def test_without_a_final_label_annotator_a_stands(tmp_path: Path) -> None:
    ds = load_labelled_csv(_write(tmp_path, _row(a=DINING)), dataset_version="v1")
    assert ds.records[0].gold_label == DINING


def test_acceptable_labels_are_pipe_separated(tmp_path: Path) -> None:
    ds = load_labelled_csv(
        _write(
            tmp_path,
            _row(
                description="AMAZON",
                a=GROCERY,
                acceptable=f"{GROCERY}|{OTHER}",
                note="basket contents unknown",
            ),
        ),
        dataset_version="v1",
    )
    assert ds.records[0].acceptable_labels == frozenset({GROCERY, OTHER})


def test_authoring_an_acceptable_set_marks_the_row_ambiguous(tmp_path: Path) -> None:
    # Ambiguity is exactly the presence of an authored set — never inferred
    # from the annotators disagreeing.
    ds = load_labelled_csv(
        _write(
            tmp_path,
            _row(
                description="AMAZON",
                a=GROCERY,
                acceptable=f"{GROCERY}|{OTHER}",
                note="basket contents unknown",
            ),
        ),
        dataset_version="v1",
    )
    assert ds.records[0].ambiguous is True


def test_a_disagreement_alone_does_not_mark_a_row_ambiguous(tmp_path: Path) -> None:
    # The rule that keeps scores honest: disagreement usually means one
    # annotator was wrong, not that the row has two right answers.
    ds = load_labelled_csv(
        _write(tmp_path, _row(a=DINING, b=GROCERY, final=GROCERY)), dataset_version="v1"
    )
    assert ds.records[0].ambiguous is False


def test_an_acceptable_set_without_a_note_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="ambiguity_note"):
        load_labelled_csv(
            _write(tmp_path, _row(a=GROCERY, acceptable=f"{GROCERY}|{OTHER}")),
            dataset_version="v1",
        )


def test_an_optional_date_column_is_used_when_present(tmp_path: Path) -> None:
    header = HEADER + ",posted_on"
    ds = load_labelled_csv(
        _write(tmp_path, _row() + ",2026-08-17", header=header), dataset_version="v1"
    )
    assert ds.records[0].posted_on.isoformat() == "2026-08-17"


def test_a_missing_column_is_reported_by_name(tmp_path: Path) -> None:
    header = ",".join(c for c in REQUIRED_COLUMNS if c != "final_label")
    path = _write(tmp_path, _row()[: _row().rindex(",")], header=header)
    with pytest.raises(ValidationError, match="final_label"):
        load_labelled_csv(path, dataset_version="v1")


def test_an_empty_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="no rows"):
        load_labelled_csv(_write(tmp_path), dataset_version="v1")


def test_a_missing_file_is_reported_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="no annotation file"):
        load_labelled_csv(tmp_path / "nope.csv", dataset_version="v1")


def test_an_unparseable_amount_names_its_line(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="line 2"):
        load_labelled_csv(_write(tmp_path, _row(amount="PENDING")), dataset_version="v1")


# --- Validation ------------------------------------------------------------


def test_a_clean_file_is_usable(tmp_path: Path) -> None:
    rows = [
        _row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY, b=GROCERY if i < 60 else "")
        for i in range(120)
    ]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert report.usable is True


def test_a_misspelled_label_is_an_error(tmp_path: Path) -> None:
    # The most common annotation mistake, invisible until a class of one shows
    # up in the report.
    report = validate_labelled_csv(_write(tmp_path, _row(a="LIVING_GROCERIES")))
    assert report.usable is False
    assert any("not a valid label" in e for e in report.errors)


def test_an_unadjudicated_disagreement_is_an_error(tmp_path: Path) -> None:
    # The damaging one: the dataset would silently adopt annotator A and the
    # disagreement would never surface.
    report = validate_labelled_csv(_write(tmp_path, _row(a=DINING, b=GROCERY, final="")))
    assert report.usable is False
    assert any("disagree" in e for e in report.errors)


def test_an_adjudicated_disagreement_is_fine(tmp_path: Path) -> None:
    report = validate_labelled_csv(_write(tmp_path, _row(a=DINING, b=GROCERY, final=GROCERY)))
    assert not any("disagree" in e for e in report.errors)


def test_too_few_double_annotations_warns(tmp_path: Path) -> None:
    rows = [_row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY) for i in range(30)]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("kappa" in w for w in report.warnings) or any("kappa" in e for e in report.errors)


def test_no_double_annotation_at_all_is_an_error(tmp_path: Path) -> None:
    rows = [_row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY) for i in range(10)]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("kappa" in e for e in report.errors)


def test_a_thin_class_warns(tmp_path: Path) -> None:
    rows = [_row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY, b=GROCERY) for i in range(60)]
    rows.append(_row("rare", "ODD SHOP", "-10.00", a=OTHER, b=OTHER))
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("fewer than" in w for w in report.warnings)


def test_a_dominant_merchant_warns(tmp_path: Path) -> None:
    rows = [_row(f"t{i}", "AMAZON", "-10.00", a=GROCERY, b=GROCERY) for i in range(40)]
    rows += [_row(f"u{i}", f"SHOP {i}", "-10.00", a=DINING, b=DINING) for i in range(20)]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("lopsided" in w for w in report.warnings)


def test_the_report_counts_double_annotations(tmp_path: Path) -> None:
    rows = [
        _row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY, b=GROCERY if i < 25 else "")
        for i in range(60)
    ]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("25 rows independently double-annotated" in n for n in report.notes)


def test_the_report_states_raw_agreement(tmp_path: Path) -> None:
    rows = [_row(f"t{i}", f"MERCHANT {i}", "-10.00", a=GROCERY, b=GROCERY) for i in range(60)]
    report = validate_labelled_csv(_write(tmp_path, *rows))
    assert any("raw annotator agreement" in n for n in report.notes)


def test_the_report_renders_as_text(tmp_path: Path) -> None:
    report = validate_labelled_csv(_write(tmp_path, _row()))
    rendered = report.render()
    assert "rows" in rendered
    assert isinstance(rendered, str)


def test_a_broken_file_reports_rather_than_raises(tmp_path: Path) -> None:
    # The whole point of the validator: one exception at a time is a poor way
    # to fix a file with thirty typos in it.
    report = validate_labelled_csv(tmp_path / "missing.csv")
    assert report.usable is False
    assert report.errors
