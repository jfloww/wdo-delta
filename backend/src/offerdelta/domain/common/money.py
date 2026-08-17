"""Monetary amounts.

Money is deliberately strict at construction. A float that reaches a monetary
value is a silent, compounding error, and no downstream test reliably catches
it, so the type refuses one outright rather than trusting callers.

Rounding is *not* a property of Money. Different rules round differently — IRS
whole-dollar, payroll to the cent, largest-remainder allocation — so the policy
is applied explicitly at boundaries via `offerdelta.domain.common.rounding`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from offerdelta.domain.common.rounding import ALLOCATION_PLACES, RoundingPolicy

_ISO_4217_LENGTH: Final = 3


@dataclass(frozen=True)
class Money:
    """An exact monetary amount in a single currency."""

    amount: Decimal
    currency: str = "USD"

    def __post_init__(self) -> None:
        # A frozen dataclass performs no runtime type checking, so without this
        # guard `Money(1234.56)` would construct happily and stay wrong forever.
        #
        # mypy reports the float branches below as unreachable, and for callers
        # it can see, they are — which is the point: the annotation rejects a
        # float statically. The runtime guard catches everything the checker
        # never sees, notably JSON payloads and values built dynamically.
        if isinstance(self.amount, float):  # type: ignore[unreachable]
            raise TypeError(
                "Money rejects float because binary floating point cannot represent "
                "decimal currency exactly; pass a Decimal or use Money.parse()"
            )
        if not isinstance(self.amount, Decimal):
            raise TypeError(f"Money.amount must be Decimal, got {type(self.amount).__name__}")
        if not self.amount.is_finite():
            raise ValueError(f"Money requires a finite amount, got {self.amount}")
        if (
            len(self.currency) != _ISO_4217_LENGTH
            or not self.currency.isalpha()
            or not self.currency.isupper()
        ):
            raise ValueError(
                f"currency must be an ISO 4217 code such as 'USD', got {self.currency!r}"
            )

    @classmethod
    def parse(cls, value: str, currency: str = "USD") -> Money:
        """Build Money from a decimal string.

        The only supported way to construct Money from external input. Note that
        `Decimal(0.1)` is 0.1000000000000000055511151231257827021181583404541015625,
        so routing a float through here would reintroduce the exact defect this
        type exists to prevent.
        """
        if isinstance(value, float):  # type: ignore[unreachable]
            raise TypeError("Money.parse rejects float; pass a decimal string such as '0.10'")
        if not isinstance(value, str):
            raise TypeError(f"Money.parse expects a string, got {type(value).__name__}")
        try:
            amount = Decimal(value)
        except InvalidOperation:
            raise ValueError(f"{value!r} is not a valid decimal") from None
        return cls(amount, currency)

    @classmethod
    def zero(cls, currency: str = "USD") -> Money:
        """The additive identity, for seeding accumulations."""
        return cls(Decimal("0"), currency)

    def is_zero(self) -> bool:
        return self.amount == 0

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"cannot combine different currency values: {self.currency} and {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._same_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def __abs__(self) -> Money:
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        """Scale by a dimensionless quantity, such as months or a percentage factor.

        Deliberately no rounding: twelve months of rent keeps full precision and
        is quantised once, at the boundary where it is displayed or persisted.
        """
        if isinstance(factor, float):
            raise TypeError("Money cannot be scaled by a float; use Decimal for exact factors")
        if isinstance(factor, Money):  # type: ignore[unreachable]
            raise TypeError("multiplying Money by Money has no meaning in this domain")
        if not isinstance(factor, Decimal | int):
            raise TypeError(
                f"Money can only be scaled by Decimal or int, got {type(factor).__name__}"
            )
        return Money(self.amount * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._same_currency(other)
        return self.amount >= other.amount

    def quantize(self, policy: RoundingPolicy) -> Money:
        """Reduce to a presentable scale under an explicit, named policy.

        The only place rounding is allowed to happen. Arithmetic keeps full
        precision so that quantising once at a boundary cannot compound.
        """
        return Money(policy.quantize(self.amount), self.currency)

    def allocate(
        self, weights: Sequence[Decimal | int], places: int = ALLOCATION_PLACES
    ) -> list[Money]:
        """Split this amount across weighted shares without losing a unit.

        Uses largest remainder: every share is floored to the quantum, then the
        leftover units go to the shares that discarded the largest fraction,
        earliest first on a tie. The shares always sum back to exactly this
        amount, which is what keeps household splitting from silently breaking
        the monthly reconciliation invariant.

        Raises if this amount cannot be expressed exactly at `places`, rather
        than quantising it silently inside an operation whose entire purpose is
        exactness.
        """
        integral_weights = _validate_weights(weights)
        total_weight = sum(integral_weights)
        if total_weight <= 0:
            raise ValueError("allocation weights must sum to a positive value")

        quantum = Decimal(1).scaleb(-places)
        scaled = abs(self.amount) / quantum
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"cannot allocate {self} exactly at {places} decimal places; "
                f"quantise it with a RoundingPolicy first"
            )

        total_units = int(scaled)
        sign = -1 if self.amount < 0 else 1

        # Integer arithmetic throughout: no division, so no precision loss can
        # perturb the tie-breaking order.
        numerators = [total_units * weight for weight in integral_weights]
        units = [numerator // total_weight for numerator in numerators]
        remainders = [
            numerator - unit * total_weight
            for numerator, unit in zip(numerators, units, strict=True)
        ]

        leftover = total_units - sum(units)
        by_largest_remainder = sorted(range(len(units)), key=lambda i: (-remainders[i], i))
        for index in by_largest_remainder[:leftover]:
            units[index] += 1

        return [Money(sign * unit * quantum, self.currency) for unit in units]

    def __str__(self) -> str:
        return f"{self.amount} {self.currency}"


def _validate_weights(weights: Sequence[Decimal | int]) -> list[int]:
    """Normalise allocation weights to exact integers.

    Decimal weights such as 2.5 and 7.5 are scaled to 25 and 75 so the whole
    allocation can run in integer arithmetic.
    """
    if not weights:
        raise ValueError("allocate requires at least one weight")

    decimals: list[Decimal] = []
    for weight in weights:
        if isinstance(weight, float):
            raise TypeError("allocation weights reject float; use Decimal or int for exact ratios")
        if not isinstance(weight, Decimal | int):
            raise TypeError(
                f"allocation weights must be Decimal or int, got {type(weight).__name__}"
            )
        as_decimal = Decimal(weight)
        if not as_decimal.is_finite():
            raise ValueError(f"allocation weights must be finite, got {weight}")
        if as_decimal < 0:
            raise ValueError(f"allocation weights cannot be negative, got {weight}")
        decimals.append(as_decimal)

    max_places = max(max(-value.as_tuple().exponent, 0) for value in decimals)  # type: ignore[operator]
    scale = Decimal(10) ** max_places
    return [int(value * scale) for value in decimals]
