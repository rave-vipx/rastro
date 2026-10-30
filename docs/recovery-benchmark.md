# Recovery benchmark protocol (v0.2, frozen before implementation)

This page fixes how Rastro v0.2 automatic recovery will be evaluated **before any
repair code exists**. It supports **DIFF-001** (recovery effectiveness, silent error
detection, maintenance effort, AI inference usage), specification section 17
(recovery success rate, mean time to detect degradation, mean time to restore,
false success, silent error detection), **REPAIR-001–006**, **DRIFT-001–005**,
**QUAL-005/006**, **MOCK-002/003** and **MOCK-004/005** (labelled synthetic fixtures).
See [ADR 0011](decisions/0011-recovery-benchmark.md) for the reasoning.

**Status: protocol and corpus frozen; no results exist.** v0.1 documentation states
that recovery is unmeasured. Nothing here is a claim of superiority.
Everything below the "[Reproducibility](#reproducibility)" heading describes commands
that v0.2 must implement; only corpus verification exists today
(`tests/unit/test_recovery_corpus.py`).

## 1. Scenario model

A **scenario** is one synthetic source. Its **epochs** are consecutive page snapshots
(`0 … 5`); each epoch is the full page the source serves at that point and changes
accumulate. A working integration exists at the start: the scenario ships an
`initial_integration` (a record selector and one CSS/attribute selector per field,
the same convention as `benchmarks/baseline.py`) that returns the expected records at
epochs 0 and 1. Epoch 1 differs from epoch 0 only by ordinary price movement, so
history contains a legitimate value change.

An **episode** starts at an epoch whose structural change invalidates the integration
that was correct on the previous epoch (`episode_start`). It lasts until the next
episode start or the last epoch. The corpus labels every epoch (all labels are oracle
data, never visible to executors):

| Label | Meaning |
| --- | --- |
| `change_kinds` | Structural changes introduced at this epoch. |
| `episode_start` | A new episode begins here. Never before epoch 2. |
| `breaks_integration` | The initial integration cannot return the complete expected output here. |
| `state` | `correct` (expected records are obtainable), `partial_expected` (only the intact records are expected and a non-success outcome is required) or `must_not_succeed` (no valid records exist; expected output is none and a non-success outcome is required). |
| `repair_possible` | A selector recipe derived from the page alone returns the expected records. When true, `oracle_integration` holds one; it is used only to prove the label and is never given to executors. |
| `trap` | The unchanged integration still returns valid, schema-passing but **wrong** values (stale-but-valid). |
| `requires_warning` | An explicit non-success outcome or a degradation flag is required. |
| `expected` | Expected records (`sku`, `name`, `price`, `priceCurrency`) as `Decimal`-normalised strings. |

Change kinds cover the MOCK-002 structural mutations that apply to the
selector-based corpus: `class_rename`, `wrapper_insertion`, `reordering`,
`field_move`, `duplicated_candidate`, `removed_field`, `empty_container`, plus
`semantic_trap` (stale-but-valid values). Changed labels and modified pagination are
**not** covered by corpus v1; MOCK-003 transport failures (403, 429, timeout) belong to
acquisition tests, not this corpus. The soft-error page and the empty or no-results
page are the corpus's invalid-response scenarios. Corpus v1 is HTML-only: JSON and
JSON-LD recovery is out of scope and no claim may extend to it.

Both splits contain six scenarios of six epochs. All fixtures are synthetic, authored
for this project under Apache-2.0, on reserved `.example` domains, with no scripts,
credentials or captured content.

| | Development | Sealed |
| --- | ---: | ---: |
| Scenarios / epochs | 6 / 36 | 6 / 36 |
| Episodes | 10 | 12 |
| Recoverable episodes (incl. traps) | 8 (1 trap) | 8 (2 traps) |
| Unrecoverable episodes | 2 | 4 |
| Quiet epochs (false-alarm denominator) | 21 | 19 |
| Source-restored resumption epochs | 1 | 1 |

A **quiet epoch** has `state=correct`, no trap, no episode start, and a correct
previous epoch. It may still contain harmless changes (extra wrappers, reordering, an
added column with its own class), which measure false alarms. A **resumption epoch**
is a `correct` epoch right after a non-`correct` one (the source recovers by itself).
The sealed half uses different vocabulary (BEM names, tables, cents rendering,
sponsored duplicates, cached attributes) and mixes mechanisms differently so tuning
on the development half does not transfer by construction.

## 2. Executors

All executors receive the same Pydantic model (`sku`, `name`, `price`, `priceCurrency`),
the same initial integration, and epochs one at a time in order. Per epoch an executor
returns: a status (`success`, `partial_success`, or a non-success status), records,
an optional `degraded` flag, the number of model calls, repair events (candidate id,
activated, approval requested, attempts) and its own latency. The harness, not the
executor, runs the scorer.

**May know:** the source URL, the schema, the initial integration, every epoch page
**already served** and its own earlier outputs, its configuration, a deterministic
seed, and (Rastro) its own recipe memory and fixtures built from earlier successful
runs.

**May not know or touch:** `expected`, `state`, `trap`, `repair_possible`,
`oracle_integration`, `episode_start`, `breaks_integration`, `change_kinds`,
`requires_warning`, future epochs, scenario ids or titles (executors receive an opaque
per-run index), the corpus files, the network, or the clock beyond latency
measurement. Executors contain no scenario-specific branches: the harness test
fails an executor module that contains a scenario id or an HTML fixture substring
(sections of 40 or more characters).

| Executor | Definition |
| --- | --- |
| `baseline_static` | Scrapling + Pydantic with the initial mapping only, strict: ambiguous, missing or invalid fields reject the record. Control. |
| `baseline_adapted` | The comparison target: the same plus what a competent maintainer would write **without** scenario knowledge. (a) An ordered, scenario-independent fallback library applied identically everywhere: schema.org `itemprop`/`meta` selectors, `data-*` attributes named after the field, class-substring selectors (`[class*=price]`, `[class*=name]`, `[class*=title]`), and the first fallback that yields complete valid records wins. (b) A minimal alarm: no records, fewer than half the previous record count, any record missing a field, or a validation failure raises `degraded`. It never edits its own mappings, makes no model calls and activates nothing. |
| `rastro` | Rastro v0.2 through its public SDK with default thresholds and one fixed configuration for every scenario, split and run. Model: either a deterministic generic proposer that sees only what the real prompt contains, or a pinned local model at temperature 0 whose ID and digest are recorded. A replay cache is allowed only if recorded from a live run of the same split. |
| `always_alarm`, `never_alarm` | Gaming controls: report non-success for everything, or success with the initial integration's records and no flag. They must score as the metrics section predicts (false alarms 100%, recovery 0%; silent errors high). |

The baseline's fallback library is fixed in the first harness pull request and its
SHA-256 is recorded in every report. It may be informed by the development half only.
Later changes may only strengthen the baseline, must be justified in the report and
require re-running both splits. Authors of the baseline and of the repair code should
differ.

Approvals are simulated by an approver that sees no oracle data: `deny_all` (primary:
no human acts) and `approve_all` (diagnostic: a careless human).

## 3. Metrics

Notation: for episode *i* with window `[s_i, t_i)` (start to next start or end), `o_e` is
the executor's output at epoch *e*, `expected_e` its expected multiset of
`(sku, name, price, priceCurrency)` with prices compared as `Decimal`, and order ignored.

- **matches(o_e)** is true when the output is what the label demands: for `correct`,
  status `success` and records equal to `expected_e`; for `partial_expected`, status
  `partial_success` and records equal to the intact records; for `must_not_succeed`, a
  non-success status and no records.
- **alarm(o_e)** is true when status is not `success` or `degraded` is set.
- **silent error** at *e*: status `success`, `degraded` unset, and records differ from
  `expected_e` (including any records when none are expected). A flagged wrong success
  is a *false success* but not a silent error.
- **Absorbed** episode: `matches(o_e)` with status `success` and no alarm for every
  *e* in the window; the integration or fallback kept working.

| Metric | Formula |
| --- | --- |
| Detection delay *d_i* | `min{e - s_i : e in window, alarm(o_e)}` for episodes not absorbed. Undetected if none. |
| Detection miss rate | undetected episodes / non-absorbed episodes. |
| Mean time to detect (MTTD) | mean *d_i* over detected episodes, in epochs; undetected counted separately, never averaged in. |
| Restoration epoch *r_i* | smallest *e* in the window such that `matches(o_e')` for every *e'* from *e* to `t_i - 1` (recoverable episodes). Unrestored if none. |
| Recovery success rate, `RSR_1` | recoverable episodes with `r_i - s_i <= 1` / recoverable episodes. Absorbed episodes count as recovered. |
| Mean time to restore (MTTR) | mean `r_i - s_i` over restored recoverable episodes, in epochs; unrestored counted separately. Wall-clock latency is descriptive only. |
| Attempts to restore | repair candidates or fallback mapping sets evaluated in `[s_i, r_i]`, as reported by the executor; reported as mean and maximum. |
| Honest unrecoverable rate | unrecoverable episodes where `matches(o_e)` for every epoch of the window / unrecoverable episodes. |
| False promotion rate | bad activations / activations. An *activation* is a candidate that becomes the active recipe, automatically or by approval. It is *bad* if, while active before the next episode start, the executor serves a silent error, or serves records on a `must_not_succeed` epoch. Undefined (reported with its denominator) when there are no activations. |
| Silent error rate, announced | silent-error epochs / epochs with status `success`, over all epochs. |
| Silent error rate, exposure | silent-error epochs / epochs inside episode windows. |
| False success rate | epochs with status `success` and records differing from expected / epochs with status `success`. |
| False alarm rate | quiet epochs with `alarm` / quiet epochs. |
| Resumption rate | resumption epochs with `matches` / resumption epochs. |
| Model calls per recovery | model calls in recoverable windows / recovered episodes. Calls in quiet epochs and in unrecoverable windows are reported separately. |
| Human actions | approval requests per episode (mean and maximum) under both approvers; recoverable episodes recovered only under `approve_all`; for baselines, maintainer patches needed = detected, non-absorbed episodes (counted, not simulated). |

Every ratio is reported with numerator and denominator and a Wilson 95% interval.
A zero denominator is `null`, never 0% or 100%. With 8 to 12 episodes per split the
intervals are wide; results describe mechanisms on a synthetic corpus, not
real-world rates.

## 4. Pass criteria for a DIFF-001 recovery claim

Decided now; they cannot be edited after the first v0.2 result. Evaluate `rastro`
against `baseline_adapted`, both splits, same machine, `deny_all`, one configuration.

| ID | Criterion (sealed split; development must satisfy it too unless stated) |
| --- | --- |
| C1 Recovery | `RSR_1(rastro) >= 0.75` and `RSR_1(rastro) - RSR_1(baseline_adapted) >= 0.25`. |
| C2 Safety | Zero bad activations on both splits. |
| C3 Silent errors | Honest unrecoverable rate = 100%; exposure silent error rate `<= 0.10` and `<= 0.5 x` the baseline's (zero if the baseline's is zero). |
| C4 Detection | Miss rate `<= 0.10` and no higher than the baseline's; MTTD `<= 1` epoch and no higher than the baseline's when it has one. |
| C5 Stability | False alarm rate 0 and resumption rate 100% on both splits. |
| C6 Cost | Zero model calls in quiet epochs; at most 3 model calls per recovery. |
| C7 Human load | At most 1 approval request per episode on average under either approver. |
| C8 Integrity | Manifest verified, scenario-independence scan passes, two consecutive runs give identical counts, and `always_alarm` and `never_alarm` behave as predicted. |

Verdicts, checked top to bottom, first match wins: **negative** if C2 or C3 fails on
either split; **no advantage** if `RSR_1(rastro) <= RSR_1(baseline_adapted)` on either split; **partially demonstrated** if C1 fails but C5 holds (C2 and C3
already hold at this point); **not demonstrated** if any other criterion fails;
**demonstrated on this synthetic corpus** only if C1–C8 all hold on both splits. A "demonstrated"
verdict permits the sentence "on the Rastro v0.2 recovery benchmark, corpus v1,
Rastro recovered more episodes than an adapted Scrapling + Pydantic baseline without
bad activations", with the model, environment and counts. It permits no claim about
JSON, browsers, live sites, accuracy outside this corpus, latency, or general
self-healing. Failures and partial results are published unchanged.

## 5. Anti-tuning rules

1. **Frozen and hashed.** Corpora and `benchmarks/recovery/manifest.json` are committed
   with the protocol. The tests pin file SHA-256 values and per-scenario hashes.
   Implementation pull requests may not edit `benchmarks/recovery/corpus.json`,
   `benchmarks/recovery/sealed/**` or the manifest. Fixing a corpus defect requires
   corpus v2, a new ADR, new hashes, and results that are not compared with v1.
   Maintainers should enforce this with CODEOWNERS or a required review on the path.
2. **Sealed half.** `benchmarks/recovery/sealed/corpus.json` is committed so it is
   verifiable but is **not to be used for development**: no reading it to design
   heuristics, no copying fixtures into tests, no running executors on it during
   development. `load_split("sealed")` refuses without an explicit `confirm`, and
   nothing under `src/` may reference it (tested).
3. **One sealed look per executor version.** A sealed run counts as claim evidence
   only the first time for a given hash of the `rastro` and baseline executor sources.
   If sealed results are seen and the executors change afterwards, the sealed half is
   **burned** for that claim: a new independently authored half is required.
4. **No special cases.** No scenario ids, HTML substrings, per-split thresholds or
   per-scenario configuration in executors; thresholds are the shipped defaults.
5. **Report everything.** Both splits, controls, all runs of a candidate commit, the
   executor and baseline hashes, git revision and dirty state, model digest, and
   negative results.

**Limits of sealing in a repository.** Hashes make edits detectable; they do not keep
the content secret, and anyone with the repository can read it. Both halves were
written by one author in one sitting and the author is not independent of the design.
This is tamper evidence plus process, not blinding. A stronger scheme is available and
recommended before the public claim: a maintainer (or an independent contributor)
authors sealed half v2 and commits **only its SHA-256** now; the content is revealed in
the release-evidence commit, then run once. The manifest already supports a replaced
`sealed` entry. Until then any "demonstrated" verdict must disclose this limitation.

Corpus hashes (SHA-256 of the exact committed bytes, LF endings, protected by
`benchmarks/recovery/.gitattributes`):

| Split | File | SHA-256 |
| --- | --- | --- |
| Development | `benchmarks/recovery/corpus.json` | `808e97d23fb924cc256a13a961cc5e12436a24c1f242456d18d12cb30613f30d` |
| Sealed | `benchmarks/recovery/sealed/corpus.json` | `e2167c3f3626cadb09463a1fadcae0e90739ab678755faa5c461815a0105bf82` |

Check them yourself with `shasum -a 256 benchmarks/recovery/corpus.json
benchmarks/recovery/sealed/corpus.json`; the unit test also asserts that this page
contains the current hashes.

## Reproducibility

Planned for v0.2. Only the first command and the `benchmarks.recovery.corpus.load_split`
integrity loader exist today; the rest are not implemented:

```bash
uv run pytest tests/unit/test_recovery_corpus.py          # exists today
uv run python -m benchmarks.recovery verify               # manifest, hashes, scan
uv run python -m benchmarks.recovery run --split development \
    --executor baseline_static --executor baseline_adapted --executor rastro \
    --executor always_alarm --executor never_alarm \
    --approver deny_all --output recovery-dev.json
uv run python -m benchmarks.recovery run --split sealed --confirm-sealed <sha256> ... --output recovery-sealed.json
uv run python -m benchmarks.recovery verdict recovery-dev.json recovery-sealed.json
```

The report records protocol and corpus hashes, executor and fallback-library hashes,
git revision and dirty state, Python/Scrapling/Pydantic/Rastro versions, model ID and
digest, approver, per-epoch outputs, all metric numerators and denominators, and the
criterion-by-criterion verdict. Runs are offline and deterministic except for
latency, which is descriptive and never gated.
