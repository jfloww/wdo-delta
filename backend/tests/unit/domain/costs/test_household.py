"""Household cost splitting.

Splitting shared costs is where `Money.allocate` earns its keep. A household
splits rent, utilities, and internet every month for the whole horizon, so a
naive division that loses a cent per split loses it 36 times over a three-year
comparison — and the monthly reconciliation invariant fails for a reason that
has nothing to do with the model being wrong.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.costs.household import HouseholdProfile, SplitMethod


def test_a_single_person_household_bears_the_whole_cost() -> None:
    household = HouseholdProfile.solo()
    assert household.share_of(Money.parse("1150.00")) == Money.parse("1150.00")


def test_an_even_split_halves_a_shared_cost() -> None:
    household = HouseholdProfile.even(size=2)
    assert household.share_of(Money.parse("1150.00")) == Money.parse("575.00")


def test_an_even_split_of_an_odd_amount_keeps_the_extra_cent() -> None:
    # Largest remainder gives the leftover to the first share, and the user is
    # the first share, so the household total still reconciles exactly.
    household = HouseholdProfile.even(size=3)
    assert household.share_of(Money.parse("100.00")) == Money.parse("33.34")


def test_a_percentage_split_applies_the_stated_share() -> None:
    household = HouseholdProfile(size=2, method=SplitMethod.PERCENTAGE, user_weight=Decimal("70"))
    assert household.share_of(Money.parse("1000.00")) == Money.parse("700.00")


def test_a_percentage_split_never_loses_a_cent() -> None:
    household = HouseholdProfile(size=2, method=SplitMethod.PERCENTAGE, user_weight=Decimal("70"))
    cost = Money.parse("1234.56")
    user = household.share_of(cost)
    others = household.others_share_of(cost)
    assert user + others == cost


def test_shares_reconcile_for_an_even_split_too() -> None:
    household = HouseholdProfile.even(size=3)
    cost = Money.parse("100.00")
    assert household.share_of(cost) + household.others_share_of(cost) == cost


def test_a_fixed_split_uses_an_absolute_amount() -> None:
    # "I pay $800 of the rent, whatever the rent is."
    household = HouseholdProfile(
        size=2, method=SplitMethod.FIXED, user_fixed_amount=Money.parse("800.00")
    )
    assert household.share_of(Money.parse("1950.00")) == Money.parse("800.00")


def test_a_fixed_share_is_capped_at_the_total_cost() -> None:
    # Paying more than the whole bill is a data error, not a generous roommate.
    household = HouseholdProfile(
        size=2, method=SplitMethod.FIXED, user_fixed_amount=Money.parse("800.00")
    )
    assert household.share_of(Money.parse("500.00")) == Money.parse("500.00")


def test_household_size_must_be_at_least_one() -> None:
    with_zero = {"size": 0, "method": SplitMethod.EVEN}
    with pytest.raises(ValidationError, match="at least one"):
        HouseholdProfile(**with_zero)  # type: ignore[arg-type]


def test_a_percentage_split_requires_a_weight() -> None:
    with pytest.raises(ValidationError, match="user_weight"):
        HouseholdProfile(size=2, method=SplitMethod.PERCENTAGE)


def test_a_percentage_weight_cannot_exceed_one_hundred() -> None:
    with pytest.raises(ValidationError, match="between 0 and 100"):
        HouseholdProfile(size=2, method=SplitMethod.PERCENTAGE, user_weight=Decimal("140"))


def test_a_fixed_split_requires_an_amount() -> None:
    with pytest.raises(ValidationError, match="user_fixed_amount"):
        HouseholdProfile(size=2, method=SplitMethod.FIXED)


def test_a_solo_household_ignores_split_configuration() -> None:
    # One person pays everything regardless of method, so the config is moot.
    assert HouseholdProfile.solo().share_of(Money.parse("77.77")) == Money.parse("77.77")


def test_splitting_zero_costs_nothing() -> None:
    assert HouseholdProfile.even(size=2).share_of(Money.zero()).is_zero()


def test_is_immutable() -> None:
    household = HouseholdProfile.even(size=2)
    with pytest.raises(AttributeError):
        household.size = 4  # type: ignore[misc]
