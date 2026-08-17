"""A tax model backed by one verified net-pay observation.

Resolves the conflict in blueprint section 6.8.2. `base_salary` sits in the
override's locked set, so an override goes stale the moment salary changes — but
the equivalent-salary solver works precisely by varying salary. Taken literally,
the mechanism meant to defer the tax engine also disables the headline solver.

The resolution is indirection: solvers depend on the `TaxModel` port. This
implementation calibrates an effective rate at the observed point and
extrapolates linearly using a user-supplied marginal rate.

It is deliberately explicit about being an approximation. Every result reports
how far the query sits from the calibration point, and flags when that distance
makes the answer untrustworthy. A single-point linear model is accurate near its
calibration and degrades away from it; saying so is the difference between a
useful estimate and a confident wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.taxes.ports import TaxResult

_RATE_PERIODS: Final = frozenset({PeriodKind.MONTHLY, PeriodKind.ANNUAL})

#: Beyond this distance from the calibration point, a single-point linear model
#: is extrapolating further than it can support and results say so.
_TRUST_RADIUS: Final = Decimal("0.20")

MODEL_NAME: Final = "NET_PAY_OVERRIDE"


@dataclass(frozen=True)
class NetPayOverrideTaxModel:
    """After-tax cash extrapolated from one verified (gross, net) observation."""

    observed_gross: PeriodicAmount
    observed_net: PeriodicAmount
    marginal_rate: Percentage

    def __post_init__(self) -> None:
        for label, amount in (
            ("observed_gross", self.observed_gross),
            ("observed_net", self.observed_net),
        ):
            if amount.period not in _RATE_PERIODS:
                raise ValidationError(
                    f"{label} must describe a rate (monthly or annual), got {amount.period}"
                )

        if self._annual_gross.amount <= 0:
            raise ValidationError(
                f"observed_gross must be positive to calibrate against, got "
                f"{self.observed_gross.money}"
            )
        if self._annual_net > self._annual_gross:
            raise ValidationError(
                f"observed net pay cannot exceed gross: {self._annual_net} against "
                f"{self._annual_gross}"
            )
        if not Decimal(0) <= self.marginal_rate.as_fraction() <= Decimal(1):
            raise ValidationError(
                f"a marginal rate must be between 0% and 100%, got {self.marginal_rate}"
            )

    @property
    def name(self) -> str:
        return MODEL_NAME

    @property
    def _annual_gross(self) -> Money:
        return self.observed_gross.to_annual().money

    @property
    def _annual_net(self) -> Money:
        return self.observed_net.to_annual().money

    @property
    def effective_rate(self) -> Percentage:
        """The overall rate implied at the calibration point."""
        kept = self._annual_net.amount / self._annual_gross.amount
        return Percentage(Decimal(1) - kept)

    def after_tax_cash(self, gross: PeriodicAmount) -> TaxResult:
        """Extrapolate take-home pay from the calibration point.

        Linear in gross: every dollar above or below the observed point keeps
        `1 - marginal_rate`. Exact at the calibration point by construction,
        which is what makes a verified paystub figure come back unchanged.
        """
        if gross.period not in _RATE_PERIODS:
            raise ValidationError(f"tax applies to a rate (monthly or annual), got {gross.period}")

        annual_gross = gross.to_annual().money
        difference = annual_gross - self._annual_gross
        kept_share = Decimal(1) - self.marginal_rate.as_fraction()
        annual_net = self._annual_net + difference * kept_share

        # Guard rails the linear form does not provide on its own: extrapolating
        # far enough in either direction would otherwise produce take-home above
        # gross or below zero.
        annual_net = min(annual_net, annual_gross)
        annual_net = max(annual_net, Money.zero(annual_gross.currency))

        distance = self._distance_from_calibration(annual_gross)
        after_tax = PeriodicAmount(annual_net, PeriodKind.ANNUAL)

        return TaxResult(
            after_tax=(after_tax.to_monthly() if gross.period is PeriodKind.MONTHLY else after_tax),
            model_name=MODEL_NAME,
            is_extrapolated=annual_gross != self._annual_gross,
            calibration_distance=distance,
            is_far_from_calibration=distance.as_fraction() > _TRUST_RADIUS,
            # An override knows the total but not its parts. Inventing a split
            # from an effective rate would look authoritative and be fiction.
            breakdown=None,
        )

    def after_tax_one_time(self, amount: Money) -> Money:
        """Tax a single event at the marginal rate.

        An approximation, and a knowingly imperfect one: US supplemental wages
        are withheld at a flat statutory rate rather than at the recipient's
        marginal rate. Phase 2 applies the real rule. Using the marginal rate
        here is closer than ignoring tax entirely, which would overstate every
        signing bonus by a third.
        """
        return amount - self.marginal_rate.of(amount)

    def _distance_from_calibration(self, annual_gross: Money) -> Percentage:
        gap = abs((annual_gross - self._annual_gross).amount)
        return Percentage(gap / self._annual_gross.amount)
