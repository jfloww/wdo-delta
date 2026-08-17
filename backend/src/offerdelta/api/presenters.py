"""Turning domain results into wire schemas.

Every monetary value becomes a string here, and nowhere else does a Money reach
a response. Keeping the conversion in one module means the rule "money crosses
as a string" has exactly one place it could be broken, and one place to check.
"""

from __future__ import annotations

from decimal import Decimal

from offerdelta.api.schemas import (
    BreakEvenSchema,
    ComparisonSchema,
    ComponentDeltaSchema,
    DerivationNodeSchema,
    EquivalentSalarySchema,
    NegotiationOptionSchema,
    NegotiationSchema,
)
from offerdelta.application.queries.get_demo_comparison import ComparisonView
from offerdelta.domain.common.money import Money
from offerdelta.domain.solvers.equivalent_salary import EquivalentSalaryResult
from offerdelta.domain.solvers.negotiation_gap import NegotiationGapResult


def _amount(value: Money) -> str:
    return str(value.amount)


def present_comparison(view: ComparisonView) -> ComparisonSchema:
    comparison = view.comparison
    reconciled = all(
        month.residual.is_zero()
        for side in (comparison.current, comparison.candidate)
        for month in side.months
    )

    return ComparisonSchema(
        current_label=view.current_label,
        candidate_label=view.candidate_label,
        horizon_months=view.horizon_months,
        currency=comparison.cash_delta.currency,
        cash_delta=_amount(comparison.cash_delta),
        wealth_delta=_amount(comparison.wealth_delta),
        time_delta_hours=str(comparison.time_delta_hours),
        cumulative_cash_delta=tuple(_amount(value) for value in comparison.cumulative_cash_delta),
        component_deltas=tuple(
            ComponentDeltaSchema(
                code=component.code,
                label=component.label,
                current=_amount(component.current_cash),
                candidate=_amount(component.candidate_cash),
                delta=_amount(component.delta),
            )
            for component in comparison.component_deltas
        ),
        current_derivation=DerivationNodeSchema.of(view.current_derivation),
        candidate_derivation=DerivationNodeSchema.of(view.candidate_derivation),
        break_even=BreakEvenSchema(
            metric=str(view.break_even.metric),
            horizon_months=view.break_even.horizon_months,
            first_crossing_month=view.break_even.first_crossing_month,
            stable_break_even_month=view.break_even.stable_break_even_month,
        ),
        equivalent_salary=_present_equivalent(view.equivalent_salary),
        equivalent_salary_error=view.equivalent_salary_error,
        negotiation=_present_negotiation(view.negotiation),
        negotiation_error=view.negotiation_error,
        reconciled=reconciled,
    )


def _present_equivalent(
    result: EquivalentSalaryResult | None,
) -> EquivalentSalarySchema | None:
    if result is None:
        return None
    return EquivalentSalarySchema(
        equivalent_salary=_amount(result.equivalent_salary),
        target_metric=result.target_metric,
        tax_model=result.tax_model_name,
        calibration_distance_percent=str(
            result.calibration_distance.as_percent().quantize(Decimal("0.1"))
        ),
        is_far_from_calibration=result.is_far_from_calibration,
        converged=result.converged,
        iterations=result.iterations,
    )


def _present_negotiation(
    result: NegotiationGapResult | None,
) -> NegotiationSchema | None:
    if result is None:
        return None
    return NegotiationSchema(
        gap=_amount(result.gap),
        needs_negotiation=result.needs_negotiation,
        options=tuple(
            NegotiationOptionSchema(
                lever=str(option.lever),
                feasible=option.feasible,
                note=option.note,
                required_amount=(
                    None if option.required_amount is None else _amount(option.required_amount)
                ),
                required_days=(None if option.required_days is None else str(option.required_days)),
            )
            for option in result.options
        ),
    )
