# Web data, with evidence

![Rastro: AI writes the scraper once, recipes keep it running](assets/rastro-banner.svg)

**Rastro: AI writes the scraper once, recipes keep it running.** Rastro converts supplied HTML or permitted web sources into validated Python records and explains where each observed field came from. Its goal is reliable, continuously updated web datasets.

**Status: 1.0 in development.** Typed, self-healing web scraping: declare a Pydantic model, get validated records with provenance; when the site changes Rastro detects it and proposes or applies a verified repair. A local Studio, an MCP server and shareable recipe bundles are included; see the [README](https://github.com/rave-vipx/rastro#readme) for results and limits.

## Start here

- [Run the offline example](getting-started.md).
- [Acquire permitted URLs](acquisition.md).
- [Remember validated recipes across restarts](memory.md).
- [Bring your own AI](ai.md): fully offline by default. Connect a local model, any API, a subscription CLI or your MCP agent. The model proposes and Rastro verifies.
- [Discover recipes with optional model assistance](intelligence.md).
- [Understand results, evidence, and limits](api.md), including the [run report](run-report.md) and [reading results](reading-results.md).
- [Declare list and object fields](fields.md) and [crawl linked pages](crawling.md).
- [See which engine features Rastro exposes](engines.md).
- [See the architecture and tradeoffs](architecture.md).
- [Use Rastro from an AI agent](ai-agents.md).
- [Know what is stable across releases](stability.md).

## What makes the foundation useful?

A Pydantic schema defines what the caller accepts. A recipe records where fields should be found. Rastro validates each candidate separately, preserves accepted records, and reports missing, ambiguous, and invalid inputs without disguising them as successful empty data.

Evidence records a source URL, observation time, locator, method, and recipe version. It describes the origin of a value; it does not independently establish that the source is truthful or that the entire dataset is complete.

## Project resources

[Source code](https://github.com/rave-vipx/rastro) · [Contributing](https://github.com/rave-vipx/rastro/blob/main/CONTRIBUTING.md) · [Security policy](https://github.com/rave-vipx/rastro/blob/main/SECURITY.md)

Rastro is licensed under Apache 2.0 and builds on Scrapling and Pydantic. Performance and accuracy comparisons will be published only when a reproducible benchmark exists.
