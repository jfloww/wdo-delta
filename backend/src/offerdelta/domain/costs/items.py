"""Cost items and cost profiles.

A cost item carries everything needed to route it to exactly one calculator and
to explain it afterwards: its category, the period its amount describes, which
track it affects, when it applies, and where the figure came from.

Two deliberate choices:

- The owning calculator is **derived** from the category rather than stored, so
  the two cannot drift apart.
- Amounts are **positive magnitudes**. Sign is applied by the calculator, which
  knows whether a category reduces or increases cash. An item carrying its own
  negative sign would be subtracted twice by a calculator that also negates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.costs.categories import (
    CalculatorName,
    CashFlowType,
    CostCategory,
    owner_of,
)

_RATE_PERIODS = frozenset({PeriodKind.MONTHLY, PeriodKind.ANNUAL})


@dataclass(frozen=True)
class CostItem:
    """One cost, owned by exactly one calculator."""

    category: CostCategory
    amount: PeriodicAmount
    cash_flow_type: CashFlowType
    effective_date: date
    evidence: Evidence

    def __post_init__(self) -> None:
        if self.amount.money.amount < 0:
            raise ValidationError(
                f"a cost item holds a positive magnitude; sign is applied by the "
                f"owning calculator. {self.category} was given {self.amount.money}"
            )

        if self.cash_flow_type is CashFlowType.TIME:
            raise ValidationError(
                "TIME impacts are produced by calculators, not entered as cost "
                "items; a cost item's amount is money"
            )

        if self.cash_flow_type is CashFlowType.RECURRING_CASH and (
            self.amount.period not in _RATE_PERIODS
        ):
            raise ValidationError(
                f"a RECURRING_CASH item must describe a rate (monthly or annual), "
                f"got {self.amount.period}"
            )

        if (
            self.cash_flow_type is CashFlowType.ONE_TIME_CASH
            and self.amount.period is not PeriodKind.ONE_TIME
        ):
            raise ValidationError(
                f"a ONE_TIME_CASH item must have period ONE_TIME, got "
                f"{self.amount.period}; annualising a deposit is the error this "
                f"prevents"
            )

        if self.category.value.startswith("RELOCATION_") and (
            self.cash_flow_type is not CashFlowType.ONE_TIME_CASH
        ):
            raise ValidationError(
                f"relocation costs are one-time events, not rates; {self.category} "
                f"was given {self.cash_flow_type}"
            )

    @property
    def owner_calculator(self) -> CalculatorName:
        """The single calculator permitted to consume this item."""
        return owner_of(self.category)


@dataclass(frozen=True)
class CostProfile:
    """Every cost belonging to one side of a comparison."""

    items: tuple[CostItem, ...] = ()

    def items_for(self, calculator: CalculatorName) -> tuple[CostItem, ...]:
        """The items this calculator owns, and only those.

        Because ownership is derived from a category that has exactly one owner,
        routing every calculator's items reproduces the profile with nothing
        lost and nothing duplicated.
        """
        return tuple(item for item in self.items if item.owner_calculator is calculator)

    def has_assumptions(self) -> bool:
        """Whether any figure here is an assumption rather than confirmed data."""
        return any(item.evidence is Evidence.ASSUMED for item in self.items)
