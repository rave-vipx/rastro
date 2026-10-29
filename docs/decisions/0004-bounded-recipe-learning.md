# ADR 0004: bounded optional recipe learning

- Status: accepted for M4 / v0.1
- Requirements: AI-001–004, REC-004, INT-002; supporting OBS-001/002 and SEC-004/005

## Context

M3 reuses recipes but requires callers to supply the first mapping. M4 needs optional
local assistance without making models, browsers or cloud services core dependencies.
Untrusted page text and model output must not control operational capabilities.

## Decision

Keep discovery/model orchestration in `RecipeLearner`, behind `RecipeDiscovery` and
`ModelPort`. The SDK opts in with `discover_recipes=True` or `model=` on asynchronous
methods. Existing synchronous and default memory semantics remain intact.
Scrapling performs exact-name JSON-LD and simple microdata discovery before inference.
External API discovery is not implemented, and the roadmap continues to mark that
part of INT-002 incomplete.

The Ollama adapter uses an optional HTTPX 0.28.x extra against the documented chat
API. Direct HTTP lets the client bound streamed response bytes, reject compression,
disable redirects/proxy environment, and cancel requests. Reusing the acquisition
transport would require the HTTP extra's native curl dependency and would conflate
public-source policy with access to a trusted loopback inference service. The Ollama
SDK is unnecessary for this single endpoint. HTTPX is BSD-3-Clause; the lockfile adds
HTTPX, httpcore, h11 and AnyIO while reusing existing certifi/idna dependencies.

The model receives fixed instructions and a JSON envelope with HTML, field names,
required names, and a safe last-failure category. The structured output permits only
mapping fields, strategy and selectors. Rastro parses strict JSON, rejects unknown
fields/duplicate keys and validates the generated recipe against the original source
and Pydantic model. No tools, network callbacks, code, model-supplied data or confidence
score can bypass validation.

Activation is a separate final step after full success. Learned recipes persist
`require_evidence=True`, enforced by the shared extractor before validation on every
run. This prevents a custom default-filling validator from hiding missing required
observations during reuse. Explicit recipes retain their previous default behavior.
SQLite compares validated recipe definitions, rather than textual JSON, so M3 payloads
without the new default remain reusable. No table migration is needed; older engines
reject new payload fields, so downgrade compatibility is not claimed.

An inference policy bounds calls, failed-proposal corrections, the entire model-phase
deadline including semaphore wait, message/schema bytes, response bytes, context and
output tokens, and concurrency per client. Discovery additionally has candidate,
depth and node bounds. Partial datasets stop selection without activation. Stored
recipe failures and pinned-version misses do not trigger repair. Concurrent differing
first recipes preserve immutable version 1 and raise a conflict for the losing write.

## Consequences

Core installs and tests work without a model, HTTPX, browser or service. Contract tests
can validate the actual adapter offline. Deterministic discovery is conservative;
semantic field mismatches can pass structural validation and need caller validators
and review. Client cancellation cannot guarantee that a server stops GPU computation
or that a running Python/native validation thread stops. Local server configuration
and egress isolation remain trusted infrastructure concerns.

The default byte budget may reject documents that a particular model could handle.
It avoids silent Rastro truncation but is not a tokenizer-specific context-fit proof.
This milestone does not claim live-model extraction quality, automatic stored-recipe
repair, cloud inference, or comparative performance; release evidence remains M5.

## Primary references

- [Ollama chat API](https://docs.ollama.com/api/chat): messages, structured format,
  non-streaming generation and completion metadata.
- [Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs):
  JSON Schema format and client validation.
- [Ollama API errors](https://docs.ollama.com/api/errors): status/error contract.
- [Ollama context configuration](https://docs.ollama.com/faq): `num_ctx` option.
- [HTTPX asynchronous streaming](https://www.python-httpx.org/async/): response
  lifetime and cancellation-compatible async API.
- [HTTPX environment variables](https://www.python-httpx.org/environment_variables/):
  `trust_env=False` disables inherited transport configuration.
