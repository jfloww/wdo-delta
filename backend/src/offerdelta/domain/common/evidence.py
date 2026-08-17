"""Where a value came from.

Blueprint section 23 requires assumed values to be distinguishable from sourced
data everywhere they appear, in the model and in the UI. A derivation tree is
not worth opening without it: knowing a figure is a guess matters as much as
knowing how it was computed.
"""

from __future__ import annotations

from enum import StrEnum


class Evidence(StrEnum):
    """Provenance of a value."""

    #: Taken from a versioned public dataset or an extracted document field.
    SOURCED = "SOURCED"

    #: Entered or explicitly confirmed by the user, such as a verified paystub.
    USER_CONFIRMED = "USER_CONFIRMED"

    #: An assumption. Must be visually distinct wherever it is displayed.
    ASSUMED = "ASSUMED"

    #: Computed from other values; provenance is the weakest of its inputs.
    DERIVED = "DERIVED"
