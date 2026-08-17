"""Calculators that derive figures from the employment profile.

These own no cost categories — they read compensation, benefits, and schedule
rather than consuming cost items — so they take no part in the category
partition check.

Three separations are enforced here, and each one exists because blurring it
produces a plausible-looking wrong answer:

- **Take-home pay is cash; employer money is wealth.** Employer contributions
  never touch the employee's cash flow, so counting them as cash would break the
  monthly reconciliation identity by exactly the employer's contribution.
- **Vested equity is wealth net of withholding; unvested equity is neither.**
  Counting gross overstates every offer with equity; counting unvested equity as
  wealth overstates offers you might leave before the cliff.
- **Commuting is time, not cash.** Its cash costs already belong to the COMMUTE
  categories the cost calculator owns; emitting them here too would double count
  them.
"""

from __future__ import annotations

from dataclasses import dataclass

from offerdelta.domain.common.evidence import Evidence
from offerdelta.domain.common.money import Money
from offerdelta.domain.common.periods import PeriodicAmount, PeriodKind
from offerdelta.domain.comparisons.context import CalculationContext
from offerdelta.domain.comparisons.impacts import CostImpact, InputRef
from offerdelta.domain.costs.categories import CalculatorName, CostCategory


def _no_categories() -> frozenset[CostCategory]:
    return frozenset()


@dataclass(frozen=True)
class CompensationCalculator:
    """Take-home pay and the cash parts of an offer."""

    @property
    def name(self) -> CalculatorName:
        return CalculatorName.LIVING  # unused for routing; owns no categories

    def owned_categories(self) -> frozenset[CostCategory]:
        return _no_categories()

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]:
        compensation = context.employment.compensation
        start = context.horizon.start
        impacts: list[CostImpact] = []

        taxed = context.tax_model.after_tax_cash(
            PeriodicAmount(compensation.base_salary, PeriodKind.ANNUAL)
        )
        monthly_net = taxed.after_tax.to_monthly().money

        impacts.append(
            CostImpact(
                code="take_home_pay",
                label="Monthly take-home pay",
                category=CostCategory.LIVING_OTHER,
                produced_by=CalculatorName.LIVING,
                period=PeriodKind.MONTHLY,
                effective_date=start,
                formula_id="base_salary_after_tax",
                evidence=Evidence.DERIVED,
                cash_amount=monthly_net,
                inputs=(
                    InputRef("base_salary", str(compensation.base_salary)),
                    InputRef("tax_model", taxed.model_name),
                ),
                rule_version=taxed.model_name,
                assumption=(
                    f"extrapolated {taxed.calibration_distance} from the calibration point"
                    if taxed.is_far_from_calibration
                    else None
                ),
            )
        )

        if compensation.signing_bonus is not None:
            net = context.tax_model.after_tax_one_time(compensation.signing_bonus.amount)
            impacts.append(
                CostImpact(
                    code="signing_bonus",
                    label="Signing bonus",
                    category=CostCategory.LIVING_OTHER,
                    produced_by=CalculatorName.LIVING,
                    period=PeriodKind.ONE_TIME,
                    effective_date=start,
                    formula_id="signing_bonus_after_tax",
                    evidence=Evidence.DERIVED,
                    cash_amount=net,
                    inputs=(InputRef("gross", str(compensation.signing_bonus.amount)),),
                    assumption=(
                        f"repayable if leaving within "
                        f"{compensation.signing_bonus.repayment_months} months"
                        if compensation.signing_bonus.repayment_months
                        else None
                    ),
                )
            )

        if compensation.target_bonus is not None:
            expected = compensation.target_bonus.expected_value(compensation.base_salary)
            net = context.tax_model.after_tax_one_time(expected)
            impacts.append(
                CostImpact(
                    code="target_bonus",
                    label="Expected annual bonus",
                    category=CostCategory.LIVING_OTHER,
                    produced_by=CalculatorName.LIVING,
                    period=PeriodKind.ANNUAL,
                    effective_date=start,
                    formula_id="target_bonus_expected_after_tax",
                    evidence=Evidence.DERIVED,
                    cash_amount=net,
                    inputs=(
                        InputRef("rate", str(compensation.target_bonus.rate)),
                        InputRef("probability", str(compensation.target_bonus.probability)),
                    ),
                    assumption="discounted by the stated probability of payout",
                )
            )

        if compensation.relocation_reimbursement is not None:
            impacts.append(
                CostImpact(
                    code="relocation_reimbursement",
                    label="Relocation reimbursement",
                    category=CostCategory.LIVING_OTHER,
                    produced_by=CalculatorName.LIVING,
                    period=PeriodKind.ONE_TIME,
                    effective_date=start,
                    formula_id="relocation_reimbursement_as_offered",
                    evidence=Evidence.ASSUMED,
                    cash_amount=compensation.relocation_reimbursement,
                )
            )

        return tuple(impacts)


@dataclass(frozen=True)
class EmployerBenefitCalculator:
    """Employer contributions, and the employee's own health premium."""

    @property
    def name(self) -> CalculatorName:
        return CalculatorName.HEALTH

    def owned_categories(self) -> frozenset[CostCategory]:
        return _no_categories()

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]:
        benefits = context.employment.benefits
        salary = context.employment.compensation.base_salary
        start = context.horizon.start

        match = benefits.annual_employer_match(salary)
        hsa = benefits.employer_hsa_contribution.to_annual().money
        premium = benefits.employee_health_premium.to_monthly().money

        return (
            CostImpact(
                code="employer_retirement_match",
                label="Employer retirement match",
                category=CostCategory.LIVING_OTHER,
                produced_by=CalculatorName.HEALTH,
                period=PeriodKind.ANNUAL,
                effective_date=start,
                formula_id="employer_match_capped_at_limit_rate",
                evidence=Evidence.DERIVED,
                # Wealth, never cash: this money never passes through the
                # employee's account, so treating it as cash would break the
                # monthly reconciliation identity.
                wealth_amount=match,
                inputs=(
                    InputRef("match_rate", str(benefits.retirement_match.match_rate)),
                    InputRef("limit_rate", str(benefits.retirement_match.match_limit_rate)),
                    InputRef("employee_rate", str(benefits.employee_contribution_rate)),
                ),
            ),
            CostImpact(
                code="employer_hsa_contribution",
                label="Employer HSA contribution",
                category=CostCategory.LIVING_OTHER,
                produced_by=CalculatorName.HEALTH,
                period=PeriodKind.ANNUAL,
                effective_date=start,
                formula_id="employer_hsa_as_offered",
                evidence=Evidence.ASSUMED,
                wealth_amount=hsa,
            ),
            CostImpact(
                code="employee_health_premium",
                label="Health premium",
                category=CostCategory.HEALTH_PREMIUM,
                produced_by=CalculatorName.HEALTH,
                period=PeriodKind.MONTHLY,
                effective_date=start,
                formula_id="employee_health_premium_as_entered",
                evidence=Evidence.ASSUMED,
                cash_amount=-premium,
            ),
        )


@dataclass(frozen=True)
class EquityCalculator:
    """Equity vesting across the horizon, net of withholding."""

    @property
    def name(self) -> CalculatorName:
        return CalculatorName.LIVING

    def owned_categories(self) -> frozenset[CostCategory]:
        return _no_categories()

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]:
        grant = context.employment.compensation.equity
        if grant is None:
            return ()

        months = context.horizon.month_count
        net_vested = grant.net_vested_by(months)
        gross_vested = grant.gross_vested_by(months)
        unvested = grant.total_value - gross_vested

        return (
            CostImpact(
                code="vested_equity",
                label="Vested equity",
                category=CostCategory.LIVING_OTHER,
                produced_by=CalculatorName.LIVING,
                period=PeriodKind.HORIZON_CUMULATIVE,
                effective_date=context.horizon.start,
                formula_id="equity_vested_net_of_withholding",
                evidence=Evidence.DERIVED,
                wealth_amount=net_vested,
                inputs=(
                    InputRef("grant", str(grant.total_value)),
                    InputRef("months_vested", str(months)),
                    InputRef("withholding", str(grant.estimated_withholding_rate)),
                ),
                assumption="withholding rate is a user estimate, not a computed rate",
            ),
            CostImpact(
                code="unvested_equity",
                label="Unvested equity",
                category=CostCategory.LIVING_OTHER,
                produced_by=CalculatorName.LIVING,
                period=PeriodKind.HORIZON_CUMULATIVE,
                effective_date=context.horizon.start,
                formula_id="equity_not_yet_vested",
                evidence=Evidence.DERIVED,
                # Deliberately zero on both tracks: reported for visibility, but
                # forfeited by leaving, so it is not wealth you hold.
                inputs=(InputRef("unvested_gross", str(unvested)),),
                assumption=(
                    f"{unvested} remains unvested at month {months} and is forfeited on leaving"
                ),
            ),
        )


@dataclass(frozen=True)
class CommuteTimeCalculator:
    """The hours a job takes without paying for them."""

    @property
    def name(self) -> CalculatorName:
        return CalculatorName.COMMUTE

    def owned_categories(self) -> frozenset[CostCategory]:
        return _no_categories()

    def calculate(self, context: CalculationContext) -> tuple[CostImpact, ...]:
        schedule = context.employment.schedule
        hours = schedule.annual_commute_hours
        if hours == 0:
            return ()

        return (
            CostImpact(
                code="commute_time",
                label="Annual commute time",
                category=CostCategory.COMMUTE_TRANSIT_FARE,
                produced_by=CalculatorName.COMMUTE,
                period=PeriodKind.ANNUAL,
                effective_date=context.horizon.start,
                formula_id="onsite_days_x_weeks_x_round_trip_minutes",
                evidence=Evidence.DERIVED,
                # No cash here. The cash side of commuting belongs to the
                # COMMUTE cost categories, and emitting it twice would double
                # count it.
                cash_amount=Money.zero(),
                time_hours=hours,
                inputs=(
                    InputRef("onsite_days", str(schedule.onsite_days_per_week)),
                    InputRef("one_way_minutes", str(schedule.one_way_commute_minutes)),
                    InputRef("working_weeks", str(schedule.annual_working_weeks)),
                ),
            ),
        )


def default_income_calculators() -> tuple[
    CompensationCalculator,
    EmployerBenefitCalculator,
    EquityCalculator,
    CommuteTimeCalculator,
]:
    return (
        CompensationCalculator(),
        EmployerBenefitCalculator(),
        EquityCalculator(),
        CommuteTimeCalculator(),
    )
