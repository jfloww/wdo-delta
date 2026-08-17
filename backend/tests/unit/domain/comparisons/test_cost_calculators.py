"""Cost calculators.

Each calculator consumes exactly the categories it owns and emits one
`CostImpact` per item, carrying the provenance and formula the derivation tree
needs.

The household question is settled per item rather than per category, because
shareability is a fact about the arrangement rather than about the kind of cost:
two people usually split rent, sometimes split groceries, and rarely split a
gym membership.
"""

from datetime import date
from decimal import Decimal

from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.cost_calculators import CostItemCalculator
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.costs.categories import CalculatorName, CashFlowType, CostCategory
from offerdelta.domain.costs.household import HouseholdProfile
from offerdelta.domain.costs.items import CostItem, CostProfile

SHARED_RENT = CostItem(
    category=CostCategory.HOUSING_RENT_OR_MORTGAGE,
    amount=PeriodicAmount(Money.parse("2400.00"), PeriodKind.MONTHLY),
    cash_flow_type=CashFlowType.RECURRING_CASH,
    effective_date=date(2026, 1, 1),
    evidence=Evidence.USER_CONFIRMED,
    is_shared=True,
)

PERSONAL_GYM = CostItem(
    category=CostCategory.LIVING_GYM,
    amount=PeriodicAmount(Money.parse("60.00"), PeriodKind.MONTHLY),
    cash_flow_type=CashFlowType.RECURRING_CASH,
    effective_date=date(2026, 1, 1),
    evidence=Evidence.ASSUMED,
)

DEPOSIT = CostItem(
    category=CostCategory.RELOCATION_DEPOSIT,
    amount=PeriodicAmount(Money.parse("4800.00"), PeriodKind.ONE_TIME),
    cash_flow_type=CashFlowType.ONE_TIME_CASH,
    effective_date=date(2026, 7, 1),
    evidence=Evidence.ASSUMED,
    is_shared=True,
)


def _impacts(
    calculator: CalculatorName,
    items: tuple[CostItem, ...],
    household: HouseholdProfile,
) -> tuple[CostImpact, ...]:
    return CostItemCalculator(calculator).calculate(
        costs=CostProfile(items=items), household=household
    )


def test_a_calculator_owns_exactly_its_categories() -> None:
    housing = CostItemCalculator(CalculatorName.HOUSING)
    assert CostCategory.HOUSING_RENT_OR_MORTGAGE in housing.owned_categories()
    assert CostCategory.LIVING_GYM not in housing.owned_categories()


def test_a_calculator_ignores_items_it_does_not_own() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT, PERSONAL_GYM), HouseholdProfile.solo())
    assert [impact.category for impact in impacts] == [CostCategory.HOUSING_RENT_OR_MORTGAGE]


def test_costs_are_emitted_as_negative_cash() -> None:
    # Sign is applied here, once, by the calculator that owns the item. Items
    # themselves hold positive magnitudes.
    impacts = _impacts(CalculatorName.LIVING, (PERSONAL_GYM,), HouseholdProfile.solo())
    assert impacts[0].cash_amount == Money.parse("-60.00")


def test_a_solo_household_bears_the_whole_shared_cost() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.solo())
    assert impacts[0].cash_amount == Money.parse("-2400.00")


def test_a_shared_cost_is_split_across_the_household() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.even(size=2))
    assert impacts[0].cash_amount == Money.parse("-1200.00")


def test_a_personal_cost_is_never_split() -> None:
    # Living in a two-person household does not halve your own gym membership.
    impacts = _impacts(CalculatorName.LIVING, (PERSONAL_GYM,), HouseholdProfile.even(size=2))
    assert impacts[0].cash_amount == Money.parse("-60.00")


def test_a_one_time_cost_keeps_its_period_and_date() -> None:
    impacts = _impacts(CalculatorName.RELOCATION, (DEPOSIT,), HouseholdProfile.even(size=2))
    assert impacts[0].period is PeriodKind.ONE_TIME
    assert impacts[0].effective_date == date(2026, 7, 1)


def test_a_one_time_shared_cost_is_split_too() -> None:
    impacts = _impacts(CalculatorName.RELOCATION, (DEPOSIT,), HouseholdProfile.even(size=2))
    assert impacts[0].cash_amount == Money.parse("-2400.00")


def test_costs_carry_no_wealth_or_time_impact() -> None:
    impacts = _impacts(CalculatorName.LIVING, (PERSONAL_GYM,), HouseholdProfile.solo())
    assert impacts[0].wealth_amount.is_zero()
    assert impacts[0].time_hours == Decimal(0)


def test_each_impact_carries_its_provenance() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.solo())
    assert impacts[0].evidence is Evidence.USER_CONFIRMED


def test_each_impact_names_the_calculator_that_produced_it() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.solo())
    assert impacts[0].produced_by is CalculatorName.HOUSING


def test_each_impact_carries_a_formula_identifier() -> None:
    # The derivation tree renders this, so it must never be empty.
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.solo())
    assert impacts[0].formula_id


def test_a_split_impact_records_that_it_was_split() -> None:
    # Otherwise a reader cannot tell why their 2,400 rent shows as 1,200.
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.even(size=2))
    assert "split" in impacts[0].formula_id


def test_an_unsplit_impact_does_not_claim_to_be_split() -> None:
    impacts = _impacts(CalculatorName.HOUSING, (SHARED_RENT,), HouseholdProfile.solo())
    assert "split" not in impacts[0].formula_id


def test_impact_codes_are_unique_within_a_calculator() -> None:
    second_gym = CostItem(
        category=CostCategory.LIVING_GYM,
        amount=PeriodicAmount(Money.parse("25.00"), PeriodKind.MONTHLY),
        cash_flow_type=CashFlowType.RECURRING_CASH,
        effective_date=date(2026, 1, 1),
        evidence=Evidence.ASSUMED,
    )
    impacts = _impacts(CalculatorName.LIVING, (PERSONAL_GYM, second_gym), HouseholdProfile.solo())
    codes = [impact.code for impact in impacts]
    assert len(set(codes)) == len(codes)


def test_no_items_produce_no_impacts() -> None:
    assert _impacts(CalculatorName.HEALTH, (), HouseholdProfile.solo()) == ()
