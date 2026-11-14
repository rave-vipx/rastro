# Bring your own AI

Rastro works without any AI. When you want one, it uses **whatever AI you already have**:
a local model, any OpenAI-compatible API, a subscription CLI such as Claude Code or Codex,
or a plain Python function. No provider is special-cased in code; the products below are
documented presets of three generic transports.

A model proposes a **recipe** (selectors and field mappings), never the extracted records.
Rastro executes the recipe on the original document and validates every observation with
the same deterministic gates as a hand-written recipe. Model output is untrusted: it can only
help Rastro find more correct recipes and reject more wrong ones.
Requirements: **INT-002, AI-001–005, REC-004, REPAIR-001, SEC-004/005/011, P-001**.

## Start without a model

```bash
uv run python examples/intelligence.py --database rastro-discovery.db
uv run python examples/intelligence.py --database rastro-discovery.db
```

The first run discovers synthetic JSON-LD with zero inference calls. The second
reuses its validated recipe. No model, browser, or network is needed.

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
exact-context lookup behavior. Synchronous `extract_html` continues to use
explicit or stored recipes, even on a client configured with a model.

## `rastro.model()`: one entry point, three transports

```python
import rastro

local = rastro.model("ollama:qwen3:4b")  # loopback HTTP, no opt-in
api = rastro.model(
    "http:gpt-4.1-mini@https://api.openai.com/v1", api_key_env="OPENAI_API_KEY", allow_remote=True
)
cli = rastro.model(
    'command:claude -p --output-format json --tools ""',
    json_path="result",
    allow_remote=True,
    timeout=120,
)
fn = rastro.model(my_function)  # async or sync callable
env = rastro.model()  # reads RASTRO_MODEL

engine = rastro.Rastro(model=cli, inference_policy=rastro.InferencePolicy(timeout_seconds=120))
```

`rastro.model(spec=None, **options)` returns a `ModelPort`. It accepts:

| Spec | Transport | Notes |
| --- | --- | --- |
| `"http:<model>@<base_url>"`, or `"http"` with `model=`, `base_url=` | OpenAI-compatible Chat Completions | Needs `rastro-engine[openai]` (HTTPX). Without `@<base_url>` the endpoint is `http://127.0.0.1:11434/v1` |
| `"ollama:<model>"` | Same, on loopback Ollama `/v1` | Shorthand; `"ollama"` with `model=` keeps the native `/api/chat` adapter |
| `"command:<program and arguments>"`, or `"command"` with `argv=[...]` | Any local CLI, prompt on stdin | Core install; no extra |
| a function | In-process Python | `async def f(request) -> str` or a blocking `def` |
| a `ModelPort` object | Passed through unchanged | Options are refused |

Anything else raises `ConfigurationError` listing the valid forms. Options given as
keywords override what the spec string says (`allow_remote`, `local`, `timeout`,
`output`, `json_path`, `env`, `api_key_env`, `response_format`, `token_limit`,
`temperature`); unknown options are refused, never ignored.

**Environment.** With no spec, `RASTRO_MODEL` holds a spec string. Because a string cannot
carry consent, set `RASTRO_ALLOW_REMOTE=1` to allow a remote endpoint or a command
that may send data off the machine, or `RASTRO_MODEL_LOCAL=1` to declare a command local.
`RASTRO_ALLOW_REMOTE` is the one remote opt-in for `RASTRO_MODEL` and for models declared in
job files. Every boolean opt-in (`RASTRO_ALLOW_REMOTE`, `RASTRO_ALLOW_EGRESS`,
`RASTRO_MODEL_LOCAL`) accepts `1`, `true` or `yes` in any case; anything else is off.

**Command strings are never run by a shell.** `command:` text is split with `shlex` into an
argument list for `exec`; `|`, `;`, `$(...)` and redirections are passed as literal
arguments. Pass `argv=[...]` to avoid quoting questions entirely.

### OpenAI-compatible HTTP

One strategy covers OpenAI, Ollama's `/v1`, LM Studio, vLLM, llama.cpp server, OpenRouter,
Groq, Gemini's OpenAI-compatible endpoint and any gateway such as LiteLLM. Literal loopback
addresses need no opt-in; every other host, including `localhost` and LAN addresses,
requires `allow_remote=True` **and** HTTPS. `api_key_env` names the environment variable
holding the key; the key is read per request, sent only as `Authorization: Bearer …`, and
never stored, logged or shown by `repr`. Details of the request, endpoint rules and failure
mapping are in [OpenAI-compatible endpoints](#openai-compatible-endpoints).

### Command (any local CLI)

```python
rastro.model(
    "command",
    argv=["llm", "-m", "mistral"],
    local=True,  # or allow_remote=True if the CLI calls a hosted service
    timeout=120,  # capped by InferencePolicy.timeout_seconds
    output="text",  # "json": stdout is one JSON document; "text": free text
    json_path=None,  # e.g. "result" to dig into {"result": "..."} wrappers
    env=["LLM_USER_PATH"],  # extra variable names passed through
)
```

Rastro writes one prompt to stdin: the fixed trusted instructions, the answer's JSON Schema
(`request.output_schema`), then the JSON data envelope. It reads stdout within
`max_response_bytes` and the deadline, then extracts the answer: with `output="json"` the
stdout must be one JSON document, `json_path` (dot-separated keys) selects a field, and a
string value is searched for its first JSON object (so Markdown fences are fine); with
`output="text"` the first top-level JSON object in stdout is used.

- **Consent.** A CLI may forward page content to a hosted service, so `allow_remote=True`
  is required unless you declare `local=True`.
- **Environment.** The child sees only `PATH`, `HOME` and the names listed in `env=`.
  Values are read per call and never stored.
- **Failures.** A missing program or non-zero exit is `model_unavailable`, the deadline is
  `model_timeout` (the process is killed), oversized stdout is `model_response_limit`, and
  output without a JSON object is `invalid_model_response`. Stderr is discarded and never
  surfaces in errors or logs.

### Python function

```python
async def my_function(request: rastro.ModelRequest) -> str:
    # request.system: trusted instructions; request.content: untrusted JSON envelope;
    # request.output_schema: the JSON Schema the answer must match.
    return await my_sdk_call(request.system, request.content, request.output_schema)
```

A blocking function runs in a worker thread. Rastro applies the context, deadline and
response-size budgets; raise `ModelError` with a fixed category for provider failures.
Other exceptions propagate as programmer errors.

## Presets

These are documentation, not code paths. Flags were checked against the installed
`--help` output where the tool was available (Claude Code, Codex CLI, Ollama); check your
version's help before relying on them.

| You have | Spec | Privacy |
| --- | --- | --- |
| Claude Code subscription | `rastro.model("command", argv=["claude", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence"], json_path="result", allow_remote=True, timeout=120)` | Page content goes to Anthropic under your plan's terms. `--tools ""` disables all tools |
| Codex CLI subscription | `rastro.model("command", argv=["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "-"], output="text", allow_remote=True, timeout=180)` | Page content goes to OpenAI. `-` reads the prompt from stdin; the read-only sandbox stops it writing files |
| Gemini CLI | `rastro.model("command", argv=["gemini", "-p", ""], output="text", allow_remote=True)` (not installed here; check `gemini --help` for how it reads stdin) | Page content goes to Google |
| `llm` (Simon Willison) | `rastro.model("command", argv=["llm", "-m", "<model>"], output="text", local=True)` for a local plugin model, `allow_remote=True` for hosted ones | Depends on the selected model |
| Ollama | `rastro.model("ollama:<model>")`, or `rastro.model("command", argv=["ollama", "run", "<model>", "--format", "json", "--think=false"], local=True)` | Stays on the machine unless the model is a `*cloud*` model |
| LM Studio | `rastro.model("http:<model>@http://127.0.0.1:1234/v1")` | Local |
| OpenRouter | `rastro.model("http:<vendor/model>@https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY", allow_remote=True)` | Third-party gateway and the upstream vendor |
| Any OpenAI-compatible gateway | `rastro.model("http:<model>@https://gateway.example/v1", api_key_env="...", allow_remote=True)` | The gateway's terms |
| The MCP agent itself | No model needed: the agent calls `suggest_recipe`, `recipe_repair` and validates with Rastro's tools | Whatever the agent already sees |

Subscription CLIs are agents: keep their tools disabled or sandboxed as above, give them
a generous `timeout`, and raise `InferencePolicy.timeout_seconds` (the effective deadline is
the smaller of the two).

## Prompts small models can follow

The trusted system message gives numbered rules and a one-line example of the JSON shape.
The data envelope carries:

- `fields` and `required_fields`, plus `field_hints`: each field's type and the
  `Field(description=...)` you wrote;
- `untrusted_html`, or for a page too large for `max_context_bytes`, `untrusted_page_digest`;
- for repair, `previous_recipe` (the serving recipe, without version or fallbacks), a safe
  `last_failure` category such as `serving_recipe_extracts_no_records`, and
  `untrusted_last_success_samples` (up to two records from the last retained success,
  each value clipped to 120 characters). The rules ask the model to keep selectors that
  still match and to keep working on the previous structure (CSS unions such as
  `h3.old, h2.new` are allowed), because candidates must pass historical replay;
- on a retry, `last_failure` names why the previous candidate was rejected. The previous
  model output is never echoed back.

The **page digest** (`rastro.application.page_digest.digest_page(html, *, limits)`) is a
deterministic, bounded (12,000 characters by default) description of the page: repeated
container candidates (`parent > child` selectors with counts and one or two sample records
as indented `tag.class` trees with clipped text and `href`/`src`/`datetime`/`content`
values), the title, headings, JSON-LD types and URL- or date-like attributes. Selectors
use only safe tag names and stable class tokens; every page string is clipped, stripped of
control, bidi and zero-width characters, quoted, and the digest opens with a label saying
quoted text is untrusted data. Parsing lives behind the `PageOutliner` port. A recipe
proposed from a digest is still executed against the full original page.

## Operator trust for models in job files

A job file is data that anyone (including an agent through MCP or Studio) may write. It may
**declare** a `[model]`, but only the operator decides what that model may do:

| A job-declared model that... | Needs from the operator |
| --- | --- |
| uses a loopback endpoint on 11434, 1234, 8000 or 8080 | nothing |
| uses any other endpoint, `base_url_env`, or sets `allow_remote = true` | `--allow-remote`, `allow_remote=True` or `RASTRO_ALLOW_REMOTE=1` |
| uses `provider = "command"` | always refused: configure a command model as the operator (`rastro.model`, `--model`, `RASTRO_MODEL`) |
| reads `api_key_env` or `base_url_env` names outside `RASTRO_*` | `--allow-env NAME`, `allow_env=["NAME"]` or `RASTRO_ALLOWED_ENV="NAME,..."` (the refusal never echoes the name) |

`allow_remote = true` inside a job means "this job needs a remote model"; it is not consent.
Without the operator's opt-in the run fails closed with a `ConfigurationError` naming the
flag, before any request is made or any secret is read. The CLI flags work with every
command and may follow the subcommand (`rastro job run job.toml --allow-remote`,
`rastro mcp --allow-remote`, `rastro studio --allow-env NAME`). The SDK keywords
`allow_remote`, `allow_egress` and `allow_env` exist on `run_job`, `run_job_async`,
`run_job_file`, `load_job_file` and `repair_job_source(s)`. A model passed in code
(`model=` or `rastro.model(...)`) is trusted and not restricted.

Every recipe a model or an MCP agent proposes is checked against the untrusted-recipe
selector policy (no `:has()`, no sibling combinators, bounded compounds, alternatives and
nesting) before it is evaluated; a refused proposal counts as `invalid_model_candidate`
and uses one call of the budget.

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

Rastro never starts Ollama, downloads models, or registers tools. The native adapter uses
`POST /api/chat`, the request's JSON Schema as the response format, non-streaming
generation, temperature zero, disabled thinking (`think: false`), and explicit
context/output token options. Transport reads are streamed to enforce the response-byte
budget. Model quality and schema-format support vary; validation remains mandatory
regardless of model choice.

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
interface; `rastro.model()` builds one for you. The HTTP strategies need HTTPX, so the
core install never does (P-002). Requirements: **AI-001–005, SEC-004/005/011, P-001, P-007**.

| Provider | Install | Endpoint | Use when |
| --- | --- | --- | --- |
| `OllamaModel` | `rastro-engine[ollama]` | native `/api/chat`, loopback only | A local Ollama service; strictest local-only checks |
| `OpenAICompatibleModel` | `rastro-engine[openai]` | `POST {base_url}/chat/completions` | Any Chat Completions server, local or (opt-in) remote |
| `CommandModel` | core | a local program, prompt on stdin | Any CLI, including subscription agents |
| `CallableModel` | core | an in-process function | SDKs and local runtimes you call from Python |

The two HTTP classes are importable from `rastro` lazily (`from rastro import
OpenAICompatibleModel`); without the extra, the attribute access raises an `ImportError`
naming it. They are intentionally absent from `rastro.__all__`, so `from rastro import *`
stays core-only. `CommandModel` and `CallableModel` live in `rastro.adapters`.

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

OpenAI reasoning models (for example models used with an OpenAI or Codex API key)
reject `max_tokens` and sampling parameters. Pass
`token_limit="max_completion_tokens"` and `temperature=None` to send
`max_completion_tokens` and omit `temperature`; the budget is unchanged.

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

Most users do not need a custom class: `rastro.model(function)` wraps a function, and
`rastro.model("command", ...)` runs a CLI. An Anthropic Messages API provider, for example, sends `request.system` as the
top-level system prompt and `request.content` as the user turn, sets `max_tokens`
from the policy, asks for JSON matching `proposal_schema()`, rejects any non-text or
tool-use content and any truncated stop reason, reads its key from an environment
variable and maps HTTP/SDK errors to the categories above. Anthropic's API is not
Chat Completions-compatible by default, so it needs its own strategy rather than
`OpenAICompatibleModel`.

## Selection and activation

1. An explicit recipe or exact-context stored recipe is executed first. Neither a
   failure nor a missing pinned version triggers model repair.
2. With no recipe, discovery tries JSON-LD mappings using exact schema field names,
   plus any declared Pydantic field alias or `discovery_aliases` name. Nested names
   must be unique within each object; paths use JSON Pointer escaping.
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
window; model tokenization can still truncate its internal context. A page whose
envelope exceeds `max_context_bytes` is replaced by its bounded digest; if even that
does not fit, the outcome is `model_context_limit`. Rastro never slices the page itself. Responses with a reported output-token
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
Field names, required-field names and `field_hints` (each field's type name and your
`Field(description=...)`, clipped to 200 characters) accompany the page; repair adds the
serving recipe as `previous_recipe` and up to two clipped values per field from the last
retained success, labelled untrusted. Source URLs, defaults, examples and validators are
not sent. `ModelRequest.response_schema` lets a request ask for another JSON shape (the
semantic judge does); `output_schema` is that schema or `proposal_schema()`, and providers
should constrain decoding with it. The page may contain sensitive content: opting into a
model authorizes sending it (or its digest) and those samples to the configured service
(remote only with `allow_remote=True`). Rastro never logs prompts, page content, keys or
tokens.
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
