# Clean code and module size

Rastro stays maintainable by keeping modules small, responsibilities single, and
dependencies pointing inward. These rules apply to all code under `src/rastro`
and to its tests. They implement the Engineering Standards (master spec section 15)
and the Dependency Rule (section 10).

## Size budgets

| Unit | Soft limit | Hard trigger |
| --- | --- | --- |
| Module | about 250 lines | about 400 lines: split before merge |
| Function or method | about 40 lines | Extract named helpers |
| Class | one responsibility | More than about 7 public methods is a smell |

A module past the soft limit needs a reason in the PR description. A module past
the hard trigger does not merge until it is split. Line counts include
docstrings; do not game them by compressing readable code.

Current state (v0.1 plus the recipe-model refactor): the largest modules are
`domain/models.py` (324), `application/acquisition.py` (315),
`application/intelligence.py` (273), `application/service.py` (255) and
`application/engine.py` (254). Four are at or just past the soft limit and none
exceeds the hard trigger. Treat them as the first candidates to split when they
next need a feature.

## Signals that a module must split

- **Repeated type or strategy branching.** If the same `if kind == ...` or
  `match` on a representation, strategy or backend appears in more than one
  place, replace it with polymorphic variants (a discriminated union whose
  members own their behaviour), a strategy object, or a registry keyed by name.
  One branch is fine; the second copy is the signal.
- **An orchestrator accumulating feature logic.** An orchestrator sequences
  steps and delegates. When it starts holding feature rules, split into a thin
  facade plus focused services.
- **Adding a feature.** Prefer adding a module that plugs into the existing
  seam over growing a central class. If a feature cannot be added without
  editing many methods of one class, fix the seam first, in its own commit.
- **Mixed concerns.** Parsing, policy, persistence and presentation in one file
  are four modules.

## Dependency direction

```text
adapters  ->  application  ->  domain
    \             |
     `--> ports <-'
```

- `domain` depends on nothing in Rastro and on no I/O library.
- `application` depends on `domain` and on `ports` (protocols), never on
  concrete adapters.
- `adapters` implement `ports` and may import `domain` types. Scrapling, HTTP,
  SQLite and Ollama stay behind adapters.
- Composition (wiring adapters into services) happens only at the edge, such as
  the public facade or the CLI.

## Hygiene

- No compatibility shims or aliases for internal ports before 1.0. Change the
  port and update every caller in the same PR.
- Names match content. If a field carries JSON, it is not called `html`; if a
  module grew a second purpose, rename or split it.
- Prefer small pure functions in `domain`: typed input, typed output, no hidden
  state.
- Tests mirror modules: `src/rastro/application/memory.py` is covered by
  `tests/unit/test_memory.py`.
- No dead code, commented-out code, or placeholder implementations. Unfinished
  work is tracked in `docs/roadmap.md`, not stubbed in `src`.
- Failures stay explicit and provenance stays attached. Never turn invalid or
  missing data into successful empty output to shorten a function.

## Reviewer one-liner

List modules over the soft budget, largest first:

```sh
wc -l src/rastro/*/*.py src/rastro/*.py | awk '$1 > 250 && $2 != "total"' | sort -rn
```

Anything over 400 lines blocks the merge.

## Worked example: `engine.py` in v0.1

In v0.1 `application/engine.py` reached 388 lines. Each feature added to it,
JSON responses and then explicit egress, introduced `if representation == "json"`
branches, and the same branch was copied into several methods. The engine also
kept compatibility shims for internal ports, and `AcquisitionResult.html`
carried JSON bodies, so a name no longer matched its content.

Every symptom in the lists above was present: duplicated type branching, an
orchestrator holding feature logic, shims, and a misleading name. The fix
(PR #25) had three parts:

1. **`Recipe` became a strategy-discriminated union** of `CssRecipe`,
   `JsonLdRecipe` and `JsonRecipe`. Each variant owns its extraction behaviour,
   so the `if representation` branches disappeared and a new strategy is a new
   class rather than an edit to every method.
2. **Definitions were separated from operational metadata.** What a recipe
   extracts is distinct from when it was learned or how it has performed, so
   each side changes independently.
3. **`Rastro` became a thin facade over `application/service.py`**
   (`ExtractionService`). The public API stays stable while orchestration lives
   in a focused service that depends only on ports.

The lesson: when the second copy of a branch appears, stop and introduce the
seam, then add the feature behind it.

## Pull request checklist

- [ ] No module over about 250 lines without a stated reason; none over 400.
- [ ] No function over about 40 lines; no class with more than about 7 public
      methods.
- [ ] No repeated type or strategy branching; variants, strategy or registry
      used instead.
- [ ] New feature added as a module, not by growing an orchestrator.
- [ ] Dependencies point inward; domain imports nothing from adapters or I/O.
- [ ] No compatibility shims, dead code or placeholders.
- [ ] Names match what the code holds.
- [ ] Tests mirror the modules touched and run offline.
- [ ] Relevant master spec requirement IDs are named in the PR.
