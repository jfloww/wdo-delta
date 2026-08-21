# Phase 1 Scope

> Target: 5 weeks part-time
> Goal: a deployed, trustworthy, explainable comparison
> Status: largely delivered — see the note below

This document exists for one reason: to be the thing you check when you are tempted to build
something interesting. The full design lives in `../BLUEPRINT.md`.
**This file outranks it for the next five weeks.**

**Status note (2026-08-21).** M0 through M6 are delivered and deployed. Two gate items remain open:
a non-developer cannot yet complete the flow, because there are no input forms. Work has since
continued past this contract into transaction ingest, the categorisation taxonomy, and the
evaluation harness — see the [README](../../README.md). This file is kept as the record of what the
five-week contract actually said, not as a live plan.

If it is not on the IN list below, it is out. Not "later in phase 1" — out.

---

## The one-sentence definition of done

A stranger can open a URL, complete an Auburn-to-New-Jersey comparison, click any number in the
result, and see exactly how it was calculated and what it was assumed from.

---

## Week one is a walking skeleton, not a foundation

The tempting order is to build every layer bottom-up and connect them at the end. Do not. That
defers all integration risk to the final week, which is the week that has none to spare.

Week one ships a deliberately ugly end-to-end path: one hardcoded profile, through one real
calculator, out of one FastAPI endpoint, into one rendered number with its derivation, on a public
URL. It proves the two things that are cheap to fix now and expensive to fix in week five:

1. **The deployment path works** while the app is small enough to debug in minutes.
2. **Money survives the boundary.** Every monetary value crosses the API as a JSON **string**, never
   a JSON number. JavaScript's only numeric type is an IEEE 754 double, so `4217.33` becomes an
   approximation the instant the browser parses it as a number. Display-only rendering hides this;
   the first client-side subtotal exposes it. The frontend never calls `Number()` on money, and all
   money arithmetic stays server-side.

Retrofitting either of these in week five means touching every response schema and every component
that renders a figure.

---

## IN

**Domain**

- `Money` — rejects `float`, rejects cross-currency arithmetic, `allocate()` preserves totals
- `RoundingPolicy` — the four named policies, injected, never global
- `PeriodicAmount` and period conversion, including biweekly at 26 periods
- `Percentage`, `DateRange`, `Location`, domain error hierarchy

**Model**

- Employment profile, compensation items, benefits, work schedule
- Cost profile with the full field set: category, owner, period, cash-flow type, effective date, evidence
- Household profile with a split strategy
- Net-pay override with basis fingerprint and `ACTIVE`/`STALE`
- Conservative, expected, and optimistic bands

**Engine**

- `NetPayOverrideTaxModel` behind the `TaxModel` port
- Calculators: cash compensation, housing, living, commute cash and time, relocation, retirement
  match, health, equity vesting net of withholding
- Category ownership partition check at import
- Monthly cash-flow reconciliation invariant, asserted before any result returns
- Result components with `parent_code`, `formula_id`, inputs, rounding policy, evidence

**Solvers**

- Equivalent salary, with bracket validation and monotonicity guards
- Break-even, reporting both `first_crossing_month` and `stable_break_even_month`
- Negotiation gap, predefined alternatives evaluated independently

**API**

- FastAPI, SQLAlchemy, Alembic, PostgreSQL
- Profile, cost, comparison, run, solver, and derivation endpoints
- Idempotency-key contract
- `409` naming the invalidating field when an edit would stale an override
- Error envelope, immutable completed runs

**Frontend**

- Current profile, candidate offer, and cost assumption forms
- Comparison summary distinguishing cash, wealth, time, and liquidity
- Delta waterfall
- Equivalent salary and break-even timeline
- Sensitivity list
- **Derivation tree** — clicking any number expands its full calculation
- Assumed values visually distinct from confirmed ones

**Deployment**

- One container on Render or Fly.io
- Neon PostgreSQL
- Seeded synthetic demo profile
- Health checks and error tracking
- A URL you can put in a message to a recruiter

---

## OUT

Everything below is a phase 2 or later-track item, or excluded entirely. None of it is in phase 1.

| Out | Where it goes |
|---|---|
| Federal, state, or payroll tax calculation | Phase 2 — this is what the override is for |
| Any LLM, OCR, or document upload | Phase 2 |
| AI evaluation harness | Phase 2 |
| AWS — any service, including S3 | Track B |
| Terraform | Track B |
| SQS, workers, transactional outbox, DynamoDB | Track B |
| PySpark, Glue, public-data pipeline | Track B |
| HUD, BLS, Census data of any kind | Track B |
| Step Functions | Excluded permanently |
| RAG document Q&A | Excluded permanently |
| Monte Carlo | Deferred indefinitely |
| Cognito or third-party auth | Simple session auth is enough; real auth is phase 2 |
| Multi-state support beyond AL, NJ, NY | Phase 2, and only those three |
| Any state other than AL, NJ, NY | Never — raise unsupported-jurisdiction |

---

## Week-by-week

Adjust the dates, not the scope. If a week slips, the following weeks slip — the IN list does not
shrink and does not grow.

| Week | Work | Checkpoint |
|---|---|---|
| 1 | M0 bootstrap, M1 primitives, **M0.5 walking skeleton** | Live URL showing one real calculated number and its derivation; `Money` and `RoundingPolicy` pass property tests; no money crosses the API as a JSON number |
| 2 | M2 profiles, cost items, override | Both real profiles serialize to fixture JSON; category ownership partition check passes at import |
| 3 | M3a calculators | One- and three-year comparisons run in memory; reconciliation invariant green for every month and band |
| 4 | M4 solvers, M5 API and persistence | All three solvers tested; full flow works over HTTP against PostgreSQL |
| 5 | M6 frontend and derivation tree | A non-developer completes the Auburn-to-NJ comparison; gate checklist clear |

Week 5 carries the most risk, because frontend work always takes longer than planned. Decide the cut
order **now**, while it is a calm decision rather than a panicked one:

1. Cut the sensitivity list UI first.
2. Then the negotiation-gap UI — keep the endpoint, drop the screen.
3. Then the three-year view — ship one-year only.

**Never cut the derivation tree or the reconciliation invariant.** The derivation tree is the demo,
and the invariant is what makes the numbers trustworthy. Cutting either removes the reason the
project is worth showing.

---

## Rules for the five weeks

1. **Write the test first for anything involving money.** Every financial bug found by a test is a
   bug not found by an interviewer.
2. **The reconciliation invariant is never skipped or marked xfail.** If it fails, the model is
   wrong, not the test.
3. **No new AWS, LLM, or Spark dependency enters `pyproject.toml`.** If you are installing `boto3`
   in phase 1, something has gone wrong.
4. **Every assumed number is marked `ASSUMED` the moment it is written**, in fixtures and in the UI.
5. **Deploy in week 1, not week 5.** The walking skeleton goes to a public URL by day three, and
   every subsequent week redeploys. A demo URL that is four weeks stale is not a demo.
6. **Money is a string at every boundary.** JSON, TypeScript types, form values. The frontend never
   calls `Number()` on a monetary value.
7. **When you want to build something not on the IN list, write it in `docs/parking-lot.md` and keep
   going.** The idea will still be there in five weeks.

---

## Phase 1 gate

Do not start phase 2 until all of these are true.

- [x] Live URL, reachable by someone who is not you
- [ ] A non-developer completed the Auburn-to-NJ comparison without help
      <- the only one still open; there are no input forms (verified 2026-08-21)
- [x] Every result number expands to a derivation showing formula, inputs, and evidence
- [x] Reconciliation invariant passes for every month, every band, both sides
- [x] Property tests green, including allocation sum preservation and the break-even crossings
- [x] Changing a 401(k) contribution marks the override `STALE` and the API names the field
- [x] No monetary value crosses the API as a JSON number
- [x] `pyproject.toml` contains no AWS, LLM, or Spark dependency
- [x] README shows the live URL and what the project does, above any implementation detail

When every box is checked, you have something worth linking to in an application. That is the point
of phase 1, and nothing in any later phase is worth delaying it for.

**Start applying at this gate, not at the end of the project.** Everything after phase 1 improves an
application you are already sending, which is a far better position than perfecting something you
have not shown anyone.
