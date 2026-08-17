"""The comparison engine and the monthly reconciliation invariant.

The engine composes calculators, projects their impacts onto months, and
refuses to return a result that does not balance.

The reconciliation invariant has teeth because it computes each month twice:
once as the plain sum of every cash impact, and once by classifying each impact
into exactly one bucket and summing the buckets. An impact counted in two
buckets, or in none, makes the two disagree. That is the shape of the real bug
it exists to catch — a cost consumed by two calculators, or dropped by all of
them.
"""

from datetime import date
from decimal import Decimal
from itertools import pairwise

import pytest

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.engine import ComparisonEngine, default_calculators
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.comparisons.reconciliation import CashBucket, classify, reconcile
from offerdelta.domain.costs.categories import CalculatorName, CostCategory
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel
from tests.fixtures.profiles import auburn_current, new_jersey_candidate

HORIZON = DateRange.of_months(date(2026, 1, 1), 12)


def _context(build: object) -> CalculationContext:
    assert callable(build)
    side = build()
    salary = side.employment.compensation.base_salary
    return CalculationContext(
        employment=side.employment,
        costs=side.costs,
        household=side.household,
        tax_model=NetPayOverrideTaxModel(
            observed_gross=PeriodicAmount(salary, PeriodKind.ANNUAL),
            observed_net=PeriodicAmount(salary * Decimal("0.74"), PeriodKind.ANNUAL),
            marginal_rate=Percentage.from_percent("32"),
        ),
        horizon=HORIZON,
    )


def _impact(code: str, cash: str, period: PeriodKind = PeriodKind.MONTHLY) -> CostImpact:
    return CostImpact(
        code=code,
        label=code,
        category=CostCategory.LIVING_OTHER,
        produced_by=CalculatorName.LIVING,
        period=period,
        effective_date=date(2026, 1, 1),
        formula_id="test",
        evidence=Evidence.ASSUMED,
        cash_amount=Money.parse(cash),
    )


# --- Assembly --------------------------------------------------------------


def test_the_default_calculators_partition_every_category() -> None:
    # Constructing the engine runs the check; a gap or overlap raises here
    # rather than silently producing a wrong total.
    assert ComparisonEngine(default_calculators()) is not None


def test_a_missing_calculator_is_rejected_at_assembly() -> None:
    incomplete = tuple(
        calculator
        for calculator in default_calculators()
        if calculator.owned_categories() != frozenset()
    )[:2]
    with pytest.raises(ValidationError, match="not claimed"):
        ComparisonEngine(incomplete)


# --- Running ---------------------------------------------------------------


def test_the_engine_produces_impacts_from_every_calculator() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    producers = {impact.produced_by for impact in result.impacts}
    assert CalculatorName.HOUSING in producers
    assert CalculatorName.LIVING in producers


def test_the_projection_covers_the_whole_horizon() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    assert len(result.months) == 12


def test_a_monthly_cost_recurs_every_month() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    spending = {month.spending for month in result.months}
    assert len(spending) == 1, "a profile of only recurring costs should be flat"


def test_a_one_time_cost_lands_only_in_its_month() -> None:
    # The New Jersey relocation costs are dated to the move, not the start.
    result = ComparisonEngine(default_calculators()).calculate(_context(new_jersey_candidate))
    with_one_time = [m for m in result.months if not m.one_time_net.is_zero()]
    assert len(with_one_time) >= 1
    assert all(m.month_start.month == 7 or m.month_index == 0 for m in with_one_time)


def test_every_month_reconciles() -> None:
    # The invariant the engine asserts before returning. If this fails, the
    # model is wrong, not the test.
    for build in (auburn_current, new_jersey_candidate):
        result = ComparisonEngine(default_calculators()).calculate(_context(build))
        for month in result.months:
            assert month.residual.is_zero(), (
                f"{build.__name__} month {month.month_index} does not balance: {month.residual}"
            )


def test_closing_cash_carries_into_the_next_month() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    for earlier, later in pairwise(result.months):
        assert later.opening_cash == earlier.closing_cash


def test_the_first_month_opens_at_zero() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    assert result.months[0].opening_cash.is_zero()


def test_wealth_is_tracked_apart_from_cash() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(new_jersey_candidate))
    assert result.total_wealth.amount > 0


def test_time_is_tracked_apart_from_money() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    assert result.total_time_hours > 0


def test_identical_profiles_produce_identical_results() -> None:
    # Reproducibility: the same versioned inputs must give the same outputs.
    first = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    second = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    assert first.total_cash == second.total_cash
    assert [m.closing_cash for m in first.months] == [m.closing_cash for m in second.months]


# --- The reconciliation check itself ---------------------------------------


def test_every_impact_falls_into_exactly_one_bucket() -> None:
    result = ComparisonEngine(default_calculators()).calculate(_context(auburn_current))
    for impact in result.impacts:
        assert isinstance(classify(impact), CashBucket)


def test_reconciliation_accepts_a_balanced_month() -> None:
    impacts = (_impact("pay", "3000.00"), _impact("rent", "-1200.00"))
    assert reconcile(impacts, Money.parse("1800.00")).is_zero()


def test_reconciliation_detects_a_missing_amount() -> None:
    # What a dropped cost looks like: the buckets no longer reach the total.
    impacts = (_impact("pay", "3000.00"), _impact("rent", "-1200.00"))
    assert not reconcile(impacts, Money.parse("1900.00")).is_zero()


def test_reconciliation_detects_a_double_counted_amount() -> None:
    # What a cost consumed by two calculators looks like.
    impacts = (
        _impact("pay", "3000.00"),
        _impact("rent", "-1200.00"),
        _impact("rent_again", "-1200.00"),
    )
    assert not reconcile(impacts, Money.parse("1800.00")).is_zero()


def test_income_and_spending_land_in_different_buckets() -> None:
    assert classify(_impact("pay", "3000.00")) is CashBucket.INCOME
    assert classify(_impact("rent", "-1200.00")) is CashBucket.SPENDING


def test_one_time_flows_have_their_own_bucket() -> None:
    bonus = _impact("bonus", "10000.00", PeriodKind.ONE_TIME)
    assert classify(bonus) is CashBucket.ONE_TIME


def test_wealth_only_impacts_are_excluded_from_cash() -> None:
    # Employer money must not enter the cash identity at all.
    employer = CostImpact(
        code="match",
        label="match",
        category=CostCategory.LIVING_OTHER,
        produced_by=CalculatorName.HEALTH,
        period=PeriodKind.ANNUAL,
        effective_date=date(2026, 1, 1),
        formula_id="test",
        evidence=Evidence.DERIVED,
        wealth_amount=Money.parse("3120.00"),
    )
    assert classify(employer) is CashBucket.NOT_CASH
