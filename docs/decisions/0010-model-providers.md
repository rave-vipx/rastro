# ADR 0010: model providers as strategies with an OpenAI-compatible adapter

- **Status:** accepted for pre-v0.2; AI-005 pulled forward from v0.2 by owner decision.
- **Requirements:** AI-001–005, SEC-004, SEC-005, SEC-011, P-001, P-002, P-007.

## Context

ADR 0004 introduced `ModelPort` and a loopback-only Ollama adapter. Users also run
vLLM, LM Studio, llama.cpp server, Ollama's `/v1` endpoint, and hosted services
such as OpenAI or OpenRouter. All of these speak the Chat Completions API. AI-005
asks for additional local or OpenAI-compatible endpoints through provider adapters.
Hosted endpoints change the trust model: page excerpts leave the machine and a
credential is required.

## Decision

`ModelPort` is already the strategy interface; no new port, registry or plugin
mechanism is added. A second strategy, `OpenAICompatibleModel`, lives in
`rastro.adapters.openai_compatible` behind a new `openai` extra (HTTPX 0.28.x, the
same pin as `ollama`, so the lockfile adds no packages). Both providers are exposed
from `rastro` through a lazy module `__getattr__`, keeping `import rastro` free of
HTTPX (P-002) and leaving them out of `__all__` so star imports never need an extra.

The adapter sends one non-streaming completion with `temperature: 0`, `n: 1`,
`max_tokens` from the policy and `response_format` of type `json_schema` carrying
`proposal_schema()` with `strict: false`. OpenAI strict mode requires an object
root with closed properties; the recipe field map and per-strategy `anyOf` cannot be
expressed, and changing the domain schema to fit one vendor would also change the
Ollama contract. Constrained decoding is therefore best effort; correctness still
rests on strict JSON parsing, `RecipeProposal` validation and source/schema
validation (ADR 0004). An explicit `response_format="json_object"` mode serves
servers without JSON Schema support and appends the schema to the trusted system
message. We chose an explicit switch over automatic fallback on HTTP 400: a silent
second request would be a hidden transport retry, contrary to the AI-003 budget
semantics, and would blur which mode produced a result.

No tools, functions or tool choice are ever sent; responses that report tool calls,
function calls, refusals, a `finish_reason` other than `stop`, multiple choices or
reported completion tokens above budget are rejected as `invalid_model_response`.

**Remote opt-in.** Literal loopback (IPv4 127/8 and `::1`) needs no opt-in (P-001).
Any other host, including `localhost` whose resolution Rastro does not control,
requires `allow_remote=True` and HTTPS. Loopback model names containing `cloud`
also require the opt-in because Ollama can proxy them to a hosted service.
Credentials, queries, fragments and dot path segments are rejected; redirects and
ambient proxy configuration are disabled, so a server cannot move the request or
its `Authorization` header elsewhere.

**Secrets.** The constructor accepts only `api_key_env`, an environment variable
name (SEC-011). The variable is checked at construction and re-read for each
request; the value is never assigned to the object, so `repr`, logs,
recipes and errors cannot contain it (SEC-004). Values must be printable ASCII
without spaces, preventing header injection.

**Budgets.** As in the Ollama adapter, context bytes are checked before connecting,
the raw body is streamed against `max_response_bytes`, compressed bodies are
refused, and one `asyncio.timeout` covers the call. The adapter additionally holds
a per-instance, per-event-loop semaphore sized by `max_concurrency` inside that
deadline, so a provider shared between Rastro instances still bounds in-flight
requests. Every failure maps to a fixed `ModelError` category raised `from None`.

## Consequences

One adapter serves the common local runtimes and hosted Chat Completions services
without vendor SDKs (P-007). Remote inference is possible but never implicit, and a
remote endpoint is an explicit privacy decision by the caller. Providers that are
not Chat Completions-compatible, such as Anthropic's Messages API, need their own
`ModelPort` strategy; `docs/intelligence.md` documents the contract with a typed
example rather than shipping more adapters now.

Best-effort schema decoding means weaker servers may emit off-schema JSON more often
than with Ollama's native `format`; those candidates fail validation and consume the
repair budget. `localhost` users must write `127.0.0.1` or opt in. Rastro cannot
verify where a local server forwards requests, what a hosted provider retains, or
that a server stops generating after client cancellation.

## Verification

Offline contract tests use HTTPX `MockTransport` for request shape, endpoint and
opt-in rules, key handling, budgets, deadlines, concurrency, cancellation and every
rejection category; an integration test learns, activates and reuses a recipe
through the adapter against a mock server. An opt-in live test
(`tests/live/test_openai_compatible_live.py`) ran once on 2026-10-30 against local
Ollama `http://127.0.0.1:11434/v1` with `llama3.2:3b`: both `json_schema` and
`json_object` modes learned a valid CSS recipe in one call and the `json_schema`
run was reused with zero calls. This is a single synthetic page, not a quality claim.

## Primary references

- [OpenAI Chat Completions API](https://platform.openai.com/docs/api-reference/chat):
  messages, `response_format`, `max_tokens`, `finish_reason`, `refusal`.
- [OpenAI structured outputs](https://platform.openai.com/docs/guides/structured-outputs):
  `json_schema` vs JSON mode and strict-mode schema restrictions.
- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility):
  `/v1/chat/completions` and `response_format` support.
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
  and [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server):
  structured output support varies by server and version.
