"""Cost items and cost profiles.

A cost item carries everything needed to route it to exactly one calculator and
to explain it afterwards: its category, the period its amount describes, which
track it affects, when it applies, and where the figure came from.

The owning calculator is *derived* from the category rather than stored, so the
two can never disagree.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.costs.categories import CalculatorName, CashFlowType, CostCategory
from offerdelta.domain.costs.items import CostItem, CostProfile

RENT = CostItem(
    category=CostCategory.HOUSING_RENT_OR_MORTGAGE,
    amount=PeriodicAmount(Money.parse("1150.00"), PeriodKind.MONTHLY),
    cash_flow_type=CashFlowType.RECURRING_CASH,
    effective_date=date(2026, 1, 1),
    evidence=Evidence.USER_CONFIRMED,
)

GROCERIES = CostItem(
    category=CostCategory.LIVING_GROCERY,
    amount=PeriodicAmount(Money.parse("520.00"), PeriodKind.MONTHLY),
    cash_flow_type=CashFlowType.RECURRING_CASH,
    effective_date=date(2026, 1, 1),
    evidence=Evidence.ASSUMED,
)

DEPOSIT = CostItem(
    category=CostCategory.RELOCATION_DEPOSIT,
    amount=PeriodicAmount(Money.parse("2400.00"), PeriodKind.ONE_TIME),
    cash_flow_type=CashFlowType.ONE_TIME_CASH,
    effective_date=date(2026, 7, 1),
    evidence=Evidence.ASSUMED,
)


def test_the_owning_calculator_is_derived_from_the_category() -> None:
    # Deriving rather than storing means the two cannot drift apart.
    assert RENT.owner_calculator is CalculatorName.HOUSING


def test_an_item_keeps_its_provenance() -> None:
    assert GROCERIES.evidence is Evidence.ASSUMED


def test_amounts_are_positive_magnitudes() -> None:
    # Sign is applied by the calculator, which knows whether a category reduces
    # or increases cash. An item carrying its own negative sign would be
    # subtracted twice.
    with pytest.raises(ValidationError, match="positive magnitude"):
        CostItem(
            category=CostCategory.LIVING_GROCERY,
            amount=PeriodicAmount(Money.parse("-520.00"), PeriodKind.MONTHLY),
            cash_flow_type=CashFlowType.RECURRING_CASH,
            effective_date=date(2026, 1, 1),
            evidence=Evidence.ASSUMED,
        )


def test_a_zero_amount_is_allowed() -> None:
    # A user who genuinely spends nothing on a category should be able to say so
    # rather than omit it, so the derivation can show the zero.
    item = CostItem(
        category=CostCategory.LIVING_GYM,
        amount=PeriodicAmount(Money.zero(), PeriodKind.MONTHLY),
        cash_flow_type=CashFlowType.RECURRING_CASH,
        effective_date=date(2026, 1, 1),
        evidence=Evidence.USER_CONFIRMED,
    )
    assert item.amount.money.is_zero()


def test_a_recurring_cost_must_describe_a_rate() -> None:
    with pytest.raises(ValidationError, match="RECURRING_CASH"):
        CostItem(
            category=CostCategory.LIVING_GROCERY,
            amount=PeriodicAmount(Money.parse("520.00"), PeriodKind.ONE_TIME),
            cash_flow_type=CashFlowType.RECURRING_CASH,
            effective_date=date(2026, 1, 1),
            evidence=Evidence.ASSUMED,
        )


def test_a_one_time_cost_must_not_describe_a_rate() -> None:
    # The error this prevents: annualising a security deposit.
    with pytest.raises(ValidationError, match="ONE_TIME_CASH"):
        CostItem(
            category=CostCategory.RELOCATION_DEPOSIT,
            amount=PeriodicAmount(Money.parse("2400.00"), PeriodKind.MONTHLY),
            cash_flow_type=CashFlowType.ONE_TIME_CASH,
            effective_date=date(2026, 7, 1),
            evidence=Evidence.ASSUMED,
        )


def test_relocation_costs_are_always_one_time() -> None:
    with pytest.raises(ValidationError, match="one-time"):
        CostItem(
            category=CostCategory.RELOCATION_MOVE,
            amount=PeriodicAmount(Money.parse("3000.00"), PeriodKind.MONTHLY),
            cash_flow_type=CashFlowType.RECURRING_CASH,
            effective_date=date(2026, 7, 1),
            evidence=Evidence.ASSUMED,
        )


def test_time_is_not_entered_as_a_cost_item() -> None:
    # Commute hours are produced by the commute calculator, not entered as a
    # monetary item. Allowing TIME here would mean a Money amount that is not
    # money.
    with pytest.raises(ValidationError, match="TIME"):
        CostItem(
            category=CostCategory.COMMUTE_TRANSIT_FARE,
            amount=PeriodicAmount(Money.parse("120.00"), PeriodKind.MONTHLY),
            cash_flow_type=CashFlowType.TIME,
            effective_date=date(2026, 1, 1),
            evidence=Evidence.ASSUMED,
        )


def test_an_item_is_immutable() -> None:
    with pytest.raises(AttributeError):
        RENT.evidence = Evidence.SOURCED  # type: ignore[misc]


# --- CostProfile -----------------------------------------------------------


def test_a_profile_holds_its_items() -> None:
    profile = CostProfile(items=(RENT, GROCERIES, DEPOSIT))
    assert len(profile.items) == 3


def test_a_profile_routes_items_to_their_owning_calculator() -> None:
    profile = CostProfile(items=(RENT, GROCERIES, DEPOSIT))
    assert profile.items_for(CalculatorName.HOUSING) == (RENT,)
    assert profile.items_for(CalculatorName.LIVING) == (GROCERIES,)


def test_a_calculator_with_no_items_gets_an_empty_tuple() -> None:
    profile = CostProfile(items=(RENT,))
    assert profile.items_for(CalculatorName.HEALTH) == ()


def test_routing_covers_every_item_exactly_once() -> None:
    # The partition guarantee, seen from the data side: summing each
    # calculator's items must reproduce the profile with nothing lost or
    # duplicated.
    profile = CostProfile(items=(RENT, GROCERIES, DEPOSIT))
    routed = [item for name in CalculatorName for item in profile.items_for(name)]
    assert sorted(routed, key=lambda i: i.category.value) == sorted(
        profile.items, key=lambda i: i.category.value
    )


def test_several_items_may_share_a_category() -> None:
    # Two subscriptions are two items, not one merged figure, so the derivation
    # can show them separately.
    second = CostItem(
        category=CostCategory.LIVING_SUBSCRIPTIONS,
        amount=PeriodicAmount(Money.parse("12.99"), PeriodKind.MONTHLY),
        cash_flow_type=CashFlowType.RECURRING_CASH,
        effective_date=date(2026, 1, 1),
        evidence=Evidence.USER_CONFIRMED,
    )
    third = CostItem(
        category=CostCategory.LIVING_SUBSCRIPTIONS,
        amount=PeriodicAmount(Money.parse("9.99"), PeriodKind.MONTHLY),
        cash_flow_type=CashFlowType.RECURRING_CASH,
        effective_date=date(2026, 1, 1),
        evidence=Evidence.USER_CONFIRMED,
    )
    profile = CostProfile(items=(second, third))
    assert len(profile.items_for(CalculatorName.LIVING)) == 2


def test_a_profile_reports_whether_anything_is_assumed() -> None:
    # Drives the "these figures are estimates" treatment in the UI.
    assert CostProfile(items=(RENT,)).has_assumptions() is False
    assert CostProfile(items=(RENT, GROCERIES)).has_assumptions() is True


def test_an_empty_profile_is_allowed() -> None:
    assert CostProfile(items=()).items == ()
