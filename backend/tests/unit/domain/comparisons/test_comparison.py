"""Comparing two sides.

Runs the engine over both profiles on the same horizon and reports the
difference component by component, keeping cash, wealth, and time apart.

The invariant that matters most here is the boring one: comparing a profile
against itself must produce exactly zero on every track. It is the cheapest
possible check that nothing in the pipeline introduces an asymmetry — a
calculator that reads the candidate side by accident, a rounding step applied
once rather than twice, an ordering dependency in the projection.
"""

from datetime import date
from decimal import Decimal

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.comparison import compare
from offerdelta.domain.comparisons.context import CalculationContext
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


AUBURN = _context(auburn_current)
JERSEY = _context(new_jersey_candidate)


# --- The symmetry invariant ------------------------------------------------


def test_comparing_a_profile_against_itself_gives_zero_cash_delta() -> None:
    assert compare(current=AUBURN, candidate=AUBURN).cash_delta.is_zero()


def test_comparing_a_profile_against_itself_gives_zero_wealth_delta() -> None:
    assert compare(current=AUBURN, candidate=AUBURN).wealth_delta.is_zero()


def test_comparing_a_profile_against_itself_gives_zero_time_delta() -> None:
    assert compare(current=AUBURN, candidate=AUBURN).time_delta_hours == Decimal(0)


def test_comparing_a_profile_against_itself_gives_zero_component_deltas() -> None:
    result = compare(current=AUBURN, candidate=AUBURN)
    assert all(component.delta.is_zero() for component in result.component_deltas)


def test_comparing_a_profile_against_itself_gives_a_flat_cumulative_series() -> None:
    result = compare(current=AUBURN, candidate=AUBURN)
    assert all(value.is_zero() for value in result.cumulative_cash_delta)


# --- Direction -------------------------------------------------------------


def test_the_delta_is_candidate_minus_current() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    expected = result.candidate.total_cash - result.current.total_cash
    assert result.cash_delta == expected


def test_reversing_the_sides_negates_the_delta() -> None:
    forward = compare(current=AUBURN, candidate=JERSEY)
    backward = compare(current=JERSEY, candidate=AUBURN)
    assert forward.cash_delta == -backward.cash_delta


def test_the_longer_commute_shows_as_a_positive_time_cost() -> None:
    # Jersey City to Manhattan against a 15-minute Auburn drive.
    assert compare(current=AUBURN, candidate=JERSEY).time_delta_hours > 0


# --- Component deltas ------------------------------------------------------


def test_every_component_from_either_side_appears() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    codes = {component.code for component in result.component_deltas}
    assert "take_home_pay" in codes
    # Relocation exists only on the candidate side and must still be reported.
    assert any(code.startswith("relocation_") for code in codes)


def test_a_component_present_on_one_side_only_treats_the_other_as_zero() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    signing = next(c for c in result.component_deltas if c.code == "signing_bonus")
    assert signing.current_cash.is_zero()
    assert signing.candidate_cash.amount > 0


def test_component_deltas_sum_to_the_headline_cash_delta() -> None:
    # If these disagree, a component was dropped from the breakdown or counted
    # twice — the same class of defect the monthly invariant guards against,
    # checked here across the whole horizon.
    result = compare(current=AUBURN, candidate=JERSEY)
    total = Money.zero()
    for component in result.component_deltas:
        total = total + component.delta
    assert total == result.annualised_cash_delta


def test_components_are_ordered_by_impact() -> None:
    # The sensitivity list reads top-down, so the biggest mover comes first.
    result = compare(current=AUBURN, candidate=JERSEY)
    magnitudes = [abs(c.delta.amount) for c in result.component_deltas]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_top_improvements_are_positive() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    assert all(c.delta.amount > 0 for c in result.top_improvements(3))


def test_top_regressions_are_negative() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    assert all(c.delta.amount < 0 for c in result.top_regressions(3))


def test_top_movers_respect_the_requested_count() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    assert len(result.top_improvements(2)) <= 2


# --- Cumulative series -----------------------------------------------------


def test_the_cumulative_series_covers_the_horizon() -> None:
    assert len(compare(current=AUBURN, candidate=JERSEY).cumulative_cash_delta) == 12


def test_the_final_cumulative_value_matches_the_headline_delta() -> None:
    result = compare(current=AUBURN, candidate=JERSEY)
    assert result.cumulative_cash_delta[-1] == result.cash_delta


def test_both_sides_keep_their_own_reconciled_projection() -> None:
    # The comparison never recomputes; it reads two results that each already
    # proved they balance.
    result = compare(current=AUBURN, candidate=JERSEY)
    assert all(month.residual.is_zero() for month in result.current.months)
    assert all(month.residual.is_zero() for month in result.candidate.months)
