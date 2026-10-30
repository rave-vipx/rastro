# ADR 0011: freeze the recovery benchmark before repair code exists

- **Status:** accepted for v0.2 planning.
- **Requirements:** DIFF-001, REPAIR-001–006, DRIFT-001–005, QUAL-005/006, MOCK-002/003,
  specification section 17 (recovery success rate, mean time to detect degradation,
  mean time to restore, false success, silent error detection).

## Context

The held-out DIFF-001 evaluation (v0.1) measured one-shot mutations of a working
integration and found no advantage in silent error detection. DIFF-001 therefore
remains open for recovery. v0.2 adds repair candidates, drift signals and promotion
rules, all of which can be tuned until a benchmark passes. A recovery claim is only
meaningful if scenarios, metrics, thresholds and the comparison target are fixed
by someone who has not yet seen the implementation.

## Decision

1. **Epoch sequences, not single mutations.** A scenario is six cumulative page
   snapshots with oracle-labelled episodes. Recovery, detection delay, false alarms,
   source-restored resumption and stale-but-valid traps cannot be measured on isolated
   mutations.
2. **Label what is recoverable.** Each epoch states whether a page-derived selector
   recipe exists, proven by a stored oracle recipe that tests check. Unrecoverable
   episodes (removed field, empty or soft-error page, partial page) reward honest
   non-success and punish a bad repair.
3. **Two executors that differ by capability.** `baseline_adapted` has generic fallbacks
   and an alarm but no repair loop, so Rastro is compared with a competent maintainer's
   code, not a strawman. `baseline_static`, `always_alarm` and `never_alarm` are
   controls against gaming.
4. **Exact formulas and criteria now.** Absolute and relative thresholds (C1–C8) are
   recorded before data exist. Intervals are reported, but criteria use counts because
   8–12 episodes per split cannot support significance tests.
5. **Hash, do not hide.** Both corpora are committed with SHA-256 and per-scenario
   hashes in `manifest.json`; tests fail on any edit; the sealed half is documented as
   not for development, loads only with explicit confirmation, is usable once per
   executor version and is burned otherwise.

## Alternatives considered

- **Sealed content kept out of the repository** (commit-reveal). Better secrecy, but
  contradicts the requested reproducible layout and offers no verification before
  reveal. It is recorded as the upgrade path: replace the `sealed` manifest entry with
  an independently authored v2 half revealed at release evidence time.
- **Hash only the combined corpus.** Rejected; per-scenario hashes let a later corpus
  v2 reuse or retire individual scenarios without ambiguity.
- **Statistical significance criteria.** Rejected for this size; mechanism-level
  claims are scoped to the synthetic corpus.
- **Mutating real captured pages.** Rejected: licensing, private-content and
  reproducibility risks (MOCK-005).

## Consequences

- Corpus v1 is HTML-only and omits label changes, pagination changes and transport
  failures; claims cannot extend to them.
- The sealed half shares an author with the development half and is readable by
  everyone, so it gives tamper evidence, not blinding. Any positive verdict must say so
  until an independent half exists.
- Both halves were designed with knowledge of the v0.1 SDK's strict, selector-only
  behaviour; behaviours that depend on JSON/JSON-LD redundancy are untested.
- No runtime or wheel change; `benchmarks/` remains excluded from the wheel.

## Verification

`tests/unit/test_recovery_corpus.py` checks structure, label truth against a strict
reference extractor (initial integration breaks exactly where labelled; every
`repair_possible` oracle recipe returns the expected records), mutation-kind coverage,
split sizes, file and per-scenario hashes, the hashes quoted in the protocol,
tamper rejection and the sealed-split guard, all offline.
