# Web data, with evidence

![Rastro — the open source web data engine](assets/rastro-banner.svg)

Rastro converts supplied HTML into validated Python records and explains where each observed field came from. Its long-term goal is reliable, continuously updated web datasets.

**Status: pre-alpha; first v0.1 milestone.** JSON-LD and CSS extraction work offline today. Web acquisition, persistent recipes, and optional local AI are still being built. There is no published package release from this project yet.

## Start here

- [Run the offline example](getting-started.md).
- [Understand results, evidence, and limits](api.md).
- [See the architecture and tradeoffs](architecture.md).
- [Follow the milestone plan](roadmap.md).

## What makes the foundation useful?

A Pydantic schema defines what the caller accepts. A recipe records where fields should be found. Rastro validates each candidate separately, preserves accepted records, and reports missing, ambiguous, and invalid inputs without disguising them as successful empty data.

Evidence records a source URL, observation time, locator, method, and recipe version. It describes the origin of a value; it does not independently establish that the source is truthful or that the entire dataset is complete.

## Project resources

[Source code](https://github.com/rave-vipx/rastro) · [Contributing](https://github.com/rave-vipx/rastro/blob/main/CONTRIBUTING.md) · [Security policy](https://github.com/rave-vipx/rastro/blob/main/SECURITY.md) · [Master specification](https://github.com/rave-vipx/rastro/blob/main/RASTRO_MASTER_SPEC.md)

Rastro is licensed under Apache 2.0 and builds on Scrapling and Pydantic. Performance and accuracy comparisons will be published only when a reproducible benchmark exists.
