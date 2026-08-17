"""Work schedules and the time cost of commuting.

Time is one of the four tracks reported separately, and commuting is where an
offer quietly takes it. A job paying more but costing an extra 250 hours a year
of unpaid travel is exactly the comparison this product exists to surface, and
it is invisible in any calculator that compares salary alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from offerdelta.domain.common.errors import TypeConstraintError, ValidationError

_DAYS_PER_WEEK: Final = Decimal(7)
_HOURS_PER_WEEK: Final = Decimal(168)
_WEEKS_PER_YEAR: Final = Decimal(52)
_MINUTES_PER_HOUR: Final = Decimal(60)
_ROUND_TRIP: Final = Decimal(2)


def _require_decimal(value: Decimal, label: str) -> None:
    if isinstance(value, float):  # type: ignore[unreachable]
        raise TypeConstraintError(f"{label} rejects float; use Decimal for exact values")
    if not isinstance(value, Decimal):
        raise TypeConstraintError(f"{label} must be Decimal, got {type(value).__name__}")


@dataclass(frozen=True)
class WorkSchedule:
    """How much time a job takes, including the part it does not pay for."""

    weekly_work_hours: Decimal
    annual_working_weeks: Decimal
    onsite_days_per_week: Decimal
    one_way_commute_minutes: Decimal

    def __post_init__(self) -> None:
        for label, value in (
            ("weekly_work_hours", self.weekly_work_hours),
            ("annual_working_weeks", self.annual_working_weeks),
            ("onsite_days_per_week", self.onsite_days_per_week),
            ("one_way_commute_minutes", self.one_way_commute_minutes),
        ):
            _require_decimal(value, label)

        if self.weekly_work_hours < 0:
            raise ValidationError("weekly_work_hours cannot be negative")
        if self.weekly_work_hours > _HOURS_PER_WEEK:
            raise ValidationError(
                f"weekly_work_hours cannot exceed the 168 hours in a week, got "
                f"{self.weekly_work_hours}"
            )

        if self.annual_working_weeks < 0:
            raise ValidationError("annual_working_weeks cannot be negative")
        if self.annual_working_weeks > _WEEKS_PER_YEAR:
            raise ValidationError(
                f"annual_working_weeks cannot exceed 52, got {self.annual_working_weeks}"
            )

        if not Decimal(0) <= self.onsite_days_per_week <= _DAYS_PER_WEEK:
            raise ValidationError(
                f"onsite_days_per_week must be between 0 and 7, got {self.onsite_days_per_week}"
            )

        if self.one_way_commute_minutes < 0:
            raise ValidationError("one_way_commute_minutes cannot be negative")

    @property
    def annual_work_hours(self) -> Decimal:
        return self.weekly_work_hours * self.annual_working_weeks

    @property
    def annual_commute_hours(self) -> Decimal:
        """Door-to-door travel time per year, counting both directions.

        Falls to zero when `onsite_days_per_week` is zero, which is the rule
        that makes the COMMUTE cost category boundary meaningful.
        """
        return (
            self.onsite_days_per_week
            * self.annual_working_weeks
            * self.one_way_commute_minutes
            * _ROUND_TRIP
            / _MINUTES_PER_HOUR
        )

    @property
    def total_annual_committed_hours(self) -> Decimal:
        """The denominator of work-adjusted compensation.

        Time the job costs, not only the time it pays for.
        """
        return self.annual_work_hours + self.annual_commute_hours
