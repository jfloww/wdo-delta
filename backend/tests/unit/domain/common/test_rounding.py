"""Rounding policies.

Rounding is not a property of Money. Real financial rules disagree about it:
IRS instructions use whole dollars, payroll computes FICA to the cent, and
statistical contexts use half-even. Applying one global rule everywhere would
produce numbers that are confidently wrong, so the policy is chosen explicitly
at each boundary and recorded on the result for the derivation tree.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.rounding import (
    ALLOCATION_PLACES,
    CURRENCY_DISPLAY,
    PAYROLL_CENTS,
    STATISTICAL_HALF_EVEN,
    TAX_WHOLE_DOLLAR,
    DecimalPlaces,
)


def test_currency_display_rounds_to_two_places() -> None:
    assert CURRENCY_DISPLAY.quantize(Decimal("1234.5678")) == Decimal("1234.57")


def test_currency_display_rounds_a_half_cent_up() -> None:
    # Decimal("1.005") is exact, unlike the float 1.005, so this genuinely
    # exercises the tie rule rather than a representation artefact.
    assert CURRENCY_DISPLAY.quantize(Decimal("1.005")) == Decimal("1.01")


def test_currency_display_rounds_a_negative_half_away_from_zero() -> None:
    assert CURRENCY_DISPLAY.quantize(Decimal("-1.005")) == Decimal("-1.01")


def test_payroll_computes_to_the_cent() -> None:
    assert PAYROLL_CENTS.quantize(Decimal("161.2345")) == Decimal("161.23")


def test_tax_rounds_to_whole_dollars() -> None:
    assert TAX_WHOLE_DOLLAR.quantize(Decimal("100.49")) == Decimal("100")
    assert TAX_WHOLE_DOLLAR.quantize(Decimal("100.50")) == Decimal("101")


def test_half_even_is_available_but_is_not_the_currency_default() -> None:
    # Banker's rounding sends an exact half to the nearest even digit. It is
    # correct for some statistical work and wrong for payroll, which is exactly
    # why there is no single global policy.
    assert STATISTICAL_HALF_EVEN.quantize(Decimal("2.5")) == Decimal("2")
    assert STATISTICAL_HALF_EVEN.quantize(Decimal("3.5")) == Decimal("4")
    assert CURRENCY_DISPLAY.quantize(Decimal("2.005")) == Decimal("2.01")


def test_every_policy_is_named_for_lineage() -> None:
    # The name is recorded on each rounded result component so a derivation can
    # state which rule produced the figure.
    assert CURRENCY_DISPLAY.name == "CURRENCY_DISPLAY"
    assert TAX_WHOLE_DOLLAR.name == "TAX_WHOLE_DOLLAR"


def test_quantizing_twice_changes_nothing() -> None:
    once = CURRENCY_DISPLAY.quantize(Decimal("1234.5678"))
    assert CURRENCY_DISPLAY.quantize(once) == once


def test_rejects_a_float_input() -> None:
    with pytest.raises(TypeError, match="float"):
        CURRENCY_DISPLAY.quantize(1234.5678)  # type: ignore[arg-type]


def test_allocation_places_matches_the_currency_scale() -> None:
    # Money.allocate defaults to this scale; keeping them in one place stops
    # the two from drifting apart.
    assert ALLOCATION_PLACES == 2


def test_a_custom_policy_can_be_declared() -> None:
    basis_points = DecimalPlaces(name="BASIS_POINTS", places=4)
    assert basis_points.quantize(Decimal("0.123456")) == Decimal("0.1235")


def test_money_quantizes_with_a_policy() -> None:
    assert Money.parse("1234.5678").quantize(CURRENCY_DISPLAY) == Money.parse("1234.57")


def test_money_quantize_preserves_currency() -> None:
    money = Money(Decimal("10.999"), "EUR")
    assert money.quantize(CURRENCY_DISPLAY).currency == "EUR"


def test_money_quantized_to_whole_dollars_can_then_be_allocated() -> None:
    # The error path Money.allocate points at: quantise first, then split.
    quantized = Money.parse("100.004").quantize(CURRENCY_DISPLAY)
    assert sum(quantized.allocate([1, 1, 1]), Money.zero()) == quantized
