"""Work schedules and the time cost of commuting.

Time is one of the four tracks the product reports separately, and commuting is
where an offer quietly takes it. A job paying more but costing an extra 250
hours a year of unpaid travel is the comparison this exists to surface.

Annual commute hours, from blueprint section 6.4:

    onsite_days_per_week * annual_working_weeks * one_way_minutes * 2 / 60
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.employment.work_schedule import WorkSchedule


def _schedule(**changes: object) -> WorkSchedule:
    defaults: dict[str, object] = {
        "weekly_work_hours": Decimal("40"),
        "annual_working_weeks": Decimal("48"),
        "onsite_days_per_week": Decimal("3"),
        "one_way_commute_minutes": Decimal("25"),
    }
    defaults.update(changes)
    return WorkSchedule(**defaults)  # type: ignore[arg-type]


def test_annual_work_hours_multiply_weeks_by_weekly_hours() -> None:
    assert _schedule().annual_work_hours == Decimal("1920")


def test_annual_commute_hours_count_both_directions() -> None:
    # 3 days * 48 weeks * 25 minutes * 2 / 60 = 120 hours.
    assert _schedule().annual_commute_hours == Decimal("120")


def test_zero_onsite_days_produce_zero_commute_hours() -> None:
    # The rule that makes the COMMUTE category boundary meaningful: a fully
    # remote role has no commute time and no commute cash cost.
    assert _schedule(onsite_days_per_week=Decimal("0")).annual_commute_hours == Decimal("0")


def test_a_zero_length_commute_produces_zero_hours() -> None:
    assert _schedule(one_way_commute_minutes=Decimal("0")).annual_commute_hours == Decimal("0")


def test_a_hybrid_schedule_scales_with_onsite_days() -> None:
    two_days = _schedule(onsite_days_per_week=Decimal("2")).annual_commute_hours
    four_days = _schedule(onsite_days_per_week=Decimal("4")).annual_commute_hours
    assert four_days == two_days * 2


def test_total_committed_hours_include_the_commute() -> None:
    # The denominator of work-adjusted compensation: time the job costs, not
    # only time it pays for.
    assert _schedule().total_annual_committed_hours == Decimal("2040")


def test_a_fractional_onsite_pattern_is_allowed() -> None:
    # Alternating weeks of two and three days averages 2.5.
    schedule = _schedule(onsite_days_per_week=Decimal("2.5"))
    assert schedule.annual_commute_hours == Decimal("100")


def test_onsite_days_cannot_exceed_seven() -> None:
    with pytest.raises(ValidationError, match="between 0 and 7"):
        _schedule(onsite_days_per_week=Decimal("8"))


def test_onsite_days_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="between 0 and 7"):
        _schedule(onsite_days_per_week=Decimal("-1"))


def test_weekly_hours_cannot_exceed_the_hours_in_a_week() -> None:
    with pytest.raises(ValidationError, match="168"):
        _schedule(weekly_work_hours=Decimal("200"))


def test_weekly_hours_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="negative"):
        _schedule(weekly_work_hours=Decimal("-1"))


def test_working_weeks_cannot_exceed_fifty_two() -> None:
    with pytest.raises(ValidationError, match="52"):
        _schedule(annual_working_weeks=Decimal("60"))


def test_commute_minutes_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="negative"):
        _schedule(one_way_commute_minutes=Decimal("-5"))


def test_rejects_float_inputs() -> None:
    with pytest.raises(TypeError):
        _schedule(weekly_work_hours=40.0)


def test_is_immutable() -> None:
    schedule = _schedule()
    with pytest.raises(AttributeError):
        schedule.weekly_work_hours = Decimal("50")  # type: ignore[misc]
