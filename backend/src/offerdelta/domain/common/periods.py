"""Periods, pay frequency, and the one place amounts are normalised.

Every amount entering or leaving the engine carries its period. A monthly
figure summed as though it were annual is off by a factor of twelve and looks
entirely plausible on a screen, so the period travels with the value rather
than living in a variable name or a caller's memory.

All period conversion happens here. Nothing else in the domain divides by
twelve.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from offerdelta.domain.common.money import Money

_MONTHS_PER_YEAR = 12


class PeriodKind(StrEnum):
    """What span an amount describes."""

    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"

    #: A single event, such as a signing bonus or a security deposit. Not a
    #: rate, and therefore never annualised.
    ONE_TIME = "ONE_TIME"

    #: A total already accumulated over a comparison horizon. Also not a rate.
    HORIZON_CUMULATIVE = "HORIZON_CUMULATIVE"


class PayFrequency(StrEnum):
    """How often a paycheck arrives.

    Biweekly and semimonthly are the pair that causes real errors: 26 paychecks
    a year versus 24. Reading a biweekly paycheck as semimonthly drops two
    periods and understates annual pay by about 7.7%.
    """

    WEEKLY = "WEEKLY"
    BIWEEKLY = "BIWEEKLY"
    SEMIMONTHLY = "SEMIMONTHLY"
    MONTHLY = "MONTHLY"

    @property
    def periods_per_year(self) -> int:
        return _PERIODS_PER_YEAR[self]


_PERIODS_PER_YEAR: dict[PayFrequency, int] = {
    PayFrequency.WEEKLY: 52,
    PayFrequency.BIWEEKLY: 26,
    PayFrequency.SEMIMONTHLY: 24,
    PayFrequency.MONTHLY: 12,
}

#: Periods that describe a rate and can therefore be converted between spans.
_CONVERTIBLE = frozenset({PeriodKind.MONTHLY, PeriodKind.ANNUAL})


@dataclass(frozen=True)
class PeriodicAmount:
    """A monetary amount together with the span it describes."""

    money: Money
    period: PeriodKind

    def _require_convertible(self) -> None:
        if self.period not in _CONVERTIBLE:
            raise ValueError(
                f"a {self.period} amount describes an event or an accumulated "
                f"total, not a rate, so it cannot be converted between periods"
            )

    def to_annual(self) -> PeriodicAmount:
        self._require_convertible()
        if self.period is PeriodKind.ANNUAL:
            return self
        return PeriodicAmount(self.money * _MONTHS_PER_YEAR, PeriodKind.ANNUAL)

    def to_monthly(self) -> PeriodicAmount:
        self._require_convertible()
        if self.period is PeriodKind.MONTHLY:
            return self
        # Division does not generally terminate. The remainder is kept at
        # Decimal context precision and quantised once, at a display or
        # persistence boundary, so the error cannot compound across months.
        monthly = Money(self.money.amount / Decimal(_MONTHS_PER_YEAR), self.money.currency)
        return PeriodicAmount(monthly, PeriodKind.MONTHLY)

    @classmethod
    def from_paycheck(cls, paycheck: Money, frequency: PayFrequency) -> PeriodicAmount:
        """Annualise a single paycheck using its true period count."""
        return cls(paycheck * frequency.periods_per_year, PeriodKind.ANNUAL)

    def __add__(self, other: PeriodicAmount) -> PeriodicAmount:
        if self.period is not other.period:
            raise ValueError(
                f"cannot add amounts of different periods: {self.period} and {other.period}"
            )
        return PeriodicAmount(self.money + other.money, self.period)

    def __sub__(self, other: PeriodicAmount) -> PeriodicAmount:
        if self.period is not other.period:
            raise ValueError(
                f"cannot subtract amounts of different periods: {self.period} and {other.period}"
            )
        return PeriodicAmount(self.money - other.money, self.period)

    def __str__(self) -> str:
        return f"{self.money} / {self.period}"
