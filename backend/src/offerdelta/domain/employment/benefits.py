"""Benefits: health cost, retirement match, and paid time off.

The employer match carries the real logic. "100% of the first 4%" means the
employer matches every dollar contributed until the *employee's* contribution
reaches 4% of salary. Contributing 6% therefore earns the same match as
contributing 4%, and contributing 2% earns only 2%.

Getting this wrong in either direction misprices an offer by thousands a year,
and it is the benefit people most often compare by eye.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind

_RATE_PERIODS: Final = frozenset({PeriodKind.MONTHLY, PeriodKind.ANNUAL})


@dataclass(frozen=True)
class RetirementMatch:
    """An employer 401(k) match and its vesting schedule."""

    match_rate: Percentage
    match_limit_rate: Percentage
    vesting_months: int

    def __post_init__(self) -> None:
        for label, rate in (
            ("match_rate", self.match_rate),
            ("match_limit_rate", self.match_limit_rate),
        ):
            if not Decimal(0) <= rate.as_fraction() <= Decimal(1):
                raise ValidationError(f"{label} must be between 0% and 100%, got {rate}")
        if self.vesting_months < 0:
            raise ValidationError("vesting_months cannot be negative")

    def annual_match(self, base_salary: Money, employee_rate: Percentage) -> Money:
        """Employer money earned in a year at this contribution rate.

        The cap applies to the employee's contribution, not to the match, which
        is the distinction that makes "100% of the first 4%" mean 4% of salary
        rather than 4% of the contribution.
        """
        matched_rate = min(employee_rate.as_fraction(), self.match_limit_rate.as_fraction())
        matched = Percentage(matched_rate).of(base_salary)
        return self.match_rate.of(matched)

    def vested_fraction(self, month: int) -> Decimal:
        """How much of the accrued match is actually keepable at this month."""
        if month < 0:
            raise ValidationError(f"month cannot be negative, got {month}")
        if self.vesting_months == 0:
            return Decimal(1)
        return min(Decimal(month) / Decimal(self.vesting_months), Decimal(1))

    def vested_match(self, base_salary: Money, employee_rate: Percentage, month: int) -> Money:
        """The part of the match that survives leaving in this month.

        Unvested employer money is reported separately from wealth, because
        leaving early forfeits it.
        """
        return self.annual_match(base_salary, employee_rate) * self.vested_fraction(month)


@dataclass(frozen=True)
class Benefits:
    """Everything an employer provides beyond cash compensation."""

    employee_health_premium: PeriodicAmount
    employer_hsa_contribution: PeriodicAmount
    retirement_match: RetirementMatch
    employee_contribution_rate: Percentage

    #: The employee's own HSA/FSA contribution. Kept separate from the employer
    #: contribution above because only this one reduces the employee's taxable
    #: pay — conflating them would misstate the override basis and the wealth
    #: track at once.
    employee_hsa_fsa_contribution: PeriodicAmount = field(
        default_factory=lambda: PeriodicAmount(Money.zero(), PeriodKind.ANNUAL)
    )
    pto_days: int = 0
    paid_holidays: int = 0

    def __post_init__(self) -> None:
        for label, amount in (
            ("employee_health_premium", self.employee_health_premium),
            ("employer_hsa_contribution", self.employer_hsa_contribution),
            ("employee_hsa_fsa_contribution", self.employee_hsa_fsa_contribution),
        ):
            if amount.period not in _RATE_PERIODS:
                raise ValidationError(
                    f"{label} must describe a rate (monthly or annual), got {amount.period}"
                )
            if amount.money.amount < 0:
                raise ValidationError(f"{label} cannot be negative, got {amount.money}")

        if self.pto_days < 0:
            raise ValidationError("pto_days cannot be negative")
        if self.paid_holidays < 0:
            raise ValidationError("paid_holidays cannot be negative")

    def annual_health_premium(self) -> Money:
        return self.employee_health_premium.to_annual().money

    def annual_employer_match(self, base_salary: Money) -> Money:
        return self.retirement_match.annual_match(base_salary, self.employee_contribution_rate)

    @property
    def total_paid_days_off(self) -> int:
        return self.pto_days + self.paid_holidays
