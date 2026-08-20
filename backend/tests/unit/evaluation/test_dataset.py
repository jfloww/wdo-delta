"""The labelled evaluation dataset.

A benchmark is only worth the discipline in its schema. This one has to survive
three specific pressures:

- **Two annotators disagree.** Both labels are kept, and the adjudicated one is
  a third field rather than an overwrite, so agreement can still be measured
  after adjudication.
- **Some transactions have no single right answer.** AMAZON is genuinely
  groceries or shopping depending on the basket. Forcing one gold label there
  manufactures errors that teach nothing, so ambiguous rows carry a set of
  acceptable labels and are reported separately.
- **The dataset must be provably frozen.** Three systems compared on "the same
  data" is a claim; a content checksum turns it into a fact.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.categories import CostCategory
from offerdelta.evaluation.dataset import LabelledDataset, LabelledTransaction
from offerdelta.evaluation.labels import ABSTAIN, LABEL_SPACE

GROCERY = "LIVING_GROCERY"
DINING = "LIVING_DINING"
OTHER = "LIVING_OTHER"


def _record(
    transaction_id: str = "t1",
    merchant: str = "BLUE BOTTLE",
    primary: str = DINING,
    **overrides: object,
) -> LabelledTransaction:
    fields: dict[str, object] = {
        "transaction_id": transaction_id,
        "posted_on": date(2026, 8, 1),
        "raw_description": "SQ *BLUE BOTTLE 4412",
        "normalised_merchant": merchant,
        "amount": Money.parse("-4.50"),
        "account_type": "credit_card",
        "source": "redacted_personal_export",
        "bank_format": "chase_csv_v1",
        "primary_label": primary,
    }
    fields.update(overrides)
    return LabelledTransaction(**fields)  # type: ignore[arg-type]


# --- Label space -----------------------------------------------------------


def test_the_label_space_covers_every_cost_category() -> None:
    assert {c.value for c in CostCategory} <= LABEL_SPACE


def test_the_label_space_includes_the_non_spending_kinds() -> None:
    # A categoriser that cannot say TRANSFER will mislabel it as spending,
    # which is the double count the whole taxonomy exists to prevent.
    assert {"INCOME", "TRANSFER", "REFUND"} <= LABEL_SPACE


def test_abstain_is_a_label() -> None:
    # A model that declines to guess is more useful than one that guesses, so
    # abstention has to be expressible and therefore measurable.
    assert ABSTAIN in LABEL_SPACE


def test_an_unknown_label_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not in the label space"):
        _record(primary="CRYPTO_MOONSHOT")


# --- The gold label --------------------------------------------------------


def test_the_gold_label_is_the_primary_when_there_is_no_adjudication() -> None:
    assert _record().gold_label == DINING


def test_adjudication_overrides_the_primary_label() -> None:
    record = _record(primary=DINING, secondary_label=GROCERY, adjudicated_label=GROCERY)
    assert record.gold_label == GROCERY


def test_adjudication_does_not_erase_the_original_labels() -> None:
    # Overwriting would make inter-annotator agreement unmeasurable after the
    # fact, and agreement is one of the headline numbers.
    record = _record(primary=DINING, secondary_label=GROCERY, adjudicated_label=GROCERY)
    assert record.primary_label == DINING
    assert record.secondary_label == GROCERY


def test_a_record_knows_whether_it_was_double_annotated() -> None:
    assert _record().has_second_annotation is False
    assert _record(secondary_label=GROCERY).has_second_annotation is True


def test_annotators_agreeing_is_visible() -> None:
    assert _record(secondary_label=DINING).annotators_agree is True
    assert _record(secondary_label=GROCERY).annotators_agree is False


def test_agreement_is_undefined_without_a_second_annotator() -> None:
    assert _record().annotators_agree is None


# --- Ambiguity -------------------------------------------------------------


def test_a_prediction_in_the_acceptable_set_counts_as_correct() -> None:
    # AMAZON is groceries or shopping depending on the basket. Marking one of
    # them wrong manufactures an error that teaches nothing.
    record = _record(
        merchant="AMAZON",
        primary=GROCERY,
        ambiguous=True,
        acceptable_labels=frozenset({GROCERY, OTHER}),
    )
    assert record.is_correct(GROCERY) is True
    assert record.is_correct(OTHER) is True
    assert record.is_correct(DINING) is False


def test_an_unambiguous_record_accepts_only_its_gold_label() -> None:
    record = _record()
    assert record.is_correct(DINING) is True
    assert record.is_correct(GROCERY) is False


def test_an_ambiguous_record_needs_more_than_one_acceptable_label() -> None:
    # Otherwise the ambiguity claim has nothing behind it.
    with pytest.raises(ValidationError, match="at least two"):
        _record(ambiguous=True, acceptable_labels=frozenset({DINING}))


def test_the_acceptable_set_must_contain_the_gold_label() -> None:
    # A gold label the record itself would score wrong is incoherent.
    with pytest.raises(ValidationError, match="acceptable"):
        _record(ambiguous=True, acceptable_labels=frozenset({GROCERY, OTHER}))


def test_needs_review_is_recorded_separately_from_ambiguity() -> None:
    # Ambiguous means there is no single right answer. Needs-review means the
    # annotator was unsure. Different problems, counted differently.
    record = _record(needs_review=True)
    assert record.needs_review is True
    assert record.ambiguous is False


# --- Provenance ------------------------------------------------------------


def test_a_record_carries_its_source_and_bank_format() -> None:
    # Any generalisation claim depends on knowing which export a row came from.
    record = _record()
    assert record.source == "redacted_personal_export"
    assert record.bank_format == "chase_csv_v1"


def test_a_record_needs_a_normalised_merchant() -> None:
    # The split key. Without it, leakage between rule data and evaluation data
    # is undetectable.
    with pytest.raises(ValidationError, match="normalised merchant"):
        _record(merchant="  ")


# --- The dataset -----------------------------------------------------------


def test_a_dataset_carries_its_version() -> None:
    ds = LabelledDataset(dataset_version="2026.08.1", records=(_record(),))
    assert ds.dataset_version == "2026.08.1"


def test_a_dataset_carries_a_schema_version() -> None:
    # The schema will change. A stored result must say which shape produced it.
    assert LabelledDataset(dataset_version="v1", records=(_record(),)).schema_version


def test_duplicate_transaction_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        LabelledDataset(
            dataset_version="v1",
            records=(_record("t1"), _record("t1", merchant="X")),
        )


def test_an_empty_dataset_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        LabelledDataset(dataset_version="v1", records=())


def test_the_checksum_is_stable_for_identical_content() -> None:
    a = LabelledDataset(dataset_version="v1", records=(_record(),))
    b = LabelledDataset(dataset_version="v1", records=(_record(),))
    assert a.checksum == b.checksum


def test_the_checksum_changes_when_a_label_changes() -> None:
    a = LabelledDataset(dataset_version="v1", records=(_record(primary=DINING),))
    b = LabelledDataset(dataset_version="v1", records=(_record(primary=GROCERY),))
    assert a.checksum != b.checksum


def test_the_checksum_ignores_record_order() -> None:
    # Two systems evaluated on the same rows in a different order ran on the
    # same dataset, and the checksum has to agree that they did.
    one, two = _record("t1"), _record("t2", merchant="OTHER")
    assert (
        LabelledDataset(dataset_version="v1", records=(one, two)).checksum
        == LabelledDataset(dataset_version="v1", records=(two, one)).checksum
    )


def test_the_dataset_reports_its_double_annotated_subset() -> None:
    ds = LabelledDataset(
        dataset_version="v1",
        records=(_record("t1"), _record("t2", merchant="B", secondary_label=DINING)),
    )
    assert len(ds.double_annotated) == 1


def test_the_dataset_reports_its_ambiguous_subset() -> None:
    ambiguous = _record(
        "t2",
        merchant="AMAZON",
        primary=GROCERY,
        ambiguous=True,
        acceptable_labels=frozenset({GROCERY, OTHER}),
    )
    ds = LabelledDataset(dataset_version="v1", records=(_record("t1"), ambiguous))
    assert len(ds.ambiguous) == 1
    assert len(ds.unambiguous) == 1


def test_the_dataset_lists_its_merchants() -> None:
    ds = LabelledDataset(
        dataset_version="v1",
        records=(_record("t1", merchant="A"), _record("t2", merchant="B")),
    )
    assert ds.merchants == frozenset({"A", "B"})


def test_a_generator_of_records_is_rejected() -> None:
    # Caught in real use: a generator is consumed by the duplicate check and
    # reads as empty ever after, so the benchmark silently scores zero rows and
    # reports whatever that produces.
    with pytest.raises(ValidationError, match="must be a tuple"):
        LabelledDataset(
            dataset_version="v1",
            records=(_record(f"t{i}") for i in range(3)),  # type: ignore[arg-type]
        )
