"""Wire formats.

Every monetary value crosses the boundary as a **string**, never a JSON number.

JavaScript has one numeric type, an IEEE 754 double, so `4217.33` becomes an
approximation the instant a browser parses it as a number. Rendering alone
usually rounds back correctly and hides the problem; the first client-side
subtotal exposes it, in a product whose entire premise is that its numbers can
be trusted.

Typing the field as `str` makes emitting a number structurally impossible,
which is a stronger guarantee than configuring a serializer that a future
library version might change.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from offerdelta.domain.comparisons.derivation import DerivationNode


class DerivationNodeSchema(BaseModel):
    """One step in the explanation of a calculated figure."""

    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    amount: str = Field(description="Exact decimal string. Never parse this as a number.")
    currency: str
    period: str
    formula: str
    evidence: str
    children: tuple[DerivationNodeSchema, ...] = ()

    @classmethod
    def of(cls, node: DerivationNode) -> DerivationNodeSchema:
        return cls(
            code=node.code,
            label=node.label,
            amount=str(node.amount.amount),
            currency=node.amount.currency,
            period=str(node.period),
            formula=node.formula,
            evidence=str(node.evidence),
            children=tuple(cls.of(child) for child in node.children),
        )


class ComponentDeltaSchema(BaseModel):
    """One line of the breakdown. All amounts annualised, all as strings."""

    code: str
    label: str
    current: str
    candidate: str
    delta: str


class BreakEvenSchema(BaseModel):
    """Two months, because one is misleading when an offer front-loads cash."""

    metric: str
    horizon_months: int
    first_crossing_month: int | None
    stable_break_even_month: int | None


class EquivalentSalarySchema(BaseModel):
    equivalent_salary: str
    target_metric: str
    tax_model: str
    calibration_distance_percent: str
    is_far_from_calibration: bool
    converged: bool
    iterations: int


class NegotiationOptionSchema(BaseModel):
    lever: str
    feasible: bool
    note: str
    required_amount: str | None = None
    required_days: str | None = None


class NegotiationSchema(BaseModel):
    gap: str
    needs_negotiation: bool
    options: tuple[NegotiationOptionSchema, ...]


class ComparisonSchema(BaseModel):
    """A full comparison, ready to render."""

    current_label: str
    candidate_label: str
    horizon_months: int
    currency: str

    cash_delta: str
    wealth_delta: str
    time_delta_hours: str

    cumulative_cash_delta: tuple[str, ...]
    component_deltas: tuple[ComponentDeltaSchema, ...]

    current_derivation: DerivationNodeSchema
    candidate_derivation: DerivationNodeSchema

    break_even: BreakEvenSchema
    equivalent_salary: EquivalentSalarySchema | None = None
    equivalent_salary_error: str | None = None
    negotiation: NegotiationSchema | None = None
    negotiation_error: str | None = None

    #: True when every projected month on both sides balanced. The engine
    #: refuses to return an unbalanced result, so this is always true — it is
    #: surfaced so a reader can see the guarantee rather than take it on trust.
    reconciled: bool


class VersionSchema(BaseModel):
    service: str
    version: str
    engine: str


class HealthSchema(BaseModel):
    status: str
