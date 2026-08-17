"""Money arithmetic.

Two invariants matter here. Arithmetic never crosses currencies silently, and
arithmetic never rounds. Rounding is a separate, explicit decision applied at
boundaries, so intermediate sums keep full precision.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.money import Money


def test_adds_amounts_in_the_same_currency() -> None:
    assert Money.parse("10.25") + Money.parse("4.75") == Money.parse("15.00")


def test_subtracts_amounts_in_the_same_currency() -> None:
    assert Money.parse("10.25") - Money.parse("4.25") == Money.parse("6.00")


def test_subtraction_can_produce_a_negative_amount() -> None:
    # Deltas between two offers are routinely negative; this is not an error.
    assert Money.parse("1.00") - Money.parse("3.50") == Money.parse("-2.50")


def test_negates() -> None:
    assert -Money.parse("2.50") == Money.parse("-2.50")


def test_absolute_value() -> None:
    assert abs(Money.parse("-2.50")) == Money.parse("2.50")


def test_addition_rejects_a_different_currency() -> None:
    with pytest.raises(ValueError, match="currency"):
        Money(Decimal("1"), "USD") + Money(Decimal("1"), "EUR")


def test_subtraction_rejects_a_different_currency() -> None:
    with pytest.raises(ValueError, match="currency"):
        Money(Decimal("1"), "USD") - Money(Decimal("1"), "EUR")


def test_addition_does_not_round() -> None:
    # Three thirds of a cent must survive as written. Rounding here would make
    # the reconciliation invariant fail for reasons unrelated to the model.
    total = Money.parse("0.001") + Money.parse("0.001") + Money.parse("0.001")
    assert total.amount == Decimal("0.003")


def test_multiplies_by_an_integer_quantity() -> None:
    assert Money.parse("1200.00") * 12 == Money.parse("14400.00")


def test_multiplies_by_a_decimal_factor() -> None:
    assert Money.parse("1000.00") * Decimal("1.075") == Money.parse("1075.000")


def test_multiplication_rejects_a_float_factor() -> None:
    with pytest.raises(TypeError, match="float"):
        Money.parse("100.00") * 1.5  # type: ignore[operator]


def test_multiplication_rejects_another_money() -> None:
    # Dollars times dollars has no meaning in this domain.
    with pytest.raises(TypeError):
        Money.parse("2.00") * Money.parse("3.00")  # type: ignore[operator]


def test_sums_an_iterable_starting_from_zero() -> None:
    amounts = [Money.parse("1.10"), Money.parse("2.20"), Money.parse("3.30")]
    assert Money.zero() + amounts[0] + amounts[1] + amounts[2] == Money.parse("6.60")


def test_zero_defaults_to_usd() -> None:
    assert Money.zero() == Money.parse("0")


def test_compares_amounts_in_the_same_currency() -> None:
    assert Money.parse("1.00") < Money.parse("2.00")
    assert Money.parse("2.00") > Money.parse("1.00")
    assert Money.parse("1.00") <= Money.parse("1.00")
    assert Money.parse("1.00") >= Money.parse("1.00")


def test_comparison_ignores_decimal_scale() -> None:
    # Decimal("1.0") and Decimal("1.00") are equal in value but differ in exponent.
    assert not Money.parse("1.0") < Money.parse("1.00")


def test_comparison_rejects_a_different_currency() -> None:
    with pytest.raises(ValueError, match="currency"):
        _ = Money(Decimal("1"), "USD") < Money(Decimal("1"), "EUR")


def test_is_zero() -> None:
    assert Money.parse("0.00").is_zero()
    assert not Money.parse("0.01").is_zero()
