"""Splitting one amount across several parties without losing or inventing money.

Naive division loses pennies: $100 split three ways gives $33.33 each and
silently destroys a cent. Household cost splitting does this on every line item
of every month, so the error compounds and the reconciliation invariant fails.

The rule is largest remainder — floor every share, then hand the leftover units
to the shares with the largest discarded fraction, earliest first on a tie.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.money import Money


def test_splits_evenly_when_the_amount_divides_exactly() -> None:
    assert Money.parse("90.00").allocate([1, 1, 1]) == [
        Money.parse("30.00"),
        Money.parse("30.00"),
        Money.parse("30.00"),
    ]


def test_distributes_the_leftover_penny_on_an_even_split() -> None:
    # 100.00 / 3 is 33.333..., so one cent is left over after flooring.
    assert Money.parse("100.00").allocate([1, 1, 1]) == [
        Money.parse("33.34"),
        Money.parse("33.33"),
        Money.parse("33.33"),
    ]


def test_leftover_goes_to_the_largest_remainder_not_the_first_share() -> None:
    # 1234.56 split 70/30 is 864.192 and 370.368. Flooring gives 864.19 + 370.36
    # = 1234.55, leaving one cent. The second share discarded the larger
    # fraction (.8 of a cent vs .2), so it receives the cent.
    assert Money.parse("1234.56").allocate([70, 30]) == [
        Money.parse("864.19"),
        Money.parse("370.37"),
    ]


def test_preserves_the_total_for_an_awkward_split() -> None:
    shares = Money.parse("0.05").allocate([3, 7])
    assert sum(shares, Money.zero()) == Money.parse("0.05")


def test_handles_a_negative_amount() -> None:
    # A negative delta split across a household must still preserve its total.
    shares = Money.parse("-100.00").allocate([1, 1, 1])
    assert sum(shares, Money.zero()) == Money.parse("-100.00")


def test_accepts_decimal_weights() -> None:
    shares = Money.parse("100.00").allocate([Decimal("2.5"), Decimal("7.5")])
    assert shares == [Money.parse("25.00"), Money.parse("75.00")]


def test_allocates_to_whole_dollars_when_asked() -> None:
    shares = Money.parse("100.00").allocate([1, 1, 1], places=0)
    assert shares == [Money.parse("34"), Money.parse("33"), Money.parse("33")]


def test_a_single_share_receives_everything() -> None:
    assert Money.parse("12.34").allocate([1]) == [Money.parse("12.34")]


def test_zero_amount_allocates_to_zeros() -> None:
    assert Money.parse("0.00").allocate([1, 1]) == [Money.zero(), Money.zero()]


def test_rejects_empty_weights() -> None:
    with pytest.raises(ValueError, match="at least one weight"):
        Money.parse("10.00").allocate([])


def test_rejects_weights_summing_to_zero() -> None:
    with pytest.raises(ValueError, match="sum to a positive"):
        Money.parse("10.00").allocate([0, 0])


def test_rejects_a_negative_weight() -> None:
    with pytest.raises(ValueError, match="negative"):
        Money.parse("10.00").allocate([3, -1])


def test_rejects_a_float_weight() -> None:
    with pytest.raises(TypeError, match="float"):
        Money.parse("10.00").allocate([0.5, 0.5])  # type: ignore[list-item]


def test_preserves_the_currency_of_each_share() -> None:
    shares = Money(Decimal("10.00"), "EUR").allocate([1, 1])
    assert all(share.currency == "EUR" for share in shares)
