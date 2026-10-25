# ADR 0005: Reproducible release evidence

- **Status:** Accepted for the M5 evidence slice; release acceptance remains open.
- **Requirements:** DIFF-001, QUAL-003, OBS-001, master specification sections 17–18.

## Context

A comparison against a naive scraper would not establish Rastro's value. The
baseline must validate records, reject ambiguous fields, and try known fallbacks.
Synthetic fixtures are distributable and stable; they cannot establish behavior
across the real web or the quality of a live model.

## Decision

Keep the comparison in `benchmarks/`, outside the runtime wheel. Both independent
executors receive the same HTML, schema, explicit mappings, and fallback order.
Only the scorer can access the separately declared oracle. The baseline uses
Scrapling and Pydantic directly, without importing Rastro's extraction services.

Score records and fields as multisets using a stable fixture SKU. Publish primitive
counts, denominators, per-case results, raw timing samples, dependency versions,
and source hashes. Include a valid but semantically wrong price to expose the
limits of schema validation. Do not tune mappings against that oracle.

Time complete extraction/validation calls after warmup with alternating execution
order. Exclude input loading and scoring. Rastro also builds provenance and public
result contracts, so elapsed times compare the actual integrations, not identical
internal work. CI verifies deterministic results without a hardware timing gate.

Keep real HTTP execution evidence separate from offline comparison. The public
demo site's HTML is not retained. A synthetic equivalent tests the same recipe,
currency contract, and persistence offline.

## Consequences

The first comparison may show a tie or disadvantage. Publish it without claiming
DIFF-001 is fulfilled. Rendered HTML is a supplied snapshot, not browser execution;
a large page is not pagination. Human effort and maintenance reduction require a
separate study. License metadata is an inventory, not distribution clearance.
No release or publishing workflow is enabled by adding this evidence.
