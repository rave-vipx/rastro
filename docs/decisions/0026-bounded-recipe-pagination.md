# ADR 0026: Bounded recipe pagination

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

A normal end of the list is a complete snapshot: no next link (`no_next`), a next
link to an already read page (`repeat`) and an HTTP 200 page after the first with no
records (`empty_page`). Policy stops (`max_pages`, `budget`, `off_host`,
`https_downgrade`, `invalid_next`), limits and any page failure keep earlier records
as partial, preventing dataset deletions. A cursor loop is how some sites mark the
last page, so treating `repeat` as partial would make such sources never complete.
Host comparison lowercases and strips one trailing dot, since both spell the same
host. Fallback recipes cannot declare pagination; the validator rejects it instead
of silently ignoring it.
The result keeps page one's identity so its existing drift and repair captures
remain usable. Additional pages retain an ordinal and redacted page provenance.

Dataset identity stays separate from traversal. Jobs with a dataset keep the first
identity across pages and count conflicting later values. Same-page duplicates
still reach the store's existing error boundary. No persisted format migration or
required protocol method is introduced; optional result fields are absent for
non-paginated runs. Third-party acquisition ports remain responsible for enforcing
their physical transfer policies; reported request counts also reduce the traversal
budget, while `WebAcquirer` enforces it before each physical attempt.
