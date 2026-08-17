"""The comparison engine.

Composes calculators, projects their impacts onto months, and refuses to return
a result that does not balance.

Two checks run automatically and neither is optional:

- At assembly, the cost calculators must partition every category. A gap
  silently drops a cost from the total; an overlap subtracts it twice. Both are
  invisible in the output, which is why they are checked rather than trusted.
- Before returning, every projected month must reconcile. If it does not, the
  model is wrong and the engine raises rather than reporting a number nobody
  should act on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext, ComponentCalculator
from offerdelta.domain.comparisons.cost_calculators import default_cost_calculators
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.comparisons.income_calculators import default_income_calculators
from offerdelta.domain.comparisons.reconciliation import CashBucket, classify, reconcile
from offerdelta.domain.costs.categories import assert_categories_partitioned

_MONTHS_PER_YEAR: Final = 12


def default_calculators() -> tuple[ComponentCalculator, ...]:
    """Every calculator, cost-consuming and profile-derived alike."""
    return (*default_cost_calculators(), *default_income_calculators())


@dataclass(frozen=True)
class MonthlyProjection:
    """One month of the cash projection, decomposed for the reconciliation check."""

    month_index: int
    month_start: date
    opening_cash: Money
    income: Money
    spending: Money
    one_time_net: Money
    closing_cash: Money
    residual: Money


@dataclass(frozen=True)
class CalculationResult:
    """Everything one side of a comparison produced."""

    impacts: tuple[CostImpact, ...]
    months: tuple[MonthlyProjection, ...]
    total_cash: Money
    total_wealth: Money
    total_time_hours: Decimal


@dataclass(frozen=True)
class ComparisonEngine:
    """Runs a set of calculators over one side of a comparison."""

    calculators: Sequence[ComponentCalculator]

    def __post_init__(self) -> None:
        # Only cost-consuming calculators take part. Income and benefit
        # calculators derive their figures from the profile and own nothing.
        claimed = [
            calculator.owned_categories()
            for calculator in self.calculators
            if calculator.owned_categories()
        ]
        assert_categories_partitioned(claimed)

    def calculate(self, context: CalculationContext) -> CalculationResult:
        impacts = tuple(
            impact for calculator in self.calculators for impact in calculator.calculate(context)
        )

        months = self._project(impacts, context)

        for month in months:
            if not month.residual.is_zero():
                raise ValidationError(
                    f"month {month.month_index} ({month.month_start}) does not "
                    f"reconcile: residual {month.residual}. An impact was counted "
                    f"twice, dropped, or misclassified."
                )

        total_wealth = Money.zero()
        total_time = Decimal(0)
        for impact in impacts:
            total_wealth = total_wealth + impact.wealth_amount
            total_time = total_time + impact.time_hours

        return CalculationResult(
            impacts=impacts,
            months=months,
            total_cash=months[-1].closing_cash if months else Money.zero(),
            total_wealth=total_wealth,
            total_time_hours=total_time,
        )

    def _project(
        self, impacts: tuple[CostImpact, ...], context: CalculationContext
    ) -> tuple[MonthlyProjection, ...]:
        """Spread impacts across the horizon month by month.

        Annual cash is divided evenly rather than landing in the month it is
        actually paid. That is a deliberate phase-1 simplification: bonus timing
        changes the break-even month but not the annual total, and modelling it
        properly needs a payment schedule the profile does not yet carry.
        """
        months: list[MonthlyProjection] = []
        opening = Money.zero()

        for index, month_start in enumerate(context.horizon.months()):
            active = tuple(self._for_month(impacts, month_start, index))

            income = Money.zero()
            spending = Money.zero()
            one_time = Money.zero()
            for impact in active:
                bucket = classify(impact)
                amount = self._monthly_amount(impact)
                if bucket is CashBucket.INCOME:
                    income = income + amount
                elif bucket is CashBucket.SPENDING:
                    spending = spending + amount
                elif bucket is CashBucket.ONE_TIME:
                    one_time = one_time + amount

            net = income + spending + one_time
            closing = opening + net

            # Computed independently of the buckets above, so a double-counted
            # or dropped impact makes the two disagree.
            stated = Money.zero()
            for impact in active:
                if classify(impact) is not CashBucket.NOT_CASH:
                    stated = stated + self._monthly_amount(impact)

            months.append(
                MonthlyProjection(
                    month_index=index,
                    month_start=month_start,
                    opening_cash=opening,
                    income=income,
                    spending=spending,
                    one_time_net=one_time,
                    closing_cash=closing,
                    residual=reconcile(
                        tuple(
                            self._as_monthly(impact)
                            for impact in active
                            if classify(impact) is not CashBucket.NOT_CASH
                        ),
                        stated,
                    ),
                )
            )
            opening = closing

        return tuple(months)

    @staticmethod
    def _for_month(
        impacts: tuple[CostImpact, ...], month_start: date, index: int
    ) -> list[CostImpact]:
        active: list[CostImpact] = []
        for impact in impacts:
            if impact.period is PeriodKind.HORIZON_CUMULATIVE:
                continue  # wealth totals are not a monthly cash flow
            if impact.ends_before is not None and month_start >= impact.ends_before:
                # An inherited cost stops the month the replacement starts,
                # otherwise both would be charged after the move.
                continue
            if impact.period is PeriodKind.ONE_TIME:
                # Lands in the month containing its effective date, or the first
                # month if it predates the horizon.
                same_month = (
                    impact.effective_date.year == month_start.year
                    and impact.effective_date.month == month_start.month
                )
                if same_month or (index == 0 and impact.effective_date < month_start):
                    active.append(impact)
                continue
            if impact.effective_date <= month_start or index == 0:
                active.append(impact)
        return active

    @staticmethod
    def _monthly_amount(impact: CostImpact) -> Money:
        if impact.period is PeriodKind.ANNUAL:
            return impact.cash_amount / _MONTHS_PER_YEAR
        return impact.cash_amount

    @classmethod
    def _as_monthly(cls, impact: CostImpact) -> CostImpact:
        """The impact with its amount already reduced to this month's share."""
        if impact.period is not PeriodKind.ANNUAL:
            return impact
        return CostImpact(
            code=impact.code,
            label=impact.label,
            category=impact.category,
            produced_by=impact.produced_by,
            period=PeriodKind.MONTHLY,
            effective_date=impact.effective_date,
            formula_id=impact.formula_id,
            evidence=impact.evidence,
            cash_amount=cls._monthly_amount(impact),
            wealth_amount=impact.wealth_amount,
            time_hours=impact.time_hours,
            ends_before=impact.ends_before,
            inputs=impact.inputs,
            rounding_policy=impact.rounding_policy,
            rule_version=impact.rule_version,
            dataset_version=impact.dataset_version,
            assumption=impact.assumption,
        )
