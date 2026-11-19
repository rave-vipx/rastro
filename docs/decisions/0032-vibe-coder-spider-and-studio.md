# ADR 0032: vibe-coder Studio, JavaScript, and a bounded spider

- **Status:** accepted by owner request, 2026-11-16.
- **Requirements:** product needs N1–N8. Amends the product bans implied by ADR 0020 and by the "you name the pages" comparison. Does not amend SEC-001–SEC-011.

## Problem

Rastro extracts known pages reliably and refuses wrong repairs. A newcomer still cannot paste a URL, follow a next link, crawl a listing they named, or see what a run is doing without reading TOML and exit codes. Those gaps are product choices, not security properties. JavaScript rendering and bounded pagination already exist and stay hidden behind the same choices.

## Options

1. Keep the bans. Studio stays a saved-HTML form, and every URL is written by hand.
2. Become an unbounded crawler and a hosted scraper. Drops the security model that makes a repair trustworthy.
3. Repeal only the product bans. Keep fail-visible extraction, operator grants, and recipes-as-data. Add a URL-first Studio, same-source browser reuse, and a spider with hard caps.

## Decision

Option 3. The product needs are the planning source for this cut. Where `RASTRO_MASTER_SPEC.md` forbids discovered links, a URL-first Studio, or progress for a long run, this ADR wins. Do not edit the master spec in place.

Security boundaries listed in the product needs stay in force. In particular, stealth, private networks, ignore-robots, and egress remain operator grants; a spider does not grant them.

## Consequences

- Claude implements the spider and same-source browser reuse, with offline tests.
- Codex implements the Studio and README surface on the current services. Spider controls in Studio wait until the `[spider]` schema exists.
- A richer React Studio, a hosted registry, and infinite-scroll capture stay out of this cut.
- The README may describe bounded crawling. It may not claim higher accuracy than the published benchmarks.
