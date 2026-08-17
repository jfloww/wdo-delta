"""Percentages and rates.

The recurring bug with percentages is that 7.5 and 0.075 are the same rate
written two ways. A value handed between two functions that disagree about the
form is wrong by a factor of a hundred and still looks plausible on a screen.

The internal representation is always the fraction, and the named constructors
force a caller to say which form they are holding.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from offerdelta.domain.common.errors import TypeConstraintError, ValidationError
from offerdelta.domain.common.money import Money

_PERCENT_DIVISOR: Final = Decimal(100)


@dataclass(frozen=True)
class Percentage:
    """A rate, stored as a fraction. 7.5% is `fraction = 0.075`."""

    fraction: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.fraction, float):  # type: ignore[unreachable]
            raise TypeConstraintError(
                "Percentage rejects float; use Decimal or a named constructor"
            )
        if not isinstance(self.fraction, Decimal):
            raise TypeConstraintError(
                f"Percentage.fraction must be Decimal, got {type(self.fraction).__name__}"
            )
        if not self.fraction.is_finite():
            raise ValidationError(f"Percentage requires a finite rate, got {self.fraction}")

    @staticmethod
    def _as_decimal(value: str | Decimal) -> Decimal:
        if isinstance(value, float):  # type: ignore[unreachable]
            # Unreachable for callers mypy can type-check, which is the point:
            # the annotation rejects a float statically, and this catches the
            # untyped paths the checker never sees.
            raise TypeConstraintError(
                "Percentage rejects float; pass a decimal string such as '7.5'"
            )
        if isinstance(value, Decimal):
            return value
        if not isinstance(value, str):
            raise TypeConstraintError(
                f"Percentage expects a string or Decimal, got {type(value).__name__}"
            )
        try:
            return Decimal(value)
        except InvalidOperation:
            raise ValidationError(f"{value!r} is not a valid decimal") from None

    @classmethod
    def from_percent(cls, value: str | Decimal) -> Percentage:
        """Build from a percent figure: `from_percent("7.5")` is 7.5%."""
        return cls(cls._as_decimal(value) / _PERCENT_DIVISOR)

    @classmethod
    def from_fraction(cls, value: str | Decimal) -> Percentage:
        """Build from a fraction: `from_fraction("0.075")` is 7.5%."""
        return cls(cls._as_decimal(value))

    @classmethod
    def probability(cls, percent: str | Decimal) -> Percentage:
        """Build a rate that must lie between certainty and impossibility.

        Used for figures like bonus probability, where a value outside the range
        is a data error rather than an unusual case.
        """
        rate = cls.from_percent(percent)
        if not Decimal(0) <= rate.fraction <= Decimal(1):
            raise ValidationError(f"a probability must be between 0% and 100%, got {rate}")
        return rate

    @classmethod
    def zero(cls) -> Percentage:
        return cls(Decimal("0"))

    def as_fraction(self) -> Decimal:
        return self.fraction

    def as_percent(self) -> Decimal:
        return self.fraction * _PERCENT_DIVISOR

    def of(self, money: Money) -> Money:
        """Apply this rate to an amount, without rounding.

        The remainder survives to be quantised once at a display or persistence
        boundary, so a rate applied across many months cannot compound an error.
        """
        return money * self.fraction

    def __str__(self) -> str:
        return f"{_plain(self.as_percent())}%"


def _plain(value: Decimal) -> Decimal:
    """Strip trailing zeros without falling into exponent notation.

    `Decimal("7.500").normalize()` is 7.5, but `Decimal("100").normalize()` is
    1E+2, which is not what anyone wants to read.
    """
    normalized = value.normalize()
    exponent = normalized.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        return normalized.quantize(Decimal(1))
    return normalized
