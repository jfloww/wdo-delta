"""Employment value objects."""

from __future__ import annotations

from enum import StrEnum


class FilingStatus(StrEnum):
    """US federal filing status.

    Affects bracket thresholds and the standard deduction, so it belongs to the
    locked set an override is calibrated against.
    """

    SINGLE = "SINGLE"
    MARRIED_FILING_JOINTLY = "MARRIED_FILING_JOINTLY"
    MARRIED_FILING_SEPARATELY = "MARRIED_FILING_SEPARATELY"
    HEAD_OF_HOUSEHOLD = "HEAD_OF_HOUSEHOLD"
