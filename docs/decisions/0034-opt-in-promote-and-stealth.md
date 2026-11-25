# ADR 0034: opt-in promote, stealth, and local code

- **Status:** accepted 2026-11-16. These capabilities exist with an activation choice, not as the default.

## Decision

Three capabilities exist. None turns itself on because a page used JavaScript, because a run scrolled, or because a bundle was installed.

### Promote without a click

`[repair] auto_promote` stays `false` unless the operator sets it on the job they own, or turns the Studio switch on. That switch writes only that job key.

When it is on, a candidate becomes active only if the gates already required today pass and the semantic judge agrees the values kept their meaning. A failed gate quarantines the candidate, leaves the previous recipe serving, and surfaces the alert in Studio's Alerts view. A repair that extracted nothing is not a success.

An imported or URL-installed bundle cannot set `auto_promote`. Install stays a candidate.

### Stealth

The job key is `render`, already shipped. Values are `http`, `browser`, and `stealth`, on `[acquisition]` or on a source. Do not rename it to `engine`. That would be a job-format migration for no change in behavior.

`render = "stealth"` runs only when the operator grants `--allow-stealth`, `RASTRO_ALLOW_STEALTH=1`, or the Studio session switch. The switch grants the running Studio process. It does not write a grant into the job file.

Choosing `browser` or scroll does not check that switch and does not solve challenges. `solve_challenges` stays a separate job request and still needs the stealth grant. No proxy rotation and no external CAPTCHA service.

Reusing one browser context across pages of the same source does not change this key. It replaces a fresh context per page, for one source, for one run.

### Code and other people's pages

Shared recipes stay data. There is no flag that lets a bundle run Python. Extra logic lives in the operator's own job or SDK code, which is the activation: they wrote it, a registry did not.

A run scrapes a URL and keeps the recipe. A bundle sample is optional replay evidence, not the next scrape target.

There is no account server to turn on. Hosting a registry is publishing the directory or its HTTPS URL (ADR 0033).

## Consequences

Studio shows both switches off, with one sentence each on what they do and what they refuse. Tests cover: bundle install leaves `auto_promote` untouched; `render = "stealth"` without the grant does not launch the stealth browser; with the grant, it may.
