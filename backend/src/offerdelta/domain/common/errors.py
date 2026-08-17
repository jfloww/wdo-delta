"""Domain error hierarchy.

Domain rules raise domain errors so the API layer can map them to responses by
type rather than by string-matching a message.

Each error also subclasses the builtin it stands in for. `ValidationError` is a
`ValueError` and `TypeConstraintError` is a `TypeError`, so ordinary handling at
a boundary keeps working and no caller is forced to import domain types just to
catch bad input.

The hierarchy is shaped by what a caller needs to tell apart. Resist adding a
class per raise site; add one when something downstream must react differently.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every violation of a domain rule."""


class ValidationError(DomainError, ValueError):
    """A value is well-typed but breaks a domain rule."""


class TypeConstraintError(DomainError, TypeError):
    """A value has the wrong type for a domain constraint.

    Most often a float where an exact Decimal is required.
    """


class CurrencyMismatchError(ValidationError):
    """An operation combined amounts denominated in different currencies."""


class PeriodMismatchError(ValidationError):
    """An operation combined amounts describing different periods."""


class AllocationError(ValidationError):
    """An amount could not be split as requested."""
