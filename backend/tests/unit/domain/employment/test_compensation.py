"""Compensation: salary, bonuses, and equity.

The correction this file exists to lock in: equity vests as ordinary income and
is taxed on vest. Counting the gross grant as wealth overstates every offer
containing equity, which is precisely the case where the comparison most needs
to be right.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PayFrequency
from offerdelta.domain.common.rounding import CURRENCY_DISPLAY
from offerdelta.domain.employment.compensation import (
    Compensation,
    EquityGrant,
    SigningBonus,
    TargetBonus,
)

# A standard four-year grant with a one-year cliff, sized so the arithmetic is exact.
GRANT = EquityGrant(
    total_value=Money.parse("96000.00"),
    vesting_months=48,
    cliff_months=12,
    estimated_withholding_rate=Percentage.from_percent("30"),
)


# --- Target bonus ----------------------------------------------------------


def test_a_target_bonus_is_a_share_of_base_salary() -> None:
    bonus = TargetBonus(
        rate=Percentage.from_percent("15"),
        probability=Percentage.from_percent("100"),
    )
    assert bonus.expected_value(Money.parse("120000.00")) == Money.parse("18000.00")


def test_an_uncertain_bonus_is_discounted_by_its_probability() -> None:
    # A 15% target paid 80% of the time is worth 12% of base in expectation.
    bonus = TargetBonus(
        rate=Percentage.from_percent("15"),
        probability=Percentage.from_percent("80"),
    )
    assert bonus.expected_value(Money.parse("120000.00")) == Money.parse("14400.00")


def test_a_bonus_that_never_pays_is_worth_nothing() -> None:
    bonus = TargetBonus(
        rate=Percentage.from_percent("15"),
        probability=Percentage.from_percent("0"),
    )
    assert bonus.expected_value(Money.parse("120000.00")).is_zero()


def test_a_bonus_probability_cannot_exceed_certainty() -> None:
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        TargetBonus(
            rate=Percentage.from_percent("15"),
            probability=Percentage.from_percent("150"),
        )


# --- Equity vesting --------------------------------------------------------


def test_nothing_vests_before_the_cliff() -> None:
    assert GRANT.gross_vested_by(month=11).is_zero()


def test_the_cliff_releases_its_full_accrued_share() -> None:
    # Twelve of forty-eight months is a quarter of a 96,000 grant.
    assert GRANT.gross_vested_by(month=12) == Money.parse("24000.00")


def test_vesting_continues_after_the_cliff() -> None:
    assert GRANT.gross_vested_by(month=24) == Money.parse("48000.00")


def test_the_grant_fully_vests_at_the_end_of_its_schedule() -> None:
    assert GRANT.gross_vested_by(month=48) == Money.parse("96000.00")


def test_vesting_is_capped_at_the_grant() -> None:
    # Staying past the schedule does not keep accruing this grant.
    assert GRANT.gross_vested_by(month=72) == Money.parse("96000.00")


def test_a_grant_with_no_cliff_vests_from_the_first_month() -> None:
    grant = EquityGrant(
        total_value=Money.parse("48000.00"),
        vesting_months=48,
        cliff_months=0,
        estimated_withholding_rate=Percentage.from_percent("30"),
    )
    assert grant.gross_vested_by(month=1) == Money.parse("1000.00")


def test_vested_equity_is_counted_net_of_withholding() -> None:
    # The v0.2 correction. 24,000 gross at a 30% withholding rate is 16,800 of
    # actual wealth, not 24,000.
    assert GRANT.net_vested_by(month=12) == Money.parse("16800.00")


def test_net_vesting_tracks_gross_across_the_schedule() -> None:
    assert GRANT.net_vested_by(month=48) == Money.parse("67200.00")


def test_net_vesting_does_not_round_early() -> None:
    grant = EquityGrant(
        total_value=Money.parse("100000.00"),
        vesting_months=48,
        cliff_months=12,
        estimated_withholding_rate=Percentage.from_percent("22"),
    )
    net = grant.net_vested_by(month=13)
    assert net.quantize(CURRENCY_DISPLAY) == Money.parse("21125.00")


def test_a_cliff_cannot_outlast_the_vesting_schedule() -> None:
    with pytest.raises(ValidationError, match="cliff"):
        EquityGrant(
            total_value=Money.parse("96000.00"),
            vesting_months=48,
            cliff_months=60,
            estimated_withholding_rate=Percentage.from_percent("30"),
        )


def test_a_grant_must_vest_over_at_least_one_month() -> None:
    with pytest.raises(ValidationError, match="at least one month"):
        EquityGrant(
            total_value=Money.parse("96000.00"),
            vesting_months=0,
            cliff_months=0,
            estimated_withholding_rate=Percentage.from_percent("30"),
        )


def test_a_withholding_rate_cannot_exceed_the_grant() -> None:
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        EquityGrant(
            total_value=Money.parse("96000.00"),
            vesting_months=48,
            cliff_months=12,
            estimated_withholding_rate=Percentage.from_percent("120"),
        )


def test_vesting_before_the_grant_starts_is_zero() -> None:
    assert GRANT.gross_vested_by(month=0).is_zero()


def test_a_negative_month_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        GRANT.gross_vested_by(month=-1)


# --- Signing bonus ---------------------------------------------------------


def test_a_signing_bonus_outside_its_clawback_is_kept() -> None:
    bonus = SigningBonus(amount=Money.parse("10000.00"), repayment_months=12)
    assert bonus.is_at_risk(month=13) is False


def test_a_signing_bonus_inside_its_clawback_is_at_risk() -> None:
    # Leaving in month 6 of a 12-month clawback means repaying it, which changes
    # the break-even picture rather than the headline number.
    bonus = SigningBonus(amount=Money.parse("10000.00"), repayment_months=12)
    assert bonus.is_at_risk(month=6) is True


def test_a_bonus_with_no_clawback_is_never_at_risk() -> None:
    bonus = SigningBonus(amount=Money.parse("10000.00"), repayment_months=0)
    assert bonus.is_at_risk(month=1) is False


# --- Compensation ----------------------------------------------------------


def test_compensation_requires_a_non_negative_salary() -> None:
    with pytest.raises(ValidationError, match="negative"):
        Compensation(
            base_salary=Money.parse("-1.00"),
            pay_frequency=PayFrequency.BIWEEKLY,
        )


def test_compensation_defaults_to_no_extras() -> None:
    comp = Compensation(
        base_salary=Money.parse("78000.00"),
        pay_frequency=PayFrequency.BIWEEKLY,
    )
    assert comp.signing_bonus is None
    assert comp.target_bonus is None
    assert comp.equity is None


def test_expected_first_year_cash_includes_bonuses() -> None:
    comp = Compensation(
        base_salary=Money.parse("120000.00"),
        pay_frequency=PayFrequency.BIWEEKLY,
        signing_bonus=SigningBonus(amount=Money.parse("10000.00"), repayment_months=12),
        target_bonus=TargetBonus(
            rate=Percentage.from_percent("15"),
            probability=Percentage.from_percent("80"),
        ),
    )
    assert comp.expected_first_year_cash() == Money.parse("144400.00")


def test_expected_first_year_cash_excludes_equity() -> None:
    # Equity is wealth, not cash, and is reported on its own track.
    comp = Compensation(
        base_salary=Money.parse("120000.00"),
        pay_frequency=PayFrequency.BIWEEKLY,
        equity=GRANT,
    )
    assert comp.expected_first_year_cash() == Money.parse("120000.00")


def test_a_raise_rate_may_be_zero() -> None:
    comp = Compensation(
        base_salary=Money.parse("78000.00"),
        pay_frequency=PayFrequency.BIWEEKLY,
        expected_annual_raise=Percentage.zero(),
    )
    assert comp.expected_annual_raise.as_fraction() == Decimal("0")


def test_is_immutable() -> None:
    comp = Compensation(
        base_salary=Money.parse("78000.00"),
        pay_frequency=PayFrequency.BIWEEKLY,
    )
    with pytest.raises(AttributeError):
        comp.base_salary = Money.parse("1.00")  # type: ignore[misc]
