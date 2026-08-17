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


class VersionSchema(BaseModel):
    service: str
    version: str
    engine: str


class HealthSchema(BaseModel):
    status: str
