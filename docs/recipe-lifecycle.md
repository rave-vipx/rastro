# Recipe lifecycle and rollback

Every stored recipe version has a lifecycle state (REC-005). Exactly one version per
[recipe key](memory.md#identity-and-validation) serves reuse when no version is
pinned. A candidate never replaces that version unless it is explicitly promoted
(REC-006), and a version that has served before can be restored (REPAIR-006).

This page covers states, transitions, persistence and rollback. Repair candidates
and drift detection build on these operations in later v0.2 work.

## States

| State | Serves unpinned reuse | Meaning |
| --- | --- | --- |
| `candidate` | No | Stored but not validated for service, such as a proposed repair. |
| `active` | Yes | The known working version. |
| `degraded` | Yes | Still the best known version, but flagged as failing. It keeps serving until it recovers, is retired, or a candidate is promoted. |
| `retired` | No | No longer serving. It can return only through rollback, and only if it served before. |

Pinning a version (`recipe_version=` or `repository.get(key, version=n)`) selects
it in any state. That is an explicit caller decision and does not change its state.

## Transitions

```text
            promote                degrade
candidate ──────────► active ◄──────────────► degraded
    │                  │  ▲       recover        │
    │ reject           │  │ rollback             │ retire
    ▼                  ▼  │ (served before)      ▼
 retired ◄─────────────┴──┴──────────────────────┘
          retire / superseded
```

| From | To | Action |
| --- | --- | --- |
| `candidate` | `active` | `promoted`; the serving version becomes `retired` (`superseded`) |
| `candidate` | `retired` | `rejected` |
| `active` | `degraded` | `degraded` |
| `degraded` | `active` | `recovered` |
| `active` or `degraded` | `retired` | `retired` |
| `retired`, served before | `active` | `rolled_back`, through `rollback` only |

Every other change, including any self-transition, raises
`LifecycleTransitionError` and leaves storage unchanged. The rules are pure functions
in `rastro.domain.lifecycle` (`plan_creation`, `plan_transition`, `plan_rollback`);
the SQLite adapter applies the planned changes in a single transaction.

## Storing versions

`save_success` records a validated extraction, as in v0.1:

- A **new** version becomes `active` and the previously serving version becomes
  `retired` with action `superseded`. An explicit recipe that passes validation is
  therefore still adopted, as before.
- A new version lower than the serving version is stored as `candidate`, so a
  request that completes late cannot replace a newer recipe.
- Repeated success on an **existing** version only updates its success history. It
  never changes the state, so re-running an old version does not reactivate it.

`save_candidate` (on the `RecipeLifecycle` port) stores an evaluated proposal as a
new `candidate` version. It never touches the serving version, and any version
number that already exists raises `RecipeConflictError`, so a candidate cannot
change the history or selected alternative of a stored version.

Cold-context learning (`discover_recipes=True` or `model=`) stores learned recipes
at `next_version(key)` from the `RecipeLifecycle` port: one above every stored
version in any state. A rejected or retired version 1 therefore never blocks learning
or collides with it. A store that implements only the four-method 0.1.0
`RecipeRepository` port cannot hold candidate or retired rows, so learning on it
stores version 1, as before.

With `source` and `Product` from the [memory example](memory.md#remember-and-reuse),
version 1 serving, and a proposed `repaired` recipe with version 2:

```python
from datetime import UTC, datetime

from rastro import RecipeKey, SQLiteRecipeRepository
from rastro.domain.lifecycle import RecipeState

repository = SQLiteRecipeRepository("rastro-recipes.db")
key = RecipeKey.for_source(source, Product)
now = datetime.now(UTC)

repository.save_candidate(key, repaired, selected_index=0, at=now)

if passed_evaluation:
    # Version 2 becomes active; version 1 is retired as superseded.
    repository.transition(key, 2, RecipeState.ACTIVE, reason="Thresholds met", at=now)
else:
    # Version 1 keeps serving; version 2 can never be a rollback target.
    repository.transition(key, 2, RecipeState.RETIRED, reason="Failed fixtures", at=now)

# Later, after a promotion: return to the earlier working version.
repository.rollback(key, 1, reason="Regression reported", at=now)
```

Rollback accepts only a `retired` version that served at some point: it has a
`validated_at` timestamp. A rejected candidate never served, so rolling back to it
raises `LifecycleTransitionError`. Reasons are required: 1–500 characters, not blank. Control characters (including
ANSI escapes), bidi controls and line or paragraph separators are rejected;
non-breaking spaces and emoji sequences are accepted.
They are stored as written, so never include credentials, URLs or extracted data.

## History

`repository.history(key)` returns a `LifecycleHistory`: each version's state and
`validated_at`, and every event in order with sequence number, version, previous and
new state, action, reason and UTC timestamp. Serialize it with `model_dump_json()`:

```json
{
  "key": {"locale": "en-US", "...": "..."},
  "versions": [
    {"version": 1, "state": "active", "validated_at": "2026-01-02T00:00:00Z"},
    {"version": 2, "state": "retired", "validated_at": null}
  ],
  "events": [
    {"sequence": 1, "version": 1, "from_state": null, "to_state": "active",
     "action": "created", "reason": "Validated extraction succeeded",
     "at": "2026-01-02T00:00:00Z"},
    {"sequence": 2, "version": 2, "from_state": null, "to_state": "candidate",
     "action": "created", "reason": "Stored candidate", "at": "2026-01-02T00:00:00Z"},
    {"sequence": 3, "version": 2, "from_state": "candidate", "to_state": "retired",
     "action": "rejected", "reason": "Failed fixtures", "at": "2026-01-02T00:00:00Z"}
  ]
}
```

`delete` and `prune` remove a version's events together with its definition. Caller
reasons are stored only on the event of the version they describe; the version a
promotion or rollback displaces records the fixed reason
`Superseded by another version`. Sequence numbers are never reissued while the key
keeps any stored version.

## Upgrading existing databases

Opening a 0.1.0 or M3 database migrates it from schema 1 to schema 2 in one
transaction. Payloads are not rewritten and keep loading as before. Schema 1 reused
the highest version of each key, so that version becomes `active` and older versions
`retired`. Every schema 1 row had passed validation, so all of them get
`validated_at` set to their creation time and remain rollback targets. Each row gets a
`migrated` event.

The migration is forward-only. Once migrated, a database cannot be opened by 0.1.0,
which rejects schema versions it does not know. Back up the file before upgrading if
you might downgrade. See [ADR 0015](decisions/0015-recipe-lifecycle.md) for the
design tradeoffs.
