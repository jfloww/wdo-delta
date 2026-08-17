"""Date ranges and month arithmetic.

The comparison engine walks a horizon month by month, so month stepping has to
be right at the awkward boundaries: a 31-day month followed by a 28-day one, and
February in a leap year.

Ranges are half-open, [start, end). An inclusive end invites off-by-one errors
in precisely the loop that produces every monthly projection.
"""

from __future__ import annotations

import calendar
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Final

from offerdelta.domain.common.errors import ValidationError

_MONTHS_PER_YEAR: Final = 12


def add_months(start: date, months: int) -> date:
    """Advance a date by whole months, clamping to the end of a short month.

    January 31 plus one month is February 28 (or 29 in a leap year). Callers
    that step repeatedly should compute each step from the original date rather
    than from the previous result, otherwise a clamped day sticks: stepping
    31 Jan -> 28 Feb -> 28 Mar would silently lose three days.
    """
    total = start.month - 1 + months
    year = start.year + total // _MONTHS_PER_YEAR
    month = total % _MONTHS_PER_YEAR + 1
    last_day_of_month = calendar.monthrange(year, month)[1]
    return date(year, month, min(start.day, last_day_of_month))


@dataclass(frozen=True)
class DateRange:
    """A half-open span of dates, [start, end)."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValidationError(
                f"a date range must end after it starts, got {self.start} to {self.end}"
            )

    @classmethod
    def of_months(cls, start: date, months: int) -> DateRange:
        """Build a horizon of whole months beginning at `start`."""
        if months < 1:
            raise ValidationError(f"a horizon must span at least one month, got {months}")
        return cls(start, add_months(start, months))

    @property
    def month_count(self) -> int:
        """How many whole month steps the range spans.

        Comparing day numbers is not enough to decide whether the final month
        completed: a range built by `of_months` from January 31 ends on April 30,
        whose day number is lower than the start's despite spanning three whole
        months. Re-deriving the anniversary answers it exactly — if stepping
        `elapsed` months from the start lands past the end, the last month is
        incomplete.
        """
        elapsed = (self.end.year - self.start.year) * _MONTHS_PER_YEAR + (
            self.end.month - self.start.month
        )
        if elapsed > 0 and add_months(self.start, elapsed) > self.end:
            elapsed -= 1
        return elapsed

    def contains(self, moment: date) -> bool:
        return self.start <= moment < self.end

    def months(self) -> Iterator[date]:
        """Yield the anchor date of each month in the range.

        Each step is computed from `start`, so a day clamped by a short month
        recovers on the next longer one rather than drifting.
        """
        for offset in range(self.month_count):
            yield add_months(self.start, offset)

    def __str__(self) -> str:
        return f"{self.start} to {self.end} ({self.month_count} months)"
