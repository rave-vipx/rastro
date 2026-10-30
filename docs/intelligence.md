# Optional recipe learning

M4 adds opt-in discovery and local Ollama assistance for contexts without a stored
recipe. A model proposes a **recipe**, never the extracted records. Rastro executes
that recipe against the original document and validates the observations itself.
Requirements: **INT-002, AI-001–004, REC-004**.

## Start without a model

```bash
uv run python examples/intelligence.py --database rastro-discovery.db
uv run python examples/intelligence.py --database rastro-discovery.db
```

The first run discovers synthetic JSON-LD with zero inference calls. The second
reuses its validated recipe. No Ollama installation, browser, or network is needed.

```python
from pydantic import BaseModel
from rastro import Rastro, Source


class Product(BaseModel):
    name: str
    price: float


async def extract_product():
    html = '<script type="application/ld+json">{"name":"Book","price":12.5}</script>'
    return await Rastro(discover_recipes=True).extract_html_async(
        html,
        source=Source(url="https://example.com/products"),
        schema=Product,
    )
```

`discover_recipes=True` enables automatic recipes on asynchronous methods. Supplying
`model=` also enables this mode. Without either option, omitting a recipe retains
M3's exact-context lookup behavior. Synchronous `extract_html` continues to use
explicit or stored recipes, even on a client configured with a model.

## Add a local Ollama model

Install the optional adapter from the checkout with `uv sync --extra ollama`.
Run your own trusted local Ollama service with a model already installed, then:

```python
from rastro import InferencePolicy, Rastro, SQLiteRecipeRepository
from rastro.adapters.ollama import OllamaModel

engine = Rastro(
    model=OllamaModel("your-installed-model"),
    inference_policy=InferencePolicy(max_calls=2, max_repair_attempts=1),
    repository=SQLiteRecipeRepository("rastro-recipes.db"),
)
# result = await engine.extract_html_async(html, source=source, schema=Product)
```

For a runnable synthetic DOM example:

```bash
uv run --extra ollama python examples/intelligence.py --model YOUR_INSTALLED_MODEL
```

Rastro never starts Ollama, downloads models, or registers tools. The adapter uses
`POST /api/chat`, a per-strategy JSON Schema response format, non-streaming generation,
temperature zero, disabled thinking (`think: false`), and explicit context/output token
options. Transport reads are streamed to
enforce the response-byte budget. Model quality and schema-format support vary;
validation remains mandatory regardless of model choice.

The default origin is `http://127.0.0.1:11434`. `base_url=` permits only literal
IPv4 loopback addresses or `::1`, with HTTP(S) and a valid port. Hostnames, credentials,
paths, queries, fragments, redirects and ambient proxy configuration are rejected
or disabled. Names containing `cloud` are rejected. The local service is trusted
infrastructure: its configuration or aliases could still route inference elsewhere.
Run it in local-only mode and enforce egress rules if local processing is mandatory.

For permitted web sources, pass the same `model`, `inference_policy` and/or
`discover_recipes` options to `Rastro.web(...)`. `extract` fetches once through the
existing HTTP policy before learning. Redirects preserve the requested URL's memory
identity and use the final URL for evidence. `extract_many` shares model concurrency
limits across its bounded workers.

## Selection and activation

1. An explicit recipe or exact-context stored recipe is executed first. Neither a
   failure nor a missing pinned version triggers model repair.
2. With no recipe, discovery tries JSON-LD mappings using exact schema field names.
   Nested names must be unique within each object; paths use JSON Pointer escaping.
3. It then tries simple `itemscope` / `itemprop` mappings, preferring `content`,
   `href`, `src`, or `datetime` attributes when present, otherwise direct text.
4. Only after deterministic candidates fail does the optional model propose a
   JSON-LD or CSS recipe. Model output cannot set versions, fallbacks, evidence
   policy, record values, tools, endpoints or executable code.

   `RecipeProposal` is a strategy union whose `CssProposal` and `JsonLdProposal`
   variants are derived from `CssRecipe` and `JsonLdRecipe` minus `version`,
   `require_evidence` and `fallbacks`, so selector constraints have one source.
   `proposal_schema()` is the single constrained-decoding schema builder for model
   adapters: an `anyOf` of the two proposal variants, each with
   `additionalProperties: false`. A proposal becomes a recipe only through
   `proposal.recipe()`, which validates it as the matching `Recipe` variant.
5. The recipe must map all required fields, produce source observations with
   locators, and pass the full Pydantic contract. Only `success` activates it in
   memory. `partial_success` returns its valid records and stops without activation.

Learned recipes carry `require_evidence=True`. The parser requires observations for
every required schema field **before** calling validators, including during reuse
after restart. A validator cannot manufacture a missing required field. Optional
defaults remain allowed and have no evidence. Validators are trusted caller code;
provenance describes source observations, not a proof of normalized values.

`result.learned_recipe` exposes a successfully validated new recipe, also when no
repository is configured. It can be inspected, serialized, or used with the
synchronous SDK. Reuse returns `recipe_reused=True` with no inference calls.
Two concurrent first runs proposing different recipes for the same key/version
raise an explicit `RecipeConflictError` for the losing write. The existing recipe
stays intact; there is no automatic version increment or replacement.

Discovery is intentionally conservative. It does not infer synonyms, crawl links,
fetch alternate APIs, resolve remote JSON-LD contexts, or render JavaScript.
Supplied structured metadata precedes DOM discovery; acquisition of separate
structured API representations remains a documented INT-002 gap.

## Budgets and outcomes

| `InferencePolicy` field | Default | Maximum |
| --- | --- | --- |
| `max_calls` | 2 | 4 |
| `max_repair_attempts` | 1 | 3 |
| `timeout_seconds` | 30 | 300 |
| `max_context_bytes` | 32,768 | 262,144 |
| `max_response_bytes` | 65,536 | 1,048,576 |
| `max_output_tokens` | 2,048 | 8,192 |
| `context_tokens` | 8,192 | 32,768 |
| `max_concurrency` | 1 | 16 |
| `max_discovery_candidates` | 8 | 32 |

Calls are bounded by `min(max_calls, 1 + max_repair_attempts)`. “Repair” here means
correcting a rejected cold-context proposal; the v0.2 stored-recipe lifecycle is not
implemented. Provider failures and response-budget failures stop immediately without
transport retries. A retry includes only a safe failure category, never the previous
model output or validator error text.

The timeout covers the complete model phase: queue wait, calls and candidate
validation. It excludes initial deterministic discovery and storage. Concurrency
is per Rastro instance in one event loop, not global across clients/processes.
Cancellation propagates and closes HTTP streams. Python/native worker threads and
an already-started Ollama generation may continue after client cancellation;
these limits are not an OS sandbox or a guarantee of server compute termination.

Context accounting includes both message envelopes and the recipe output schema,
using conservative escaped JSON byte sizing. It is separate from Ollama's token
window; model tokenization can still truncate its internal context. Oversized input
is rejected, never silently sliced by Rastro. Responses with a reported output-token
limit are rejected. Compressed responses are rejected before decompression.
Existing HTML, record and JSON-LD block limits apply. Nested discovery is bounded
to depth 8 and 4,096 keys per JSON-LD object; no candidate combinations are generated.

No valid recipe yields `insufficient_evidence`, with categories such as
`no_valid_recipe`, `invalid_model_candidate`, `model_context_limit` or
`model_response_limit`. An unavailable/timed-out provider yields `transient_failure`.
Storage/configuration/resource errors retain the existing explicit exception
behavior (HTTP converts extraction resource limits to `permanent_failure`).
`recipe_attempts` records origin and outcome; `metrics.inference_calls` counts calls
started after acquiring capacity, and `inference_seconds` includes model-phase queue
and validation time. Candidate counts/parsing times sum actual parsing attempts.

## Trust and verification

System instructions are fixed; page text lives in a separate JSON data envelope.
Only field names and required-field names accompany the HTML. Source URLs and full
Pydantic schemas, descriptions, defaults and examples are not sent as prompt metadata.
The HTML itself may contain sensitive content: opting into a model authorizes sending
that document to the configured local service. Prompt separation does not guarantee
semantic correctness. A malicious page or model can still suggest the wrong existing
node; use meaningful schema validators and review samples.

The enforceable boundary is declarative output plus independent source/schema
validation. Nothing in page/model text can add tools, execute code, change network
destinations or bypass source policy. Recipes/evidence may include literal selector
values from source content; keep the database and results private when needed.

Default tests use synthetic pages and fake model/HTTP transports with sockets
disabled. They cover attacks, limits, cancellation, activation, restart and M3
payload compatibility. Core wheel installation is checked without HTTPX or Ollama.
These tests do not claim real-model quality. The separate
[live Ollama evidence](live-ollama-evidence.md) records one local `qwen3:4b` run
with its opt-in harness, model digest and limitations.

See [ADR 0004](decisions/0004-bounded-recipe-learning.md) for adapter and trust decisions.
