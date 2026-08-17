"""Compensation: salary, bonuses, and equity.

The correction this module locks in: **equity vests as ordinary income and is
taxed on vest.** Counting a gross grant as wealth overstates every offer
containing equity, which is exactly the case where a comparison most needs to be
right. `net_vested_by` is the figure that belongs in the wealth track.

The withholding rate is a user-supplied estimate. Phase 1 does not build an
equity tax engine, for the same reason it does not build an income tax engine —
an explicit approximation the user can see beats a hidden one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PayFrequency


@dataclass(frozen=True)
class TargetBonus:
    """An annual bonus expressed as a share of base salary."""

    rate: Percentage
    probability: Percentage

    def __post_init__(self) -> None:
        if not Decimal(0) <= self.probability.as_fraction() <= Decimal(1):
            raise ValidationError(
                f"a bonus probability must be between 0% and 100%, got {self.probability}"
            )

    def expected_value(self, base_salary: Money) -> Money:
        """Target amount discounted by how often it actually pays.

        Recording the probability separately keeps an aspirational target from
        being compared against a guaranteed salary as though they were equal.
        """
        return self.probability.of(self.rate.of(base_salary))


@dataclass(frozen=True)
class SigningBonus:
    """An up-front payment, usually repayable if you leave early."""

    amount: Money
    repayment_months: int = 0

    def __post_init__(self) -> None:
        if self.amount.amount < 0:
            raise ValidationError(f"a signing bonus cannot be negative, got {self.amount}")
        if self.repayment_months < 0:
            raise ValidationError("repayment_months cannot be negative")

    def is_at_risk(self, month: int) -> bool:
        """Whether leaving in this month would trigger repayment.

        Affects the break-even picture rather than the headline figure: the cash
        arrives either way, but it is not yet safely yours.
        """
        return month <= self.repayment_months


@dataclass(frozen=True)
class EquityGrant:
    """An equity grant with a vesting schedule and a cliff."""

    total_value: Money
    vesting_months: int
    cliff_months: int
    estimated_withholding_rate: Percentage

    def __post_init__(self) -> None:
        if self.vesting_months < 1:
            raise ValidationError(
                f"a grant must vest over at least one month, got {self.vesting_months}"
            )
        if self.cliff_months < 0:
            raise ValidationError("cliff_months cannot be negative")
        if self.cliff_months > self.vesting_months:
            raise ValidationError(
                f"a cliff cannot outlast the vesting schedule: {self.cliff_months} "
                f"months of cliff against {self.vesting_months} months of vesting"
            )
        if not Decimal(0) <= self.estimated_withholding_rate.as_fraction() <= Decimal(1):
            raise ValidationError(
                f"a withholding rate must be between 0% and 100%, got "
                f"{self.estimated_withholding_rate}"
            )

    def gross_vested_by(self, month: int) -> Money:
        """Grant value vested by the end of `month`, before tax."""
        if month < 0:
            raise ValidationError(f"month cannot be negative, got {month}")
        if month < self.cliff_months:
            return Money.zero(self.total_value.currency)

        # Multiply before dividing. Scaling by a precomputed ratio would lose
        # precision, since a term like 1/48 does not terminate in decimal.
        elapsed = min(month, self.vesting_months)
        return self.total_value * elapsed / self.vesting_months

    def net_vested_by(self, month: int) -> Money:
        """Vested value after estimated withholding — the wealth-track figure.

        No rounding here: the remainder survives to be quantised once at a
        display boundary, so a rate applied across many vest events cannot
        compound an error.
        """
        gross = self.gross_vested_by(month)
        return gross - self.estimated_withholding_rate.of(gross)


@dataclass(frozen=True)
class Compensation:
    """Everything an offer pays, cash and equity alike."""

    base_salary: Money
    pay_frequency: PayFrequency
    signing_bonus: SigningBonus | None = None
    target_bonus: TargetBonus | None = None
    equity: EquityGrant | None = None
    relocation_reimbursement: Money | None = None
    expected_annual_raise: Percentage = field(default_factory=Percentage.zero)

    def __post_init__(self) -> None:
        if self.base_salary.amount < 0:
            raise ValidationError(f"base salary cannot be negative, got {self.base_salary}")

    def expected_first_year_cash(self) -> Money:
        """Base plus expected bonuses. Equity is excluded by design.

        Equity is wealth, not cash, and mixing the two is how a comparison
        collapses into a single misleading number.
        """
        total = self.base_salary
        if self.signing_bonus is not None:
            total = total + self.signing_bonus.amount
        if self.target_bonus is not None:
            total = total + self.target_bonus.expected_value(self.base_salary)
        return total
