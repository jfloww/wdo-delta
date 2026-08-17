"""Date ranges and month arithmetic.

The comparison engine walks a horizon month by month, so month stepping has to
be exactly right at the awkward boundaries — the 31st of a month followed by a
28-day month, and February in a leap year.

Ranges are half-open, [start, end). An inclusive end invites off-by-one errors
in exactly the loop that produces every monthly projection.
"""

from datetime import date

import pytest

from offerdelta.domain.common.dates import DateRange, add_months
from offerdelta.domain.common.errors import ValidationError


def test_a_range_spans_from_start_up_to_but_excluding_end() -> None:
    horizon = DateRange(date(2026, 1, 1), date(2027, 1, 1))
    assert horizon.contains(date(2026, 1, 1))
    assert horizon.contains(date(2026, 12, 31))
    assert not horizon.contains(date(2027, 1, 1))


def test_a_one_year_horizon_has_twelve_months() -> None:
    assert DateRange.of_months(date(2026, 1, 1), 12).month_count == 12


def test_a_three_year_horizon_has_thirty_six_months() -> None:
    assert DateRange.of_months(date(2026, 1, 1), 36).month_count == 36


def test_a_horizon_ends_the_day_the_next_period_begins() -> None:
    assert DateRange.of_months(date(2026, 1, 1), 12).end == date(2027, 1, 1)


def test_months_yields_the_first_day_of_each_month() -> None:
    months = list(DateRange.of_months(date(2026, 1, 1), 3).months())
    assert months == [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]


def test_months_yields_exactly_month_count_entries() -> None:
    horizon = DateRange.of_months(date(2026, 6, 15), 18)
    assert len(list(horizon.months())) == 18


def test_a_horizon_starting_mid_month_still_steps_by_month() -> None:
    months = list(DateRange.of_months(date(2026, 3, 15), 3).months())
    assert months == [date(2026, 3, 15), date(2026, 4, 15), date(2026, 5, 15)]


def test_rejects_an_end_before_its_start() -> None:
    with pytest.raises(ValidationError, match="after"):
        DateRange(date(2026, 6, 1), date(2026, 1, 1))


def test_rejects_an_empty_range() -> None:
    with pytest.raises(ValidationError, match="after"):
        DateRange(date(2026, 1, 1), date(2026, 1, 1))


def test_rejects_a_zero_month_horizon() -> None:
    with pytest.raises(ValidationError, match="at least one month"):
        DateRange.of_months(date(2026, 1, 1), 0)


def test_is_immutable() -> None:
    horizon = DateRange.of_months(date(2026, 1, 1), 12)
    with pytest.raises(AttributeError):
        horizon.start = date(2027, 1, 1)  # type: ignore[misc]


def test_adding_a_month_to_the_thirty_first_clamps_to_the_month_end() -> None:
    # 2026 is not a leap year, so January 31 plus one month is February 28.
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_adding_a_month_clamps_to_a_leap_day() -> None:
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)


def test_adding_a_year_to_a_leap_day_clamps_to_february_twenty_eighth() -> None:
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_adding_months_rolls_over_the_year() -> None:
    assert add_months(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_adding_zero_months_changes_nothing() -> None:
    assert add_months(date(2026, 5, 20), 0) == date(2026, 5, 20)


def test_clamping_does_not_drift_across_successive_steps() -> None:
    # Stepping from January 31 must not stick at February 28 forever. Each step
    # is computed from the original day, so March returns to the 31st.
    months = list(DateRange.of_months(date(2026, 1, 31), 3).months())
    assert months == [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)]
