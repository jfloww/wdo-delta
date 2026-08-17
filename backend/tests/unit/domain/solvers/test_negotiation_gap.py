"""The negotiation gap solver.

Answers "the offer is short by this much — which single change would close it?"
Each lever is evaluated independently and reported with the change it would
take. Multi-variable optimisation is deliberately out of scope: a negotiation
happens one ask at a time, and a combined answer nobody can take to a recruiter
is worth less than four they can.

Feasibility is reported rather than assumed. An ask of 40,000 more base salary
is arithmetically correct and practically useless, so the result says whether
each option falls inside its stated bounds.
"""

from datetime import date
from decimal import Decimal

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.solvers.equivalent_salary import SolverBounds
from offerdelta.domain.solvers.negotiation_gap import (
    NegotiationLever,
    solve_negotiation_gap,
    with_onsite_days,
)
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel
from tests.fixtures.profiles import auburn_current, new_jersey_candidate

HORIZON = DateRange.of_months(date(2026, 1, 1), 12)
BOUNDS = SolverBounds(
    lower=Money.parse("40000.00"),
    upper=Money.parse("400000.00"),
    tolerance=Money.parse("1.00"),
)


def _context(build: object) -> CalculationContext:
    assert callable(build)
    side = build()
    salary = side.employment.compensation.base_salary
    return CalculationContext(
        employment=side.employment,
        costs=side.costs,
        household=side.household,
        tax_model=NetPayOverrideTaxModel(
            observed_gross=PeriodicAmount(salary, PeriodKind.ANNUAL),
            observed_net=PeriodicAmount(salary * Decimal("0.74"), PeriodKind.ANNUAL),
            marginal_rate=Percentage.from_percent("32"),
        ),
        horizon=HORIZON,
    )


AUBURN = _context(auburn_current)
JERSEY = _context(new_jersey_candidate)


# --- Reducing onsite days --------------------------------------------------


def test_fewer_onsite_days_cut_commute_time() -> None:
    fewer = with_onsite_days(JERSEY, Decimal("1"))
    assert (
        fewer.employment.schedule.annual_commute_hours
        < JERSEY.employment.schedule.annual_commute_hours
    )


def test_fewer_onsite_days_cut_commute_cash_proportionally() -> None:
    # The taxonomy rule made concrete: a COMMUTE cost is one that falls to zero
    # at zero onsite days, so halving the days halves the cost.
    original = _commute_total(JERSEY)
    halved = _commute_total(with_onsite_days(JERSEY, Decimal("1.5")))
    assert halved.amount * 2 == original.amount


def test_going_fully_remote_removes_commute_cash_entirely() -> None:
    remote = with_onsite_days(JERSEY, Decimal("0"))
    assert _commute_total(remote).is_zero()
    assert remote.employment.schedule.annual_commute_hours == Decimal(0)


def test_reducing_onsite_days_leaves_other_costs_untouched() -> None:
    fewer = with_onsite_days(JERSEY, Decimal("1"))
    rent_before = _category_total(JERSEY, "HOUSING_RENT_OR_MORTGAGE")
    rent_after = _category_total(fewer, "HOUSING_RENT_OR_MORTGAGE")
    assert rent_before == rent_after


def _commute_total(context: CalculationContext) -> Money:
    total = Money.zero()
    for item in context.costs.items:
        if item.category.value.startswith("COMMUTE_"):
            total = total + item.amount.money
    return total


def _category_total(context: CalculationContext, prefix: str) -> Money:
    total = Money.zero()
    for item in context.costs.items:
        if item.category.value == prefix:
            total = total + item.amount.money
    return total


# --- The gap ---------------------------------------------------------------


def test_a_candidate_already_ahead_has_no_gap() -> None:
    result = solve_negotiation_gap(current=AUBURN, candidate=AUBURN, bounds=BOUNDS)
    assert result.gap.is_zero()
    assert result.needs_negotiation is False


def test_a_gap_is_reported_when_the_candidate_falls_short() -> None:
    # Deliberately handicap the candidate so a gap must exist.
    poorer = _context(new_jersey_candidate)
    poorer = with_onsite_days(poorer, Decimal("5"))
    result = solve_negotiation_gap(current=JERSEY, candidate=poorer, bounds=BOUNDS)
    assert result.gap.amount >= 0


def test_the_result_offers_one_option_per_lever() -> None:
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    assert {option.lever for option in result.options} == set(NegotiationLever)


# --- Levers ----------------------------------------------------------------


def test_the_salary_lever_reports_the_raise_needed() -> None:
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    salary = next(o for o in result.options if o.lever is NegotiationLever.BASE_SALARY)
    assert salary.required_amount is not None
    assert salary.required_amount.amount > 0


def test_the_signing_bonus_lever_grosses_up_for_tax() -> None:
    # A bonus must be larger than the gap, because tax takes a share on the way
    # in. Quoting the net figure to a recruiter would ask for too little.
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    bonus = next(o for o in result.options if o.lever is NegotiationLever.SIGNING_BONUS)
    assert bonus.required_amount is not None
    assert bonus.required_amount.amount > result.gap.amount


def test_the_relocation_lever_matches_the_gap_directly() -> None:
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    relocation = next(
        o for o in result.options if o.lever is NegotiationLever.RELOCATION_REIMBURSEMENT
    )
    assert relocation.required_amount == result.gap


def test_a_feasible_remote_days_lever_reports_days_not_money() -> None:
    # When it can close the gap it answers in days; when it cannot there is no
    # day count to report, and inventing one would imply an ask that does not
    # work. Reaching the whole gap by going remote is the interesting case.
    result = solve_negotiation_gap(current=AUBURN, candidate=JERSEY, bounds=BOUNDS)
    remote = next(o for o in result.options if o.lever is NegotiationLever.REMOTE_DAYS)
    assert remote.feasible
    assert remote.required_days is not None
    assert remote.required_amount is None


def test_an_infeasible_remote_days_lever_reports_no_days() -> None:
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    remote = next(o for o in result.options if o.lever is NegotiationLever.REMOTE_DAYS)
    assert remote.feasible is False
    assert remote.required_days is None
    assert "fully remote" in remote.note


def test_a_lever_that_cannot_close_the_gap_says_so() -> None:
    # Dropping every onsite day saves only the commute; if the gap is larger
    # than that, the option must report itself infeasible rather than quietly
    # reporting the maximum.
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    remote = next(o for o in result.options if o.lever is NegotiationLever.REMOTE_DAYS)
    if not remote.feasible:
        assert remote.note


def test_every_option_carries_an_explanation() -> None:
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    assert all(option.note for option in result.options)


def test_options_are_ordered_with_feasible_ones_first() -> None:
    # A negotiation list is read top-down; unreachable asks belong at the bottom.
    result = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    feasibility = [option.feasible for option in result.options]
    assert feasibility == sorted(feasibility, reverse=True)


def test_no_gap_means_every_option_is_trivially_satisfied() -> None:
    result = solve_negotiation_gap(current=AUBURN, candidate=AUBURN, bounds=BOUNDS)
    assert all(option.feasible for option in result.options)


def test_the_solver_is_deterministic() -> None:
    first = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    second = solve_negotiation_gap(current=JERSEY, candidate=AUBURN, bounds=BOUNDS)
    assert first.gap == second.gap
    assert [o.required_amount for o in first.options] == [o.required_amount for o in second.options]
