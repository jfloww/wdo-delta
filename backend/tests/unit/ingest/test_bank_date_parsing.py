"""Reading dates out of bank exports.

The trap: `03/04/2026` is 4 March in most of the world and 3 April in the United
States, and nothing in the value says which. Guessing wrong moves a transaction
by up to eleven months, silently, and every monthly total that touches it is
then wrong in a way no downstream check can see.

So the order is inferred from the whole column rather than one value, and when
the column genuinely cannot settle it, the importer says so and asks instead of
picking.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.ingest.dates import (
    DateOrder,
    detect_date_order,
    looks_like_dates,
    parse_date,
)

# --- Unambiguous formats ---------------------------------------------------


def test_an_iso_date_parses() -> None:
    assert parse_date("2026-08-17", DateOrder.ISO) == date(2026, 8, 17)


def test_a_us_date_parses_month_first() -> None:
    assert parse_date("08/17/2026", DateOrder.MONTH_FIRST) == date(2026, 8, 17)


def test_a_european_date_parses_day_first() -> None:
    assert parse_date("17/08/2026", DateOrder.DAY_FIRST) == date(2026, 8, 17)


def test_dots_and_dashes_are_accepted() -> None:
    assert parse_date("17.08.2026", DateOrder.DAY_FIRST) == date(2026, 8, 17)
    assert parse_date("17-08-2026", DateOrder.DAY_FIRST) == date(2026, 8, 17)


def test_a_two_digit_year_is_read_as_this_century() -> None:
    # Bank exports predating 2000 are not a case worth supporting, and reading
    # 26 as 1926 would be worse than refusing.
    assert parse_date("08/17/26", DateOrder.MONTH_FIRST) == date(2026, 8, 17)


def test_a_named_month_parses_regardless_of_order() -> None:
    # "17 Aug 2026" carries its own order and needs no convention.
    assert parse_date("17 Aug 2026", DateOrder.MONTH_FIRST) == date(2026, 8, 17)
    assert parse_date("Aug 17, 2026", DateOrder.DAY_FIRST) == date(2026, 8, 17)


def test_surrounding_whitespace_is_ignored() -> None:
    assert parse_date("  2026-08-17  ", DateOrder.ISO) == date(2026, 8, 17)


def test_a_timestamp_suffix_is_ignored() -> None:
    # Some exports carry a posting time nobody needs.
    assert parse_date("2026-08-17 14:32:00", DateOrder.ISO) == date(2026, 8, 17)


# --- The order actually matters --------------------------------------------


def test_the_same_string_reads_differently_under_each_order() -> None:
    # The entire reason the order is detected rather than assumed.
    assert parse_date("03/04/2026", DateOrder.MONTH_FIRST) == date(2026, 3, 4)
    assert parse_date("03/04/2026", DateOrder.DAY_FIRST) == date(2026, 4, 3)


# --- Failures --------------------------------------------------------------


def test_an_impossible_date_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not a date"):
        parse_date("31/02/2026", DateOrder.DAY_FIRST)


def test_a_non_date_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not a date"):
        parse_date("PENDING", DateOrder.ISO)


def test_an_empty_value_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty"):
        parse_date("   ", DateOrder.ISO)


def test_an_ambiguous_order_cannot_be_used_to_parse() -> None:
    # Refusing beats guessing: a wrong guess here is invisible downstream.
    with pytest.raises(ValidationError, match="ambiguous"):
        parse_date("03/04/2026", DateOrder.AMBIGUOUS)


# --- Detecting the order from a column -------------------------------------


def test_iso_dates_are_detected() -> None:
    assert detect_date_order(["2026-08-17", "2026-01-02"]) is DateOrder.ISO


def test_a_day_above_twelve_in_first_position_means_day_first() -> None:
    # 17 cannot be a month, so the column settles itself.
    assert detect_date_order(["17/08/2026", "03/04/2026"]) is DateOrder.DAY_FIRST


def test_a_day_above_twelve_in_second_position_means_month_first() -> None:
    assert detect_date_order(["08/17/2026", "03/04/2026"]) is DateOrder.MONTH_FIRST


def test_a_column_that_never_exceeds_twelve_is_ambiguous() -> None:
    # Every value reads either way. This is the case that must not be guessed.
    assert detect_date_order(["03/04/2026", "05/06/2026"]) is DateOrder.AMBIGUOUS


def test_contradictory_evidence_is_ambiguous_rather_than_majority_rule() -> None:
    # One value says day-first, another says month-first. The column is broken
    # or mixed, and picking the more common one would bury that.
    assert detect_date_order(["17/08/2026", "08/17/2026"]) is DateOrder.AMBIGUOUS


def test_named_months_are_detected_as_unambiguous() -> None:
    assert detect_date_order(["17 Aug 2026", "02 Jan 2026"]) is DateOrder.ISO


def test_an_empty_column_is_ambiguous() -> None:
    assert detect_date_order([]) is DateOrder.AMBIGUOUS


def test_blanks_and_junk_are_ignored_while_detecting() -> None:
    # A stray "PENDING" row should not stop the column from settling.
    assert detect_date_order(["17/08/2026", "", "PENDING", "03/04/2026"]) is (DateOrder.DAY_FIRST)


def test_detection_needs_only_one_decisive_value() -> None:
    assert detect_date_order(["03/04/2026", "05/06/2026", "25/12/2026"]) is (DateOrder.DAY_FIRST)


# --- Sniffing whether a column holds dates at all --------------------------


def test_a_date_column_is_recognised() -> None:

    assert looks_like_dates(["2026-08-17", "2026-08-18", "2026-08-19"]) is True


def test_a_description_column_is_not_mistaken_for_dates() -> None:

    assert looks_like_dates(["BLUE BOTTLE", "NETFLIX", "SHELL"]) is False


def test_a_mostly_date_column_still_counts() -> None:

    # One junk row among many should not disqualify the column.
    assert looks_like_dates(["2026-08-17", "2026-08-18", "PENDING", "2026-08-19"]) is True


def test_an_amount_column_is_not_mistaken_for_dates() -> None:

    assert looks_like_dates(["-4.50", "1234.56", "-88.10"]) is False
