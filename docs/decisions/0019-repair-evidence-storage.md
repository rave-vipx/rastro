# ADR 0019: last-success repair evidence stays with the dataset

- **Status:** accepted for v0.2.
- **Requirements:** REPAIR-002/004/005, SCR-002, REC-006; preserves ADR 0015/0017/0018.

## Problem and options

Adaptive relocation needs the exact successful element fingerprints; recipe CI also
needs a historical document whose expected values can be replayed. Drift topology
hashes cannot supply either. Options were raw source retention, an additional file
per source, or an additive namespace in the dataset SQLite file. Raw pages retain
unapproved content; sidecar files make binding and atomic replacement harder.

## Decision

Use `relocation_schema` (one version row, currently 1) and `relocation_samples`, keyed
by dataset, complete source digest, schema fingerprint and canonical recipe digest.
Each key retains its latest complete success: R2's `RelocationSnapshot` for CSS and
a sanitized, verified Mock Lab `FixtureBundle` for historical replay. Other recipe
strategies retain the bundle without CSS fingerprints. Source binding includes regional
context and query identity. Replay origins are explicitly relabeled to
`https://fixture.invalid/`; the separate source digest preserves the original binding
without retaining a sensitive source URL path in the fixture manifest. Approved
extracted URLs remain values and are subject to Mock Lab's sensitive-data scan.

Capture is scoped to `job load`, records the exact successful selected attempt, and
filters final outcomes before retention. Partial/failed attempts never overwrite evidence.
Sanitization and replay verification happen before persistence. Fingerprint hashes are
private comparison data, not anonymization. Bounds use existing `Limits`; foreign or newer
namespace versions are refused. Read operations neither migrate nor create missing files.
Creation and upsert use the dataset adapter's transactional staging/publish and symlink
refusal. The recipe-memory schema and dataset `user_version` remain unchanged.

## Tradeoffs and verification

Evidence retention follows the dataset write in a separate transaction. A refused sample
or disk error may therefore follow an already committed valid dataset update; the CLI
reports failure explicitly. Latest-per-key retention keeps evidence for earlier recipe
versions so rollback remains useful, but global history pruning is deferred. Recipe
promotion and report persistence are also separate; an I/O failure cannot undo a committed
promotion. All paths require trusted private parents, as in ADR 0017.

Tests cover failed-load retention, canonical digest round trips through recipe memory,
source/schema/recipe lookup, repeated initialization, foreign versions, migration rollback,
read-only missing lookup, symlink parents, byte budgets, historical replay and the real
CLI's promotion/rollback/next-load behavior. No frozen recovery corpus is used.
