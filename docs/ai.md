# Bring your own AI

Rastro works fully offline and deterministically. You can also connect a model: a local
one, any API you have a key for, the command-line tool of a subscription you already pay
for, or the agent that drives Rastro over MCP. Rastro does not prefer any provider. It
talks to all of them through one interface, `ModelPort`, and builds one with
`rastro.model(spec)`.

**A model never writes your data. It proposes, and Rastro verifies.** A model can suggest a
recipe (selectors and field mappings) or a judgement about one, never the records. Every
proposal is untrusted. Rastro parses it strictly, runs it on the real page, validates the
results against your schema and puts it through the same gates as a deterministic
candidate. Requirements: **AI-001–005, INT-002, REPAIR-001–005, QUAL-005/006, AGENT-002,
QUAL-001, P-001**.

What to expect, measured on the benchmark below:

- With a model that proposes the right recipe and judges honestly, Rastro repaired
  **23 of 28** broken pages. Without a model it repaired 12. Neither served wrong data.
- The small local models we tried (`qwen3:4b`, `llama3.2:3b`) recovered nothing new, and
  as judges they blocked correct repairs: 5/28 and 11/28 correct, 0 wrong. That run
  predates the change that stopped judging deterministic candidates.
- A model that is wrong in both roles (a meaning-swapping recipe, then a judge that
  approves it) gets **0 of 28** wrong repairs promoted: model candidates must pass the
  deterministic value checks before the judge sees them. Before those checks it got 6.

[Measured results](#measured-results) has the details.

## Connect a model

```python
import rastro

local = rastro.model("ollama:your-model")  # loopback, no opt-in
api = rastro.model(
    "http:your-model@https://api.example.com/v1", api_key_env="YOUR_API_KEY", allow_remote=True
)
cli = rastro.model("command", argv=["your-cli", "--print"], output="text", allow_remote=True)
fn = rastro.model(my_function)  # in-process callable
env = rastro.model()  # reads RASTRO_MODEL
```

| You have | Spec | Page content leaves the machine? |
| --- | --- | --- |
| A local runtime (Ollama, LM Studio, llama.cpp, vLLM, ...) | `ollama:<model>` or `http:<model>@http://127.0.0.1:<port>/v1` | No |
| An API key for any Chat Completions-compatible service | `http:<model>@<https url>` with `api_key_env` and `allow_remote=True` | Yes, opt-in |
| The command-line tool of a subscription | `command:<program and arguments>` (prompt on stdin), with `allow_remote=True` | Yes, opt-in |
| Any other runtime or SDK | A Python function, or your own `ModelPort` | It depends |
| An agent that already uses Rastro over MCP | No model inside Rastro. The agent calls `repair_context`, proposes with `recipe_propose` and judges, and Rastro verifies | Under the agent's own terms |

Pass the result as `model=` to `rastro.scrape`, `rastro.suggest`, `repair_job_source` or
`Rastro(...)`, put `--model SPEC` on the CLI, or declare `[model]` in a job file. Exact
options and presets for specific products are listed in
[optional intelligence](intelligence.md). Model names here are placeholders.

## What the model does in each flow

| Flow | Without a model | With a model |
| --- | --- | --- |
| `scrape` with no selectors | JSON-LD and microdata discovery | The model proposes a recipe from field names, types and descriptions. It is kept only if it extracts valid records |
| `suggest` | Selectors that reproduce your examples | Examples become optional. A model recipe must still reproduce every given example |
| Repair (`scrape(..., memory=)`, `recipe repair`) | Relocation, declared fallbacks, discovery | Consulted **only when no deterministic candidate would pass**. Its recipe is joined with the serving selectors (`old, new`), gated like any other and then held to the deterministic value checks: values of the field's type and shape, never taken from another field's earlier values. A failure rejects it |
| Semantic gate | `[[consistency]]` rules and replay of the last good sample | Before a model candidate is promoted automatically, the model judges whether each changed field still means the same. `mismatch` or a failed answer quarantines; it can block a promotion, never force one. Deterministic candidates are never sent to the judge |
| MCP agent in the loop | `recipe_repair`, `recipe_promote` (a reason is required) | The agent's own model proposes and judges through the tools. The same recipe CI decides |

Models are never enabled implicitly. Without `model=`, `--model`, `[model]` or
`RASTRO_MODEL`, Rastro makes no inference calls, and its core install and tests need no
model runtime.

## Privacy

- Using a model sends the page to it: the raw HTML, or a bounded digest when the page is
  over the context budget. Field names, types and descriptions, the serving recipe and up
  to two clipped earlier values go with it. Page text is labelled untrusted data.
- Any non-loopback endpoint, and any command that may call a hosted service, requires
  `allow_remote=True` (`--allow-remote`, `RASTRO_MODEL_ALLOW_REMOTE=1`). A model declared
  in a job file gets those permissions only with the operator's consent.
- A subscription CLI forwards prompts to its vendor's service under your plan's terms.
  Treat it as remote, even though the process runs locally.
- Keys are passed by environment variable name only. They never go into recipes, reports,
  logs, `repr` or errors. Rastro never logs prompts or page content.

## Cost and call budgets

`InferencePolicy` bounds every model phase: at most 2 calls by default (4 at most), 1
retry after a rejected or invalid answer, a 30 s timeout, 32 KiB of context, 64 KiB of
response and 2,048 output tokens (see [budgets](intelligence.md#budgets-and-outcomes)).
With a model, a repair costs nothing when a deterministic candidate passes, and one or two
proposal calls plus a judge call when none does. On the benchmark that was 32 calls for 28
cases with the scripted oracle.

## Measured results

The [AI repair benchmark](ai-evidence.md) has 28 synthetic before/after pages built from
four layouts (cards, table, definition list, nested grid). It covers class renames, tag
changes, added wrappers, restructured containers, fields moved into attributes, thinned
optional fields, decoy promo blocks and a semantic swap of two text fields. Each executor
ends a case as **correct** (it served exactly the expected records), **wrong** (it served
anything else without warning) or **refused** (it served nothing new and said why).

| Executor | Correct | Wrong (silent) | Refused | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Deterministic Rastro, no model | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 0 |
| Hand-written Scrapling + Pydantic baseline | 14/28 (0.33–0.67) | **4/28 (0.06–0.32)** | 10/28 | 0 |
| Scripted oracle: correct recipe, honest judge | **23/28 (0.64–0.92)** | 0/28 (0.00–0.12) | 5/28 | 32 |
| Scripted oracle: swapped recipe, honest judge | 12/28 (0.27–0.61) | 0/28 (0.00–0.12) | 16/28 | 32 |
| Scripted oracle: swapped recipe, judge approves all | 12/28 (0.27–0.61) | **0/28 (0.00–0.12)** | 16/28 | 32 |
| `qwen3:4b` (local, before the value checks) | 5/28 (0.08–0.36) | 0/28 (0.00–0.12) | 23/28 | 44 |
| `llama3.2:3b` (local, before the value checks) | 11/28 (0.24–0.58) | 0/28 (0.00–0.12) | 17/28 | 44 |

What the numbers say:

- **Escalation works.** With a model that answers correctly, Rastro now recovers
  restructured containers, semantic swaps and decoy cases that deterministic repair
  refuses, and it keeps every deterministic win: 23/28, with 0 wrong.
- **The judge is not the only gate.** The `old, new` union keeps the last good sample
  parsing through the old selector, so replay alone cannot catch a meaning swap in the new
  one. Model candidates therefore also meet the deterministic value checks, which reject a
  field whose values are another field's earlier values. A swapped recipe is now rejected
  before any judge call, so a judge that approves everything gets 0 wrong (it got 6
  before these checks). The checks need retained earlier values to compare with.
- **Small local models hurt more than they help as judges.** They compare values rather
  than meanings, so they call correct repairs `mismatch` and block them. `qwen3:4b`
  dropped to 5/28 and `llama3.2:3b` to 11/28, with no new recoveries and no wrong data.
  Those runs also judged deterministic candidates, which Rastro no longer does; they have
  not been rerun. Use a stronger model for the judge, or no model.
- The hand-written baseline recovers two more pages than deterministic Rastro, but it
  silently served promo blocks as events four times.
- The scripted oracle measures Rastro's gates, not model quality. Live results are from
  one machine, one run per case at temperature 0, and are not a ranking of providers.
