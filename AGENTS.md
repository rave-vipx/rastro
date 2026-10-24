# Working on Rastro

Read `RASTRO_MASTER_SPEC.md` before implementation. The active release is **v0.1**;
`docs/roadmap.md` tracks milestone scope and unfinished requirements.

- Deliver one usable vertical slice at a time, with tests and documentation.
- Keep domain code independent of adapters. Use Scrapling through the parser or acquisition boundary.
- Preserve explicit failure outcomes and field provenance. Invalid or missing data cannot become successful empty output.
- Keep default tests offline. Fixtures are synthetic; never commit captured credentials or private content.
- Treat recipes as data. Core installation and tests must remain usable without AI or browsers.
- Run the checks in `CONTRIBUTING.md` before committing. Report actual results and known limitations.
- For a new milestone or architectural boundary, update `docs/roadmap.md` and add an ADR under `docs/decisions/` when a tradeoff needs explanation.
- Each meaningful change identifies the relevant requirement IDs from the master spec.
