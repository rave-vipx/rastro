# ADR 0024: Bounded recipe pagination

Status: accepted

Requirements: SYNC-002, SEC-008

## Decision

Recipes declare a next expression and a page cap. The acquisition service delegates
traversal to a separate application module; parsers read next links through existing
declarative recipes, without expanding either public port. URL jobs reuse this path.

A task-local acquisition scope carries the source hostname, HTTPS requirement and
remaining transfer budget. `WebAcquirer` checks the scope before every transfer,
including robots, redirects, retries and alternate addresses. Per-document budgets
alone would reset on every page and could exceed the source's request allowance.
The scope is reset on completion, failure or cancellation.

## Consequences

Only natural exhaustion is a complete snapshot. Repeats, policy stops, limits and
later-page failures keep earlier records as partial, preventing dataset deletions.
The result keeps page one's identity so its existing drift and repair captures
remain usable. Additional pages retain an ordinal and redacted page provenance.

Dataset identity stays separate from traversal. Jobs with a dataset keep the first
identity across pages and count conflicting later values. Same-page duplicates
still reach the store's existing error boundary. No persisted format migration or
required protocol method is introduced; optional result fields are absent for
non-paginated runs. Third-party acquisition ports remain responsible for enforcing
their physical transfer policies; reported request counts also reduce the traversal
budget, while `WebAcquirer` enforces it before each physical attempt.
