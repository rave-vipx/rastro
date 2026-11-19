# ADR 0040: unbounded crawls keep a JSON checkpoint in Rastro's own frontier

Status: proposed, 2026-11-19. Builds on ADR 0036 and ADR 0037.

## Context

ADR 0036 lets an operator crawl without a page cap (`max_pages = "unbounded"`), stopped by a
time budget and resumed on the next run. The default acquisition engine ships a crawl engine
that does this: it pauses, saves a checkpoint, resumes and adapts its pace per host.

Two properties of that engine rule it out as the crawler:

- Its checkpoint is a pickle. Loading a checkpoint that someone has edited runs their code
  with the operator's rights.
- It fetches through its own sessions, which resolve names themselves. Rastro's DNS pinning,
  private-network guard, per-hop redirect checks and robots gate would not see those requests.

Rastro's spider also had two hard numbers that an unbounded crawl cannot keep: `max_pages`
(at most 100) and the job's `max_requests` (at most 100), shared by every page of a crawl.

## Decision

1. **Rastro's own frontier crawls.** An unbounded crawl uses the same breadth-first spider as
   a bounded one, so every page goes through Rastro's acquisition and its checks. The engine's
   crawl engine is not used.

2. **Time, not counts, bounds an unbounded crawl.** `time_budget_seconds` (60 to 86400,
   default 1800) replaces both the page cap and the request budget for that crawl. The crawl
   fetches its frontier in small chunks and looks at the clock between them; a chunk in
   flight finishes. The record limit (`[limits] max_records`) still applies: pages past it go
   back to the frontier instead of losing their records.

3. **The checkpoint is versioned JSON.** It holds a version number, a key naming the crawl,
   the links still to fetch with their depth, and a digest of every page identity already
   seen. It is validated on load. An unreadable file, an unknown version, a key for another
   crawl or a link that is not plain `http(s)` makes Rastro remove the file, report
   `crawl_checkpoint_discarded` and start over. Each saved link is checked again against the
   job's hosts, paths and depth before it is fetched.

4. **The checkpoint is private state.** It lives under `.rastro/crawl/` in the job's state
   folder, written atomically with mode 0600 in a 0700 folder; symbolic links are never
   followed. Seen pages are stored as digests, so the file names no visited query. Saved
   links keep their full URL because they must be fetched; treat the folder like the dataset.

5. **One checkpoint per job and start URL.** The key covers the job's fields and its spider
   table (the time budget aside), so changing the budget keeps the checkpoint and changing
   what the crawl extracts or follows starts a new one. A finished crawl removes it.

6. **The engine's throttle sits behind a port.** `CrawlThrottle` adapts the delay per host
   from latency, doubles it on a block (429, 503 or a refusal) and honours `Retry-After`. The
   adapter wraps the engine's throttle arithmetic only. The delay never drops below the job's
   `min_interval_seconds`.

7. **An unbounded crawl is a capability.** It requests `unbounded_crawl` (ADR 0037): on for
   the operator's jobs, granted per origin for agent tools, bundles, registry recipes and
   model output.

## Alternatives considered

- **Use the engine's crawl engine.** Rejected for the pickle checkpoint and the unchecked
  requests above.
- **Keep the request budget for unbounded crawls.** Rejected: a budget of at most 100 requests
  would make the crawl bounded again. Time is the bound the operator can reason about.
- **Save the checkpoint after every page.** Rejected: a crash would keep pages as seen whose
  records were never returned. The checkpoint is written once, when the run stops, so it
  always matches the records that run produced.

## Consequences

- An unbounded crawl run with no state folder (a bare Python call) still stops at its time
  budget but starts over each run.
- The crawl report keeps at most 600 rows; past that it counts the rest in `unreported` and
  summarises every page by outcome in `outcomes`. The run report's crawl note states the
  pages fetched, the frontier left, whether the run resumed and what happened to the
  checkpoint.
- Two runs of the same job at once share one checkpoint file; the dataset lock serialises
  `rastro job load`, but plain runs are not locked against each other.
