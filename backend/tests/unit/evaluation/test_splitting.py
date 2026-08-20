"""Splitting by merchant.

The leak this prevents is specific and easy to miss. If NETFLIX appears in the
data the rules were written against *and* in the evaluation set, the rule
`contains NETFLIX -> subscriptions` scores perfectly on a merchant it was
literally written for. The reported F1 then measures memorisation, and the
number looks excellent right up until a merchant nobody anticipated arrives.

So the unit of splitting is the normalised merchant, never the transaction. A
merchant lands wholly on one side or the other.

Assignment is by hash of the merchant name, which makes it deterministic and
order-independent: the same merchant lands in the same split on every machine,
in every run, regardless of how the rows arrived.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.evaluation.dataset import LabelledDataset, LabelledTransaction
from offerdelta.evaluation.splitting import merchant_disjoint_split

DINING = "LIVING_DINING"


def _record(txn_id: str, merchant: str) -> LabelledTransaction:
    return LabelledTransaction(
        transaction_id=txn_id,
        posted_on=date(2026, 8, 1),
        raw_description=merchant,
        normalised_merchant=merchant,
        amount=Money.parse("-4.50"),
        account_type="credit_card",
        source="redacted_personal_export",
        bank_format="chase_csv_v1",
        primary_label=DINING,
    )


def _dataset(merchant_counts: dict[str, int]) -> LabelledDataset:
    records = []
    counter = 0
    for merchant, count in merchant_counts.items():
        for _ in range(count):
            counter += 1
            records.append(_record(f"t{counter}", merchant))
    return LabelledDataset(dataset_version="v1", records=tuple(records))


MANY = _dataset({f"MERCHANT_{i:03d}": 3 for i in range(60)})


def test_no_merchant_appears_on_both_sides() -> None:
    # The whole point.
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert not (split.development.merchants & split.holdout.merchants)


def test_every_record_lands_somewhere() -> None:
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert len(split.development) + len(split.holdout) == len(MANY)


def test_no_record_is_duplicated_across_sides() -> None:
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    dev = {r.transaction_id for r in split.development.records}
    hold = {r.transaction_id for r in split.holdout.records}
    assert not (dev & hold)


def test_a_merchants_transactions_stay_together() -> None:
    # A merchant split across sides is the leak in miniature.
    dataset = _dataset({"NETFLIX": 12, "SPOTIFY": 8, "BLUE BOTTLE": 20, "SHELL": 5})
    split = merchant_disjoint_split(dataset, holdout_fraction=0.5)
    for merchant in dataset.merchants:
        in_dev = any(r.normalised_merchant == merchant for r in split.development.records)
        in_hold = any(r.normalised_merchant == merchant for r in split.holdout.records)
        assert not (in_dev and in_hold)


def test_the_split_is_deterministic() -> None:
    # Two runs on two machines must produce the same benchmark, or results are
    # not comparable across time.
    first = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    second = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert first.holdout.merchants == second.holdout.merchants


def test_the_split_ignores_record_order() -> None:
    shuffled = LabelledDataset(
        dataset_version=MANY.dataset_version, records=tuple(reversed(MANY.records))
    )
    assert (
        merchant_disjoint_split(MANY, holdout_fraction=0.3).holdout.merchants
        == merchant_disjoint_split(shuffled, holdout_fraction=0.3).holdout.merchants
    )


def test_the_holdout_is_roughly_the_requested_size() -> None:
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    share = len(split.holdout.merchants) / len(MANY.merchants)
    assert 0.2 <= share <= 0.4


def test_changing_the_salt_changes_the_split() -> None:
    # A second, independent split is sometimes wanted; the salt provides one
    # without abandoning determinism.
    a = merchant_disjoint_split(MANY, holdout_fraction=0.3, salt="a")
    b = merchant_disjoint_split(MANY, holdout_fraction=0.3, salt="b")
    assert a.holdout.merchants != b.holdout.merchants


def test_both_sides_carry_the_parent_version() -> None:
    # A result must be traceable to the dataset it came from.
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert split.development.dataset_version.startswith(MANY.dataset_version)
    assert split.holdout.dataset_version.startswith(MANY.dataset_version)


def test_the_split_records_the_parent_checksum() -> None:
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert split.source_checksum == MANY.checksum


def test_an_impossible_fraction_is_rejected() -> None:
    with pytest.raises(ValidationError, match="between 0 and 1"):
        merchant_disjoint_split(MANY, holdout_fraction=1.5)


def test_a_split_that_would_empty_a_side_is_rejected() -> None:
    # Two merchants and a 1% holdout gives an empty evaluation set, and an
    # empty benchmark reporting 100% is worse than no benchmark.
    tiny = _dataset({"A": 5, "B": 5})
    with pytest.raises(ValidationError, match="empty"):
        merchant_disjoint_split(tiny, holdout_fraction=0.01)


def test_the_split_reports_its_merchant_counts() -> None:
    split = merchant_disjoint_split(MANY, holdout_fraction=0.3)
    assert split.development_merchants + split.holdout_merchants == len(MANY.merchants)
