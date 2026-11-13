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

**Flow.** Each Rastro executor works in a temporary folder through the public API:
`load_job_file` on the before page (it must succeed and retain the success sample),
then the after page is swapped in and loaded again. If that load succeeds, its records
are judged. Otherwise `repair_job_source` runs with `[repair] auto_promote = true` and
`min_records = 5`. If it promotes, the serving recipe's records (`run_job_file`) are
judged. The job declares no `[[consistency]]` rules.

**Verdicts.** **correct**: exactly the expected records, compared as text, order-insensitive,
absent optional values ignored. **wrong**: anything else served without refusal (a silent
error). **refused**: nothing new served; the detail says quarantined, rejected,
no_candidates or repair_error. Intervals are 95% Wilson score intervals over 28 cases.

**Executors.**

- `deterministic`: Rastro 0.6.0 with no model.
- `baseline`: a careful hand-written Scrapling + Pydantic scraper. It knows the base
  selectors, falls back to `[data-id]` records and generic per-field selectors (headings,
  `[class*=title]`, `time::attr(datetime)`, `a::attr(href)`, ...) and refuses when a record
  fails validation.
- `oracle_*`: a fake `ModelPort` that sees only Rastro's prompt. It finds the page by
  digest and answers with a scripted recipe. `correct` gives the right recipe for the new
  page. `compatible` joins the old and new selectors (`old, new`), which needs the serving
  recipe that this release's prompt does not send. `adversarial` gives the compatible
  recipe with the title and performer selectors swapped. **This measures Rastro's gates,
  not any model.**
- `escalate+<model>`: repair without a model first, then add `[model]` to the job and
  repair again only when nothing was promoted.
- `ollama:<name>`: the job's real `[model] provider = "ollama"` path at temperature 0,
  `think: false`, default `InferencePolicy` (2 calls, 1 retry after invalid output) with
  `timeout_seconds = 180`.

## Results

Offline executors: commit `121e9be` (harness), Rastro 0.6.0, Python 3.12.16. Every run is
deterministic and reproducible with `python -m benchmarks.ai_repair.run`.

| Executor | Correct | Wrong (silent) | Refused | Model calls |
| --- | ---: | ---: | ---: | ---: |
| deterministic | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 0 |
| baseline | 14/28 (0.33–0.67) | **4/28 (0.06–0.32)** | 10/28 | 0 |
| oracle_correct | 11/28 (0.24–0.58) | 0/28 (0.00–0.12) | 17/28 | 28 |
| oracle_compatible | 9/28 (0.18–0.51) | 0/28 (0.00–0.12) | 19/28 | 28 |
| oracle_adversarial | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 28 |
| escalate+oracle_compatible | **21/28 (0.57–0.87)** | 0/28 (0.00–0.12) | 7/28 | 16 |
| escalate+oracle_adversarial | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 16 |

Live local models, recorded on one Apple Silicon Mac with Ollama 0.40.13, one run per case:

| Executor | Correct | Wrong (silent) | Refused | Model calls | Wall time |
| --- | ---: | ---: | ---: | ---: | ---: |
| ollama:qwen3:4b (commit `c2e604b`) | 11/28 (0.24–0.58) | 0/28 (0.00–0.12) | 17/28 | 36 | 181 s |
| escalate+ollama:qwen3:4b | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 22 | 88 s |
| ollama:llama3.2:3b (commit `121e9be`) | 10/28 (0.21–0.54) | 0/28 (0.00–0.12) | 18/28 (4 repair errors) | 39 | 135 s |
| escalate+ollama:llama3.2:3b | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 (2 repair errors) | 23 | 68 s |

Commits `c2e604b` and `121e9be` differ only in how the harness counts an aborted repair.
The first `llama3.2:3b` attempt on `c2e604b` crashed the harness on that defect.

Models: `qwen3:4b` digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`
(4.0B, Q4_K_M); `llama3.2:3b` digest
`a80c4f17acd55265feec403c7aef86be0c25983ab279d83f3bcd3abbcb5b8b72` (3.2B, Q4_K_M). Raw
results: `benchmarks/ai_repair/results.json`, `results-live-qwen3-4b.json`,
`results-live-llama3.2-3b.json` (per case: verdict, detail, model calls, seconds; no
prompts or model output are stored).

## What it shows

1. **No silent errors from Rastro.** No Rastro executor served wrong data, including the
   adversarial oracle and the live models. The hand-written baseline recovered two more
   pages, but it served the promo blocks as events in four of the trap cases.
2. **Consulting a model on every repair does not help in 0.6.0.** When deterministic
   repair already has a passing candidate, a second passing candidate from the model makes
   recipe CI quarantine both (competing proposals). `oracle_compatible` loses 12
   deterministic wins this way.
3. **Proposals for the new page alone fail the history gate.** Recipe CI replays the last
   good sample, and a selector for the new structure finds nothing there. The repair
   prompt sends field names and the page, not the serving recipe, so even a perfect reader
   of the page (`oracle_correct`) recovers nothing new.
4. **With escalation and old+new selectors, a perfect proposer recovers 9 more pages**:
   three restructured containers, all four semantic swaps and two decoy cases. It loses
   none, for 21/28 correct with 16 calls. This is the ceiling for a model in this design,
   not a result of any real model.
5. **Small local models add nothing on these pages.** `qwen3:4b` recovered no page that
   deterministic repair could not. Consulted every time, it cost one correct repair (the
   grid restructure, through a competing candidate). `llama3.2:3b` also recovered nothing
new. Four of its proposals contained invalid CSS, which aborted the repair (see below).
Two of those aborts erased correct deterministic repairs (cards class rename and tag
change). Escalation kept both models at the deterministic 12/28 with about 40% fewer
calls.
6. **Still out of reach for everyone:** a date moved into an attribute of the record
   itself. Rastro reports the grouped `time::attr(datetime), ::attr(data-starts-at)`
   selector as ambiguous on the old page. A table that lost an optional field in 2 of 5
   rows is quarantined by the documented 75% completeness rule.

## Defects found

- An invalid CSS selector in a model proposal makes `recipe repair` raise
  `ConfigurationError` and exit 2, instead of rejecting that one candidate. No data is
  written, but the deterministic candidates of the same run are lost. The benchmark counts
  this as `refused/repair_error`.

## Run it yourself

Offline (CI-safe, no network, no model):

```sh
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run python -m benchmarks.ai_repair.run
```

Local models (never in CI):

```sh
uv run --extra ollama python -m benchmarks.ai_repair.run \
  --executor ollama:YOUR_MODEL --executor escalate+ollama:YOUR_MODEL \
  --output benchmarks/ai_repair/results-live-YOUR_MODEL.json
```

Any other provider, including the CLI of a subscription, goes through the generic model
factory `rastro.model(spec)` with the same spec you would put in `RASTRO_MODEL`. These
runs send every benchmark page (synthetic, no private content) to that provider, so the
spec must opt into remote use:

```sh
uv run python -m benchmarks.ai_repair.run \
  --executor "spec:$RASTRO_MODEL" --executor "escalate+spec:$RASTRO_MODEL" \
  --output benchmarks/ai_repair/results-live-mine.json
```

The subscription CLIs were not run for this page. Commands for them are left for the owner,
using the generic `command` transport of `rastro.model`.
