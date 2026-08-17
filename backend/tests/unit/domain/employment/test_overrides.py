"""The net-pay override and its invalidation rule.

An override is a verified take-home figure from a paystub, used so phase 1 can
produce a real comparison before the tax engine exists.

It reflects one specific set of elections at one point in time. Change any of
them and the figure silently stops describing reality — so the override goes
STALE and names the field that invalidated it, rather than quietly producing a
wrong answer.
"""

from datetime import date

import pytest

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PayFrequency, PeriodicAmount, PeriodKind
from offerdelta.domain.employment.overrides import (
    NetPayOverride,
    OverrideBasis,
    OverrideStatus,
)
from offerdelta.domain.employment.value_objects import FilingStatus


def _basis(**changes: object) -> OverrideBasis:
    defaults: dict[str, object] = {
        "base_salary": Money.parse("78000.00"),
        "filing_status": FilingStatus.SINGLE,
        "pay_frequency": PayFrequency.BIWEEKLY,
        "residence_jurisdiction": "US-AL",
        "work_jurisdiction": "US-AL",
        "pretax_401k_contribution": Money.parse("390.00"),
        "hsa_fsa_contribution": Money.parse("120.00"),
        "employee_health_premium": Money.parse("165.00"),
    }
    defaults.update(changes)
    return OverrideBasis(**defaults)  # type: ignore[arg-type]


def _override(basis: OverrideBasis | None = None) -> NetPayOverride:
    return NetPayOverride(
        observed_net_pay=PeriodicAmount(Money.parse("4820.00"), PeriodKind.MONTHLY),
        basis=basis if basis is not None else _basis(),
        captured_at=date(2026, 8, 1),
    )


def test_an_override_matching_its_basis_is_active() -> None:
    override = _override()
    assert override.status_against(_basis()) is OverrideStatus.ACTIVE


def test_changing_a_retirement_contribution_makes_it_stale() -> None:
    # The exact scenario v0.1 handled silently: edit the 401(k) and the stored
    # net pay no longer corresponds to anything.
    override = _override()
    changed = _basis(pretax_401k_contribution=Money.parse("500.00"))
    assert override.status_against(changed) is OverrideStatus.STALE


def test_a_stale_override_names_the_field_that_invalidated_it() -> None:
    override = _override()
    changed = _basis(pretax_401k_contribution=Money.parse("500.00"))
    assert override.invalidating_fields(changed) == ("pretax_401k_contribution",)


def test_every_changed_field_is_named() -> None:
    override = _override()
    changed = _basis(
        base_salary=Money.parse("92000.00"),
        work_jurisdiction="US-NJ",
    )
    assert override.invalidating_fields(changed) == ("base_salary", "work_jurisdiction")


def test_an_unchanged_basis_names_nothing() -> None:
    assert _override().invalidating_fields(_basis()) == ()


def test_changing_the_salary_invalidates_it() -> None:
    # Which is why the equivalent-salary solver cannot run against an override
    # directly, and depends on the TaxModel port instead.
    override = _override()
    changed = _basis(base_salary=Money.parse("92000.00"))
    assert override.status_against(changed) is OverrideStatus.STALE


def test_changing_the_work_jurisdiction_invalidates_it() -> None:
    override = _override()
    changed = _basis(work_jurisdiction="US-NY")
    assert override.status_against(changed) is OverrideStatus.STALE


def test_changing_pay_frequency_invalidates_it() -> None:
    override = _override()
    changed = _basis(pay_frequency=PayFrequency.SEMIMONTHLY)
    assert override.status_against(changed) is OverrideStatus.STALE


def test_changing_filing_status_invalidates_it() -> None:
    override = _override()
    changed = _basis(filing_status=FilingStatus.MARRIED_FILING_JOINTLY)
    assert override.status_against(changed) is OverrideStatus.STALE


def test_the_locked_set_is_exactly_the_documented_fields() -> None:
    # Blueprint section 6.8.1. If a field is added to the basis it must be a
    # deliberate decision, not an accident, because widening the set makes the
    # override go stale more often.
    assert OverrideBasis.LOCKED_FIELDS == (
        "base_salary",
        "filing_status",
        "pay_frequency",
        "residence_jurisdiction",
        "work_jurisdiction",
        "pretax_401k_contribution",
        "hsa_fsa_contribution",
        "employee_health_premium",
    )


def test_equal_bases_fingerprint_identically() -> None:
    assert _basis().fingerprint() == _basis().fingerprint()


def test_different_bases_fingerprint_differently() -> None:
    assert _basis().fingerprint() != _basis(work_jurisdiction="US-NJ").fingerprint()


def test_the_fingerprint_is_stable_across_processes() -> None:
    # A plain hash() would vary per process under hash randomisation, so a
    # fingerprint persisted today would compare unequal tomorrow.
    assert _basis().fingerprint() == (
        OverrideBasis(
            base_salary=Money.parse("78000.00"),
            filing_status=FilingStatus.SINGLE,
            pay_frequency=PayFrequency.BIWEEKLY,
            residence_jurisdiction="US-AL",
            work_jurisdiction="US-AL",
            pretax_401k_contribution=Money.parse("390.00"),
            hsa_fsa_contribution=Money.parse("120.00"),
            employee_health_premium=Money.parse("165.00"),
        ).fingerprint()
    )


def test_net_pay_must_describe_a_rate() -> None:
    with pytest.raises(ValidationError, match="rate"):
        NetPayOverride(
            observed_net_pay=PeriodicAmount(Money.parse("4820.00"), PeriodKind.ONE_TIME),
            basis=_basis(),
            captured_at=date(2026, 8, 1),
        )


def test_net_pay_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="negative"):
        NetPayOverride(
            observed_net_pay=PeriodicAmount(Money.parse("-100.00"), PeriodKind.MONTHLY),
            basis=_basis(),
            captured_at=date(2026, 8, 1),
        )


def test_net_pay_cannot_exceed_gross() -> None:
    # Take-home above gross means the figures were entered wrong or one of them
    # is for a different period.
    with pytest.raises(ValidationError, match="exceed"):
        NetPayOverride(
            observed_net_pay=PeriodicAmount(Money.parse("9000.00"), PeriodKind.MONTHLY),
            basis=_basis(base_salary=Money.parse("78000.00")),
            captured_at=date(2026, 8, 1),
        )


def test_an_override_is_immutable() -> None:
    override = _override()
    with pytest.raises(AttributeError):
        override.captured_at = date(2027, 1, 1)  # type: ignore[misc]
