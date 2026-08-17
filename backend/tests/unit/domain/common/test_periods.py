"""Periods and pay-frequency conversion.

Amounts are never bare. Blueprint section 6.1 requires every amount entering or
leaving the engine to carry its period, because mismatched periods are the most
common source of silently wrong financial numbers — a monthly figure summed as
though it were annual is off by twelve and looks entirely plausible.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PayFrequency, PeriodicAmount, PeriodKind
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY


def test_monthly_converts_to_annual() -> None:
    monthly = PeriodicAmount(Money.parse("1200.00"), PeriodKind.MONTHLY)
    assert monthly.to_annual().money == Money.parse("14400.00")


def test_annual_converts_to_monthly() -> None:
    annual = PeriodicAmount(Money.parse("14400.00"), PeriodKind.ANNUAL)
    assert annual.to_monthly().money == Money.parse("1200.00")


def test_conversion_reports_the_resulting_period() -> None:
    monthly = PeriodicAmount(Money.parse("100.00"), PeriodKind.MONTHLY)
    assert monthly.to_annual().period is PeriodKind.ANNUAL


def test_converting_to_its_own_period_is_a_no_op() -> None:
    monthly = PeriodicAmount(Money.parse("100.00"), PeriodKind.MONTHLY)
    assert monthly.to_monthly() == monthly


def test_annual_to_monthly_keeps_full_precision() -> None:
    # 1000 / 12 does not terminate. The remainder is kept rather than rounded,
    # so that quantising once at a display boundary cannot compound.
    annual = PeriodicAmount(Money.parse("1000.00"), PeriodKind.ANNUAL)
    monthly = annual.to_monthly()
    assert monthly.money.quantize(CURRENCY_DISPLAY) == Money.parse("83.33")
    assert monthly.money.amount != Decimal("83.33")


def test_a_one_time_amount_cannot_be_annualised() -> None:
    # A signing bonus is an event, not a rate. Multiplying it by twelve is the
    # kind of error explicit periods exist to make impossible.
    signing_bonus = PeriodicAmount(Money.parse("8000.00"), PeriodKind.ONE_TIME)
    with pytest.raises(ValueError, match="ONE_TIME"):
        signing_bonus.to_annual()


def test_a_cumulative_total_cannot_be_annualised() -> None:
    total = PeriodicAmount(Money.parse("50000.00"), PeriodKind.HORIZON_CUMULATIVE)
    with pytest.raises(ValueError, match="HORIZON_CUMULATIVE"):
        total.to_monthly()


def test_weekly_pay_annualises_over_fifty_two_periods() -> None:
    assert PayFrequency.WEEKLY.periods_per_year == 52


def test_biweekly_pay_annualises_over_twenty_six_periods() -> None:
    assert PayFrequency.BIWEEKLY.periods_per_year == 26


def test_semimonthly_pay_annualises_over_twenty_four_periods() -> None:
    assert PayFrequency.SEMIMONTHLY.periods_per_year == 24


def test_biweekly_and_semimonthly_are_not_interchangeable() -> None:
    # The classic payroll error. The same paycheck read as semimonthly rather
    # than biweekly loses two periods — understating annual pay by ~7.7%.
    paycheck = Money.parse("2000.00")
    biweekly = PeriodicAmount.from_paycheck(paycheck, PayFrequency.BIWEEKLY)
    semimonthly = PeriodicAmount.from_paycheck(paycheck, PayFrequency.SEMIMONTHLY)

    assert biweekly.money == Money.parse("52000.00")
    assert semimonthly.money == Money.parse("48000.00")


def test_a_paycheck_annualises_to_an_annual_amount() -> None:
    annual = PeriodicAmount.from_paycheck(Money.parse("2000.00"), PayFrequency.MONTHLY)
    assert annual.period is PeriodKind.ANNUAL
    assert annual.money == Money.parse("24000.00")


def test_amounts_of_the_same_period_add() -> None:
    rent = PeriodicAmount(Money.parse("1800.00"), PeriodKind.MONTHLY)
    utilities = PeriodicAmount(Money.parse("150.00"), PeriodKind.MONTHLY)
    assert (rent + utilities).money == Money.parse("1950.00")


def test_amounts_of_different_periods_refuse_to_add() -> None:
    monthly = PeriodicAmount(Money.parse("100.00"), PeriodKind.MONTHLY)
    annual = PeriodicAmount(Money.parse("100.00"), PeriodKind.ANNUAL)
    with pytest.raises(ValueError, match="period"):
        _ = monthly + annual


def test_is_immutable() -> None:
    amount = PeriodicAmount(Money.parse("1.00"), PeriodKind.MONTHLY)
    with pytest.raises(AttributeError):
        amount.period = PeriodKind.ANNUAL  # type: ignore[misc]


def test_renders_the_period_for_a_human() -> None:
    monthly = PeriodicAmount(Money.parse("1800.00"), PeriodKind.MONTHLY)
    assert str(monthly) == "1800.00 USD / MONTHLY"
