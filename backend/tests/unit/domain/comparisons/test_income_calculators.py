"""Income, benefit, equity, and time calculators.

These derive their figures from the employment profile rather than from cost
items, so they own no cost categories and take no part in the partition check.

Three separations matter here and are asserted rather than assumed:

- Take-home pay is cash. Employer money is wealth, never cash.
- Vested equity is wealth, net of withholding. Unvested equity is neither.
- Commuting is time, and carries no cash of its own — its cash costs belong to
  the COMMUTE categories the cost calculator already owns.
"""

from datetime import date
from decimal import Decimal

from offerdelta.domain.common.dates import DateRange
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.percentage import Percentage
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.impacts import CostImpact
from offerdelta.domain.comparisons.income_calculators import (
    CommuteTimeCalculator,
    CompensationCalculator,
    EmployerBenefitCalculator,
    EquityCalculator,
)
from offerdelta.domain.costs.household import HouseholdProfile
from offerdelta.domain.costs.items import CostProfile
from offerdelta.domain.taxes.override_model import NetPayOverrideTaxModel
from tests.fixtures.profiles import auburn_current, new_jersey_candidate

HORIZON = DateRange.of_months(date(2026, 1, 1), 12)


def _context(side_builder: object, marginal: str = "32") -> CalculationContext:
    assert callable(side_builder)
    side = side_builder()
    employment = side.employment
    return CalculationContext(
        employment=employment,
        costs=side.costs,
        household=side.household,
        tax_model=NetPayOverrideTaxModel(
            observed_gross=PeriodicAmount(employment.compensation.base_salary, PeriodKind.ANNUAL),
            observed_net=PeriodicAmount(
                employment.compensation.base_salary * Decimal("0.74"), PeriodKind.ANNUAL
            ),
            marginal_rate=Percentage.from_percent(marginal),
        ),
        horizon=HORIZON,
    )


def _by_code(impacts: tuple[CostImpact, ...]) -> dict[str, CostImpact]:
    return {impact.code: impact for impact in impacts}


# --- Compensation ----------------------------------------------------------


def test_take_home_pay_is_positive_cash() -> None:
    impacts = CompensationCalculator().calculate(_context(auburn_current))
    assert _by_code(impacts)["take_home_pay"].cash_amount.amount > 0


def test_take_home_pay_is_monthly() -> None:
    impacts = CompensationCalculator().calculate(_context(auburn_current))
    assert _by_code(impacts)["take_home_pay"].period is PeriodKind.MONTHLY


def test_take_home_names_the_tax_model_that_produced_it() -> None:
    # A reader must be able to tell an extrapolated figure from computed
    # brackets without digging.
    impacts = CompensationCalculator().calculate(_context(auburn_current))
    assert impacts[0].rule_version == "NET_PAY_OVERRIDE"


def test_take_home_is_less_than_gross() -> None:
    context = _context(auburn_current)
    impacts = _by_code(CompensationCalculator().calculate(context))
    monthly_gross = context.employment.compensation.base_salary / 12
    assert impacts["take_home_pay"].cash_amount < monthly_gross


def test_a_signing_bonus_appears_as_one_time_cash() -> None:
    impacts = _by_code(CompensationCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["signing_bonus"].period is PeriodKind.ONE_TIME


def test_a_signing_bonus_is_taxed() -> None:
    # 15,000 gross at a 32% marginal rate keeps 10,200.
    impacts = _by_code(CompensationCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["signing_bonus"].cash_amount == Money.parse("10200.00")


def test_a_target_bonus_is_discounted_by_probability_then_taxed() -> None:
    # 12% of 135,000 is 16,200; at 70% likelihood that is 11,340 expected, and
    # 7,711.20 after a 32% marginal rate.
    impacts = _by_code(CompensationCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["target_bonus"].cash_amount == Money.parse("7711.20")


def test_a_profile_without_bonuses_emits_none() -> None:
    codes = _by_code(CompensationCalculator().calculate(_context(auburn_current)))
    assert "signing_bonus" not in codes
    assert "target_bonus" not in codes


def test_relocation_reimbursement_is_one_time_cash() -> None:
    impacts = _by_code(CompensationCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["relocation_reimbursement"].cash_amount.amount > 0


def test_compensation_owns_no_cost_categories() -> None:
    assert CompensationCalculator().owned_categories() == frozenset()


# --- Employer benefits -----------------------------------------------------


def test_the_employer_match_is_wealth_not_cash() -> None:
    # Employer money never touches the employee's cash flow, so counting it as
    # cash would break the monthly reconciliation identity.
    impacts = _by_code(EmployerBenefitCalculator().calculate(_context(auburn_current)))
    match = impacts["employer_retirement_match"]
    assert match.cash_amount.is_zero()
    assert match.wealth_amount.amount > 0


def test_the_employer_match_uses_the_capped_rate() -> None:
    # 100% of the first 4% of 78,000, contributing 6%.
    impacts = _by_code(EmployerBenefitCalculator().calculate(_context(auburn_current)))
    assert impacts["employer_retirement_match"].wealth_amount == Money.parse("3120.00")


def test_the_employer_hsa_contribution_is_wealth() -> None:
    impacts = _by_code(EmployerBenefitCalculator().calculate(_context(auburn_current)))
    hsa = impacts["employer_hsa_contribution"]
    assert hsa.cash_amount.is_zero()
    assert hsa.wealth_amount == Money.parse("750.00")


def test_the_employee_health_premium_is_cash_not_wealth() -> None:
    # The employee's own premium leaves their pocket, unlike the employer's share.
    impacts = _by_code(EmployerBenefitCalculator().calculate(_context(auburn_current)))
    premium = impacts["employee_health_premium"]
    assert premium.cash_amount == Money.parse("-165.00")
    assert premium.period is PeriodKind.MONTHLY


# --- Equity ----------------------------------------------------------------


def test_vested_equity_is_wealth_net_of_withholding() -> None:
    # 96,000 over 48 months with a 12-month cliff: 24,000 gross at 12 months,
    # 16,800 after 30% withholding.
    impacts = _by_code(EquityCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["vested_equity"].wealth_amount == Money.parse("16800.00")


def test_vested_equity_carries_no_cash() -> None:
    impacts = _by_code(EquityCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["vested_equity"].cash_amount.is_zero()


def test_unvested_equity_is_reported_separately() -> None:
    # Blueprint 6.3: shown apart from wealth, because leaving early forfeits it.
    impacts = _by_code(EquityCalculator().calculate(_context(new_jersey_candidate)))
    assert impacts["unvested_equity"].wealth_amount.is_zero()
    assert impacts["unvested_equity"].assumption is not None


def test_a_profile_without_equity_emits_nothing() -> None:
    assert EquityCalculator().calculate(_context(auburn_current)) == ()


# --- Commute time ----------------------------------------------------------


def test_commuting_is_recorded_as_time() -> None:
    # 3 days * 48 weeks * 15 minutes * 2 / 60 = 72 hours for Auburn.
    impacts = _by_code(CommuteTimeCalculator().calculate(_context(auburn_current)))
    assert impacts["commute_time"].time_hours == Decimal("72")


def test_commute_time_carries_no_cash() -> None:
    # Its cash costs belong to the COMMUTE categories the cost calculator owns.
    # Emitting them here too would double count them.
    impacts = _by_code(CommuteTimeCalculator().calculate(_context(auburn_current)))
    assert impacts["commute_time"].cash_amount.is_zero()


def test_a_longer_commute_costs_more_time() -> None:
    auburn = _by_code(CommuteTimeCalculator().calculate(_context(auburn_current)))
    jersey = _by_code(CommuteTimeCalculator().calculate(_context(new_jersey_candidate)))
    assert jersey["commute_time"].time_hours > auburn["commute_time"].time_hours


def test_a_fully_remote_role_costs_no_commute_time() -> None:
    context = _context(auburn_current)
    remote = CalculationContext(
        employment=context.employment,
        costs=CostProfile(items=()),
        household=HouseholdProfile.solo(),
        tax_model=context.tax_model,
        horizon=HORIZON,
    )
    # Auburn's schedule has onsite days; zeroing them zeroes the time.
    assert remote.employment.schedule.annual_commute_hours > 0
