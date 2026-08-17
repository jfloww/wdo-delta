"""Percentages.

The recurring bug with percentages is that 7.5 and 0.075 are the same rate
written two ways, and a value passed between two functions that disagree is off
by a factor of a hundred while still looking plausible. There is therefore no
bare constructor: a caller must say which form it is holding.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import TypeConstraintError, ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY


def test_built_from_a_percent_figure() -> None:
    assert Percentage.from_percent("7.5").as_fraction() == Decimal("0.075")


def test_built_from_a_fraction() -> None:
    assert Percentage.from_fraction("0.075").as_percent() == Decimal("7.5")


def test_the_two_constructors_agree() -> None:
    assert Percentage.from_percent("7.5") == Percentage.from_fraction("0.075")


def test_the_bare_constructor_takes_a_fraction_not_a_percent() -> None:
    # The field is named `fraction`, so the positional form is unambiguous:
    # Percentage(Decimal("0.075")) is 7.5%, never 750%.
    assert Percentage(Decimal("0.075")) == Percentage.from_percent("7.5")


def test_the_bare_constructor_rejects_a_string() -> None:
    # "7.5" could be either form, so it is refused rather than guessed at.
    with pytest.raises(TypeConstraintError):
        Percentage("7.5")  # type: ignore[arg-type]


def test_rejects_a_float_percent() -> None:
    with pytest.raises(TypeConstraintError, match="float"):
        Percentage.from_percent(7.5)  # type: ignore[arg-type]


def test_rejects_a_float_fraction() -> None:
    with pytest.raises(TypeConstraintError, match="float"):
        Percentage.from_fraction(0.075)  # type: ignore[arg-type]


def test_accepts_a_decimal() -> None:
    assert Percentage.from_percent(Decimal("7.5")).as_fraction() == Decimal("0.075")


def test_applies_to_money() -> None:
    bonus_rate = Percentage.from_percent("10")
    assert bonus_rate.of(Money.parse("120000.00")) == Money.parse("12000.00")


def test_applying_does_not_round() -> None:
    # 7.5% of 1000.01 is 75.00075 — a fraction of a cent. The remainder survives
    # until an explicit quantise at a boundary, so a rate applied across many
    # months cannot compound a rounding error.
    applied = Percentage.from_percent("7.5").of(Money.parse("1000.01"))
    assert applied.amount == Decimal("75.00075")
    assert applied.quantize(CURRENCY_DISPLAY) == Money.parse("75.00")


def test_applying_preserves_currency() -> None:
    rate = Percentage.from_percent("10")
    assert rate.of(Money(Decimal("100.00"), "EUR")).currency == "EUR"


def test_a_rate_may_exceed_one_hundred_percent() -> None:
    # A 150% raise is unusual but not invalid, so no upper bound by default.
    assert Percentage.from_percent("150").as_fraction() == Decimal("1.5")


def test_a_rate_may_be_negative() -> None:
    # A pay cut, or a negative expected return.
    assert Percentage.from_percent("-5").as_fraction() == Decimal("-0.05")


def test_a_probability_is_bounded_at_zero() -> None:
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        Percentage.probability("-1")


def test_a_probability_is_bounded_at_one_hundred() -> None:
    # Bonus probability specifically cannot exceed certainty.
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        Percentage.probability("101")


def test_a_probability_accepts_its_bounds() -> None:
    assert Percentage.probability("0").as_fraction() == Decimal("0")
    assert Percentage.probability("100").as_fraction() == Decimal("1")


def test_rejects_a_non_finite_rate() -> None:
    with pytest.raises(ValidationError, match="finite"):
        Percentage.from_percent("NaN")


def test_rejects_a_non_numeric_string() -> None:
    with pytest.raises(ValidationError, match="not a valid decimal"):
        Percentage.from_percent("ten")


def test_is_immutable() -> None:
    rate = Percentage.from_percent("5")
    with pytest.raises(AttributeError):
        rate.fraction = Decimal("0.1")  # type: ignore[misc]


def test_renders_as_a_percent_for_a_human() -> None:
    assert str(Percentage.from_percent("7.5")) == "7.5%"


def test_zero_is_available() -> None:
    assert Percentage.zero().of(Money.parse("100.00")) == Money.zero()
