# ADR 0015: recipe lifecycle states, single serving version and schema 2

- **Status:** accepted for v0.2.
- **Requirements:** REC-005, REC-006, REPAIR-006; groundwork for REPAIR-001/004/005 and
  DRIFT-*. Refines the lookup rule of [ADR 0003](0003-sqlite-recipe-memory.md).

## Context

Through 0.1.0, recipe memory stored immutable versions and reused the highest
version of a key. That cannot express a repair candidate that must not serve until
it passes (REPAIR-001, REC-006), a working recipe that is failing but still the best
available (`degraded`), or a return to an earlier version (REPAIR-006). Repair and
drift work in later v0.2 slices needs a small, stable vocabulary of states and
transitions before either is built.

## Decision

### States and transitions are a pure domain state machine

`RecipeState` has four values: `candidate`, `active`, `degraded` and `retired`. A table
in `rastro.domain.lifecycle` lists the allowed transitions and the action each one
records:

- `candidate` → `active` (`promoted`) or `retired` (`rejected`);
- `active` ↔ `degraded` (`degraded`, `recovered`);
- `active` or `degraded` → `retired` (`retired`).

`retired` → `active` is allowed only through rollback (`rolled_back`), and only for a
version that has served before. Every other pair, including self-transitions, raises
`LifecycleTransitionError`. Planning functions (`plan_creation`, `plan_transition` and
`plan_rollback`) return the ordered changes. They do no I/O, so every allowed and
forbidden pair is tested without a database.

### Exactly one serving version per key

`active` and `degraded` are serving states. At most one version per key is in a serving
state. Unpinned lookups return it, or nothing. Promotion and rollback first retire the
serving version (`superseded`) and then activate the target. A partial unique index
enforces the invariant in SQLite, so a bug or a concurrent writer cannot create a
second serving version.

A degraded version keeps serving. Removing a failing but best-known recipe would turn
a quality warning into an outage. Drift and repair decide when to degrade, promote
or retire. This slice provides only the operations.

### "Validated" means "has served"

A rollback target must have served as active at some point. Each row stores
`validated_at`, which is set the first time the row becomes active and never cleared. A
candidate that passed some checks but was never promoted is not validated in this
sense, so rollback to it is rejected (REPAIR-006). Promotion is the only path to
service for such a candidate, and it remains subject to the later REPAIR-004/005
gates.

### `save_success` keeps v0.1 adoption semantics

A successful explicit recipe is a caller decision. A new version saved through
`save_success` becomes active and supersedes the serving version, so 0.1.0
behaviour is preserved: an explicit version 2 that succeeds is reused afterwards.
A new version lower than the serving version is stored as a candidate instead. That keeps the ADR 0003 guarantee that a request completing late cannot
supersede a newer version. Repeated success on an existing version never changes its state.
Without this rule, re-running a pinned old version would silently roll back the key.

### Candidates enter only through the lifecycle port

Repair code stores proposals with `RecipeLifecycle.save_candidate`, which only
inserts new version numbers. A candidate flag on `save_success` was rejected: on an
existing version, its upsert path would update the serving version's selected
alternative and statistics. A store without lifecycle support could also ignore the
flag and serve the candidate. Keeping `save_success` at its 0.1.0 signature means
callers that need candidates must hold a store that implements lifecycle control.

### Learning never reuses a stored version number

Before 0.2, an unpinned miss meant the key had no rows, so learning stored version 1.
Now a miss only means no version serves. `RecipeRepository.next_version` returns one
above every stored version, and learning stamps that number on proposals before
evaluating them, so evidence and storage agree. A rejected version 1 can neither
make learning fail with a conflict nor leave a relearned recipe inactive.

### Append-only history per key

Every change appends a `recipe_events` row with a key-wide sequence number, the
version, previous and new state, action, caller-supplied reason (1–500 printable
characters) and UTC timestamp. `history(key)` returns a strict `LifecycleHistory`
model that serializes to stable JSON. `delete` and `prune` remove the history of the
versions they remove, consistent with the privacy guarantees of ADR 0003. The caller's reason is
recorded only on the target version's event; the displaced version's `superseded`
event uses a fixed reason, so deleting a version deletes every note about it. A
per-key counter (`recipe_event_sequences`) keeps sequence numbers from being
reissued while the key keeps any version.

### Schema 2 through a forward-only migration

Migrations are an ordered list in `adapters/sqlite_schema.py`. A fresh file runs
every step, so new and upgraded databases share the same DDL. Step 1 → 2:

1. adds `state` (default `active`, `CHECK` over the four values) and `validated_at`;
2. sets `validated_at = created_at` for every existing row, because schema 1 stored
   only successful, validated versions;
3. retires every version below the highest of its key, which preserves the schema 1
   serving rule exactly;
4. creates the serving-version partial unique index, `recipe_events` and the
   per-key sequence counter;
5. writes one `migrated` event per existing row.

All steps and the `user_version` change commit in one transaction. A failure leaves
the file byte-for-byte unchanged. Payloads are not rewritten. Newer, foreign or
unrecognized files are rejected without modification.

Marking every old row `active` would be simpler. For keys with several versions,
though, it would create more than one serving version and change which version is
reused. Only the highest version per key becomes active. The others are
retired but remain rollback targets, so no capability is lost.

## Alternatives considered

- **Derive state from history only.** Smaller schema, but every lookup would replay
  events, and the single-serving invariant could not be enforced by an index.
- **A boolean `active` flag plus a separate candidates table.** It cannot express
  `degraded`, and promotion would move rows between tables.
- **Make every new version a candidate.** Strictest reading of REC-006, but it breaks
  the 0.1.0 contract that a successful explicit recipe is adopted. The REC-006 risk is
  automatic repair, which uses `save_candidate`.
- **Mark all migrated rows active.** Rejected for the reason given above.

## Consequences

- Databases opened by this version cannot be opened by 0.1.0, which rejects unknown
  schema versions explicitly rather than misreading them. Users who might downgrade
  should copy the file first.
- `RecipeRepository` gains `next_version`; `save_success` keeps its 0.1.0 signature.
  The new `RecipeLifecycle` port (`save_candidate`, `transition`, `rollback`,
  `history`) is separate, so repositories without lifecycle control remain valid
  recipe stores but cannot hold candidates.
- No facade or CLI surface is added yet. Repair, drift and agent slices will decide
  how operators trigger promotion and rollback.

## Verification

`tests/unit/test_lifecycle.py` covers every allowed and forbidden state pair, the
supersede ordering and the rollback eligibility rules. `tests/integration/test_recipe_lifecycle.py`
covers REC-006 end to end through `Rastro.extract_html`, promotion, rollback with reason
and timestamp, the storage-level invariant, and history round trips.
`tests/integration/test_recipe_migration.py` builds synthetic schema 1 files and checks
loading, state assignment, rollback eligibility, schema parity with fresh files, atomic
failure, and rejection of newer files.
