"""Benefits: health cost, retirement match, and paid time off.

The employer match is the piece with real logic. "100% of the first 4%" means
the employer matches every dollar you contribute until your contribution
reaches 4% of salary — so contributing 6% earns the same match as contributing
4%, and contributing 2% earns only 2%.

Getting this wrong in either direction misprices an offer by thousands a year,
and it is the benefit people most often compare by eye.
"""

from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.employment.benefits import Benefits, RetirementMatch

SALARY = Money.parse("120000.00")

# The common US arrangement: dollar-for-dollar on the first 4% of salary.
FULL_MATCH_TO_4 = RetirementMatch(
    match_rate=Percentage.from_percent("100"),
    match_limit_rate=Percentage.from_percent("4"),
    vesting_months=24,
)


def test_contributing_above_the_limit_earns_only_the_capped_match() -> None:
    # Contributing 6% does not earn a 6% match; the cap binds at 4%.
    match = FULL_MATCH_TO_4.annual_match(SALARY, Percentage.from_percent("6"))
    assert match == Money.parse("4800.00")


def test_contributing_exactly_the_limit_earns_the_full_match() -> None:
    match = FULL_MATCH_TO_4.annual_match(SALARY, Percentage.from_percent("4"))
    assert match == Money.parse("4800.00")


def test_contributing_below_the_limit_earns_only_what_was_contributed() -> None:
    # The money left on the table that people miss when comparing offers by eye.
    match = FULL_MATCH_TO_4.annual_match(SALARY, Percentage.from_percent("2"))
    assert match == Money.parse("2400.00")


def test_contributing_nothing_earns_nothing() -> None:
    match = FULL_MATCH_TO_4.annual_match(SALARY, Percentage.zero())
    assert match.is_zero()


def test_a_partial_match_rate_halves_the_employer_contribution() -> None:
    # "50% of the first 6%" is worth 3% of salary at full participation.
    half_match = RetirementMatch(
        match_rate=Percentage.from_percent("50"),
        match_limit_rate=Percentage.from_percent("6"),
        vesting_months=0,
    )
    assert half_match.annual_match(SALARY, Percentage.from_percent("6")) == Money.parse("3600.00")


def test_a_partial_match_below_the_limit_scales_with_the_contribution() -> None:
    half_match = RetirementMatch(
        match_rate=Percentage.from_percent("50"),
        match_limit_rate=Percentage.from_percent("6"),
        vesting_months=0,
    )
    assert half_match.annual_match(SALARY, Percentage.from_percent("2")) == Money.parse("1200.00")


def test_no_match_offered_means_no_employer_money() -> None:
    none_offered = RetirementMatch(
        match_rate=Percentage.zero(),
        match_limit_rate=Percentage.zero(),
        vesting_months=0,
    )
    assert none_offered.annual_match(SALARY, Percentage.from_percent("10")).is_zero()


def test_match_vests_over_its_schedule() -> None:
    assert FULL_MATCH_TO_4.vested_fraction(month=12) == Decimal("0.5")


def test_match_is_fully_vested_at_the_end_of_the_schedule() -> None:
    assert FULL_MATCH_TO_4.vested_fraction(month=24) == Decimal(1)


def test_match_vesting_is_capped_at_full() -> None:
    assert FULL_MATCH_TO_4.vested_fraction(month=60) == Decimal(1)


def test_an_immediately_vesting_match_is_fully_vested_from_the_start() -> None:
    immediate = RetirementMatch(
        match_rate=Percentage.from_percent("100"),
        match_limit_rate=Percentage.from_percent("4"),
        vesting_months=0,
    )
    assert immediate.vested_fraction(month=0) == Decimal(1)


def test_unvested_match_is_reported_separately_from_vested() -> None:
    # Blueprint section 6.3: unvested employer money is shown apart from wealth,
    # because leaving early forfeits it.
    vested = FULL_MATCH_TO_4.vested_match(SALARY, Percentage.from_percent("4"), month=12)
    assert vested == Money.parse("2400.00")


def test_a_match_rate_cannot_exceed_one_hundred_percent() -> None:
    with pytest.raises(ValidationError, match="between 0% and 100%"):
        RetirementMatch(
            match_rate=Percentage.from_percent("150"),
            match_limit_rate=Percentage.from_percent("4"),
            vesting_months=0,
        )


def test_a_negative_vesting_schedule_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        RetirementMatch(
            match_rate=Percentage.from_percent("100"),
            match_limit_rate=Percentage.from_percent("4"),
            vesting_months=-1,
        )


# --- Benefits --------------------------------------------------------------


def _benefits(**changes: object) -> Benefits:
    defaults: dict[str, object] = {
        "employee_health_premium": PeriodicAmount(Money.parse("165.00"), PeriodKind.MONTHLY),
        "employer_hsa_contribution": PeriodicAmount(Money.parse("750.00"), PeriodKind.ANNUAL),
        "retirement_match": FULL_MATCH_TO_4,
        "employee_contribution_rate": Percentage.from_percent("6"),
        "pto_days": 15,
        "paid_holidays": 10,
    }
    defaults.update(changes)
    return Benefits(**defaults)  # type: ignore[arg-type]


def test_benefits_expose_the_annual_health_premium() -> None:
    assert _benefits().annual_health_premium() == Money.parse("1980.00")


def test_benefits_compute_the_employer_match_from_the_contribution_rate() -> None:
    assert _benefits().annual_employer_match(SALARY) == Money.parse("4800.00")


def test_total_paid_days_off_combine_pto_and_holidays() -> None:
    assert _benefits().total_paid_days_off == 25


def test_a_health_premium_must_describe_a_rate() -> None:
    with pytest.raises(ValidationError, match="rate"):
        _benefits(
            employee_health_premium=PeriodicAmount(Money.parse("165.00"), PeriodKind.ONE_TIME)
        )


def test_a_negative_premium_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        _benefits(
            employee_health_premium=PeriodicAmount(Money.parse("-165.00"), PeriodKind.MONTHLY)
        )


def test_negative_pto_is_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        _benefits(pto_days=-1)


def test_is_immutable() -> None:
    benefits = _benefits()
    with pytest.raises(AttributeError):
        benefits.pto_days = 30  # type: ignore[misc]
