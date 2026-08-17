"""Locations and tax jurisdictions.

A location produces the jurisdiction code the tax layer keys on, so residence
and work jurisdiction come from one place rather than being typed as free text
at each call site.

This type deliberately does *not* restrict which states exist. Rejecting an
unsupported state belongs to the tax registry, which can say "no rule set for
US-TX" — the state is perfectly valid, the project simply has no rules for it.
Conflating the two would make a data-entry error and a coverage gap look alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from offerdelta.domain.common.errors import ValidationError

_CODE_LENGTH: Final = 2


def _require_code(value: str, label: str) -> None:
    if len(value) != _CODE_LENGTH or not value.isalpha() or not value.isupper():
        raise ValidationError(f"{label} must be a two-letter uppercase code, got {value!r}")


@dataclass(frozen=True)
class Location:
    """Where someone lives or works."""

    state: str
    locality: str | None = None
    country: str = "US"

    def __post_init__(self) -> None:
        _require_code(self.country, "country")
        _require_code(self.state, "state")

    @property
    def jurisdiction_code(self) -> str:
        """The key the tax registry looks rules up by, such as `US-NJ`.

        Localities are excluded: state income tax keys on the state, and local
        taxes such as NYC's are handled by their own rules rather than by
        widening this key.
        """
        return f"{self.country}-{self.state}"

    def __str__(self) -> str:
        return f"{self.locality}, {self.state}" if self.locality else self.state
