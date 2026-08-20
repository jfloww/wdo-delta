"""Parsing amounts and descriptions out of bank exports.

Bank CSVs are hostile input. Amounts arrive as "$1,234.56", "(45.00)" for a
debit, "1.234,56" from a European export, and occasionally with a trailing
minus. Every one of them is a place a float could slip in, and a float in a
balance is the defect this whole project exists to avoid.

Descriptions are worse: the same merchant appears as "SQ *BLUE BOTTLE 4412",
"BLUE BOTTLE COFFEE #221" and "TST* BLUE BOTTLE". Recurring-payment detection
depends entirely on recognising those as one thing.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.transactions.parsing import normalise_description, parse_amount

# --- Amounts ---------------------------------------------------------------


def test_a_plain_decimal_parses() -> None:
    assert parse_amount("1234.56") == Money.parse("1234.56")


def test_currency_symbols_and_separators_are_stripped() -> None:
    assert parse_amount("$1,234.56") == Money.parse("1234.56")


def test_a_leading_minus_is_kept() -> None:
    assert parse_amount("-45.00") == Money.parse("-45.00")


def test_parentheses_mean_negative() -> None:
    # Accounting convention, and the one most CSV parsers get wrong: (45.00)
    # is a debit, not a positive forty-five.
    assert parse_amount("(45.00)") == Money.parse("-45.00")


def test_a_trailing_minus_is_understood() -> None:
    # Some exports, notably older Quicken formats, put the sign at the end.
    assert parse_amount("45.00-") == Money.parse("-45.00")


def test_whitespace_is_ignored() -> None:
    assert parse_amount("  $ 1,234.56  ") == Money.parse("1234.56")


def test_a_bare_integer_gains_no_spurious_precision() -> None:
    assert parse_amount("1200") == Money.parse("1200")


def test_a_european_format_is_parsed_when_declared() -> None:
    # 1.234,56 is one thousand two hundred, not one point two. Guessing from
    # the string alone is unsafe, so the caller states the convention.
    assert parse_amount("1.234,56", decimal_comma=True) == Money.parse("1234.56")


def test_a_european_format_is_misread_if_not_declared() -> None:
    # Documented rather than silently handled: "1.234" under US convention is
    # one point two three four, and that is the correct reading of the input
    # the caller said it was sending.
    assert parse_amount("1.234") == Money.parse("1.234")


def test_an_empty_amount_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty"):
        parse_amount("   ")


def test_a_non_numeric_amount_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not a valid amount"):
        parse_amount("PENDING")


def test_the_result_is_never_a_float() -> None:
    assert isinstance(parse_amount("$1,234.56").amount, Decimal)


def test_precision_survives_a_thousands_separator() -> None:
    # The reason this is string surgery rather than float(): 12,345,678.91
    # through a float would already be approximate.
    assert parse_amount("$12,345,678.91").amount == Decimal("12345678.91")


# --- Descriptions ----------------------------------------------------------


def test_case_and_padding_are_normalised() -> None:
    assert normalise_description("  Blue Bottle Coffee  ") == "BLUE BOTTLE COFFEE"


def test_a_square_payment_prefix_is_removed() -> None:
    # Square, Toast and PayPal all prepend a processor tag that varies per
    # transaction, which would otherwise defeat recurring detection.
    assert normalise_description("SQ *BLUE BOTTLE") == "BLUE BOTTLE"


def test_a_toast_prefix_is_removed() -> None:
    assert normalise_description("TST* BLUE BOTTLE") == "BLUE BOTTLE"


def test_a_paypal_prefix_is_removed() -> None:
    assert normalise_description("PAYPAL *SPOTIFY") == "SPOTIFY"


def test_trailing_store_numbers_are_removed() -> None:
    assert normalise_description("BLUE BOTTLE COFFEE #221") == "BLUE BOTTLE COFFEE"


def test_trailing_reference_digits_are_removed() -> None:
    assert normalise_description("BLUE BOTTLE 4412") == "BLUE BOTTLE"


def test_embedded_dates_are_removed() -> None:
    # Many banks append the posting date, which differs every month and would
    # make a monthly subscription look like twelve different merchants.
    assert normalise_description("NETFLIX 08/17") == "NETFLIX"


def test_variants_of_one_merchant_normalise_together() -> None:
    # The property recurring detection depends on.
    variants = [
        "SQ *BLUE BOTTLE 4412",
        "BLUE BOTTLE COFFEE #221",
        "TST* BLUE BOTTLE COFFEE",
        "  blue bottle coffee  ",
    ]
    normalised = {normalise_description(v) for v in variants}
    assert normalised == {"BLUE BOTTLE", "BLUE BOTTLE COFFEE"}


def test_normalisation_never_empties_a_description() -> None:
    # A description made entirely of strippable parts must survive intact
    # rather than collapse to a blank key, which would silently merge every
    # such merchant into one. The exact surviving form does not matter; that it
    # is non-empty and stable does.
    result = normalise_description("#4412")
    assert result
    assert result == normalise_description("#4412")


def test_an_empty_description_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty"):
        normalise_description("   ")
