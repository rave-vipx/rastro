# AI repair evidence

This page records what connecting a model changes in Rastro's job repair, measured on the
offline AI repair benchmark (`benchmarks/ai_repair/`). It is evidence, not a model ranking
and not a release gate. Requirements: **QUAL-001, REPAIR-001–005, AI-001–004, DIFF-002**.
It does not use `benchmarks/recovery/`.

## Method

**Cases.** 28 synthetic pages, generated from four base layouts with five invented events
each: cards (`section > article`), a table, a definition list and a nested grid. Each case
is a before page, an after page, the expected records for the after page and a correct
recipe for it (`benchmarks/ai_repair/cases/<case>/`; `schema.json` describes the fields).

| Mutation | Kind | Layouts |
| --- | --- | --- |
| Record class renamed | structural | all |
| Title class renamed | structural | all |
| Container restructured (cards → `ul/li`, table → `div` grid, list → `ul/li` with spans, grid flattened) | structural | all |
| Date moved into a `data-` attribute on the record | structural | all |
| Title tag changed, wrapper added | structural | cards |
| Record class renamed and an optional field thinned (2 of 5 rows; 1 of 5 in the grid) | structural | table, grid |
| Record class renamed and a complete-looking promo block added outside the listing | trap | all |
| Title and performer given the same tag and class, performer first | trap | all |

**Flow.** Each Rastro executor works in a temporary folder through the public API.
`load_job_file` runs on the before page (it must succeed and retain the success sample),
then the after page is swapped in and loaded again. If that load succeeds, its records
are judged. Otherwise `repair_job_source` runs with `[repair] auto_promote = true` and
`min_records = 5`. If it promotes, the serving recipe's records (`run_job_file`) are
judged. The job declares no `[[consistency]]` rules. Job `[[fields]]` carry no
descriptions, so prompts get field names and types only.

With a model configured, Rastro itself decides when to call it (since the AI wave):
proposals only when no deterministic candidate would pass, joined with the serving
selectors (`old, new`), and the semantic judge before every automatic promotion,
deterministic ones included.

**Verdicts.** **correct**: exactly the expected records, compared as text,
order-insensitive, absent optional values ignored. **wrong**: anything else served
without refusal (a silent error). **refused**: nothing new served; the detail says
judge_blocked, quarantined, rejected, no_candidates or repair_error. Intervals are 95%
Wilson score intervals over 28 cases.

**Executors.**

- `deterministic`: Rastro with no model.
- `baseline`: a careful hand-written Scrapling + Pydantic scraper. It knows the base
  selectors, falls back to `[data-id]` records and generic per-field selectors (headings,
  `[class*=title]`, `time::attr(datetime)`, `a::attr(href)`, ...) and refuses when a record
  fails validation.
- `oracle_*`: a fake `ModelPort` that sees only Rastro's requests. **It measures Rastro's
  gates, not any model.** For a recipe request it finds the page by digest and answers
  with a scripted recipe. For a judge request it answers per field.
  - `correct`: the right recipe for the new page, and a truthful judge (`match` only when
    every candidate value is a true value of that field).
  - `wrong_recipe`: title and performer swapped, with the truthful judge.
  - `adversarial`: the swapped recipe, and a judge that always says `match`.
- `ollama:<name>`: the job's real `[model] provider = "ollama"` path (native `/api/chat`,
  temperature 0, `think: false`, schema-constrained output), default call budget,
  `timeout_seconds = 180`.

## Results

Recorded on 2026-11-13 from `cut/ai-wave`. The offline rows are reproducible with
`python -m benchmarks.ai_repair.run`. Python 3.12.16.

| Executor | Correct | Wrong (silent) | Refused | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Deterministic Rastro, no model | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 0 |
| Hand-written Scrapling + Pydantic baseline | 14/28 (0.33–0.67) | **4/28 (0.06–0.32)** | 10/28 | 0 |
| Scripted oracle: correct recipe, honest judge | **23/28 (0.64–0.92)** | 0/28 (0.00–0.12) | 5/28 | 44 |
| Scripted oracle: swapped recipe, honest judge | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 44 |
| Scripted oracle: swapped recipe, judge approves all | 12/28 (0.27–0.61) | **6/28 (0.10–0.40)** | 10/28 | 44 |
| `qwen3:4b` (local) | 5/28 (0.08–0.36) | 0/28 (0.00–0.12) | 23/28 | 44 |
| `llama3.2:3b` (local) | 11/28 (0.24–0.58) | 0/28 (0.00–0.12) | 17/28 | 44 |

The live rows are on commit `a3ba147`, which fixes the judge schema. On `7d4a84f`, before
that fix, both local models scored 0/28 correct, 0 wrong and 28 refused: every judge call
was rejected by Ollama, and every promotion was quarantined
(`results-live-*-7d4a84f.json`).

Live runs: one Apple M1 Pro, Ollama 0.40.13, one run per case. Models: `qwen3:4b`
digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` (4.0B,
Q4_K_M); `llama3.2:3b` digest
`a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72` (3.2B, Q4_K_M).
Raw results are in `benchmarks/ai_repair/results*.json`: verdict, detail, model calls
and seconds per case. Prompts and model output are not stored.

The previous measurement on 0.6.0 before the AI wave (model consulted on every repair,
no serving recipe in the prompt, no judge) was: deterministic 12, scripted correct oracle
11, `qwen3:4b` 11, `llama3.2:3b` 10, all with 0 wrong.

## What it shows

1. **Escalation plus the `old, new` union makes a good proposer useful.** With correct
   answers, Rastro recovers 23/28, up from 12/28. That adds three restructured
   containers, all four semantic swaps and all four decoy cases, loses no deterministic
   win and serves 0 wrong records. Before the AI wave, the same oracle reached 11/28.
2. **The judge is now the only defence against a meaning swap.** Because the union keeps
   the old selector first, the last good sample replays through it and passes, so a
   swapped new alternative is not caught by history. An honest judge blocked all six such
   proposals (0 wrong). A judge that approves everything let all six through: the
   **6/28 wrong** row. The same model writes the recipe and judges it, so one bad model
   defeats both checks.
3. **Small local models make repair worse here.** Their judge compares values instead of
   meanings. A correct title from a different event is called `mismatch` (seen on both
   models with a hand-made pair). So the judge blocks correct deterministic repairs:
   `qwen3:4b` 10 times (5/28 correct), `llama3.2:3b` once (11/28). Neither recovered any
   page that deterministic repair could not. Neither served wrong data.
4. **The hand-written baseline** recovers two more pages than deterministic Rastro, but
   it silently served promo blocks as events four times.
5. **Still out of reach for every executor:** a date moved into an attribute of the record
   itself (the grouped selector is ambiguous on the old page), and a table that lost an
   optional field in 2 of 5 rows (the documented 75% completeness rule).

## Defects found by this benchmark

| Defect | Status |
| --- | --- |
| 0.6.0: a model candidate competing with a passing relocation quarantined both | Fixed in the AI wave (escalation) |
| 0.6.0: the repair prompt lacked the serving recipe, so history replay rejected every model proposal | Fixed (serving recipe in the prompt, `old, new` union) |
| 0.6.0: an invalid model CSS selector aborted `recipe repair` with exit 2 | Fixed (`invalid_model_candidate`) |
| AI wave: the judge's response schema nested `$defs`, so Ollama rejected every judge call (HTTP 400) and every promotion was quarantined (`semantic_judge_failed`); live runs scored 0/28 | Fixed in `a3ba147`, with a regression test |
| AI wave: with the `old, new` union, history replay passes through the old alternative, so a meaning swap in the new one is caught only by the judge (6/28 wrong with a lying judge) | Open. Suggested fix: run the relocation value checks (`value_matches_other_field`) on model candidates |

## Run it yourself

Offline (CI-safe, no network, no model):

```sh
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run python -m benchmarks.ai_repair.run
```

Local Ollama models (never in CI):

```sh
uv run --extra ollama python -m benchmarks.ai_repair.run \
  --executor ollama:YOUR_MODEL --output benchmarks/ai_repair/results-live-YOUR_MODEL.json
```

### Strong-model run (owner-run, not recorded here)

Any provider goes through the generic factory with `--executor "spec:<spec>"` and
`--model-options '<JSON of rastro.model keywords>'`. These runs send all 28 synthetic
benchmark pages (no private content) to the provider under your plan's terms, so they
need `allow_remote`. Examples using the generic `command` transport, with presets from
[optional intelligence](intelligence.md):

```sh
# Claude Code subscription
uv run python -m benchmarks.ai_repair.run \
  --executor 'spec:command:claude -p --output-format json --tools= --no-session-persistence' \
  --model-options '{"json_path": "result", "allow_remote": true, "timeout": 120}' \
  --output benchmarks/ai_repair/results-live-claude-code.json

# Codex CLI subscription
uv run python -m benchmarks.ai_repair.run \
  --executor 'spec:command:codex exec --sandbox read-only --skip-git-repo-check --ephemeral -' \
  --model-options '{"output": "text", "allow_remote": true, "timeout": 180}' \
  --output benchmarks/ai_repair/results-live-codex.json
```

| Executor | Correct | Wrong (silent) | Refused | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Claude Code (owner run) | _pending_ | _pending_ | _pending_ | _pending_ |
| Codex CLI (owner run) | _pending_ | _pending_ | _pending_ | _pending_ |

`--tools=` (one argument) disables Claude Code's tools. An empty separate argument
(`--tools ""`) is refused by the command transport. Check `claude --help` and
`codex exec --help` for your installed versions before running. Record the CLI version,
model and date with the result file. No subscription or hosted
model was run for this page.
