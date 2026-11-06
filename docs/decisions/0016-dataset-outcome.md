# ADR 0016: the product outcome is an updatable database; v0.2 starts from known sources

- **Status:** accepted on 2026-11-06 by the project owner, after v0.2 wave 1.
- **Requirements:** DATA-001–005, SYNC-002/003/005/006/008, COST-001/002, DISC-003, DRIFT-006, DIFF-002, AGENT-002, SCHED-007 (master specification 1.2).

## Context

v0.1 (`0.1.0rc1`) proves verifiable extraction: validated records, field provenance, recipe
memory and bounded model use. Wave 1 of v0.2 delivered the Mock Lab and the recipe lifecycle.
The specification still described the product by its capabilities (extract, verify, repair,
synchronize) and ordered v0.2 around repair. Repair without a dataset has nothing to protect:
no record identity, no "last valid value", no table an application reads. A user could extract
records but not maintain the database their application needs.

## Decision

1. **Outcome.** Rastro's promise is: *describe the data you need, name the sources you know,
   and get an updatable database for your application.* The first public case is "I want to
   feed an agenda of dance events in Uruguay". Its fields and rules live in the example job and
   schema; the engine stays domain-neutral.
2. **v0.2 — from known sources to a usable database.** First cut: two real sources, the job a
   user would write, preview, SQLite load of approved fields, JSONL export and the "upcoming
   dances" query, without Python. A second run must keep the event count, update a rescheduled
   performance, keep distinct performances distinct, keep a failed source's records with their
   last observation time, and flag uncertain cross-source matches with both origins. Second
   cut: drift and repair on that path, including a sale-date-versus-event-date trap, the
   product-task comparison (DIFF-002) and the single opening of the sealed corpus (ADR 0011).
3. **Brought forward from v0.3:** record identity, update semantics, no deletion on failure,
   last observation time and stale-source reporting (SYNC-002/003/005/006/008).
4. **Moved:** the MCP adapter (AGENT-002) moves from v0.2 to v0.4 and waits for a concrete
   integration that will consume it. v0.3 is reframed around three entry modes (known sites
   and fields, sites only, goal only) and adds pagination, JavaScript, sessions, per-domain
   concurrency and run recovery according to the obstacles the pilot finds. Hub (v0.5) starts
   with Git export and import of a recipe with its schema, a redistributable sample, its test
   and its last-verified date.
5. **Recipe memory and the dataset are separate stores.** Memory records how to extract; the
   dataset records what was extracted, with identity and observation times.
6. **Cost.** The cost of learning a source is reported apart from the cost of repeating it.
   Every documented workflow has a zero-external-spend mode (v0.2); paid services show the
   operation, its estimated cost and the user's limit, which is enforced (v0.3).
7. **Definition of ready.** A product capability is ready only when its result reaches the
   dataset correctly and survives the next update. AI may suggest sources, categories or
   recipes, but a value is written only after it is observed in a source, with provenance.
8. **Work assignment** (models, workers, reviews) is decided at the start of each slice by its
   difficulty and budget, not fixed in advance; time, tokens and rework are recorded when a
   slice closes.

## Consequences

- v0.1 stays the reference release; nothing in it is removed.
- New SQLite tables for the dataset; the recipe-memory schema is untouched. The dataset store
  needs its own ADR when its first slice lands.
- Live runs against real sites become part of v0.2 evidence. They follow every acquisition
  policy (robots, pacing, destination checks); bot challenges are reported as restrictions,
  never evaded (SCR-003). Committed fixtures stay synthetic or redistributable.
- Repair and drift are judged by their effect on the dataset, not only on extraction outcomes.
- Users who wanted MCP in v0.2 wait for v0.4 unless a concrete integration appears earlier.
