"""The LLM provider port, and mocks that stand in for it.

The port exists so the categoriser, the hybrid, and the whole harness can be
built and tested before an API key exists — and so the eventual key never
becomes a prerequisite for running the suite in CI.

Every response carries token counts and latency. Cost per transaction and
p95 latency are reported alongside F1 because a model that wins by two points
at forty times the cost has not won.

The mocks are scripted rather than random. A benchmark whose baseline shifts
between runs cannot support the comparison it exists for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from offerdelta.domain.common.errors import ValidationError


@dataclass(frozen=True)
class LLMRequest:
    """Everything the model is told about one transaction.

    Deliberately narrow. The model sees a description, an amount and an account
    type — never a balance, never another user's rows, never anything it could
    leak if the description turns out to be adversarial.
    """

    merchant: str
    raw_description: str
    amount: str
    account_type: str
    allowed_labels: tuple[str, ...]


@dataclass(frozen=True)
class LLMResponse:
    """What came back, and what it cost."""

    label: str
    confidence: Decimal
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMProvider(Protocol):
    """Anything that can answer a classification request."""

    @property
    def model(self) -> str: ...

    def classify(self, request: LLMRequest) -> LLMResponse: ...


@dataclass
class ScriptedProvider:
    """A deterministic stand-in keyed by merchant.

    Not a toy: it is how the harness, the hybrid routing, and every failure
    path get tested without a key and without paying per run.
    """

    responses: dict[str, LLMResponse] = field(default_factory=dict)
    default: LLMResponse | None = None
    model_name: str = "scripted-mock"

    #: Every request seen, so a test can assert what the model was actually
    #: shown — the only way to prove the hybrid skipped a call it should skip.
    calls: list[LLMRequest] = field(default_factory=list)

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if request.merchant in self.responses:
            return self.responses[request.merchant]
        if self.default is not None:
            return self.default
        raise ValidationError(
            f"ScriptedProvider has no response for {request.merchant!r} and no default"
        )


@dataclass
class FailingProvider:
    """Raises on every call.

    Exists so the harness is forced to prove what happens when the provider is
    down. A benchmark that has never been run against an outage does not know
    whether an outage produces abstentions or a crash.
    """

    error: str = "provider unavailable"
    model_name: str = "failing-mock"

    @property
    def model(self) -> str:
        return self.model_name

    def classify(self, request: LLMRequest) -> LLMResponse:  # noqa: ARG002
        # The argument is unused but required: dropping it would break the
        # LLMProvider protocol this exists to stand in for.
        raise RuntimeError(self.error)
