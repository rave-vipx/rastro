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

## Choosing or writing a model provider

`model=` accepts any object implementing `ModelPort`, the provider strategy
interface. Rastro ships two optional strategies; both need HTTPX, so the core
install never does (P-002). Requirements: **AI-001–005, SEC-004/005/011, P-001, P-007**.

| Provider | Install | Endpoint | Use when |
| --- | --- | --- | --- |
| `OllamaModel` | `rastro-engine[ollama]` | native `/api/chat`, loopback only | A local Ollama service; strictest local-only checks |
| `OpenAICompatibleModel` | `rastro-engine[openai]` | `POST {base_url}/chat/completions` | Any Chat Completions server, local or (opt-in) remote |

Both are importable from `rastro` lazily (`from rastro import OpenAICompatibleModel`);
without the extra, the attribute access raises an `ImportError` naming it. They are
intentionally absent from `rastro.__all__`, so `from rastro import *` stays core-only.

### OpenAI-compatible endpoints

```python
from rastro import InferencePolicy, OpenAICompatibleModel, Rastro

# Local servers: literal loopback addresses need no opt-in.
ollama_v1 = OpenAICompatibleModel("llama3.2:3b")  # http://127.0.0.1:11434/v1
lm_studio = OpenAICompatibleModel("qwen2.5-7b-instruct", base_url="http://127.0.0.1:1234/v1")
vllm = OpenAICompatibleModel("Qwen/Qwen2.5-7B-Instruct", base_url="http://127.0.0.1:8000/v1")
llama_cpp = OpenAICompatibleModel(
    "local", base_url="http://127.0.0.1:8080/v1", response_format="json_object"
)

# Remote services: page content leaves the machine, so opt in explicitly.
openai = OpenAICompatibleModel(
    "gpt-4.1-mini",
    base_url="https://api.openai.com/v1",
    api_key_env="OPENAI_API_KEY",
    allow_remote=True,
)
openrouter = OpenAICompatibleModel(
    "openai/gpt-4.1-mini",
    base_url="https://openrouter.ai/api/v1",
    api_key_env="OPENROUTER_API_KEY",
    allow_remote=True,
)

engine = Rastro(model=openai, inference_policy=InferencePolicy(max_calls=2))
```

Model names above are examples; use one your server actually serves.

**Request.** One non-streaming completion (`n: 1`) with the fixed system message,
the untrusted JSON data envelope as the user message, `temperature: 0`,
`max_tokens` from `InferencePolicy.max_output_tokens`, and
`response_format: {"type": "json_schema", "json_schema": {"name": "rastro_recipe_proposal", "strict": false, "schema": proposal_schema()}}`.
No `tools`, `tool_choice` or `functions` are ever sent. OpenAI's *strict* structured
outputs cannot express the recipe field map or the per-strategy `anyOf`, so the schema
is a best-effort decoding constraint; Rastro validates every proposal regardless.
For servers without `json_schema` support, pass `response_format="json_object"`:
JSON mode is requested and the schema is appended to the trusted system message.
There is no automatic fallback or transport retry; choose the mode explicitly.

**Endpoint rules.** `base_url` is an HTTP(S) origin with an optional plain path such as
`/v1` or `/api/v1`. Credentials, queries, fragments, dot segments and port `0` are
rejected. Literal IPv4 loopback addresses and `::1` work without opt-in. Every other
host, including `localhost` and LAN addresses, requires `allow_remote=True` **and**
HTTPS. Model names are bounded to `[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}`; on a
loopback endpoint without `allow_remote`, names containing `cloud` are rejected because
local servers such as Ollama can proxy them off the machine. Redirects are never
followed and ambient proxy environment variables are ignored (`trust_env=False`).

**API keys.** Pass only the environment variable's name in `api_key_env`. The variable
must exist when the adapter is created and is read again for each request, so keys can
rotate; it is sent only as `Authorization: Bearer …`. The value is never stored on
the object, shown by `repr`, logged, serialized into recipes or included in errors.
A key that disappears later yields `model_unavailable` without a request.

**Budgets and failures.** The adapter applies the same `InferencePolicy` as the
learner: context bytes are checked before any connection, one deadline covers the
adapter's concurrency slot wait plus the whole request, the raw body is streamed
against `max_response_bytes`, compressed responses are refused, and
`max_concurrency` bounds in-flight calls per adapter instance and event loop. The
body is parsed with strict JSON (duplicate keys, `NaN` and invalid UTF-8 rejected).
Non-200 statuses (including 3xx and 429) map to `model_unavailable`, timeouts to
`model_timeout`, oversized bodies to `model_response_limit`, and malformed bodies,
multiple choices, a `finish_reason` other than `stop` (`length`, `content_filter`,
`tool_calls`), refusals, tool calls or reported `completion_tokens` above budget to
`invalid_model_response`. Provider bodies never appear in errors.

**Privacy.** Opting into any model sends the page document to that endpoint. With
`allow_remote=True`, that is a third-party service under its own retention terms;
keep the default loopback endpoint when local processing is mandatory (P-001). The
recorded live check against Ollama's `/v1` endpoint is described in
[ADR 0010](decisions/0010-model-providers.md); it is not a model-quality claim.

### Writing a custom provider

Implement one coroutine. A provider is trusted infrastructure code, but what it
returns is untrusted: Rastro parses and validates it like any model output.

```python
import asyncio
from collections.abc import Callable

from rastro import InferencePolicy, ModelError, ModelRequest, Rastro
from rastro.domain.intelligence import proposal_schema


class LocalRuntimeModel:
    """Wrap a blocking local runtime, e.g. an in-process llama.cpp binding."""

    def __init__(self, run: Callable[[str, str, dict[str, object], int], str]) -> None:
        self._run = run

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        if request.context_bytes > policy.max_context_bytes:
            raise ModelError("model_context_limit")
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                text = await asyncio.to_thread(
                    self._run,
                    request.system,
                    request.content,
                    proposal_schema(),
                    policy.max_output_tokens,
                )
        except TimeoutError:
            raise ModelError("model_timeout") from None
        except OSError:
            raise ModelError("model_unavailable") from None
        if len(text.encode()) > policy.max_response_bytes:
            raise ModelError("model_response_limit")
        return text


engine = Rastro(model=LocalRuntimeModel(lambda system, user, schema, tokens: "{}"))
```

The contract:

1. **Return JSON text** for one recipe proposal matching `proposal_schema()`. Do not
   parse it into records or repair it; Rastro strict-parses, validates against
   `RecipeProposal`, executes it on the original document and validates the schema.
2. **Keep messages separate.** Send `request.system` as trusted instructions and
   `request.content` (the untrusted page envelope) as user data. Never add tools,
   function calling, browsing or code execution.
3. **Raise `ModelError`** with one of its fixed categories (`model_unavailable`,
   `model_timeout`, `model_response_limit`, `invalid_model_response`,
   `model_context_limit`) and `from None`, so provider messages, bodies and keys never
   surface. Truncated, refused or tool-calling responses are `invalid_model_response`.
   Other exceptions propagate as programmer errors.
4. **Respect the policy and cancellation.** Honour `timeout_seconds`,
   `max_context_bytes`, `max_response_bytes` and `max_output_tokens`; never retry
   behind Rastro's back (the learner owns call and repair budgets). Let
   `asyncio.CancelledError` propagate and close network streams. Rastro additionally
   bounds the phase deadline, call count and concurrency.
5. **Handle secrets by reference.** Read keys from the environment or a secret
   manager at call time; never accept them as constructor strings, log them or put
   them in `repr`.

An Anthropic Messages API provider, for example, sends `request.system` as the
top-level system prompt and `request.content` as the user turn, sets `max_tokens`
from the policy, asks for JSON matching `proposal_schema()`, rejects any non-text or
tool-use content and any truncated stop reason, reads its key from an environment
variable and maps HTTP/SDK errors to the categories above. Anthropic's API is not
Chat Completions-compatible by default, so it needs its own strategy rather than
`OpenAICompatibleModel`.

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
that document to the configured service (remote only with `allow_remote=True`).
Prompt separation does not guarantee semantic correctness. A malicious page or model can still suggest the wrong existing
node; use meaningful schema validators and review samples.

The enforceable boundary is declarative output plus independent source/schema
validation. Nothing in page/model text can add tools, execute code, change network
destinations or bypass source policy. Recipes/evidence may include literal selector
values from source content; keep the database and results private when needed.

Default tests use synthetic pages and fake model/HTTP transports with sockets
disabled. They cover attacks, limits, cancellation, activation, restart and M3
payload compatibility. Core wheel installation is checked without HTTPX or Ollama. The OpenAI-compatible
adapter has its own offline contract and mock-server learning tests.
These tests do not claim real-model quality. The separate
[live Ollama evidence](live-ollama-evidence.md) records one local `qwen3:4b` run
with its opt-in harness, model digest and limitations.

See [ADR 0004](decisions/0004-bounded-recipe-learning.md) for adapter and trust decisions.
