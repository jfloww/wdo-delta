"""The tax model port.

Solvers and calculators depend on this, never on a concrete tax implementation.
That indirection is what lets phase 1 run on a verified net-pay figure while
phase 2 swaps in real brackets without touching a single caller.

Every result names the model that produced it, because "your take-home is
$4,820" means something different depending on whether it came from computed
brackets or from extrapolating a single paystub.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount


@dataclass(frozen=True)
class TaxBreakdown:
    """Where the tax went, when a model can say."""

    federal: Money
    state: Money
    local: Money
    payroll: Money


@dataclass(frozen=True)
class TaxResult:
    """After-tax cash, plus everything needed to judge how much to trust it."""

    after_tax: PeriodicAmount
    model_name: str
    is_extrapolated: bool
    calibration_distance: Percentage
    is_far_from_calibration: bool

    #: None when the model cannot decompose the figure — an override knows the
    #: total but not its parts, and inventing the parts would be worse than
    #: admitting it.
    breakdown: TaxBreakdown | None = None


class TaxModel(Protocol):
    """Converts gross compensation to after-tax cash."""

    @property
    def name(self) -> str: ...

    def after_tax_cash(self, gross: PeriodicAmount) -> TaxResult: ...

    def after_tax_one_time(self, amount: Money) -> Money:
        """Tax a single event such as a signing bonus.

        Separate from `after_tax_cash` because a one-time payment is not a rate:
        annualising it to find a bracket would be wrong, and in practice US
        supplemental wages are withheld at their own flat rate rather than at
        the recipient's marginal rate. Phase 1 approximates with the marginal
        rate and says so; phase 2 applies the real supplemental rule.
        """
        ...
