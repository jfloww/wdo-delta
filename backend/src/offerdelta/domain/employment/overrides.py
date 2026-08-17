"""The net-pay override.

A verified take-home figure from a paystub, so phase 1 produces a real
comparison before the tax engine exists. The result labels it as user-supplied
and the tax breakdown stays unavailable.

An override reflects one specific set of elections at one point in time. Change
any of them and the figure silently stops describing reality, so it goes STALE
and names the field that invalidated it rather than producing a wrong answer.

Note the consequence: `base_salary` is in the locked set, so the equivalent-
salary solver — which works by varying base salary — cannot run against an
override directly. It depends on the `TaxModel` port instead, which an override
can back by supplying an effective rate at its calibration point.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import ClassVar, Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PayFrequency, PeriodicAmount, PeriodKind
from offerdelta.domain.employment.value_objects import FilingStatus

_RATE_PERIODS: Final = frozenset({PeriodKind.MONTHLY, PeriodKind.ANNUAL})


class OverrideStatus(StrEnum):
    ACTIVE = "ACTIVE"
    STALE = "STALE"


@dataclass(frozen=True)
class OverrideBasis:
    """The inputs an override is calibrated against.

    Widening this set makes overrides go stale more often, so each field is here
    because changing it genuinely changes take-home pay.
    """

    base_salary: Money
    filing_status: FilingStatus
    pay_frequency: PayFrequency
    residence_jurisdiction: str
    work_jurisdiction: str
    pretax_401k_contribution: Money
    hsa_fsa_contribution: Money
    employee_health_premium: Money

    LOCKED_FIELDS: ClassVar[tuple[str, ...]] = (
        "base_salary",
        "filing_status",
        "pay_frequency",
        "residence_jurisdiction",
        "work_jurisdiction",
        "pretax_401k_contribution",
        "hsa_fsa_contribution",
        "employee_health_premium",
    )

    def fingerprint(self) -> str:
        """A stable digest of the locked set.

        Deliberately not the builtin `hash()`, whose value varies between
        processes under hash randomisation — a fingerprint persisted today would
        compare unequal tomorrow and every override would look stale after a
        restart.
        """
        canonical = "\x1f".join(f"{name}={getattr(self, name)}" for name in self.LOCKED_FIELDS)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def differing_fields(self, other: OverrideBasis) -> tuple[str, ...]:
        return tuple(
            name for name in self.LOCKED_FIELDS if getattr(self, name) != getattr(other, name)
        )


@dataclass(frozen=True)
class NetPayOverride:
    """A verified take-home figure, valid only for the basis it was captured against."""

    observed_net_pay: PeriodicAmount
    basis: OverrideBasis
    captured_at: date

    def __post_init__(self) -> None:
        if self.observed_net_pay.period not in _RATE_PERIODS:
            raise ValidationError(
                f"net pay must describe a rate (monthly or annual), got "
                f"{self.observed_net_pay.period}"
            )
        if self.observed_net_pay.money.amount < 0:
            raise ValidationError(f"net pay cannot be negative, got {self.observed_net_pay.money}")

        annual_net = self.observed_net_pay.to_annual().money
        if annual_net > self.basis.base_salary:
            raise ValidationError(
                f"net pay cannot exceed gross: {annual_net} annualised take-home "
                f"against a {self.basis.base_salary} salary. Check that both "
                f"figures describe the same period."
            )

    def status_against(self, current: OverrideBasis) -> OverrideStatus:
        """Whether this override still describes the given inputs."""
        if self.basis.fingerprint() == current.fingerprint():
            return OverrideStatus.ACTIVE
        return OverrideStatus.STALE

    def invalidating_fields(self, current: OverrideBasis) -> tuple[str, ...]:
        """Which locked fields changed, for an error message that says what broke."""
        return self.basis.differing_fields(current)
