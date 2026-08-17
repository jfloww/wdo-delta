"""Money construction rules.

The single most-scrutinised type in the project. A float that slips into a money
value is a silent, compounding error, so construction is deliberately strict:
Decimal in, or an explicit parse from a string.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.money import Money


def test_accepts_a_decimal_amount() -> None:
    assert Money(Decimal("1234.56")).amount == Decimal("1234.56")


def test_defaults_to_usd() -> None:
    assert Money(Decimal("1")).currency == "USD"


def test_rejects_a_float_amount() -> None:
    # A frozen dataclass would silently accept this without an explicit check.
    with pytest.raises(TypeError, match="float"):
        Money(1234.56)  # type: ignore[arg-type]


def test_rejects_an_int_amount() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Money(1234)  # type: ignore[arg-type]


def test_rejects_a_string_amount() -> None:
    # Strings are legal input, but only through the explicit parse() entry point.
    with pytest.raises(TypeError, match="Decimal"):
        Money("1234.56")  # type: ignore[arg-type]


def test_parse_builds_from_a_string() -> None:
    assert Money.parse("1234.56") == Money(Decimal("1234.56"))


def test_parse_preserves_trailing_zeros_exactly() -> None:
    # Decimal("0.10") and Decimal("0.1") compare equal but differ in exponent.
    # Money must not quietly normalise the scale a caller supplied.
    assert Money.parse("0.10").amount.as_tuple().exponent == -2


def test_parse_rejects_a_float() -> None:
    # Decimal(0.1) is 0.1000000000000000055511151231257827021181583404541015625.
    # Routing floats through parse() would reintroduce exactly the bug the type exists to prevent.
    with pytest.raises(TypeError, match="float"):
        Money.parse(0.1)  # type: ignore[arg-type]


def test_parse_rejects_a_non_numeric_string() -> None:
    with pytest.raises(ValueError, match="not a valid decimal"):
        Money.parse("abc")


def test_parse_rejects_a_non_finite_value() -> None:
    with pytest.raises(ValueError, match="finite"):
        Money.parse("NaN")


def test_rejects_a_currency_that_is_not_three_letters() -> None:
    with pytest.raises(ValueError, match="ISO 4217"):
        Money(Decimal("1"), "US")


def test_rejects_a_lowercase_currency_code() -> None:
    with pytest.raises(ValueError, match="ISO 4217"):
        Money(Decimal("1"), "usd")


def test_is_immutable() -> None:
    money = Money(Decimal("1"))
    with pytest.raises(AttributeError):
        money.amount = Decimal("2")  # type: ignore[misc]


def test_equal_amounts_in_the_same_currency_are_equal() -> None:
    assert Money.parse("10.00") == Money.parse("10.00")


def test_same_amount_in_different_currencies_is_not_equal() -> None:
    assert Money(Decimal("10.00"), "USD") != Money(Decimal("10.00"), "EUR")
