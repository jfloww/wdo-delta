"""The negotiation gap solver.

Answers "the offer is short by this much — which single change would close it?"

Each lever is evaluated independently. Multi-variable optimisation is
deliberately out of scope: a negotiation happens one ask at a time, and a
combined answer nobody can take to a recruiter is worth less than four they can.

Feasibility is reported rather than assumed. An ask of 40,000 more base salary
is arithmetically correct and practically useless, so every option states
whether it falls inside its bounds and why.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from offerdelta.domain.common.errors import ValidationError
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.engine import ComparisonEngine, default_calculators
from offerdelta.domain.costs.items import CostProfile
from offerdelta.domain.solvers.equivalent_salary import (
    SolverBounds,
    solve_equivalent_salary,
)


class NegotiationLever(StrEnum):
    """A single term that could be renegotiated."""

    BASE_SALARY = "BASE_SALARY"
    SIGNING_BONUS = "SIGNING_BONUS"
    RELOCATION_REIMBURSEMENT = "RELOCATION_REIMBURSEMENT"
    REMOTE_DAYS = "REMOTE_DAYS"


@dataclass(frozen=True)
class NegotiationOption:
    """One way to close the gap, and whether it is actually reachable."""

    lever: NegotiationLever
    feasible: bool
    note: str
    required_amount: Money | None = None
    required_days: Decimal | None = None


@dataclass(frozen=True)
class NegotiationGapResult:
    """How far short the offer falls, and the single changes that would fix it."""

    gap: Money
    options: tuple[NegotiationOption, ...]

    @property
    def needs_negotiation(self) -> bool:
        return self.gap.amount > 0


def with_onsite_days(context: CalculationContext, days: Decimal) -> CalculationContext:
    """The same context with a different onsite pattern.

    Commute cash scales with the change, because a COMMUTE cost is defined as
    one that falls to zero at zero onsite days. Scaling only the schedule would
    cut the commute *time* while leaving its cash untouched, which would make a
    fully remote role still appear to burn fuel.
    """
    schedule = context.employment.schedule
    original = schedule.onsite_days_per_week

    ratio = Decimal(0) if original == 0 else days / original

    scaled_items = tuple(
        replace(
            item,
            amount=PeriodicAmount(item.amount.money * ratio, item.amount.period),
        )
        if item.category.value.startswith("COMMUTE_")
        else item
        for item in context.costs.items
    )

    return replace(
        context,
        employment=replace(
            context.employment,
            schedule=replace(schedule, onsite_days_per_week=days),
        ),
        costs=CostProfile(items=scaled_items),
    )


def _first_year_cash(context: CalculationContext) -> Money:
    return ComparisonEngine(default_calculators()).calculate(context).total_cash


def solve_negotiation_gap(
    *,
    current: CalculationContext,
    candidate: CalculationContext,
    bounds: SolverBounds,
) -> NegotiationGapResult:
    """Measure the shortfall and evaluate each lever independently."""
    target = _first_year_cash(current)
    achieved = _first_year_cash(candidate)
    gap = target - achieved
    if gap.amount < 0:
        gap = Money.zero(target.currency)

    options = (
        _salary_option(current, candidate, bounds, gap),
        _signing_bonus_option(candidate, gap),
        _relocation_option(gap),
        _remote_days_option(current, candidate, gap),
    )

    # Feasible asks first: a negotiation list is read top-down, and an
    # unreachable option belongs at the bottom rather than the middle.
    ordered = tuple(sorted(options, key=lambda option: not option.feasible))
    return NegotiationGapResult(gap=gap, options=ordered)


def _salary_option(
    current: CalculationContext,
    candidate: CalculationContext,
    bounds: SolverBounds,
    gap: Money,
) -> NegotiationOption:
    if gap.is_zero():
        return NegotiationOption(
            lever=NegotiationLever.BASE_SALARY,
            feasible=True,
            note="no raise needed; the offer already matches",
            required_amount=Money.zero(gap.currency),
        )

    try:
        solved = solve_equivalent_salary(current=current, candidate=candidate, bounds=bounds)
    except ValidationError as error:
        # The solver's own message already says why — an unbracketed target or a
        # non-monotone curve — so it becomes the note rather than being swallowed.
        return NegotiationOption(
            lever=NegotiationLever.BASE_SALARY,
            feasible=False,
            note=f"no salary within {bounds.lower}-{bounds.upper} closes the gap: {error}",
        )

    increase = solved.equivalent_salary - candidate.employment.compensation.base_salary
    return NegotiationOption(
        lever=NegotiationLever.BASE_SALARY,
        feasible=increase.amount > 0,
        note=(
            f"raise base salary to {solved.equivalent_salary} "
            f"({solved.tax_model_name}, {solved.calibration_distance} from calibration)"
        ),
        required_amount=increase,
    )


def _signing_bonus_option(candidate: CalculationContext, gap: Money) -> NegotiationOption:
    """Gross up the gap so what lands after tax actually covers it.

    Asking for the gap itself would ask for too little: tax takes its share on
    the way in, and the recruiter hears the gross number.
    """
    if gap.is_zero():
        return NegotiationOption(
            lever=NegotiationLever.SIGNING_BONUS,
            feasible=True,
            note="no bonus needed; the offer already matches",
            required_amount=Money.zero(gap.currency),
        )

    probe = Money.parse("10000.00", gap.currency)
    kept_share = candidate.tax_model.after_tax_one_time(probe).amount / probe.amount
    if kept_share <= 0:
        return NegotiationOption(
            lever=NegotiationLever.SIGNING_BONUS,
            feasible=False,
            note="the tax model keeps nothing from a one-time payment",
        )

    gross = gap / kept_share
    return NegotiationOption(
        lever=NegotiationLever.SIGNING_BONUS,
        feasible=True,
        note=(
            f"ask for a {gross} signing bonus; roughly "
            f"{(Decimal(1) - kept_share) * 100:.0f}% is withheld, so a bonus equal "
            f"to the gap would fall short"
        ),
        required_amount=gross,
    )


def _relocation_option(gap: Money) -> NegotiationOption:
    """Relocation reimbursement is modelled as cash in hand, so it matches the gap."""
    return NegotiationOption(
        lever=NegotiationLever.RELOCATION_REIMBURSEMENT,
        feasible=True,
        note=(
            "no reimbursement needed; the offer already matches"
            if gap.is_zero()
            else f"ask for {gap} of relocation reimbursement"
        ),
        required_amount=gap,
    )


def _remote_days_option(
    current: CalculationContext, candidate: CalculationContext, gap: Money
) -> NegotiationOption:
    """Search downward through onsite patterns for the first that closes the gap.

    Half-day steps, because hybrid schedules are negotiated in days rather than
    in continuous quantities, and an answer of 2.37 days is not an ask anyone
    can make.
    """
    if gap.is_zero():
        return NegotiationOption(
            lever=NegotiationLever.REMOTE_DAYS,
            feasible=True,
            note="no change needed; the offer already matches",
            required_days=candidate.employment.schedule.onsite_days_per_week,
        )

    target = _first_year_cash(current)
    onsite = candidate.employment.schedule.onsite_days_per_week

    step = Decimal("0.5")
    days = onsite
    while days >= 0:
        if _first_year_cash(with_onsite_days(candidate, days)).amount >= target.amount:
            saved = onsite - days
            return NegotiationOption(
                lever=NegotiationLever.REMOTE_DAYS,
                feasible=True,
                note=f"drop to {days} onsite days per week ({saved} fewer)",
                required_days=days,
            )
        days -= step

    fully_remote = _first_year_cash(with_onsite_days(candidate, Decimal(0)))
    shortfall = target - fully_remote
    return NegotiationOption(
        lever=NegotiationLever.REMOTE_DAYS,
        feasible=False,
        note=(
            f"even fully remote leaves {shortfall} of the gap open; commuting "
            f"costs less than the shortfall"
        ),
    )
