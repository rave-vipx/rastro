# Bring your own AI

Rastro works fully offline and deterministically. You can also connect a model: a local
one, any API you have a key for, the command-line tool of a subscription you already pay
for, or the agent that drives Rastro over MCP. Rastro does not prefer any provider. It
talks to all of them through one interface, `ModelPort`.

**A model never writes your data. It proposes, and Rastro verifies.** A model can suggest a
recipe (selectors and field mappings) or a judgement about one, never the records. Every
proposal is untrusted. Rastro parses it strictly, runs it on the real page, validates the
results against your schema and puts it through the same gates as a deterministic
candidate. Requirements: **AI-001–005, INT-002, REPAIR-001–005, AGENT-002, QUAL-001, P-001**.

Be careful what you expect. On the benchmark below, connecting a model **did not increase
the number of correct repairs** in Rastro 0.6.0, and the small local models we tried added
nothing. What a model never did was make Rastro serve wrong data: every wrong proposal was
refused. [Measured results](#measured-results) gives the numbers and the reasons.

## Connect a model

Every option ends up as a `ModelPort` passed with `model=`, or as a job's `[model]` table.
For constructor options, endpoint rules and how to write your own provider, see
[optional intelligence](intelligence.md#choosing-or-writing-a-model-provider).

| You have | Use | Page content leaves the machine? |
| --- | --- | --- |
| A local runtime (Ollama, LM Studio, llama.cpp, vLLM, ...) | `OllamaModel`, or `OpenAICompatibleModel` on a loopback URL | No |
| An API key for any Chat Completions-compatible service | `OpenAICompatibleModel(..., api_key_env=..., allow_remote=True)` | Yes, opt-in |
| The command-line tool of a subscription (any vendor's coding assistant CLI) | A `ModelPort` that runs the command: the generic command transport of `rastro.model(...)`, or a small custom provider | Yes when the tool calls a hosted service; opt-in |
| Any other runtime, SDK or in-process model | A custom `ModelPort` (one `async generate` method) | It depends on the provider |
| An agent that already uses Rastro over MCP | No model inside Rastro: the agent reads results and proposes changes through the MCP tools, and the same gates apply | Under the agent's own terms |

Model names in examples are placeholders. Use one your provider actually serves.

```python
import rastro
from rastro import InferencePolicy, OpenAICompatibleModel

local = OpenAICompatibleModel("your-local-model")  # http://127.0.0.1:11434/v1, no opt-in
engine = rastro.Rastro(model=local, inference_policy=InferencePolicy(max_calls=2))
```

A job file uses the same providers:

```toml
[model]
provider = "openai_compatible"
model = "your-model"
base_url = "https://api.example.com/v1"
api_key_env = "YOUR_API_KEY"   # the variable's name, never the key
allow_remote = true            # required for any non-loopback endpoint
```

## What the model does in each flow

| Flow | Without a model | With a model |
| --- | --- | --- |
| Asynchronous extraction with no recipe (`extract_html_async`, `Rastro.web(...)`) | JSON-LD and microdata discovery | The model proposes a CSS or JSON-LD recipe when discovery finds none. Rastro runs it, validates it and stores it only on `success` |
| `suggest` from examples | Deterministic search for selectors that reproduce your examples | Unchanged. Examples remain the most reliable way to author a recipe |
| `recipe repair` (jobs) | Relocation from saved fingerprints, declared fallbacks, discovery | One more candidate from the model. It goes through the same recipe CI: current validation, replay of the last good sample, configured thresholds |
| Semantic checks | The `[[consistency]]` rules you declare, plus replay of the last good sample | Any model judgement is advisory: it can reject a repair, never approve one. A repair is promoted only if every deterministic gate passes |
| MCP agent in the loop | The agent calls `recipe_repair`, `recipe_promote` (a reason is required) and `suggest_recipe` | The agent is the model. It never bypasses gates, and promotion with `--override-gates` still cannot activate a rejected candidate |

Models are never enabled implicitly. Without `model=` or `[model]`, Rastro makes no
inference calls, and its core install and tests need no model runtime.

## Privacy

- Opting into a model sends the page document (bounded by `max_context_bytes`) to that
  provider. The field names go with it. The source URL and full schema do not.
- Any non-loopback endpoint requires `allow_remote=True` (`allow_remote = true` in TOML)
  and HTTPS. A loopback model name containing `cloud` is refused without it, because
  local servers can forward such models to a hosted service.
- A subscription CLI usually forwards prompts to its vendor's service. Treat it as remote,
  even though the process runs locally.
- Keys are read from environment variables by name. They never go into recipes,
  reports, logs, `repr` or errors. Rastro never logs prompts or page content.

## Cost and call budgets

`InferencePolicy` bounds every model phase: at most 2 calls by default (4 at most), 1
retry after invalid output, a 30 s timeout, 32 KiB of context, 64 KiB of response and
2,048 output tokens (see [budgets](intelligence.md#budgets-and-outcomes)). A repair report
states `inference_calls` for each source. The benchmark below spent one call per repair
in most cases, and two when the first answer was invalid.

You can save calls and keep deterministic wins by **escalating**: run `recipe repair`
without `[model]` first, and add the model only for sources that are still broken. On the
benchmark, escalation never lost a correct deterministic repair and needed 16 calls
instead of 28.

## Measured results

The [AI repair benchmark](ai-evidence.md) has 28 synthetic before/after pages built from
four layouts (cards, table, definition list, nested grid). It covers class renames, tag
changes, added wrappers, restructured containers, fields moved into attributes, thinned
optional fields, decoy promo blocks and a semantic swap of two text fields. Each executor
ends a case as **correct** (it served exactly the expected records), **wrong** (it served
anything else without warning) or **refused** (it served nothing new and said why).

| Executor (Rastro 0.6.0) | Correct | Wrong | Refused | Model calls |
| --- | ---: | ---: | ---: | ---: |
| Deterministic Rastro, no model | 12 | 0 | 16 | 0 |
| Hand-written Scrapling + Pydantic with fallbacks | 14 | **4** | 10 | 0 |
| Scripted oracle, always consulted (not a model) | 9–12 | 0 | 16–19 | 28 |
| Scripted oracle, consulted only after deterministic failure (not a model) | 21 | 0 | 7 | 16 |
| `qwen3:4b` (local), always consulted | 11 | 0 | 17 | 36 |
| `qwen3:4b` (local), escalation | 12 | 0 | 16 | 22 |
| `llama3.2:3b` (local), always consulted | 10 | 0 | 18 | 39 |
| `llama3.2:3b` (local), escalation | 12 | 0 | 16 | 23 |

What the numbers say:

- **Rastro served no wrong data under any executor**, including an adversarial oracle that
  proposed recipes with title and performer swapped. The hand-written baseline recovered
  two more pages but silently served promo blocks as events four times.
- **A perfect proposer helps only when it is consulted after deterministic repair fails,
  and only if it keeps the old selectors.** That raises correct repairs from 12 to 21.
  Consulted every time, its passing candidate competes with the passing deterministic
  one, and recipe CI quarantines both. A proposal for the new page alone fails the
  replay of the last good sample, because this release's repair prompt does not send the
  serving recipe.
- **Small local models added nothing here.** `qwen3:4b` and `llama3.2:3b` recovered no
  page that deterministic repair could not. Consulted every time, they cost one and two
  correct repairs. Every bad proposal was refused. However, a syntactically invalid
  selector (4 of `llama3.2:3b`'s answers) aborts the whole `recipe repair` run with
  exit 2, a known defect. With escalation, both stayed at the deterministic 12.
- The scripted oracle measures Rastro's gates, not model quality. Live results are from
  one machine, one run per case at temperature 0, and are not a ranking of providers.
