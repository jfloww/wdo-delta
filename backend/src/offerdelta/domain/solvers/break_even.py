"""The move break-even solver.

Reports two months rather than one.

A single crossing is misleading whenever an offer front-loads cash. A signing
bonus makes month one positive; the move then claws it back when relocation
costs land. Reporting only the first crossing would claim break-even in month
one while the user is still behind in month eight — technically the first month
the cumulative delta was non-negative, and useless as an answer.

So the solver reports both:

- `first_crossing_month`: the first month the cumulative delta reaches zero.
- `stable_break_even_month`: the first month after which it never falls back.

When the horizon ends underwater there is no stable month, and the result says
so rather than returning the horizon end and implying success.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money


class BreakEvenMetric(StrEnum):
    """What the break-even is measured on.

    A break-even month means nothing without this: a move can break even on
    wealth long before it breaks even on cash, because vesting equity counts on
    one track and not the other.
    """

    CASH = "CASH"
    WEALTH = "WEALTH"


@dataclass(frozen=True)
class BreakEvenResult:
    """When a move stops costing money, on the stated metric."""

    metric: BreakEvenMetric
    horizon_months: int
    first_crossing_month: int | None
    stable_break_even_month: int | None

    @property
    def breaks_even(self) -> bool:
        """Whether the cumulative delta ever reaches zero within the horizon."""
        return self.first_crossing_month is not None

    @property
    def stays_positive(self) -> bool:
        """Whether it reaches zero and never falls back before the horizon ends."""
        return self.stable_break_even_month is not None


def solve_break_even(
    cumulative_delta: Sequence[Money],
    metric: BreakEvenMetric = BreakEvenMetric.CASH,
) -> BreakEvenResult:
    """Find both break-even months in a cumulative delta series.

    Months are reported one-indexed, matching how a horizon reads to a person:
    the first month of the comparison is month 1, not month 0.
    """
    if not cumulative_delta:
        raise ValidationError("a break-even search needs at least one month of data")

    non_negative = [value.amount >= 0 for value in cumulative_delta]

    first = next((i + 1 for i, ok in enumerate(non_negative) if ok), None)

    # Scanning backwards finds the last month that is still negative; everything
    # after it is stable. Doing it forwards would need a nested scan per month.
    last_negative = next(
        (i for i in range(len(non_negative) - 1, -1, -1) if not non_negative[i]), None
    )
    if last_negative is None:
        stable = 1 if non_negative else None
    elif last_negative == len(non_negative) - 1:
        stable = None  # still underwater when the horizon ends
    else:
        stable = last_negative + 2  # one-indexed, and the month after the dip

    return BreakEvenResult(
        metric=metric,
        horizon_months=len(cumulative_delta),
        first_crossing_month=first,
        stable_break_even_month=stable,
    )
