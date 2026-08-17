"""Rounding policies.

There is deliberately no global rounding rule. Real financial rules disagree:
IRS instructions permit whole-dollar entries, payroll computes FICA to the
cent, and statistical work often wants half-even. Applying one rule everywhere
produces numbers that are confidently wrong, and looks like a half-remembered
heuristic rather than a decision.

A policy is therefore chosen explicitly at each boundary where rounding occurs,
and its `name` is recorded on the resulting component so a derivation can state
which rule produced the figure.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from typing import Final, Protocol, runtime_checkable

# Money.allocate splits to this many places by default. Defined here so the
# allocation scale and the currency display scale cannot drift apart.
ALLOCATION_PLACES: Final = 2


@runtime_checkable
class RoundingPolicy(Protocol):
    """A named rule for reducing an exact Decimal to a presentable scale."""

    @property
    def name(self) -> str: ...

    def quantize(self, value: Decimal) -> Decimal: ...


@dataclass(frozen=True)
class DecimalPlaces:
    """Round to a fixed number of decimal places.

    Defaults to half-up — a tie rounds away from zero — which is what payroll
    and consumer-facing money conventionally use. Pass ROUND_HALF_EVEN
    explicitly where banker's rounding is genuinely wanted.
    """

    name: str
    places: int
    rounding: str = ROUND_HALF_UP

    def quantize(self, value: Decimal) -> Decimal:
        if isinstance(value, float):  # type: ignore[unreachable]
            # Same reasoning as Money: the annotation rejects a float for
            # callers mypy can see, and this catches the ones it cannot.
            raise TypeError(
                "rounding rejects float; binary floating point cannot represent "
                "decimal currency exactly"
            )
        if not isinstance(value, Decimal):
            raise TypeError(f"rounding expects Decimal, got {type(value).__name__}")
        return value.quantize(Decimal(1).scaleb(-self.places), rounding=self.rounding)


#: User-facing money. The scale every displayed amount is reduced to.
CURRENCY_DISPLAY: Final = DecimalPlaces(name="CURRENCY_DISPLAY", places=2)

#: Payroll withholding, computed to the cent.
PAYROLL_CENTS: Final = DecimalPlaces(name="PAYROLL_CENTS", places=2)

#: IRS whole-dollar rules.
TAX_WHOLE_DOLLAR: Final = DecimalPlaces(name="TAX_WHOLE_DOLLAR", places=0)

#: Banker's rounding. Correct for some statistical aggregates, wrong for
#: payroll — present so the distinction stays explicit rather than assumed.
STATISTICAL_HALF_EVEN: Final = DecimalPlaces(
    name="STATISTICAL_HALF_EVEN", places=0, rounding=ROUND_HALF_EVEN
)
