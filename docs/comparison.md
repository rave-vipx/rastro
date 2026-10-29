# Reproducible extraction comparison

This first M5 experiment supports **DIFF-001**, **QUAL-003** and specification
sections 17–18. It does **not** establish a Rastro accuracy, speed, or maintenance
advantage. Both executors produce the same records on this corpus, including the
same silent semantic error. DIFF-001 remains open.

## Reproduce

From a checkout or unpacked source distribution, using Python 3.12+:

```bash
uv sync --locked
uv run python -m benchmarks.run --repetitions 50 --warmup 5 --output report.json
```

The comparison is offline, needs no browser/model, and adds no runtime dependency.
The source distribution includes it; the runtime wheel does not.
[Raw report](https://github.com/rave-vipx/rastro/blob/staging/benchmarks/results/offline.json),
[corpus](https://github.com/rave-vipx/rastro/blob/staging/benchmarks/corpus.json),
and [baseline](https://github.com/rave-vipx/rastro/blob/staging/benchmarks/baseline.py)
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

Next evidence must expand held-out sources and mutations and measure a meaningful
advantage (such as provenance integration effort or maintenance), without weakening
the baseline. See the [release evidence register](release-evidence.md) and
[ADR 0005](decisions/0005-reproducible-release-evidence.md).
