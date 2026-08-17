"""The move break-even solver.

Reports two months, not one, because a single crossing is misleading whenever
an offer front-loads cash.

A signing bonus makes month one positive; the move then claws it back when
relocation costs land. A solver reporting only the first crossing would claim
break-even in month one while the user is still underwater in month eight. The
Auburn-to-New-Jersey fixture has exactly this shape.
"""

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.solvers.break_even import BreakEvenMetric, solve_break_even


def _series(*amounts: str) -> tuple[Money, ...]:
    return tuple(Money.parse(amount) for amount in amounts)


def test_a_series_positive_throughout_breaks_even_immediately() -> None:
    result = solve_break_even(_series("100.00", "200.00", "300.00"))
    assert result.first_crossing_month == 1
    assert result.stable_break_even_month == 1


def test_a_series_that_never_turns_positive_has_no_break_even() -> None:
    result = solve_break_even(_series("-100.00", "-200.00", "-300.00"))
    assert result.first_crossing_month is None
    assert result.stable_break_even_month is None


def test_a_simple_crossing_reports_the_same_month_twice() -> None:
    result = solve_break_even(_series("-300.00", "-100.00", "50.00", "200.00"))
    assert result.first_crossing_month == 3
    assert result.stable_break_even_month == 3


def test_a_front_loaded_offer_separates_the_two_months() -> None:
    # Positive on a signing bonus, negative once the move lands, positive again.
    # This is the case the two-figure design exists for.
    result = solve_break_even(_series("500.00", "-200.00", "-50.00", "300.00", "600.00"))
    assert result.first_crossing_month == 1
    assert result.stable_break_even_month == 4


def test_exactly_zero_counts_as_broken_even() -> None:
    # Breaking even means no longer behind, so zero qualifies.
    result = solve_break_even(_series("-100.00", "0.00", "100.00"))
    assert result.first_crossing_month == 2


def test_a_dip_back_below_zero_delays_the_stable_month() -> None:
    result = solve_break_even(_series("10.00", "-10.00", "10.00", "-10.00", "10.00"))
    assert result.first_crossing_month == 1
    assert result.stable_break_even_month == 5


def test_a_series_ending_negative_has_no_stable_break_even() -> None:
    # It crossed once, but the horizon ends underwater, so there is no month
    # after which it stays positive.
    result = solve_break_even(_series("-100.00", "100.00", "-50.00"))
    assert result.first_crossing_month == 2
    assert result.stable_break_even_month is None


def test_the_result_reports_the_horizon_it_searched() -> None:
    result = solve_break_even(_series("-100.00", "100.00"))
    assert result.horizon_months == 2


def test_the_result_names_the_metric_it_used() -> None:
    # Blueprint 9.3: a break-even month is meaningless without saying whether it
    # is measured on cash or on wealth.
    result = solve_break_even(_series("100.00"), metric=BreakEvenMetric.WEALTH)
    assert result.metric is BreakEvenMetric.WEALTH


def test_the_metric_defaults_to_cash() -> None:
    assert solve_break_even(_series("100.00")).metric is BreakEvenMetric.CASH


def test_an_empty_series_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one month"):
        solve_break_even(())


def test_a_result_can_say_whether_it_ever_breaks_even() -> None:
    assert solve_break_even(_series("100.00")).breaks_even is True
    assert solve_break_even(_series("-100.00")).breaks_even is False


def test_a_result_can_say_whether_it_stays_broken_even() -> None:
    assert solve_break_even(_series("-100.00", "100.00", "-50.00")).stays_positive is False
    assert solve_break_even(_series("-100.00", "100.00", "50.00")).stays_positive is True
