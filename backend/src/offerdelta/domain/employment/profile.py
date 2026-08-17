"""The employment profile — one side of a comparison.

Ties compensation, benefits, schedule, and location together, and owns the
relationship with the net-pay override.

Residence and work location are separate fields because the Auburn-to-New-Jersey
case routinely means living in one state and working in another. That is where
the hard tax rules live: there is no NY-NJ reciprocal agreement, so a New Jersey
resident working in New York files a New York nonresident return and claims a
New Jersey credit for taxes paid elsewhere.

The profile derives the override's basis itself, so a stored override can never
drift out of step with the profile it claims to describe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.location import Location
from offerdelta.domain.common.money import Money
from offerdelta.domain.employment.benefits import Benefits
from offerdelta.domain.employment.compensation import Compensation
from offerdelta.domain.employment.overrides import (
    NetPayOverride,
    OverrideBasis,
    OverrideStatus,
)
from offerdelta.domain.employment.value_objects import FilingStatus
from offerdelta.domain.employment.work_schedule import WorkSchedule

_EARLIEST_TAX_YEAR: Final = 1913  # ratification of the Sixteenth Amendment
_LATEST_TAX_YEAR: Final = 2100


@dataclass(frozen=True)
class EmploymentProfile:
    """A current job or a candidate offer, with everything needed to price it."""

    label: str
    work_location: Location
    residence: Location
    compensation: Compensation
    benefits: Benefits
    schedule: WorkSchedule
    tax_year: int
    filing_status: FilingStatus
    net_pay_override: NetPayOverride | None = None

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValidationError("an employment profile needs a non-empty label")
        if not _EARLIEST_TAX_YEAR <= self.tax_year <= _LATEST_TAX_YEAR:
            raise ValidationError(
                f"tax year {self.tax_year} is outside the plausible range "
                f"{_EARLIEST_TAX_YEAR}-{_LATEST_TAX_YEAR}"
            )

    @property
    def is_multi_jurisdiction(self) -> bool:
        """Whether residence and work fall in different tax jurisdictions.

        When true, a single state's rules cannot price this profile on their
        own, and the tax layer must apply the nonresident-credit path rather
        than quietly using one state.
        """
        return self.residence.jurisdiction_code != self.work_location.jurisdiction_code

    @property
    def annual_retirement_contribution(self) -> Money:
        """The employee's own pre-tax retirement contribution for a year.

        Derived from the contribution rate rather than stored separately, so the
        two cannot disagree.
        """
        return self.benefits.employee_contribution_rate.of(self.compensation.base_salary)

    def override_basis(self) -> OverrideBasis:
        """The inputs any net-pay override for this profile is calibrated against."""
        return OverrideBasis(
            base_salary=self.compensation.base_salary,
            filing_status=self.filing_status,
            pay_frequency=self.compensation.pay_frequency,
            residence_jurisdiction=self.residence.jurisdiction_code,
            work_jurisdiction=self.work_location.jurisdiction_code,
            pretax_401k_contribution=self.annual_retirement_contribution,
            hsa_fsa_contribution=self.benefits.employee_hsa_fsa_contribution.to_annual().money,
            employee_health_premium=self.benefits.annual_health_premium(),
        )

    def override_status(self) -> OverrideStatus | None:
        """Whether the stored override still describes this profile, if there is one."""
        if self.net_pay_override is None:
            return None
        return self.net_pay_override.status_against(self.override_basis())

    def stale_override_fields(self) -> tuple[str, ...]:
        """Which locked fields have drifted since the override was captured."""
        if self.net_pay_override is None:
            return ()
        return self.net_pay_override.invalidating_fields(self.override_basis())

    def require_active_override(self) -> NetPayOverride:
        """The override, or a domain error naming exactly what invalidated it.

        The engine calls this rather than reading the field directly, so a
        figure that no longer describes the inputs cannot reach a calculation.
        """
        if self.net_pay_override is None:
            raise ValidationError(
                f"{self.label!r} has no net-pay override; supply one or use a computed tax model"
            )
        if self.override_status() is OverrideStatus.STALE:
            changed = ", ".join(self.stale_override_fields())
            raise ValidationError(
                f"the net-pay override for {self.label!r} is stale: {changed} "
                f"changed since it was captured on "
                f"{self.net_pay_override.captured_at}. Supply a new verified "
                f"net-pay figure."
            )
        return self.net_pay_override
