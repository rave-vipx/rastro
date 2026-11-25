# ADR 0032: vibe-coder Studio, JavaScript, and a bounded spider

- **Status:** accepted, 2026-11-16.

## Problem

Rastro extracts known pages reliably and refuses wrong repairs. A newcomer still cannot paste a URL, follow a next link, crawl a listing they named, or see what a run is doing without reading TOML and exit codes. Those gaps are product choices, not security properties. JavaScript rendering and bounded pagination already exist and stay hidden behind the same choices.

## Options

1. Keep the bans. Studio stays a saved-HTML form, and every URL is written by hand.
2. Become an unbounded crawler and a hosted scraper. Drops the security model that makes a repair trustworthy.
3. Repeal only the product bans. Keep fail-visible extraction, operator grants, and recipes-as-data. Add a URL-first Studio, same-source browser reuse, and a spider with hard caps.

## Decision

Option 3. Where earlier requirements forbid discovered links, a URL-first Studio, or progress for a long run, this ADR wins.

The security boundaries stay in force. In particular, stealth, private networks, ignore-robots, and egress remain operator grants; a spider does not grant them.

## Consequences

- The spider and same-source browser reuse ship with offline tests. Spider controls in Studio follow the `[spider]` job schema.
- A richer React Studio, a hosted registry, and infinite-scroll capture are out of scope for this decision; ADR 0033 covers the last two.
- The README may describe bounded crawling. It may not claim higher accuracy than the published benchmarks.
