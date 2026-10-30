# Live Ollama evidence

This report records end-to-end validation of optional recipe learning against
real local Ollama models. It is evidence for the M5 register, not release approval,
and it does not claim that v0.1 is complete. Requirements: **AI-001–004, REC-001,
REC-004, INT-002, INT-003, QUAL-001, QUAL-003, QUAL-004, OBS-001**.

There are two recorded runs. The [first run](#first-run-qwen34b) (04:39 UTC) used
`qwen3:4b` only. It found and fixed two adapter defects. The
[multi-model run](#multi-model-run-qwen34b-and-llama323b) (17:47 UTC) repeated the
same scenarios for `qwen3:4b` and `llama3.2:3b`, with five learn/reuse trials each.
The [raw report](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/results/live-ollama.json)
uses `format_version: 2`: `runs` holds the latest run and `previous_runs` keeps the
first run unchanged.

## Summary

| Question | Observation |
| --- | --- |
| Does a configured model learn a recipe without a deterministic route? | Yes for the plain synthetic card: `qwen3:4b` 6/6 learn processes across both runs, `llama3.2:3b` 5/5. Each used one call, then validation, activation and storage |
| Does a second process reuse it without inference? | Yes, in every trial: 6/6 and 5/5, `recipe_reused=true`, zero inference and model-port calls |
| Is the plain-card result repeatable? | Fresh-memory repetitions: `qwen3:4b` 10/10 (both runs), `llama3.2:3b` 5/5 |
| Does a harder page succeed? | **No, for either model**: `qwen3:4b` 0/10, `llama3.2:3b` 0/5. Every run ended with `insufficient_evidence` and nothing was stored |
| Do unavailability and invalid output fail visibly? | Yes: all eight scenarios returned explicit non-success outcomes with no records or stored recipe, for both models in both runs |
| Did live testing find defects? | Two adapter defects in the first run, fixed with regression tests. None in the multi-model run |

## Multi-model run: qwen3:4b and llama3.2:3b

Recorded on **2026-10-30 17:47–17:48 UTC** from commit `606324e` (the multi-model
harness), with no tracked changes. Same machine, Ollama 0.40.13 build, and
inputs and budgets as the first run below. CPython 3.12.16 (Homebrew), harness SHA-256
`6635479e…aaaa173`, `uv.lock` SHA-256 `e21515d8…c9621b` (unchanged).

| Model | Digest | Details |
| --- | --- | --- |
| `qwen3:4b` | `359d7dd4bcda…` (same as first run) | GGUF, Qwen3, 4.0B, Q4_K_M, 2,497,293,931 bytes |
| `llama3.2:3b` | `a80c4f17acd5…` | GGUF, Llama, 3.2B, Q4_K_M, 2,019,393,189 bytes |

```bash
uv run --extra ollama python -m benchmarks.live_ollama \
  --model qwen3:4b --model llama3.2:3b --trials 5 --repetitions 5 \
  --keep-previous --output benchmarks/results/live-ollama.json
```

`--model` can be repeated. `--trials` runs N learn→reuse cycles per model, each a
pair of separate processes sharing a fresh SQLite file. `--keep-previous` appends
to an existing report instead of replacing it. Each model is unloaded before its
first trial, so trial 0 includes model load time. The other model could remain
resident (`/api/ps` listed it), so both models shared the 16 GB of memory.

| Per model (5 trials, 5 repetitions per input) | `qwen3:4b` | `llama3.2:3b` |
| --- | ---: | ---: |
| Learn success / activated (stored) / expected record | 5 / 5 / 5 | 5 / 5 / 5 |
| Reuse success with zero inference and model-port calls | 5 | 5 |
| `product_card` repetitions (success) | 5/5 | 5/5 |
| `distractors` repetitions (success) | 0/5 | 0/5 |
| Total inference calls (learn + repetitions) | 20 | 20 |
| Learn inference seconds, min / median / max (max = cold load) | 1.769 / 1.776 / 4.823 | 1.140 / 1.152 / 3.537 |
| Reuse extraction seconds, min / median / max | 0.0043 / 0.0046 / 0.0049 | 0.0044 / 0.0045 / 0.0047 |
| Repetition inference seconds, min / median / max | 1.713 / 2.814 / 4.307 | 1.095 / 1.734 / 2.622 |
| Failure scenarios with explicit non-success, no records, nothing stored | 8 / 8 | 8 / 8 |

Learned recipes: `qwen3:4b` proposed `record_selector="main > article.product-card"`
and `llama3.2:3b` proposed `"main article"`. Both used `h2.title::text` and
`span.amount::text`, identically in all five trials. Every `distractors` run used two
calls and ended with `invalid_model_candidate`.

The failure scenarios matched the first run, with one difference: for
`unsatisfiable_contract` (a required `sku` absent from the page), `llama3.2:3b`
proposed a mapping that Rastro executed and rejected
(`candidate_validation_failed`). `qwen3:4b` produced an invalid candidate
(`invalid_model_candidate`). Both stayed explicit. `think: false` is accepted by
`llama3.2:3b`, a model without thinking support, so the first run's open question
about such models is now answered for this one.

**Why `distractors` fails (exploratory probe, not part of the recorded report):**
a probe that captured raw proposals showed two different failure modes. `qwen3:4b`
again prefixed every selector with `css: `. `llama3.2:3b` returned the **record
values** as field "selectors" (`"name": "Field Notebook"`, `"price": "12.50"`), with
the heading as the record container. The AI-002 boundary is meant to block exactly
that: the model must propose a recipe, never data, and Rastro rejected it. At
temperature 0, both models repeated the same proposal on the correction call. No
prompt tuning or selector rewriting was added.

What this run adds: the integration path (learn → validate → activate → persist
→ reuse across processes, explicit failures) was repeatable across 10 learn/reuse
trials on two model families. It does **not** add evidence of recipe quality beyond
the easy fixture. Two small models failed the harder page every time. Multi-page or
real-site model quality remains unmeasured.

## First run: qwen3:4b

The sections below describe the first run unchanged. They are kept under
`previous_runs[0]` in the raw report.

### Integration versus recipe quality

This run shows that the **integration works**. A configured local model is
called within budgets when deterministic discovery fails. Its proposal is executed
and validated by Rastro, activated only on success, persisted, and reused by a
new process without inference. Failures stay explicit.

It does **not** show that model-generated recipes are good enough in general. AI
generation is the main path when deterministic discovery fails, and the evidence
for its quality here is one easy fixture. A safe rejection on a harder page shows
the boundary works. It does not show useful quality. Generalization across page
structures, models or real sites has not been evaluated.

The failures are attributed as follows:

| Failure | Attribution | Status |
| --- | --- | --- |
| Thinking consumed the output budget and deadline | Rastro adapter defect | Fixed with a regression test |
| CSS proposals omitted `record_selector` | Rastro adapter defect: the decoding schema was weaker than recipe validation | Fixed with a regression test |
| `css: ` selector prefix and wrong record container on `distractors` | Model limitation | Recorded; not worked around |

No identifiable integration bug explains the `distractors` result. Rastro behaved
as specified: it rejected invalid selectors and an invalid record. Three integration
choices may still limit recovery, so measure them with a multi-page quality
benchmark before changing anything:

- Correction feedback carries only a safe category such as `invalid_model_candidate`,
  not which selector failed (ADR 0004).
- Temperature 0 makes the correction call usually repeat the same proposal.
- The instructions do not explain how the record container relates to the field
  selectors.

### Tested code and environment

Recorded on **2026-10-30 04:39 UTC** from commit `41819c9f5be4001a899be38398773bc3cf6289b9`
with no tracked changes. That commit is based on `dev` at `1d68dd3`.

| Item | Value |
| --- | --- |
| Hardware | MacBook Pro, Apple M1 Pro (8 CPU cores), 16 GB memory, Metal backend |
| Operating system | macOS 26.6.2 (build 25G83), arm64 |
| Python / uv | CPython 3.12.16 (Homebrew) / uv 0.13.1 |
| Rastro / HTTPX | `0.1.0.dev0` / 0.28.1 |
| `uv.lock` SHA-256 | `e21515d8c09eb918e9ca21e5cf8363dd1e986c3e302644a533ae7d32c9c9621b` |
| Harness SHA-256 | `c91663259268ad66857a293fc54b9c435fb00b03283e7d98b02c5ef41c78dd1f` |
| Ollama | 0.40.13, official GitHub release `ollama-darwin.tgz` |
| Ollama archive SHA-256 | `bae29252232b9e1358cf5801a7d65a07442af18fb5467cb68c7fc3711f59c511`, matching the release `sha256sum.txt` and GitHub asset digest; binary signed with Team ID `3MU9H2V9Y9` |
| Model | `qwen3:4b`, manifest digest `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` |
| Model details | GGUF, Qwen3 family, 4.0B parameters, Q4_K_M, 2,497,293,931 bytes; weights layer `sha256:3e4cb14174460404e7a233e531675303b2fbf7749c02f91864fe311ab6344e4f` |

Ollama ran as a foreground user process, not the desktop app. It had no launch agent
or login item, and it listened only on `127.0.0.1:11434`. Cloud models were disabled
with `OLLAMA_NO_CLOUD=1`. No other model was installed, and no paid service was used.

### Reproduce

Install Ollama from an official release and pull the model yourself; Rastro and
this harness never pull models or start services.

```bash
OLLAMA_HOST=127.0.0.1:11434 OLLAMA_NO_CLOUD=1 ollama serve   # separate terminal
OLLAMA_HOST=127.0.0.1:11434 ollama pull qwen3:4b
uv sync --locked --extra ollama
uv run --extra ollama python -m benchmarks.live_ollama --model qwen3:4b \
  --trials 1 --output /tmp/live-ollama.json   # --trials 1 matches this first run
RASTRO_LIVE_OLLAMA_MODEL=qwen3:4b uv run --extra ollama pytest tests/live --no-cov
```

The harness refuses a model that `/api/tags` does not list. It unloads the model
before learning, so the first call includes model load time. It runs learning and
reuse in two separate Python processes that share one temporary SQLite file. Live
and controlled scenarios are labelled with `evidence_kind`. The
[raw report](https://github.com/rave-vipx/rastro/blob/ff9566d623edb4d02df5f4f11c1be248e5056d7d/benchmarks/results/live-ollama.json)
contains synthetic values and operational metadata only. It includes no home paths,
keys or credentials. Without the environment variable, the live test is skipped, and
the default test suite stays offline.

### Inputs and budgets

Both inputs are synthetic pages without JSON-LD or microdata, using the schema
`name: str (min length 1)` and `price: Decimal (≥ 0, finite)`.

| Input | Bytes | SHA-256 | Content |
| --- | --- | --- | --- |
| `product_card` | 183 | `1de651c5…4b126a99` | One `article.product-card` with a title and amount |
| `distractors` | 412 | `2189b0e6…c5897276` | The same card plus navigation, an instruction-like paragraph and a footer amount |

Without a model, both inputs ended with `insufficient_evidence` / `no_valid_recipe`,
zero recipe attempts and zero inference calls. Only the model path can succeed.
Offline tests also show that a declarative CSS recipe can extract the expected record
from either page. A failure on the harder page is therefore a proposal-quality
result: the page itself can be extracted.

The policy was `InferencePolicy(max_calls=2, max_repair_attempts=1)` with default
limits: 30 s model phase, 32,768 context bytes, 65,536 response bytes, 2,048 output
tokens, an 8,192-token context window and concurrency 1. The adapter sends
non-streaming chat with temperature 0, `think: false` and a per-strategy JSON Schema.

### Learning and reuse

| Observation | Process 1: learn | Process 2: reuse |
| --- | --- | --- |
| Outcome / reused | `success` / false | `success` / true |
| Attempt origin | `model` | `memory` |
| Inference calls / model-port calls | 1 / 1 | 0 / 0 |
| Inference seconds | 3.0108 (includes model load) | 0 |
| Storage seconds | 0.0024 | 0.0020 |
| Extraction total seconds | 3.0148 | 0.0052 |
| Process wall seconds | 3.2431 | 0.2317 |

The model proposed `record_selector="main > article.product-card"`,
`name="h2.title::text"` and `price="span.amount::text"`. Rastro executed it against
the source, validated `Field Notebook` / `12.50`, and stored version 1 with
`require_evidence=true`. Both processes reported CSS locators for both fields. The
model generated a recipe, never the record values.

### Repetitions

Each run used a fresh memory file and a warm model.

| Input | Runs | Success | Expected record | Calls per run | Inference seconds |
| --- | --- | --- | --- | --- | --- |
| `product_card` | 5 | 5 | 5 | 1 | 1.563–1.595 |
| `distractors` | 5 | 0 | 0 | 2 | 3.490–3.880 |

All `distractors` runs ended with `insufficient_evidence` / `invalid_model_candidate`
and no stored recipe. Exploratory probes showed the cause: `qwen3:4b` prefixed
selectors with `css: ` (for example `css: h2.title::text`), which Rastro rejects as
invalid CSS. When it omitted the prefix, it selected the heading as the record
container, and schema validation rejected the record. At temperature 0, the
correction call usually repeats the proposal. The rejection is the intended
fail-visible behavior. No prompt tuning, selector rewriting or validation relaxation
was added for one model.

### Failure scenarios

| Scenario | Evidence kind | Setup | Outcome / issue | Calls |
| --- | --- | --- | --- | --- |
| `endpoint_unavailable` | live, service absent | Real connection to a closed loopback port | `transient_failure` / `model_unavailable` | 1 |
| `model_not_installed` | live service | Real request for a model that is not installed | `transient_failure` / `model_unavailable` | 1 |
| `output_token_budget` | live model | `max_output_tokens=1`; generation stops on length | `insufficient_evidence` / `invalid_model_response` | 1 |
| `response_byte_budget` | live model | `max_response_bytes=64` | `insufficient_evidence` / `model_response_limit` | 1 |
| `phase_timeout` | live model | `timeout_seconds=0.01` | `transient_failure` / `model_timeout` | 1 |
| `unsatisfiable_contract` | live model | Required `sku` absent from the page | `insufficient_evidence` / `invalid_model_candidate` | 2 |
| `malformed_provider_body` | controlled fake service | Loopback stub returns a non-JSON body | `insufficient_evidence` / `invalid_model_response` | 1 |
| `unparseable_model_content` | controlled fake service | Loopback stub returns a valid envelope with non-recipe content | `insufficient_evidence` / `invalid_model_candidate` | 2 |

Every scenario returned no records and stored no recipe. A controlled fake service
exercises the real adapter and HTTP path, but it is not model behavior. The existing
offline adapter and attack tests (fake transports) remain separate controlled
evidence.

### Defects found and fixed

Both defects were reproduced live and covered by a failing test before the fix
(commit `40b8131`).

1. **Hidden reasoning consumed the budget.** Ollama 0.40.13 enables thinking for
   capable models when a request omits `think`. Before the fix, the synthetic
   example ended with `transient_failure` / `model_timeout` after one call and 32.6
   s. A direct request with the same payload used all 2,048 output tokens on 7,992
   characters of reasoning, returned `done_reason=length` with empty content and
   took 52.2 s. With `think: false`, the request took 1.2 s and 40 tokens. **Red:**
   the adapter contract test asserting `think is False` failed. **Green:** it passes,
   and the live example succeeds. Ollama's chat handler rejects only `think: true`
   for models without thinking support. That was checked in source but not live.
2. **The decoding schema allowed recipes that Rastro always rejects.** After fix 1,
   both proposals were CSS mappings without `record_selector` and ended with
   `invalid_model_candidate`. Adding a sentence to the instructions made the model
   drop the `::text` suffixes. A schema that requires `record_selector` for CSS
   and forbids it for JSON-LD produced a valid recipe. **Red:** the schema-contract
   tests failed. **Green:** `format` now uses that per-strategy schema, and context
   accounting measures it. `RecipeProposal` and `Recipe` validation remain
   authoritative and unchanged.

### Limitations

These limitations were written for the first run; the multi-model run above
narrows the first two.

- One model on one machine with two small synthetic inputs and five repetitions each.
  This does not measure general extraction quality or compare models. Timings are
  observations, not a benchmark. The OS file cache was warm during the post-unload
  load, and the very first cold load was not measured separately.
- The plain card is easy. The harder page shows that `qwen3:4b` can fail to propose
  a valid recipe, and correction feedback is only a safe failure category.
- Schema validation cannot prove semantic correctness. The expected-record match
  above is a harness oracle, not a runtime guarantee.
- A missing model maps to `transient_failure`, like an unavailable service. A
  permanent configuration outcome may be clearer; this change does not alter
  outcome mapping.
- After a client timeout, Ollama may continue generating. `ollama serve` creates a
  local identity key on first start; it was not used or recorded. Egress was not
  firewalled during this run. Pulling the model contacted Ollama's registry, and
  inference ran locally.
- This run used Homebrew CPython 3.12.16, because a shared uv-managed interpreter on
  this machine had been partially removed by temporary-directory cleanup.
- Models that cannot disable thinking, and models without thinking support, were
  not tested live.
