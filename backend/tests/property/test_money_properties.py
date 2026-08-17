"""Property-based invariants for Money.

Blueprint section 21.2 lists `Money.allocate` preserving the total for arbitrary
amounts and weights as a required invariant. Example-based tests confirm the
cases we thought of; these confirm the cases we did not.
"""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from offerdelta.domain.common.money import Money

# Amounts up to ten million dollars, in whole cents, positive and negative.
cent_amounts = st.integers(min_value=-(10**9), max_value=10**9).map(
    lambda cents: Money(Decimal(cents).scaleb(-2))
)

weight_lists = st.lists(st.integers(min_value=1, max_value=10_000), min_size=1, max_size=12)


@pytest.mark.property
@given(amount=cent_amounts, weights=weight_lists)
def test_allocation_preserves_the_total(amount: Money, weights: list[int]) -> None:
    shares = amount.allocate(weights)
    assert sum(shares, Money.zero()) == amount


@pytest.mark.property
@given(amount=cent_amounts, weights=weight_lists)
def test_allocation_returns_one_share_per_weight(amount: Money, weights: list[int]) -> None:
    assert len(amount.allocate(weights)) == len(weights)


@pytest.mark.property
@given(amount=cent_amounts, weights=weight_lists)
def test_every_share_is_within_one_cent_of_its_exact_proportion(
    amount: Money, weights: list[int]
) -> None:
    shares = amount.allocate(weights)
    total_weight = sum(weights)
    for share, weight in zip(shares, weights, strict=True):
        exact = amount.amount * weight / total_weight
        assert abs(share.amount - exact) < Decimal("0.01")


@pytest.mark.property
@given(amount=cent_amounts)
def test_adding_then_subtracting_returns_the_original(amount: Money) -> None:
    other = Money.parse("1234.56")
    assert amount + other - other == amount


@pytest.mark.property
@given(amount=cent_amounts)
def test_negation_is_its_own_inverse(amount: Money) -> None:
    negated = -amount
    assert -negated == amount


@pytest.mark.property
@given(amount=cent_amounts)
def test_scaling_by_one_is_identity(amount: Money) -> None:
    assert amount * 1 == amount
