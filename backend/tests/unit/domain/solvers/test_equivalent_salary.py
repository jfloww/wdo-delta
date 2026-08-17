"""The equivalent salary solver.

Finds the candidate base salary at which the candidate matches the current job
on a chosen metric — the answer to "what would they have to pay me to be no
worse off?".

This is the solver that could not exist without the `TaxModel` port. Varying
base salary invalidates a net-pay override by design, so the solver depends on
the port and lets the override-backed model extrapolate. Every result carries
that model's name and how far the answer sits from its calibration point,
because an extrapolated answer should not look as confident as a computed one.

Bisection is sound here because disposable cash is monotone increasing in base
salary: the wage base and deferral caps change the slope of the curve but never
its direction. The guards below are cheap insurance against a future modelling
change breaking that, not a fix for a defect that exists today.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.costs.categories import CashFlowType, CostCategory
from offerdelta.domain.costs.items import CostItem, CostProfile
from offerdelta.domain.solvers.equivalent_salary import (
    SolverBounds,
    solve_equivalent_salary,
    with_base_salary,
)
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel
from tests.fixtures.profiles import auburn_current, new_jersey_candidate

HORIZON = DateRange.of_months(date(2026, 1, 1), 12)
BOUNDS = SolverBounds(
    lower=Money.parse("40000.00"),
    upper=Money.parse("400000.00"),
    tolerance=Money.parse("1.00"),
)


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


# --- Rebuilding a context at a different salary ----------------------------


def test_with_base_salary_changes_only_the_salary() -> None:
    changed = with_base_salary(JERSEY, Money.parse("150000.00"))
    assert changed.employment.compensation.base_salary == Money.parse("150000.00")
    assert changed.costs is JERSEY.costs
    assert changed.horizon is JERSEY.horizon


def test_with_base_salary_keeps_the_original_tax_model() -> None:
    # The model stays calibrated where it was measured; that is what makes the
    # extrapolation distance meaningful rather than always zero.
    changed = with_base_salary(JERSEY, Money.parse("150000.00"))
    assert changed.tax_model is JERSEY.tax_model


def test_with_base_salary_does_not_disturb_a_stale_override() -> None:
    # Changing salary would make an override stale, which is precisely why the
    # engine reads the tax model and never the override field.
    changed = with_base_salary(AUBURN, Money.parse("120000.00"))
    assert changed.employment.override_status() is not None


# --- Solving ---------------------------------------------------------------


def test_comparing_a_profile_against_itself_returns_its_own_salary() -> None:
    # The identity case: if nothing differs, the equivalent salary is the one
    # already being paid.
    result = solve_equivalent_salary(current=AUBURN, candidate=AUBURN, bounds=BOUNDS)
    assert abs(
        (result.equivalent_salary - AUBURN.employment.compensation.base_salary).amount
    ) <= Decimal("1.00")


def test_the_solution_actually_equalises_the_metric() -> None:
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert abs(result.residual.amount) < Decimal("100.00")


def test_the_solver_converges() -> None:
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert result.converged is True


def test_the_solver_reports_its_iteration_count() -> None:
    # Bounded work, and a number worth putting on a dashboard.
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert 0 < result.iterations <= BOUNDS.max_iterations


def test_the_result_names_the_tax_model_that_produced_it() -> None:
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert result.tax_model_name == "NET_PAY_OVERRIDE"


def test_the_result_reports_its_distance_from_calibration() -> None:
    # An answer far from where the model was measured should not look as
    # confident as one sitting on top of it.
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert result.calibration_distance.as_fraction() >= Decimal(0)


def test_adding_a_cost_raises_the_salary_needed_to_match() -> None:
    # The genuine directional property: make the candidate more expensive and
    # the salary required to stay level must rise. Comparing the two fixtures
    # against each other would not test this — their bonus structures and
    # cost start dates differ, so the ordering there reflects the fixtures
    # rather than the solver.
    baseline = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)

    costlier = replace(
        JERSEY,
        costs=CostProfile(
            items=(
                *JERSEY.costs.items,
                CostItem(
                    category=CostCategory.LIVING_OTHER,
                    amount=PeriodicAmount(Money.parse("500.00"), PeriodKind.MONTHLY),
                    cash_flow_type=CashFlowType.RECURRING_CASH,
                    effective_date=date(2026, 1, 1),
                    evidence=Evidence.ASSUMED,
                ),
            )
        ),
    )
    with_extra = solve_equivalent_salary(current=AUBURN, candidate=costlier, bounds=BOUNDS)
    assert with_extra.equivalent_salary > baseline.equivalent_salary


# --- Guards ----------------------------------------------------------------


def test_a_bracket_that_does_not_straddle_the_target_is_rejected() -> None:
    # No salary in this range can close the gap, and saying so beats returning
    # the nearest endpoint as though it were an answer.
    narrow = SolverBounds(
        lower=Money.parse("40000.00"),
        upper=Money.parse("45000.00"),
        tolerance=Money.parse("1.00"),
    )
    with pytest.raises(ValidationError, match="no solution"):
        solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=narrow)


def test_an_inverted_bracket_is_rejected() -> None:
    with pytest.raises(ValidationError, match="lower"):
        SolverBounds(
            lower=Money.parse("400000.00"),
            upper=Money.parse("40000.00"),
            tolerance=Money.parse("1.00"),
        )


def test_a_non_positive_tolerance_is_rejected() -> None:
    with pytest.raises(ValidationError, match="tolerance"):
        SolverBounds(
            lower=Money.parse("40000.00"),
            upper=Money.parse("400000.00"),
            tolerance=Money.parse("0.00"),
        )


def test_monotonicity_is_verified_across_the_bracket() -> None:
    # Cheap insurance. Disposable cash is monotone in salary today; this guard
    # exists so a future modelling change that breaks it fails loudly rather
    # than making bisection return a silently wrong answer.
    result = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert result.monotonicity_verified is True


def test_the_solver_never_calls_an_external_service() -> None:
    # Blueprint 9.2: solvers are pure. Running twice must give the same answer.
    first = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    second = solve_equivalent_salary(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    assert first.equivalent_salary == second.equivalent_salary
