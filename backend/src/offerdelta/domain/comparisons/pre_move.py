"""Costs before the move.

Closes a modelling gap that made the comparison quietly wrong. A candidate
profile's costs begin on the move date, so without this the engine models
someone who lives nowhere until then — no rent, no groceries, no utilities. The
saving is large enough to invert the answer: the New Jersey offer appeared to
match Auburn on a *lower* salary, in a location with more than double the rent.

You still live somewhere before you move. The candidate side therefore inherits
the current side's recurring costs up to the move date, then switches to its
own. Inherited items are marked so a derivation can say "this is your Auburn
rent, still being paid" rather than presenting it as part of the offer.

One-time costs are never inherited: a relocation deposit belongs to the move,
not to the life before it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from offerdelta.domain.costs.categories import CashFlowType
from offerdelta.domain.costs.items import CostProfile


def inherit_costs_until_move(
    *, current: CostProfile, candidate: CostProfile, move_date: date
) -> CostProfile:
    """Fill the candidate's pre-move gap with the current side's recurring costs.

    A current cost is carried over only when the candidate has no cost in the
    same category already running before the move. Otherwise the month before
    the move would be charged twice.
    """
    already_covered = {
        item.category
        for item in candidate.items
        if item.cash_flow_type is CashFlowType.RECURRING_CASH and item.effective_date < move_date
    }

    inherited = tuple(
        replace(
            item,
            ends_before=move_date,
            is_inherited=True,
        )
        for item in current.items
        if item.cash_flow_type is CashFlowType.RECURRING_CASH
        and item.category not in already_covered
        and item.effective_date < move_date
    )

    return CostProfile(items=(*candidate.items, *inherited))
