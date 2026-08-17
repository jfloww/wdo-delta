"""The equivalent salary solver.

Answers "what would they have to pay me to be no worse off?" by finding the
candidate base salary at which the candidate matches the current job on a
chosen metric.

This solver is the reason the `TaxModel` port exists. Varying base salary
invalidates a net-pay override by design — salary is in its locked set — so the
solver depends on the port and lets the override-backed model extrapolate from
its calibration point. Every result carries that model's name and how far the
answer sits from where it was measured, because an extrapolated answer should
not be presented with the same confidence as a computed one.

**On monotonicity.** Disposable cash is monotone increasing in base salary. The
Social Security wage base and the elective-deferral cap change the slope of the
curve but never its direction, and marginal rates below 100% guarantee take-home
rises throughout. Bisection is therefore sound and a general root finder is
unnecessary. The bracket and sampling guards below are cheap insurance against a
future modelling change breaking that assumption — not a fix for a defect that
exists today.

One modelling rule protects the assumption and is worth stating: **no cost may
be defined as a function of income.** Housing as a percentage of gross salary
would break monotonicity, and is not permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.engine import ComparisonEngine, default_calculators

#: How many points to sample when checking the curve really is monotone.
_MONOTONICITY_SAMPLES: Final = 8


@dataclass(frozen=True)
class SolverBounds:
    """The search range, and when to stop."""

    lower: Money
    upper: Money
    tolerance: Money
    max_iterations: int = 60

    def __post_init__(self) -> None:
        if self.upper <= self.lower:
            raise ValidationError(
                f"the upper bound must exceed the lower bound, got {self.lower} to {self.upper}"
            )
        if self.tolerance.amount <= 0:
            raise ValidationError(
                f"the convergence tolerance must be positive, got {self.tolerance}"
            )
        if self.max_iterations < 1:
            raise ValidationError("max_iterations must be at least 1")


@dataclass(frozen=True)
class EquivalentSalaryResult:
    """The salary that closes the gap, and how much to trust it."""

    equivalent_salary: Money
    target_metric: str
    residual: Money
    iterations: int
    converged: bool
    monotonicity_verified: bool
    tax_model_name: str
    calibration_distance: Percentage
    is_far_from_calibration: bool


def with_base_salary(context: CalculationContext, salary: Money) -> CalculationContext:
    """The same context at a different base salary.

    The tax model is deliberately carried over unchanged. It stays calibrated
    where it was measured, which is what makes the reported extrapolation
    distance meaningful instead of always zero.
    """
    compensation = replace(context.employment.compensation, base_salary=salary)
    employment = replace(context.employment, compensation=compensation)
    return replace(context, employment=employment)


def _first_year_cash(context: CalculationContext) -> Money:
    engine = ComparisonEngine(default_calculators())
    return engine.calculate(context).total_cash


def solve_equivalent_salary(
    *,
    current: CalculationContext,
    candidate: CalculationContext,
    bounds: SolverBounds,
) -> EquivalentSalaryResult:
    """Find the candidate salary matching the current job's first-year cash."""
    target = _first_year_cash(current)

    def gap(salary: Money) -> Money:
        return _first_year_cash(with_base_salary(candidate, salary)) - target

    low_gap = gap(bounds.lower)
    high_gap = gap(bounds.upper)

    # Bracket validation. If both ends fall on the same side of the target, no
    # salary in this range closes the gap, and saying so beats returning the
    # nearest endpoint as though it were an answer.
    if (low_gap.amount > 0) == (high_gap.amount > 0):
        raise ValidationError(
            f"no solution between {bounds.lower} and {bounds.upper}: the gap is "
            f"{low_gap} at the lower bound and {high_gap} at the upper, so the "
            f"target is not bracketed. Widen the range."
        )

    monotone = _verify_monotonic(gap, bounds)

    low, high = bounds.lower, bounds.upper
    iterations = 0
    midpoint = low

    while (high - low).amount > bounds.tolerance.amount:
        if iterations >= bounds.max_iterations:
            break
        iterations += 1
        midpoint = (low + high) / 2
        if (gap(midpoint).amount > 0) == (low_gap.amount > 0):
            low = midpoint
        else:
            high = midpoint

    solution = (low + high) / 2
    converged = (high - low).amount <= bounds.tolerance.amount

    taxed = candidate.tax_model.after_tax_cash(PeriodicAmount(solution, PeriodKind.ANNUAL))

    return EquivalentSalaryResult(
        equivalent_salary=solution,
        target_metric="first_year_disposable_cash",
        residual=gap(solution),
        iterations=iterations,
        converged=converged,
        monotonicity_verified=monotone,
        tax_model_name=taxed.model_name,
        calibration_distance=taxed.calibration_distance,
        is_far_from_calibration=taxed.is_far_from_calibration,
    )


def _verify_monotonic(gap: object, bounds: SolverBounds) -> bool:
    """Sample the bracket and confirm the curve only moves one way.

    Raises rather than returning False, because a non-monotone curve means
    bisection may converge on one of several crossings and report it as though
    it were unique. A wrong answer that looks right is worse than an error.
    """
    assert callable(gap)
    span = bounds.upper - bounds.lower
    samples = [
        gap(bounds.lower + span * Decimal(step) / _MONOTONICITY_SAMPLES).amount
        for step in range(_MONOTONICITY_SAMPLES + 1)
    ]

    rising = all(later >= earlier for earlier, later in pairwise(samples))
    falling = all(later <= earlier for earlier, later in pairwise(samples))

    if not (rising or falling):
        raise ValidationError(
            "the metric is not monotone in base salary across this range, so a "
            "bisection result would not be a unique solution. Check for a cost "
            "defined as a function of income."
        )
    return True
