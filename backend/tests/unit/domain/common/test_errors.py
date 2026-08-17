"""The domain error hierarchy.

Domain rules raise domain errors, so the API layer can map them to responses
without string-matching on messages. The hierarchy is shaped by what a caller
needs to distinguish, not by how many classes it can contain.

Each domain error also subclasses the builtin it replaces, so ordinary
`except ValueError` handling keeps working and no caller is forced to import
domain types to catch a bad input.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import (
    AllocationError,
    CurrencyMismatchError,
    DomainError,
    PeriodMismatchError,
    TypeConstraintError,
    ValidationError,
)
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind


def test_validation_error_is_a_domain_error() -> None:
    assert issubclass(ValidationError, DomainError)


def test_validation_error_is_also_a_value_error() -> None:
    # So `except ValueError` at a boundary still catches domain rule violations.
    assert issubclass(ValidationError, ValueError)


def test_type_constraint_error_is_also_a_type_error() -> None:
    assert issubclass(TypeConstraintError, TypeError)


def test_specific_errors_narrow_validation_error() -> None:
    assert issubclass(CurrencyMismatchError, ValidationError)
    assert issubclass(PeriodMismatchError, ValidationError)
    assert issubclass(AllocationError, ValidationError)


def test_a_float_amount_raises_a_type_constraint_error() -> None:
    with pytest.raises(TypeConstraintError):
        Money(1234.56)  # type: ignore[arg-type]


def test_an_invalid_currency_raises_a_validation_error() -> None:
    with pytest.raises(ValidationError):
        Money(Decimal("1"), "US")


def test_combining_currencies_raises_a_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError):
        Money(Decimal("1"), "USD") + Money(Decimal("1"), "EUR")


def test_a_bad_allocation_raises_an_allocation_error() -> None:
    with pytest.raises(AllocationError):
        Money.parse("10.00").allocate([])


def test_allocating_an_inexact_amount_raises_an_allocation_error() -> None:
    with pytest.raises(AllocationError, match="quantise"):
        Money.parse("10.005").allocate([1, 1])


def test_adding_different_periods_raises_a_period_mismatch() -> None:
    monthly = PeriodicAmount(Money.parse("1.00"), PeriodKind.MONTHLY)
    annual = PeriodicAmount(Money.parse("1.00"), PeriodKind.ANNUAL)
    with pytest.raises(PeriodMismatchError):
        _ = monthly + annual


def test_annualising_a_one_time_amount_raises_a_validation_error() -> None:
    bonus = PeriodicAmount(Money.parse("8000.00"), PeriodKind.ONE_TIME)
    with pytest.raises(ValidationError):
        bonus.to_annual()


def test_every_domain_error_carries_a_message() -> None:
    with pytest.raises(CurrencyMismatchError) as caught:
        Money(Decimal("1"), "USD") + Money(Decimal("1"), "EUR")
    assert "USD" in str(caught.value)
    assert "EUR" in str(caught.value)
