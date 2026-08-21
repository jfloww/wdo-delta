# Live validation checklist

Everything in this repository currently runs without an API key. This is the sequence for turning
that on: one real call first, then twenty-five rows, then the full holdout — checking something
specific at each step, so a problem is found when it costs a fraction of a cent rather than after a
whole dataset has been spent on it.

**Total cost of the entire sequence below is under $2.** The constraint is not money. It is that a
wrong prompt, a miscalibrated confidence, or a leaked key all get more expensive the later they are
found.

---

## Rules for the key

These hold at every step.

- **Never print it.** No `echo $ANTHROPIC_API_KEY`, no `cat .env`, no adding it to a log line or an
  error message. Presence is checked with a boolean, never by displaying the value.
- **Never paste it into a chat, an issue, or a commit message** — including to an assistant helping
  with this repository.
- **Never pass it as a command-line argument.** Arguments land in shell history and in the process
  list, where any other process on the machine can read them.
- It belongs in exactly one place: `backend/.env`, which is gitignored.

The code already assumes this. `AnthropicConfig.__repr__` is overridden to print `<redacted>`,
because a plain dataclass would otherwise put the key in the first traceback that touched it.

---

## Stage 0 — before the key exists

Confirm the offline path is sound. No key, no network, no spend.

```bash
cd backend
uv run python check.py          # format, lint, types, boundaries, 916 tests
uv run python llm_smoke.py      # prints the exact request that would be sent
```

- [ ] `check.py` is green end to end
- [ ] The smoke output's section 1 shows `POST https://api.anthropic.com/v1/messages`, with
      `anthropic-version` present and `x-api-key: <redacted>`
- [ ] `tool_choice` names `categorise_transaction`, and the schema's `label.enum` lists all 30 labels
- [ ] `temperature` is `0.0`
- [ ] Section 2 shows `slept [7.0, 2.0]` — 7s taken from `Retry-After`, 2s from our own backoff
- [ ] Section 3 shows `AuthenticationError` after exactly **1** attempt — a bad key must not be retried
- [ ] Section 4 shows exactly one `</transaction>` tag, with the injected one escaped

If any of these is wrong, fix it here. Every one of them is free to check now and costs money to
discover later.

---

## Stage 1 — place the key

Add it to `backend/.env` with an editor. Do not use `echo >>`, which puts the value in shell
history.

```
ANTHROPIC_API_KEY=...
```

Then confirm it loaded **without displaying it**:

```bash
uv run python -c "from offerdelta.config import get_settings; print('key present:', get_settings().llm_available)"
```

- [ ] Prints `key present: True`
- [ ] You did not at any point run a command that displayed the value

Optionally pin a cheaper model for bulk runs:

```
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
```

---

## Stage 2 — prove it cannot escape

Do this **before** the first call, and again before any push.

```bash
cd ..
git check-ignore -v backend/.env          # must print the matching .gitignore rule
git status --porcelain                    # .env must not appear
git ls-files backend/.env                 # must print nothing
git grep -nE "sk-ant-[A-Za-z0-9_-]{20,}"  # must print nothing
```

- [ ] `.env` is ignored, untracked, and absent from `git status`
- [ ] No key-shaped string exists in any tracked file

If a key ever does reach a commit: **rotate it first**, then clean history. Rotation is immediate
and certain; history rewriting is neither, and a pushed commit must be assumed already scraped.

---

## Stage 3 — one real call

```bash
cd backend
uv run python llm_smoke.py --live
```

Cost: about **$0.004**. One request, one transaction.

- [ ] It completes without an exception
- [ ] `label` is one of the 30 taxonomy labels, and `in taxonomy: True` is printed
- [ ] The label is plausibly right — `BLUE BOTTLE COFFEE #417` should be `LIVING_DINING`
- [ ] `confidence` is between 0 and 1 and is not suspiciously always `1.0`
- [ ] `reason` is a real sentence about this transaction, not boilerplate
- [ ] `tokens` are non-zero in both directions — a zero means usage parsing is broken
- [ ] `retries 0` on a healthy API

**If `label` comes back outside the taxonomy**, the enum is not constraining as expected. Stop and
investigate before spending anything on a batch: the categoriser would discard every such answer as
an abstention, and the run would report near-zero coverage at full cost.

**If it fails**, the error type says what to do:

| Error | Meaning | Action |
|---|---|---|
| `AuthenticationError` | Key wrong, revoked, or has a stray space | Recheck `.env`. It failed after 1 attempt, as designed. |
| `ModelNotFoundError` | `ANTHROPIC_MODEL` is misspelled or retired | Correct or unset it |
| `RetryBudgetExhaustedError` | Retryable failures throughout | Check `last_error`; likely a genuine outage |
| `MalformedResponseError` | The response did not match the contract | Do not retry in a loop. Investigate the shape. |

---

## Stage 4 — the failure paths, deliberately

Worth ten seconds, because these paths are what a batch run depends on.

Temporarily set `ANTHROPIC_API_KEY` to a wrong-but-well-formed value and run `--live` again.

- [ ] Fails with `AuthenticationError`
- [ ] Fails **immediately** — no backoff, no repeated attempts, no repeated billing

Restore the real key afterwards, and re-run Stage 2's checks.

---

## Stage 5 — the first small evaluation

Only after Stages 3 and 4 pass.

```bash
uv run python validate_dataset.py           # the dataset must be usable first
uv run python run_evaluation.py --live --limit 25
```

The runner prints the model, the row count, and an estimated cost, then waits for confirmation
**before sending anything**. About **$0.10** for 25 rows.

- [ ] The estimate shown matches expectations before you answer `y`
- [ ] `merchants on both sides: 0` — the split is genuinely merchant-disjoint
- [ ] The header reads `[:25]` in the dataset version, so this can never be mistaken for a full result
- [ ] `failures 0`
- [ ] `rejected outputs 0` — anything above zero means labels are arriving outside the taxonomy
- [ ] Coverage for the LLM row is high; a low figure means it is abstaining rather than answering

Then read the comparison itself, which is the entire point:

- [ ] **Does the LLM actually beat the rule baseline?** If not, that is the finding. Record it.
      A rule baseline that wins is a legitimate and interesting result.
- [ ] Does the hybrid's escalation rate look sane — the rules handling what they know, the model
      called only where they abstain?
- [ ] Is cost per transaction acceptable next to the F1 difference it buys?

25 rows is a smoke test, not a benchmark. Do not quote any number from it.

---

## Stage 6 — the full holdout

```bash
uv run python run_evaluation.py --live
```

About **$1.60** for 400 rows, and less in practice because the hybrid calls the model only where the
rules abstain.

- [ ] The dataset version has **no** `[:N]` suffix
- [ ] The checksum in the header is verified after the run — the report refuses to print if any
      system mutated the labels it was scored against
- [ ] `failures` and `rejected outputs` are both zero or explained

Record alongside every number: **dataset version, prompt version (`categorise/v1`), model string,
and date.** A score without those four cannot be reproduced or compared, and becomes worthless the
first time anything changes.

---

## Stop conditions

Stop and investigate rather than continuing to spend:

- Any label arrives outside the taxonomy
- `failures` is non-zero on a healthy API
- Confidence is always exactly `1.0` — the model is not discriminating, and the hybrid's routing
  threshold becomes meaningless
- Cost per transaction exceeds the estimate by more than roughly double
- The LLM scores *worse* than the rules on the small run — verify the prompt before paying for 400
  rows of the same problem

## After the first real numbers exist

- [ ] Update the README's "Current status of the numbers" section, which currently states plainly
      that no scores exist
- [ ] Record the four attributes above beside every figure
- [ ] Keep the rule baseline in every future comparison — an F1 without it means nothing
