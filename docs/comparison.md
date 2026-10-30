# Reproducible extraction comparison

These experiments support **DIFF-001**, **QUAL-003**, **REC-001**, **OBS-001** and
specification sections 05 and 17–18. They compare Rastro with an independent
Scrapling + Pydantic baseline written for this repository.

**DIFF-001 status: partially demonstrated.** On the [held-out evaluation](#held-out-evaluation)
below, Rastro needed the same source-specific configuration as the adapted
baseline required by section 17 (10 versus 10 entries). The adapted baseline needs
33 more lines of one-time adapter code; versus hand-written per-source mappings
alone, Rastro needed 10 versus 25 entries. It exposed 16 of 16 operational-visibility
items; the reference baseline exposed 1. With a scripted fake model, Rastro with
SQLite memory made 0.017 calls per successful record and Rastro without memory made
0.167. The baseline makes no model calls. Rastro showed **no** advantage in silent
error detection or extraction accuracy. Every executor had 3 silent errors in 24
mutations. Rastro was slower on every page. The
[first regression result](#first-recorded-result) shows no advantage either. DIFF-001
remains open for accuracy, silent error detection, maintenance and recovery.

## Reproduce

From a checkout or unpacked source distribution, using Python 3.12+:

```bash
uv sync --locked
uv run python -m benchmarks.run --repetitions 50 --warmup 5 --output report.json
```

The comparison is offline, needs no browser/model, and adds no runtime dependency.
The source distribution includes it; the runtime wheel does not.
[Raw report](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/results/offline.json),
[corpus](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/corpus.json),
and [baseline](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/baseline.py)
are versioned with the implementation. Use the files in the same checkout when
reviewing an unmerged branch. The raw report records the parent Git revision,
dirty status, and SHA-256 hashes of the exact benchmark/runtime sources and lock.
Hashes identify measured code even when the result was generated before committing.

## Workload and fairness

Fourteen synthetic cases cover static HTML, JSON-LD, field/record reordering,
renamed selectors with a known fallback, missing/invalid/partially valid data,
ambiguous fields, broken selectors, regional decimal notation, a supplied rendered
snapshot, an unrendered JavaScript shell, a semantic trap, and a 250-record page.
All fixtures are authored for this project under Apache 2.0; no captured page is
part of the offline corpus. The large-page template expands before timing.

Both executors receive identical mappings, fallback order, and Pydantic models.
The baseline handles JSON-LD graphs and pointers, rejects ambiguous/missing fields,
validates each record and returns explicit outcomes. It is a reference for this
bounded corpus, not a general HTTP or hostile-input service. Rastro uses its public
SDK, including recipe validation, result construction and provenance. Only the
scorer receives expected records; no executor can inspect the oracle. Decimal
comma normalization is caller code shared by both, not a measured engine feature.

Both integrations are reconstructed per call; no memory/model is used. A monotonic
clock measures parsing through validation and returned result construction. Input
loading, corpus expansion and scoring are excluded. Rastro completion logging is
disabled equally for all measured calls. Each case receives five warmups and fifty
samples per executor, with executor order alternated each iteration. p50 is the
sample median; p95 is the nearest-rank 95th percentile. Raw samples are retained.

## Scoring contract

- Field truth is a multiset of `(SKU, field, normalized value)`; SKU is also one
  of the four scored fields. A wrong value adds one false positive and one false
  negative. Duplicate records retain their multiplicity. Record order is irrelevant.
- Precision is `TP / (TP + FP)` and recall is `TP / (TP + FN)`. A zero denominator
  produces JSON `null`, never a perfect score. Micro metrics pool counts; macro
  metrics average non-null per-case ratios. Both are reported because the large
  page would otherwise dominate.
- A correct complete record matches all fields. Complete-record recall divides
  matching records by expected records. Exact record sets include correct empty
  rejection cases; they must be read together with warning detection.
- False success means the executor announced `success` despite a mismatched record
  set or a fixture requiring a warning. Its denominator is announced successes.
  Warning detection is any explicit non-success outcome on a warning-required
  case, divided by warning-required cases. Partial success counts as a warning.
- Missing, invalid and ambiguous fixtures expect no accepted records and require
  a warning. Broken selectors and an unrendered shell retain the intended record
  in the oracle to count the resulting recall loss. The semantic trap contains a
  stale but valid price, which a schema cannot distinguish from the intended price.

## First recorded result

Recorded on 2026-10-25 UTC: Python 3.12.15, Darwin 25.6.0, arm64;
Rastro 0.1.0.dev0, Scrapling 0.5, Pydantic 2.14.1 and lxml 6.1.3.
CPU model, memory pressure and other host workloads were not controlled. Latencies
are descriptive measurements from one machine, not portable performance guarantees.

| Quality metric | Scrapling + Pydantic | Rastro |
| --- | ---: | ---: |
| Correct fields / false positives / false negatives | 1039 / 1 / 9 | 1039 / 1 / 9 |
| Micro precision / recall | 99.90% / 99.14% | 99.90% / 99.14% |
| Macro precision / recall | 97.22% / 79.55% | 97.22% / 79.55% |
| Correct complete records / expected | 259 / 262 | 259 / 262 |
| Exact record sets, including correct empty rejections | 11 / 14 | 11 / 14 |
| False successes / announced successes | 1 / 8 | 1 / 8 |
| Warnings detected / required | 6 / 7 | 6 / 7 |
| Model calls | 0 | 0 |

Neither engine detects the semantic trap; both miss the broken-selector record
and the JavaScript-only record. Rastro's additional provenance and orchestration
have measurable cost. No human effort savings or automatic repair was measured.

| Case | Baseline p50 / p95 (ms) | Rastro p50 / p95 (ms) |
| --- | ---: | ---: |
| static | 0.085 / 0.163 | 0.296 / 0.468 |
| metadata | 0.028 / 0.085 | 0.218 / 0.457 |
| reordered | 0.084 / 0.101 | 0.287 / 0.495 |
| renamed | 0.064 / 0.118 | 0.414 / 0.802 |
| missing | 0.058 / 0.083 | 0.370 / 0.630 |
| invalid | 0.068 / 0.136 | 0.402 / 0.699 |
| partial | 0.084 / 0.100 | 0.288 / 0.419 |
| ambiguous | 0.060 / 0.066 | 0.380 / 0.544 |
| broken | 0.032 / 0.046 | 0.335 / 0.627 |
| regional | 0.052 / 0.083 | 0.248 / 0.620 |
| rendered | 0.051 / 0.062 | 0.240 / 0.391 |
| js_shell | 0.028 / 0.047 | 0.337 / 0.689 |
| semantic_trap | 0.055 / 0.076 | 0.250 / 0.515 |
| large | 8.306 / 9.270 | 12.322 / 18.726 |

## Limits and next evidence

A synthetic rendered snapshot does not test JavaScript execution. The large page
does not test pagination. Rate-limit handling, acquisition, checkpoint recovery,
repair, live inference, browser utilization, native peak memory, throughput under
load, and developer effort are **unmeasured**, not zero. Offline acquisition timings
cannot represent website latency. This fixed corpus is a small regression set,
not a representative independent test set or an adversarial-input evaluation.

The [held-out evaluation](#held-out-evaluation) below adds held-out sources and
mutations. See the [release evidence register](release-evidence.md) and
[ADR 0005](decisions/0005-reproducible-release-evidence.md).

### Re-run after the baseline refactor

The held-out work made the baseline executor generic over the Pydantic schema. It
now reports malformed JSON-LD as `partial_success`/`invalid_data` instead of raising.
`benchmarks/results/offline.json` was regenerated on 2026-10-30 UTC with the same
command, Python 3.12.16, and otherwise identical packages. All quality counts in the
table above were reproduced exactly for both executors. p50 latencies stayed within
the same range: baseline 0.028–0.088 ms on small cases and 7.881 ms on `large`;
Rastro 0.242–0.411 ms and 11.870 ms.

## Held-out evaluation

This second experiment targets the DIFF-001 dimensions where Rastro's integration
could plausibly matter. The 14-case regression corpus does not cover them. It was
added after that corpus and **no engine code changed for it**. The engine is under
`src/rastro`, which this change does not touch.

### Reproduce

```bash
uv sync --locked
uv run python -m benchmarks.heldout --repetitions 30 --warmup 3 --model-runs 10 \
  --output /tmp/rastro-heldout.json
```

Write to a temporary path. The committed `benchmarks/results/heldout.json` was
produced by the same command with that path as `--output`; overwrite it only when
deliberately regenerating the recorded result.

The run is offline and deterministic except for latency, and needs no model or browser.
Inputs: [corpus](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/heldout.json),
[runner and scorer](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/heldout.py),
[baseline](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/baseline.py),
[raw report](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/results/heldout.json).

### Dataset

Five synthetic sources (Apache 2.0, authored for this project) share no HTML with
the regression corpus. A unit test enforces that.

| Source | Kind | Records | Mutations |
| --- | --- | ---: | ---: |
| `outdoor_jsonld` | JSON-LD `@graph` with Organization/Breadcrumb noise | 1 | 6 |
| `pantry_microdata` | Microdata grid with nested `Offer` items | 3 | 5 |
| `hardware_cards` | Plain CSS cards with BEM classes, no structured data | 3 | 6 |
| `deli_table` | Plain CSS price table | 2 | 3 |
| `books_jsonld_blocks` | One JSON-LD `Product` block per book | 3 | 4 |

The 24 mutations are declared string replacements of the original page. Each one
must apply. Kinds: renamed classes (4), reordered DOM or structure (7), removed
fields (3), duplicated candidates (7), renamed source keys (2) and malformed markup
(1). Each mutation has an oracle record set. If the data is still on the page, the
oracle keeps the original records, so an explicit failure counts as a recall loss.
Otherwise the oracle requires a warning. The schema is `sku`, `name`,
`price: Decimal ≥ 0` and `priceCurrency ∈ {USD, EUR, UYU}`.

**Schema naming choice:** Rastro discovery matches schema field names exactly.
That is why this schema uses the schema.org name `priceCurrency`. With the regression
corpus name `currency`, discovery failed on **all three** structured sources
(`insufficient_evidence`). The report records that run separately.

### Executors

All executors receive the same pages and Pydantic model; only the scorer sees oracles.

| Executor | Source-specific input | Behavior on a mutated page |
| --- | --- | --- |
| `baseline` | Hand-written Scrapling mappings for each source (JSON pointers or CSS) | Re-runs those mappings |
| `baseline_adapted` | Structured sources: none. A one-time `structured_mappings` adapter maps schema.org JSON-LD (top level, `offers` object, or a one-element `offers` list) and microdata. CSS sources: the same mappings as `baseline` | Re-runs the adapter on every page |
| `rastro_memory` | Structured sources: none (`discover_recipes=True`). CSS sources: one explicit recipe identical to the baseline mapping | Reuses the recipe stored in SQLite on the original page. In v0.1, a failing stored recipe does not trigger rediscovery |
| `rastro_rediscover` | As above, without a repository | Discovers again on every page |

The baseline rejects ambiguous and missing fields, validates every record, and
returns `success`, `partial_success`, `insufficient_evidence`, `invalid_data` or
`missing_data`. That is the same competence as in the first experiment, now also
with malformed-block handling and an adapter. Both executors had to work correctly
on every original page before the mutation run, and the runner checks this.

### Metrics

- **Configuration:** the selectors, pointers, record selectors and item types a
  developer writes for each source. Entries count them; tokens split identifiers and
  punctuation (`[A-Za-z0-9_]+` or one symbol). A source counts as zero only when
  automatic mapping produced exactly the oracle records on the original page.
  One-time code is counted separately, in logical lines (no blanks, comments or
  docstrings).
- **Mutation outcome:** `correct_success` (success with the exact oracle and no
  required warning), `explicit_non_success` (any other status, including
  `partial_success`) or `silent_error` (success with a wrong record set or a
  missing required warning).
- **Model calls:** a deterministic `ScriptedModel` replays the developer's own CSS
  selectors. A second script returns one invalid proposal first. Each run creates
  a new engine instance (a simulated restart) over the same SQLite file. These numbers
  measure call accounting, **not model quality**. Live models are covered in the
  [live Ollama evidence](live-ollama-evidence.md).
- **Visibility:** a checklist observed from real results and captured log events.
  It is not a score.
- **Latency:** as in the first experiment: alternating order, 3 warmups and 30 samples
  per executor and source, median and nearest-rank p95.

### Results

Recorded on 2026-10-30 UTC from commit `20ad721`, which includes the PR #17 and #18 engine
changes: Python 3.12.16, Darwin 25.6.0,
arm64; Rastro 0.1.0.dev0, Scrapling 0.5, Pydantic 2.14.1, lxml
6.1.3. `git_dirty` is true only because of untracked, unrelated `node_modules/` and
`package.json` files in the worktree. The source hashes in the report identify the
measured code.

**1. Integration effort (source-specific configuration, all five sources)**

| Executor | Entries | Tokens | Characters | One-time code (logical lines) |
| --- | ---: | ---: | ---: | --- |
| `baseline_adapted` (spec baseline) | 10 | 70 | 171 | 94 + 33 (structured adapter) |
| Rastro | 10 | 70 | 171 | 19 (integration) |
| `baseline` (no adaptation) | 25 | 160 | 438 | 94 (reference executor) |

Against the adapted baseline, source-specific configuration is a **tie**. Both
needed zero entries on the three structured sources and the same 5 entries on each
CSS source. The report evidences the zero counts: `baseline_adapted_mapping_origin`
is `structured_adapter` on the three structured sources and `hand_written` on
the two CSS sources, and `baseline_adapted_mappings` lists the mappings produced.
On three mutated structured pages (`price_removed`, `sku_renamed_mpn`,
`sku_renamed_gtin`) the adapter could not map every field and fell back to the
hand-written mappings, which the mutation rows also record. Without adaptation,
per-source baseline mappings needed 25 entries.

The remaining difference is one-time code the developer writes and maintains: 127
logical lines for this baseline versus 19 for the Rastro integration. Both sides
are counted the same way: the baseline counts its executor helpers
(`_objects`, `_pointer`, `_types`, `_json_items`, `_json_rows`, `run`) and adapter
(`structured_mappings`); Rastro counts `rastro_extract`, `recipe_for` and
`explicit_mappings`. Harness wrappers that convert either output to the scorer's
format are excluded for both, and the report lists the counted functions. The
count compares against this reference implementation. A packaged baseline library
could reduce it, and developer time was not measured.

**2. Re-running a previously working integration on 24 mutated pages**

| Executor | Correct success | Explicit non-success | Silent error | Correct records / expected | Extra records |
| --- | ---: | ---: | ---: | ---: | ---: |
| `baseline` | 8 | 13 | 3 | 37 / 51 | 3 |
| `baseline_adapted` | 9 | 12 | 3 | 38 / 51 | 3 |
| `rastro_memory` | 8 | 13 | 3 | 37 / 51 | 3 |
| `rastro_rediscover` | 7 | 14 | 3 | 36 / 51 | 3 |

**There was no silent-error advantage.** All four executors failed silently on the
same three mutations: a related `Product` added to the JSON-LD graph, a sponsored
duplicate card, and a price class moved to a unit-price element. All four reported
these explicitly: every removed field (`price_removed`, `currency_removed_one`,
`price_removed_one`), the malformed block (`one_block_malformed`), and the
duplicates the oracle requires a warning for (`was_price_added`, `old_price_added`,
`subtotal_row_added`). All four also failed explicitly on `review_author_added`,
where a nested author `name` made `name` ambiguous although the data was still
extractable. Of the seven duplicated-candidate mutations, the other three were
not explicit reports: two were the silent errors above, and `brand_name_added`
succeeded correctly for three executors. The differences were:

- `offers_as_list`: only `baseline_adapted` recovered. Its adapter handles one-element
  offer arrays. Rastro memory and rediscovery failed explicitly, because discovery
  does not traverse arrays.
- `brand_name_added`: `rastro_rediscover` failed explicitly, because a nested
  `brand.name` makes `name` non-unique for exact-name discovery. The stored recipe
  (`rastro_memory`) and both baselines kept working.

Rastro's memory matched the conventional baseline mutation for mutation. Re-discovery
was the least robust of the four configurations on this corpus.

**3. Model calls per successful record (scripted fake model, 10 runs per source)**

| Script / memory | Inference calls | Successful records | Calls per successful record |
| --- | ---: | ---: | ---: |
| Valid first proposal, SQLite memory | 2 | 120 | 0.017 |
| Valid first proposal, no memory | 20 | 120 | 0.167 |
| One rejected proposal first, SQLite memory | 4 | 120 | 0.033 |
| One rejected proposal first, no memory | 40 | 120 | 0.333 |

Structured sources used zero calls because discovery succeeded first. On CSS
sources, memory turned one learning call into nine reuse runs with no calls. The
baseline makes no model calls, but it needs the hand-written mappings counted in
dimension 1. This compares Rastro with and without memory, using a fake model; it
says nothing about how often a real model proposes a valid recipe.

**4. Operational visibility (observed checklist)**

| Item | Baseline | Rastro |
| --- | :---: | :---: |
| Explicit outcome status | yes | yes |
| Issue category and record index | no | yes |
| Issue names the field for an ambiguous value | no | yes |
| Issue names the field for a missing required value | no | yes |
| Per-field locator, extraction method, source URL and observation time | no | yes |
| Recipe version, reuse flag, attempt origins, inspectable learned recipe | no | yes |
| Job ID, timestamps, schema fingerprint | no | yes |
| Candidate/accepted/rejected counts, stage timings, inference calls/seconds | no | yes |
| Structured completion log event | no | yes |

The full report has 16 items, with groups split out: the baseline has 1 and Rastro
has 16. The baseline returns only status and records. Adding the rest is unmeasured
engineering work, not impossible.

An earlier recording of this evaluation (commit `eb76f4f`, before PR #18) found a
Rastro defect. `missing_evidence` issues carried `record_index` but `field=None`,
even though the missing required field was known. Failing case: `hardware_cards`
mutation `price_removed_one`, explicit recipe. Rastro returned
`Issue(code="missing_evidence", record_index=2, field=None)`, and that recording
showed Rastro at 15 of 16 items. PR #18 fixed the engine. The same case now returns
`field="price"`, and a test asserts it. The regenerated report is otherwise
unchanged: every effort, mutation-outcome and model-call count matches the earlier
recording exactly. Learned recipes now also serialize the new `record_path: null`
field.

**5. Latency on original pages (ms, p50 / p95)**

| Source | Baseline | Baseline adapted | Rastro explicit | Rastro rediscover | Rastro memory reuse |
| --- | ---: | ---: | ---: | ---: | ---: |
| `outdoor_jsonld` | 0.029 / 0.032 | 0.070 / 0.089 | 0.252 / 0.273 | 0.582 / 0.691 | 1.116 / 1.328 |
| `pantry_microdata` | 0.132 / 0.134 | 0.218 / 0.233 | 0.369 / 0.410 | 0.888 / 0.949 | 1.342 / 1.563 |
| `hardware_cards` | 0.146 / 0.157 | 0.153 / 0.163 | 0.381 / 0.422 | 0.574 / 0.635 | 1.284 / 1.479 |
| `deli_table` | 0.100 / 0.104 | 0.105 / 0.115 | 0.349 / 0.453 | 0.488 / 0.529 | 1.168 / 1.321 |
| `books_jsonld_blocks` | 0.045 / 0.047 | 0.099 / 0.118 | 0.301 / 0.322 | 0.646 / 0.739 | 1.173 / 1.304 |

Rastro is slower in every configuration: 2.6–8.7× the baseline p50 with an explicit
recipe. Memory reuse adds a SQLite read on each call, and rediscovery adds an event
loop hop plus discovery. The absolute cost is about 1.1–1.3 ms per page offline with memory reuse. That
is small next to network acquisition, which was not measured here.

### Limits

- The same author wrote the corpus, the oracle and both integrations, knowing both
  engines. "Held-out" means that no engine code was tuned on it. It does not mean
  independently authored. Five sources and 24 mutations are a small sample.
- Synthetic pages only. There was no network, JavaScript rendering, pagination,
  rate limiting, real model, repair, or developer-time study.
- Configuration counts favor schemas named after schema.org vocabulary. A different
  naming convention makes Rastro's discovery count equal to `baseline` (25 entries)
  unless aliases are added. Aliases are unsupported in v0.1.
- Silent errors that a schema cannot distinguish (duplicates, reassigned classes)
  need historical or semantic checks (QUAL-005, v0.2). Neither engine has them.
- Recovery after a mutation (repair) is out of v0.1 scope and was not measured. A
  stored recipe that fails is reported, never replaced automatically.
