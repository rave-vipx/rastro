# ADR 0030: run-local HTTP sessions and conditional dataset loads

- **Status:** accepted for 0.7.0.

## Problem

Some permitted sources require a cookie from an earlier response within a run.
Repeated loads also download unchanged documents, although their approved records
already exist. Neither optimization may widen egress or turn missing data into a
successful empty snapshot.

## Options

1. Let the HTTP library manage and persist sessions, coupling cookie policy to the
   transport and risking domain widening or cross-run credential retention.
2. Keep a bounded host-only application jar, and persist only HTTP validators in
   the dataset behind an optional port. Preserve the existing transport protocol.

## Decision

Choose option 2. Sessions are opt-in, run-local and exact-host; cross-host document
redirects withhold cookies. The transport preserves separate Set-Cookie headers
but still disables its own cookie management. Cookie values never enter results,
logs, object representations or persistence.

Dataset schema 2 adds a validators table through a forward-only, transactional
migration from schema 1 on the first write. Reads open read-only, accept schema 1 or 2
and never migrate, so they work in read-only folders and never mutate the file. Source and
final-target URLs are hashed for cache matching; only canonical, query-free origin URLs are retained for
status association. Validators are bound to the successful snapshot timestamp:
a failed cache commit or a late writer cannot enable a previous snapshot's tag.
A 304 advances status and the matching validator timestamps in one transaction.

## Consequences

- `unchanged` is a successful source outcome with retained records and provenance;
  it does not extract or assess drift. Existing open drift remains open, so exit 4
  still applies when attention is needed. Drift reports contain assessed sources.
- Validators carry a digest of the extraction inputs (job, schema, serving recipe);
  a mismatch skips conditional headers, so a 304 cannot hide a recipe or job change.
- Only successful, approved HTTP 200 loads replace validators. Robots requests
  stay unconditional and validators follow only their exact final document URL.
- Schema 1 records survive the first write, but older code refuses upgraded files.
  Downgrading requires a pre-upgrade backup; there is no destructive down migration.
- The SDK jar lives as long as its WebAcquirer client; a fresh run needs a fresh
  client. Jobs create a new client per run. There are no login or cookie-file flows.
