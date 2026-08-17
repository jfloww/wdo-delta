# OfferDelta

Calculates the real economic difference between a current job and a new offer, combining actual
personal spending, the full terms of an offer, taxes and payroll deductions, housing and relocation
costs, and commuting cash and time.

> What salary in the target location would preserve my current standard of living, when would the
> move break even, and which offer terms would close the remaining gap?

**Live demo: [offerdelta.onrender.com](https://offerdelta.onrender.com)**

Hosted on Render's free plan, so the first request after a quiet period takes about 30 seconds
while the service wakes.

---

## Status

Phase 1, week 1. Deployed, with the calculation core under test.

| Milestone | State |
|---|---|
| M0 — tooling, CI, one-command checks | done |
| M1 — domain primitives | done |
| M0.5 — walking skeleton, deployed | done |
| M2 — employment and cost profiles | in progress |

209 tests. Lint, types, architecture boundaries, and tests all run in one command.

---

## What is worth looking at

**Every number can be taken apart.** The API returns a derivation tree, not a figure. Each node
carries its formula, its provenance, and its children, and a node whose children do not sum to it
cannot be constructed at all.

**Money is exact, end to end.** `Decimal` in Python, `NUMERIC` in PostgreSQL, and decimal
**strings** across the HTTP boundary — never JSON numbers, because JavaScript's only numeric type
is an IEEE 754 double and `4217.33` has no exact binary representation. A contract test walks the
raw response body and fails if any amount is serialised as a number.

**Rounding is a decision, not a default.** IRS whole-dollar rules, payroll to the cent, and
half-even for statistics genuinely disagree. There is no global rounding rule; a named
`RoundingPolicy` is applied explicitly at each boundary and recorded on the result.

**Splitting money never loses a cent.** `Money.allocate` distributes by largest remainder in pure
integer arithmetic, and a Hypothesis property asserts the shares always sum back to the original.

**Periods travel with amounts.** A monthly figure summed as annual is wrong by twelve and looks
plausible. `PeriodicAmount` carries its period, and one-time amounts such as a signing bonus refuse
to annualise at all.

### Governance properties

The calculation core is designed using model-governance principles: versioned rule sets, immutable
calculation runs, complete input lineage, reproducible results, explicit stress scenarios, and
per-number derivations.

OfferDelta has not been through an independent model validation process. The governance properties
described here are engineering choices made to keep the calculation auditable.

---

## Running it

```bash
cd backend
uv sync
uv run python check.py                                    # format, lint, types, boundaries, tests
uv run uvicorn offerdelta.api.main:app --reload           # http://127.0.0.1:8000
```

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Layout

```
backend/src/offerdelta/
  domain/          calculation core — standard library only, enforced by import-linter
  application/     use cases
  api/             HTTP surface — the only layer that knows FastAPI exists
docs/planning/     PHASE-1-SCOPE.md — the five-week contract
OfferDelta-development-blueprint.md   full design
```

## Documentation

- [Development blueprint](OfferDelta-development-blueprint.md) — architecture, calculation
  boundaries, phases, and the decision log
- [Phase 1 scope](docs/planning/PHASE-1-SCOPE.md) — what is in, what is out, and the gate

---

Portfolio project. Not tax, legal, or financial advice.
