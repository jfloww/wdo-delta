"""The employment profile.

Ties compensation, benefits, schedule, and location into one side of a
comparison, and owns the relationship with the net-pay override.

Two things here matter beyond assembly. Residence and work location are
separate fields, because the Auburn-to-New-Jersey case routinely means living
in one state and working in another — which is where the multi-jurisdiction tax
rules live. And the profile derives the override's basis itself, so an override
cannot drift out of step with the profile it describes.
"""

from datetime import date
from decimal import Decimal

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.location import Location
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PayFrequency, PeriodicAmount, PeriodKind
from offerdelta.domain.employment.benefits import Benefits, RetirementMatch
from offerdelta.domain.employment.compensation import Compensation
from offerdelta.domain.employment.overrides import (
    NetPayOverride,
    OverrideStatus,
)
from offerdelta.domain.employment.profile import EmploymentProfile
from offerdelta.domain.employment.value_objects import FilingStatus
from offerdelta.domain.employment.work_schedule import WorkSchedule

BENEFITS = Benefits(
    employee_health_premium=PeriodicAmount(Money.parse("165.00"), PeriodKind.MONTHLY),
    employer_hsa_contribution=PeriodicAmount(Money.parse("750.00"), PeriodKind.ANNUAL),
    employee_hsa_fsa_contribution=PeriodicAmount(Money.parse("1440.00"), PeriodKind.ANNUAL),
    retirement_match=RetirementMatch(
        match_rate=Percentage.from_percent("100"),
        match_limit_rate=Percentage.from_percent("4"),
        vesting_months=24,
    ),
    employee_contribution_rate=Percentage.from_percent("6"),
    pto_days=15,
    paid_holidays=10,
)

SCHEDULE = WorkSchedule(
    weekly_work_hours=Decimal("40"),
    annual_working_weeks=Decimal("48"),
    onsite_days_per_week=Decimal("3"),
    one_way_commute_minutes=Decimal("25"),
)


def _profile(**changes: object) -> EmploymentProfile:
    defaults: dict[str, object] = {
        "label": "Current — Auburn",
        "work_location": Location(state="AL", locality="Auburn"),
        "residence": Location(state="AL", locality="Auburn"),
        "compensation": Compensation(
            base_salary=Money.parse("78000.00"),
            pay_frequency=PayFrequency.BIWEEKLY,
        ),
        "benefits": BENEFITS,
        "schedule": SCHEDULE,
        "tax_year": 2026,
        "filing_status": FilingStatus.SINGLE,
    }
    defaults.update(changes)
    return EmploymentProfile(**defaults)  # type: ignore[arg-type]


def _override(profile: EmploymentProfile) -> NetPayOverride:
    return NetPayOverride(
        observed_net_pay=PeriodicAmount(Money.parse("4820.00"), PeriodKind.MONTHLY),
        basis=profile.override_basis(),
        captured_at=date(2026, 8, 1),
    )


def test_a_profile_carries_its_label() -> None:
    assert _profile().label == "Current — Auburn"


def test_working_and_living_in_the_same_state_is_single_jurisdiction() -> None:
    assert _profile().is_multi_jurisdiction is False


def test_living_and_working_in_different_states_is_multi_jurisdiction() -> None:
    # A New Jersey resident working in New York files both returns and claims a
    # credit. Flagging it here is what lets the tax layer refuse to guess.
    profile = _profile(
        residence=Location(state="NJ", locality="Jersey City"),
        work_location=Location(state="NY", locality="New York City"),
    )
    assert profile.is_multi_jurisdiction is True


def test_the_override_basis_reflects_the_profile() -> None:
    basis = _profile().override_basis()
    assert basis.base_salary == Money.parse("78000.00")
    assert basis.residence_jurisdiction == "US-AL"
    assert basis.work_jurisdiction == "US-AL"


def test_the_override_basis_derives_the_annual_retirement_contribution() -> None:
    # 6% of 78,000. Derived rather than stored, so it cannot disagree with the
    # contribution rate the benefits already record.
    assert _profile().override_basis().pretax_401k_contribution == Money.parse("4680.00")


def test_the_override_basis_uses_the_employees_own_hsa_contribution() -> None:
    # Not the employer's. Employer money never reduces taxable pay for the
    # employee, and including it here would make the basis wrong.
    assert _profile().override_basis().hsa_fsa_contribution == Money.parse("1440.00")


def test_a_profile_with_no_override_reports_none() -> None:
    assert _profile().override_status() is None


def test_an_override_matching_its_profile_is_active() -> None:
    profile = _profile()
    with_override = _profile(net_pay_override=_override(profile))
    assert with_override.override_status() is OverrideStatus.ACTIVE


def test_changing_the_salary_makes_the_override_stale() -> None:
    original = _profile()
    override = _override(original)
    moved = _profile(
        net_pay_override=override,
        compensation=Compensation(
            base_salary=Money.parse("92000.00"),
            pay_frequency=PayFrequency.BIWEEKLY,
        ),
    )
    assert moved.override_status() is OverrideStatus.STALE


def test_a_stale_override_names_what_broke_it() -> None:
    original = _profile()
    override = _override(original)
    moved = _profile(
        net_pay_override=override,
        residence=Location(state="NJ"),
        work_location=Location(state="NY"),
    )
    assert moved.stale_override_fields() == (
        "residence_jurisdiction",
        "work_jurisdiction",
    )


def test_requiring_an_active_override_returns_it_when_valid() -> None:
    profile = _profile()
    with_override = _profile(net_pay_override=_override(profile))
    assert with_override.require_active_override().observed_net_pay.money == Money.parse("4820.00")


def test_requiring_an_active_override_refuses_a_stale_one_by_name() -> None:
    # The engine must not run on a figure that no longer describes the inputs.
    original = _profile()
    override = _override(original)
    moved = _profile(
        net_pay_override=override,
        compensation=Compensation(
            base_salary=Money.parse("92000.00"),
            pay_frequency=PayFrequency.BIWEEKLY,
        ),
    )
    with pytest.raises(ValidationError, match="base_salary"):
        moved.require_active_override()


def test_requiring_an_override_that_does_not_exist_is_an_error() -> None:
    with pytest.raises(ValidationError, match="no net-pay override"):
        _profile().require_active_override()


def test_the_tax_year_must_be_plausible() -> None:
    with pytest.raises(ValidationError, match="tax year"):
        _profile(tax_year=1899)


def test_a_profile_needs_a_label() -> None:
    with pytest.raises(ValidationError, match="label"):
        _profile(label="  ")


def test_is_immutable() -> None:
    profile = _profile()
    with pytest.raises(AttributeError):
        profile.tax_year = 2027  # type: ignore[misc]
