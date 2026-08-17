"""Comparing two sides.

Runs the engine over both profiles on the same horizon and reports the
difference component by component, keeping cash, wealth, and time apart.

Nothing is recomputed here. The comparison reads two results that each already
proved they reconcile, so a delta can never be more trustworthy than the sides
it came from — and never less.

Deltas are always **candidate minus current**, so a positive number means the
move is better on that line. Stating the direction once, here, is what keeps
every downstream reader from having to guess.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import Final

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.engine import (
    CalculationResult,
    ComparisonEngine,
    default_calculators,
)
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.comparisons.pre_move import inherit_costs_until_move

_MONTHS_PER_YEAR: Final = 12


@dataclass(frozen=True)
class ComponentDelta:
    """One line of the breakdown, on both sides and the difference."""

    code: str
    label: str
    current_cash: Money
    candidate_cash: Money
    delta: Money


@dataclass(frozen=True)
class ComparisonResult:
    """Two calculated sides and everything that differs between them."""

    current: CalculationResult
    candidate: CalculationResult

    cash_delta: Money
    annualised_cash_delta: Money
    wealth_delta: Money
    time_delta_hours: Decimal

    component_deltas: tuple[ComponentDelta, ...]
    cumulative_cash_delta: tuple[Money, ...]

    def top_improvements(self, count: int) -> tuple[ComponentDelta, ...]:
        """The lines where the candidate wins by the most."""
        return tuple(
            component for component in self.component_deltas if component.delta.amount > 0
        )[:count]

    def top_regressions(self, count: int) -> tuple[ComponentDelta, ...]:
        """The lines where the candidate loses by the most."""
        return tuple(
            component for component in self.component_deltas if component.delta.amount < 0
        )[:count]


def compare(
    *,
    current: CalculationContext,
    candidate: CalculationContext,
    move_date: date | None = None,
) -> ComparisonResult:
    """Calculate both sides and report the difference.

    When `move_date` is given, the candidate inherits the current side's
    recurring costs up to that date. Without it, a candidate whose costs all
    begin at the move would spend the intervening months paying nothing at all —
    an artificial saving large enough to invert the answer.
    """
    if move_date is not None:
        candidate = replace(
            candidate,
            costs=inherit_costs_until_move(
                current=current.costs, candidate=candidate.costs, move_date=move_date
            ),
        )

    engine = ComparisonEngine(default_calculators())
    current_result = engine.calculate(current)
    candidate_result = engine.calculate(candidate)

    components = _component_deltas(current_result, candidate_result)

    annualised = Money.zero()
    for component in components:
        annualised = annualised + component.delta

    return ComparisonResult(
        current=current_result,
        candidate=candidate_result,
        cash_delta=candidate_result.total_cash - current_result.total_cash,
        annualised_cash_delta=annualised,
        wealth_delta=candidate_result.total_wealth - current_result.total_wealth,
        time_delta_hours=(candidate_result.total_time_hours - current_result.total_time_hours),
        component_deltas=components,
        cumulative_cash_delta=_cumulative_delta(current_result, candidate_result),
    )


def _annual_cash_by_code(result: CalculationResult) -> dict[str, tuple[str, Money]]:
    """Each component's contribution to a year of cash, keyed by code.

    Monthly figures are annualised so both sides are stated on the same basis;
    one-time amounts stand as they are, because an event does not recur.
    """
    totals: dict[str, tuple[str, Money]] = {}
    for impact in result.impacts:
        contribution = _annual_contribution(impact)
        label, running = totals.get(impact.code, (impact.label, Money.zero()))
        totals[impact.code] = (label, running + contribution)
    return totals


def _annual_contribution(impact: CostImpact) -> Money:
    if impact.period is PeriodKind.MONTHLY:
        return impact.cash_amount * _MONTHS_PER_YEAR
    if impact.period is PeriodKind.HORIZON_CUMULATIVE:
        return Money.zero(impact.cash_amount.currency)
    return impact.cash_amount


def _component_deltas(
    current: CalculationResult, candidate: CalculationResult
) -> tuple[ComponentDelta, ...]:
    left = _annual_cash_by_code(current)
    right = _annual_cash_by_code(candidate)

    deltas: list[ComponentDelta] = []
    # A component present on only one side is still reported, with zero on the
    # other. Dropping it would hide exactly the lines a move introduces or
    # removes — relocation costs, a signing bonus, a commute that vanishes.
    for code in sorted(set(left) | set(right)):
        label, current_cash = left.get(code, ("", Money.zero()))
        candidate_label, candidate_cash = right.get(code, ("", Money.zero()))
        deltas.append(
            ComponentDelta(
                code=code,
                label=label or candidate_label,
                current_cash=current_cash,
                candidate_cash=candidate_cash,
                delta=candidate_cash - current_cash,
            )
        )

    # Biggest movers first: the sensitivity list is read top-down.
    deltas.sort(key=lambda component: abs(component.delta.amount), reverse=True)
    return tuple(deltas)


def _cumulative_delta(
    current: CalculationResult, candidate: CalculationResult
) -> tuple[Money, ...]:
    """Month-by-month cumulative difference in closing cash.

    This is the series the break-even solver reads: the month it first crosses
    zero, and the month after which it stays there.
    """
    return tuple(
        candidate_month.closing_cash - current_month.closing_cash
        for current_month, candidate_month in zip(current.months, candidate.months, strict=True)
    )
