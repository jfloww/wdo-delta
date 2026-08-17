"""Household cost splitting.

Where `Money.allocate` earns its keep. A household splits rent, utilities, and
internet every month for the whole horizon, so a naive division that loses a
cent per split loses it 36 times across a three-year comparison — and the
monthly reconciliation invariant then fails for a reason that has nothing to do
with the model being wrong.

Every split here goes through `allocate`, so the user's share and everyone
else's always sum back to exactly the original cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money

_PERCENT_TOTAL = Decimal(100)


class SplitMethod(StrEnum):
    """How a shared cost divides."""

    #: Equal shares across the household.
    EVEN = "EVEN"

    #: The user covers a stated percentage.
    PERCENTAGE = "PERCENTAGE"

    #: The user covers a stated absolute amount, whatever the bill.
    FIXED = "FIXED"


@dataclass(frozen=True)
class HouseholdProfile:
    """Who shares the costs, and in what proportion."""

    size: int
    method: SplitMethod
    user_weight: Decimal | None = None
    user_fixed_amount: Money | None = None

    def __post_init__(self) -> None:
        if self.size < 1:
            raise ValidationError(f"a household has at least one member, got {self.size}")

        if self.size == 1:
            return

        if self.method is SplitMethod.PERCENTAGE:
            if self.user_weight is None:
                raise ValidationError("a PERCENTAGE split requires user_weight")
            if not Decimal(0) <= self.user_weight <= _PERCENT_TOTAL:
                raise ValidationError(
                    f"user_weight must be between 0 and 100, got {self.user_weight}"
                )

        if self.method is SplitMethod.FIXED and self.user_fixed_amount is None:
            raise ValidationError("a FIXED split requires user_fixed_amount")

    @classmethod
    def solo(cls) -> HouseholdProfile:
        return cls(size=1, method=SplitMethod.EVEN)

    @classmethod
    def even(cls, size: int) -> HouseholdProfile:
        return cls(size=size, method=SplitMethod.EVEN)

    def share_of(self, cost: Money) -> Money:
        """The portion of a shared cost this user bears."""
        if self.size == 1:
            return cost

        if self.method is SplitMethod.FIXED:
            assert self.user_fixed_amount is not None  # guaranteed by __post_init__
            # Paying more than the whole bill is a data error, not generosity.
            return min(self.user_fixed_amount, cost)

        if self.method is SplitMethod.PERCENTAGE:
            assert self.user_weight is not None  # guaranteed by __post_init__
            others = _PERCENT_TOTAL - self.user_weight
            return cost.allocate([self.user_weight, others])[0]

        # EVEN: the user is the first share, so largest-remainder gives them any
        # leftover cent and the household total still reconciles exactly.
        return cost.allocate([1] * self.size)[0]

    def others_share_of(self, cost: Money) -> Money:
        """Everything the rest of the household bears.

        Defined as the remainder rather than computed independently, so the two
        shares cannot fail to sum to the cost.
        """
        return cost - self.share_of(cost)
