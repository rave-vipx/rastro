# RASTRO — Master Product & Engineering Specification

**Document:** `RASTRO_MASTER_SPEC.md`  
**Status:** Foundational Specification  
**Version:** 1.1 (2026-10-30 amendments: see Amendment Log)  
**Project:** Rastro  
**Category:** Open Source Web Data Intelligence Platform  
**Primary Language:** English  
**Implementation Language:** Python  
**License:** Apache License 2.0 (subject to dependency and trademark review)

---

# 00. Document Control

## Purpose

This document is the authoritative specification for the design, development, testing, and evolution of Rastro.

It establishes:

- Product vision, mission, motivation, and differentiation.
- Functional and non-functional requirements.
- Approved architecture and technology stack.
- Engineering principles and quality standards.
- Implementation priorities and release boundaries.
- Acceptance criteria and measurable product outcomes.
- Instructions for AI coding agents and human contributors.

Rastro must be developed as a real, reusable, production-oriented open source project.

It must not contain domain-specific business assumptions or depend on any private application.

## Requirement Vocabulary

Each requirement is assigned a unique identifier and a priority:

| Priority | Meaning |
|---|---|
| MUST | Required for the indicated release |
| SHOULD | Expected unless a documented technical limitation exists |
| FUTURE | Planned capability, explicitly excluded from the current release |

Release priorities take precedence over speculative features.

## Implementation Authority

1. This specification defines product intent and approved engineering decisions.
2. Implementation must follow the active release scope.
3. External libraries should be reused where technically appropriate.
4. Architecture changes require documented justification.
5. Automated tests must verify acceptance criteria.
6. No implementation may silently weaken security, data quality, or transparency requirements.

## Amendment Log

| Date | Version | Change | Decision record |
|---|---|---|---|
| 2026-10-30 | 1.1 | Owner decisions after the 0.1.0 release candidate: AI-005 becomes a pre-v0.2 MUST with a documented provider strategy (AI-007); declarative jobs (JOB-*), agent interface (AGENT-*), term-based source discovery (DISC-*) and Scrapling capability parity (SCR-*) are added; v0.2 scope gains the agent interface | [ADR 0012](docs/decisions/0012-product-direction.md) |

---

# 01. Executive Summary

## What Is Rastro?

Rastro is an open source, local-first web data intelligence platform that transforms websites into reliable, structured, continuously synchronized datasets.

Rather than focusing exclusively on web scraping, Rastro manages the complete lifecycle of web-derived information:

**Discover → Acquire → Understand → Extract → Verify → Remember → Synchronize → Monitor → Improve**

Rastro combines deterministic extraction, persistent recipes, optional local AI, data validation, historical synchronization, and operational observability.

Its objective is to significantly reduce the engineering effort required to build and maintain reliable web data pipelines.

## Product Positioning

**Rastro — The Open Source Web Data Engine**

*Turn websites into reliable, continuously updated structured data.*

## Core Promise

Define the data you need, identify its sources, and configure its freshness requirements.

Rastro learns how to obtain that data, validates its quality, remembers successful extraction strategies, detects degradation, and maintains synchronization over time.

Rastro must remain fully usable without mandatory cloud services or paid AI providers.

---

# 02. Vision, Mission & Motivation

## Vision

Make reliable web data accessible to any developer, team, or organization without requiring them to build and continuously maintain custom scraping infrastructure.

## Mission

Build an open, intelligent, verifiable, and extensible system capable of acquiring structured information from permitted web sources and keeping it synchronized with minimal manual intervention.

## Motivation

Traditional scraping systems often require repeated engineering effort to:

- Write and maintain selectors.
- Handle dynamic websites.
- Recover from structural changes.
- Validate extracted values.
- Detect silent data corruption.
- Schedule recurring ingestion.
- Manage historical records.
- Diagnose partial failures.
- Handle geographic variation.
- Maintain operational monitoring.

Modern AI tools improve extraction flexibility but can introduce recurring model costs, latency, hallucinations, and non-deterministic outputs.

Rastro aims to combine the predictability of deterministic software with the adaptability of modern AI.

The system should learn when learning is necessary and execute deterministically whenever possible.

## Long-Term Ambition

Rastro should become the infrastructure layer between changing websites and reliable application databases.

Its value must derive from reducing ongoing operational complexity, not simply offering another way to fetch HTML.

---

# 03. Market Problem & Opportunity

## Primary Problem

Obtaining web data is increasingly accessible.

Maintaining correct, fresh, and traceable web data over time remains difficult.

## Core Market Gaps

### Fragmented Workflows

Developers must combine crawling, parsing, validation, storage, scheduling, monitoring, and alerting libraries.

### Fragile Extraction Logic

Structural website changes can invalidate selectors or silently redirect extraction to incorrect elements.

### Insufficient Semantic Validation

A syntactically valid JSON object can still contain incorrect values.

### Repeated Discovery Costs

Extraction strategies are frequently rediscovered instead of reused.

### Lack of Operational Visibility

Most scraping projects do not expose comprehensive information about source health, freshness, processing latency, extraction quality, and degradation.

### High Integration Cost for Non-Technical Users

Even mature scraping frameworks require programming knowledge to configure reliable recurring pipelines.

## Strategic Opportunity

Provide an integrated, open source data lifecycle platform where these capabilities work together through a unified engine, public API, and visual interface.

---

# 04. Target Users

## Primary Persona: Python Developer

Needs structured web data without repeatedly implementing extraction and recovery logic.

Values:

- Typed API.
- Predictable behavior.
- Extensibility.
- Low overhead.
- Tests.
- Local execution.
- Documentation.

## Secondary Persona: Data Engineer

Maintains recurring ingestion pipelines from multiple sources.

Values:

- Concurrency.
- Data lineage.
- Incremental synchronization.
- Historical tracking.
- Alerts.
- Operational metrics.
- Database integrations.

## Third Persona: Technical Product Operator

Needs to create and supervise data sources without writing Python for every configuration change.

Values:

- Visual configuration.
- Real-time status.
- Error diagnosis.
- Manual intervention controls.
- Clear data previews.
- Reliable notifications.

## Future Persona: Community Contributor

Creates, validates, publishes, and maintains reusable extraction recipes.

---

# 05. Value Proposition & Differentiation

Rastro must provide measurable advantages over implementing equivalent features manually with existing scraping frameworks.

## Competitive Strategy

Rastro does not initially compete by implementing a faster HTTP client, a new browser engine, or another generic web crawler.

It competes through the integration of:

1. Schema-first extraction.
2. Persistent extraction intelligence.
3. Evidence-based data verification.
4. Continuous synchronization.
5. Automated regression testing.
6. Structural degradation monitoring.
7. Adaptive execution planning.
8. Unified programmatic and visual operations.
9. Community-distributed extraction knowledge.

## Relationship with Scrapling

Scrapling is the preferred initial acquisition and crawling engine.

Rastro must use it through an adapter rather than expose its implementation details throughout the architecture.

Scrapling provides capabilities such as:

- HTTP acquisition.
- Browser rendering.
- DOM parsing.
- Adaptive element selection.
- Concurrent crawling.
- Session management.
- Crawling checkpoints.

Rastro owns the higher-level responsibilities:

- Defining extraction contracts.
- Managing complete recipes.
- Validating semantic correctness.
- Tracking provenance.
- Maintaining dataset state.
- Monitoring source degradation.
- Evaluating repairs.
- Selecting extraction strategies.
- Operating synchronization pipelines.

The project must not reimplement Scrapling capabilities without benchmark evidence and a documented need.

### SCR-001 — MUST — pre-v0.2

Rastro must preserve access to Scrapling's capabilities rather than hide them: adopters can supply or obtain Scrapling parser/selector objects through the adapter boundary, and Rastro's additions (contracts, validation, provenance, memory, monitoring) wrap rather than replace them.

### SCR-002 — SHOULD — v0.2

Use Scrapling's adaptive element relocation as a deterministic source of repair candidates (REPAIR-001/002), evaluated like any other candidate before activation.

### SCR-003 — MUST

Scrapling features whose purpose is to evade access restrictions or bot defenses must not be exposed or enabled by Rastro (SEC-003, GEO-005). Browser fetchers may be adopted as a future browser adapter under the same acquisition policies.

## Differentiation Requirement

**DIFF-001 — MUST**

Rastro must demonstrate meaningful improvements against a competent Scrapling-based reference implementation.

Evaluation dimensions include:

- Developer integration effort.
- Extraction accuracy.
- Silent error detection.
- Maintenance effort.
- AI inference usage.
- Recovery effectiveness.
- End-to-end latency.
- Operational visibility.

Unsupported claims of superior performance must not appear in documentation.

---

# 06. Product Principles

## P-001 — Local First

All essential capabilities must work on local hardware.

Commercial cloud services are optional.

## P-002 — AI Optional

Users must be able to extract data without installing a model.

## P-003 — Deterministic by Default

Existing verified recipes should execute without recurring model inference.

## P-004 — Evidence Over Confidence

Every extracted field should be traceable to supporting source evidence.

Model-generated confidence scores alone must never establish correctness.

## P-005 — Fail Visibly

Invalid, suspicious, missing, and blocked data must be reported explicitly.

Never silently treat an extraction failure as an empty valid dataset.

## P-006 — Modular Architecture

Acquisition, intelligence, validation, synchronization, and presentation must have clearly defined boundaries.

## P-007 — No Mandatory Platform Lock-In

Rastro must remain useful as a Python package without Studio or hosted services.

## P-008 — Visual and Programmatic Parity

Studio, CLI, SDK, and API must use the same underlying application services.

## P-009 — Security by Default

Scraping permissions, rate limits, credential boundaries, and untrusted content must be handled explicitly.

## P-010 — Measure Before Optimizing

Performance, reliability, and repair claims require reproducible benchmarks.

---

# 07. Functional Requirements

## 7.1 Schema-First Extraction

### INT-001 — MUST — v0.1

Users must define target data structures using Pydantic models.

Rastro must accept:

- One URL.
- Multiple URLs.
- Explicit input sources.
- A target schema.
- Acquisition configuration.
- Optional extraction strategy preferences.

### INT-002 — MUST — v0.1

The extraction pipeline must attempt deterministic sources before invoking a model:

1. Existing verified recipe.
2. Structured metadata such as JSON-LD.
3. Permitted structured responses and APIs.
4. DOM heuristics and supported selectors.
5. Optional AI-assisted recipe generation.

This order is a default policy, not a requirement to fetch redundant representations when a cheaper valid source is available.

### INT-003 — MUST — v0.1

The result contract must distinguish:

- Success.
- Partial success.
- Missing data.
- Invalid data.
- Access restriction.
- Transient failure.
- Permanent failure.
- Insufficient confidence or evidence.

### INT-004 — MUST — v0.1

Results must contain field-level provenance where available.

---

## 7.2 Persistent Recipe Intelligence

### REC-001 — MUST — v0.1

Rastro must persist extraction recipes by:

- Source identity.
- Page/template identity.
- Extraction schema fingerprint.
- Relevant locale and regional context.
- Recipe version.

### REC-002 — MUST — v0.1

Recipes must be declarative and serializable.

No arbitrary executable Python code is permitted inside imported recipe definitions.

### REC-003 — MUST — v0.1

Recipes should contain:

- Extraction strategy.
- Selector or structured-data path.
- Fallback candidates.
- Field mappings.
- Validators.
- Version.
- Creation timestamp.
- Last successful execution.
- Health information.
- Compatible schema fingerprint.

### REC-004 — MUST — v0.1

AI-generated recipes must be validated before becoming active.

### REC-005 — SHOULD — v0.2

Recipes must support candidate, active, degraded, and retired lifecycle states.

### REC-006 — SHOULD — v0.2

A failed candidate repair must not replace a known working recipe.

---

## 7.3 AI-Assisted Intelligence

### AI-001 — MUST — v0.1

Support optional local inference through Ollama.

### AI-002 — MUST — v0.1

Use structured, validated model outputs.

The model should identify relevant fields or DOM nodes rather than directly control network access or execute arbitrary code.

### AI-003 — MUST — v0.1

Provide strict execution budgets:

- Maximum inference calls.
- Maximum repair attempts.
- Timeout.
- Context-size limit.
- Maximum concurrent model requests.

### AI-004 — MUST — v0.1

Untrusted webpage text must never be treated as operational instructions.

### AI-005 — MUST — pre-v0.2 (0.1.x)

Support additional local or OpenAI-compatible inference endpoints through provider adapters.

Remote (non-loopback) providers require explicit opt-in because page excerpts leave the machine. Credentials are referenced from the environment or a secret provider (SEC-011) and never persisted.

### AI-007 — MUST — pre-v0.2 (0.1.x)

Model access follows a provider strategy: one small, documented interface that any adopter can implement for another runtime or API (for example a hosted API, a different local runtime, or an organization gateway) without changing core code. Every provider is subject to the same budgets (AI-003), structured-output validation (AI-002) and untrusted-content boundary (AI-004).

### AI-006 — FUTURE

Evaluate small decision-oriented models, including Kev, for specific classification and routing tasks.

Adopt only when benchmark evidence demonstrates a practical advantage.

---

## 7.4 Verifiable Data Extraction

### QUAL-001 — MUST — v0.1

Validate extracted records using Pydantic.

### QUAL-002 — MUST — v0.1

Support field-level validation rules.

Examples:

- Required values.
- Numerical ranges.
- Date formats.
- Currency codes.
- URL validity.
- Enumerated values.
- Custom application validators.

### QUAL-003 — MUST — v0.1

Store extraction evidence for relevant fields.

Evidence may include:

- Source URL.
- Observed timestamp.
- Selector or data path.
- Safe source excerpt.
- Extraction method.
- Recipe version.

### QUAL-004 — MUST — v0.1

Validation failure must not be converted into a successful empty record.

### QUAL-005 — SHOULD — v0.2

Detect silent semantic degradation through historical consistency checks and source evidence.

### QUAL-006 — SHOULD — v0.2

Separate data completeness, structural validity, and semantic confidence into distinct indicators.

---

## 7.5 Instant Mock Lab

### MOCK-001 — MUST — v0.2

Generate reproducible fixtures from successful extraction sessions.

Supported outputs:

- Sanitized HTML fixtures.
- JSON response fixtures.
- Expected extraction results.
- Schema-valid synthetic records.

### MOCK-002 — MUST — v0.2

Generate controlled structural mutations:

- DOM element reordering.
- Class changes.
- Element wrapping.
- Missing fields.
- Duplicate elements.
- Empty containers.
- Changed labels.
- Modified pagination.

### MOCK-003 — MUST — v0.2

Generate representative failure scenarios:

- HTTP 403.
- HTTP 429.
- Timeout.
- Empty page.
- Invalid structured response.
- Missing required fields.

### MOCK-004 — MUST — v0.2

Synthetic fixtures must be clearly labeled.

### MOCK-005 — MUST — v0.2

Never automatically expose credentials, cookies, tokens, personal data, or confidential page contents in generated test artifacts.

### MOCK-006 — SHOULD — v0.2

Support deterministic random seeds and reproducible mutation configurations.

---

## 7.6 Recipe CI & Controlled Self-Repair

### REPAIR-001 — MUST — v0.2

When a recipe degrades, Rastro must create a repair candidate without immediately replacing the active recipe.

### REPAIR-002 — MUST — v0.2

Candidates must be evaluated against:

- Historical fixtures.
- Current source evidence.
- Schema validators.
- Field consistency rules.
- Generated regression tests.

### REPAIR-003 — MUST — v0.2

The system must provide a machine-readable comparison between recipe versions.

### REPAIR-004 — MUST — v0.2

Automatic promotion is permitted only when configured validation thresholds are satisfied.

### REPAIR-005 — MUST — v0.2

Ambiguous repairs require explicit approval or remain quarantined.

### REPAIR-006 — SHOULD — v0.2

Support rollback to a previously validated recipe.

---

## 7.7 Structural Drift Intelligence

### DRIFT-001 — MUST — v0.2

Track structural indicators across successful executions.

Candidate signals:

- Selector match count.
- Selector fallback frequency.
- DOM path stability.
- Relevant subtree fingerprint.
- Field coverage.
- Attribute stability.
- Schema distribution changes.
- Extraction validation failure rate.

### DRIFT-002 — MUST — v0.2

Produce a degradation risk indicator derived from observable measurements.

### DRIFT-003 — MUST — v0.2

Differentiate structural drift from actual extraction failure.

### DRIFT-004 — MUST — v0.2

Generate early warnings when degradation thresholds are exceeded.

### DRIFT-005 — MUST — v0.2

Risk scoring must be deterministic and explainable initially.

### DRIFT-006 — FUTURE

Evaluate predictive models trained on sufficient historical failure data.

Do not claim that structural changes can always be predicted before they occur.

---

## 7.8 Continuous Synchronization

### SYNC-001 — MUST — v0.3

Support persistent source configurations and recurring synchronization jobs.

### SYNC-002 — MUST — v0.3

Detect record lifecycle changes:

- Created.
- Updated.
- Unchanged.
- Missing from a successful complete snapshot.
- Uncertain due to incomplete acquisition.

### SYNC-003 — MUST — v0.3

Maintain configurable record identity and deduplication rules.

### SYNC-004 — MUST — v0.3

Support incremental updates where the source makes them possible.

### SYNC-005 — MUST — v0.3

Persist historical versions or change events according to the configured retention policy.

### SYNC-006 — MUST — v0.3

A failed or incomplete crawl must not automatically delete records that were previously present.

### SYNC-007 — SHOULD — v0.3

Support adaptive freshness policies based on observed change frequency and configured minimum/maximum intervals.

---

## 7.9 Recurrence & Scheduling

### SCHED-001 — MUST — v0.3

Each source must support an independent execution schedule.

### SCHED-002 — MUST — v0.3

Support externally triggered execution and an optional persistent scheduler.

### SCHED-003 — MUST — v0.3

Prevent unintended overlapping executions of the same source.

### SCHED-004 — MUST — v0.3

Support checkpoints and job recovery.

### SCHED-005 — SHOULD — v0.3

Use conditional HTTP requests, cached results, and content fingerprints where supported.

### SCHED-006 — SHOULD — v0.3

Apply bounded scheduling jitter to avoid synchronized bursts.

---

## 7.10 Regional Awareness

### GEO-001 — MUST — v0.1

Support explicit:

- Locale.
- Language preferences.
- Time zone.
- Browser geolocation when applicable.
- Currency interpretation.
- Network egress configuration.

### GEO-002 — MUST — v0.1

Distinguish browser geolocation from actual network origin.

### GEO-003 — MUST — v0.1

Avoid sharing incompatible caches and extraction recipes across regional contexts.

### GEO-004 — SHOULD — v0.3

Expose regional differences in extraction history and source monitoring.

### GEO-005 — MUST

Region-specific access must respect applicable permissions and source restrictions.

Rastro must not automatically rotate network identities to circumvent access denials.

---

## 7.11 Source Monitoring & Performance

### OBS-001 — MUST — v0.1

Record per-execution operational metrics.

Minimum dimensions:

- Source.
- Domain.
- URL.
- Execution identifier.
- Start time.
- Completion time.
- HTTP status where available.
- Acquisition method.
- Acquisition duration.
- Browser rendering duration.
- Extraction duration.
- Validation duration.
- Model inference duration.
- Total processing duration.
- Retry count.
- Bytes transferred where measurable.
- Records produced.
- Fields validated.
- Success or failure status.

### OBS-002 — MUST — v0.1

Maintain structured JSON logs with correlation identifiers.

### OBS-003 — MUST — v0.3

Expose aggregate metrics by:

- Domain.
- URL.
- Source.
- Execution.
- Time interval.
- Extraction strategy.

### OBS-004 — MUST — v0.3

Calculate throughput and latency distributions.

Examples:

- Pages per minute.
- Records per minute.
- Successful requests.
- Average duration.
- p50/p95 latency.
- Error rate.
- Retry rate.
- Browser utilization where available.
- AI usage frequency.
- Data freshness.

### OBS-005 — MUST — v0.3

Distinguish remote website latency from local processing latency.

### OBS-006 — SHOULD — v0.3

Provide historical performance trends.

### OBS-007 — MUST — v0.3

Track last successful extraction separately from last attempted extraction.

---

## 7.12 Alerts & Incident Management

### ALERT-001 — MUST — v0.3

Support source-level alerts for:

- Repeated acquisition failure.
- HTTP blocking.
- Invalid extracted data.
- Source freshness threshold exceeded.
- Significant structural degradation.
- Persistent low extraction coverage.
- Incomplete synchronization jobs.

### ALERT-002 — MUST — v0.3

Support outbound webhooks.

### ALERT-003 — SHOULD — v0.3

Provide notification adapters for Discord, Slack, and email.

### ALERT-004 — MUST — v0.3

Implement deduplication, cooldowns, and recovery notifications to prevent alert flooding.

### ALERT-005 — MUST — v0.3

Store alert history and current incident status.

### ALERT-006 — MUST — v0.3

Distinguish:

- No new data.
- No matching records.
- Source not reachable.
- Extraction failed.
- Data rejected by validation.

These conditions must never be collapsed into a generic empty result.

---

## 7.13 Declarative Jobs

### JOB-001 — MUST — pre-v0.2

Users can define an extraction job as data — sources (URLs or supplied files), target fields (name, type, required, constraints), acquisition policy, regional context and optional recipes — in a declarative file (TOML or JSON) and run it without writing Python.

### JOB-002 — MUST — pre-v0.2

Job definitions are validated, versioned, schema-described data. They cannot execute code, embed credentials (only environment references), or bypass acquisition policies. The SDK, CLI, agent interface and Studio all load the same job format through the same application services (P-008).

### JOB-003 — MUST — v0.4

Studio lets non-technical users create and edit jobs — sites or research terms, and the data wanted from them — producing the same declarative job format.

## 7.14 Source Discovery by Research Terms

### DISC-001 — SHOULD — v0.3

A search-provider strategy can turn research terms into candidate source URLs. Providers are optional adapters; no paid provider is required for core functionality.

### DISC-002 — MUST — v0.3

Discovered hosts are proposals only. They require explicit user approval before entering an acquisition allowlist, and acquisition of approved hosts follows every existing policy (robots, pacing, destination checks). Discovery never crawls beyond approved hosts.

## 7.15 Agent Interface

### AGENT-001 — MUST — pre-v0.2

Machine-readable interfaces: JSON input and output for jobs and results, with published JSON Schemas, so AI agents and scripts can use Rastro without parsing human text.

### AGENT-002 — SHOULD — v0.2

Provide a Model Context Protocol (MCP) server adapter exposing job validation, job execution and result/recipe inspection over the same application services and policies.

### AGENT-003 — MUST

Content returned to calling agents is labelled as untrusted source data with provenance; it must never be presented as instructions. Agent tools cannot widen allowlists, read credentials or disable validation.

---

# 08. Rastro Studio

## Product Definition

Rastro Studio is the visual control plane for the Rastro engine.

It must allow both technical and non-technical users to configure, observe, and maintain extraction pipelines.

Studio is not a separate extraction engine.

All operations must use the same application services as the Python SDK and CLI.

## Release Boundary

Studio is introduced in v0.4.

Earlier releases may expose CLI reports and structured API endpoints without delivering the complete visual interface.

## 8.1 Dashboard

### STUDIO-001 — MUST — v0.4

Provide a central operational dashboard containing:

- Active sources.
- Healthy sources.
- Degraded sources.
- Failed sources.
- Total synchronized records.
- Recent updates.
- Pending jobs.
- Current incidents.
- Recent throughput and latency trends.

## 8.2 Sources & Performance

### STUDIO-002 — MUST — v0.4

Display sources grouped by domain and URL.

Each detail view must show:

- Number of records recovered.
- Number of valid records.
- Number of rejected records.
- Last successful execution.
- Next scheduled execution.
- Response latency.
- Extraction latency.
- Total duration.
- Throughput.
- Retry count.
- Historical success rate.
- Structural health.
- Regional execution context.

This section must help users identify slow websites, inefficient recipes, and local processing bottlenecks.

## 8.3 Data Explorer

### STUDIO-003 — MUST — v0.4

Provide a structured view of synchronized records.

Supported interactions:

- Search and filtering.
- Field inspection.
- Record history.
- Source provenance.
- Last observation.
- Validation status.
- Change history.

## 8.4 Visual Selector

### STUDIO-004 — MUST — v0.4

Allow users to select webpage elements and map them to target schema fields.

Required workflow:

1. Open a permitted target URL.
2. Inspect the rendered or captured page.
3. Select an element.
4. Assign a schema field.
5. Preview extracted values.
6. Review the proposed selector.
7. Validate against the current page and fixtures.
8. Save a recipe candidate.
9. Publish after successful validation.

The visual selector must not bypass Recipe CI requirements.

## 8.5 Errors & Alerts

### STUDIO-005 — MUST — v0.4

Display incidents with:

- Severity.
- Affected sources.
- Root-cause category.
- Timestamp.
- Evidence.
- Retry history.
- Suggested actions.
- Resolution status.

## 8.6 Mock Lab Interface

### STUDIO-006 — SHOULD — v0.4

Allow users to generate and run extraction test scenarios visually.

## 8.7 Recipe Inspector

### STUDIO-007 — MUST — v0.4

Expose recipe versions, field mappings, validation rules, health indicators, and repair comparisons.

## 8.8 Real-Time Updates

### STUDIO-008 — MUST — v0.4

Use Server-Sent Events for operational status streaming.

Polling may be used for non-real-time historical views.

## Studio UX Principles

- Clear distinction between healthy, degraded, and failed states.
- No essential information conveyed only through color.
- Progressive disclosure of technical complexity.
- Technical configurations remain inspectable.
- Every risky operation requires appropriate confirmation.
- No hidden changes to active recipes.
- Responsive layout and accessible controls.
- The interface should remain practical on a single local machine.

---

# 09. Rastro Community Hub

## HUB-001 — FUTURE — v0.5

Provide a community registry for reusable recipe packages.

## HUB-002 — FUTURE — v0.5

Recipes must contain:

- Recipe identifier.
- Version.
- Compatible source patterns.
- Data schema.
- Declarative extraction rules.
- Validation requirements.
- Test fixtures.
- Author information.
- License.
- Compatibility metadata.

## HUB-003 — FUTURE — v0.5

Support publishing, importing, updating, and validating recipe packages.

## HUB-004 — FUTURE — v0.5

Private recipes must never be published automatically.

## HUB-005 — FUTURE — v0.5

Imported packages must not execute arbitrary code.

## HUB-006 — FUTURE — v0.5

Provide basic trust indicators based on reproducible tests, not popularity alone.

## Hub Rollout Strategy

Phase one: local recipe import/export.

Phase two: Git-based community registry.

Phase three: searchable hosted catalog, if adoption justifies operating it.

A hosted Hub is not required for the local Rastro engine.

---

# 10. System Architecture

## Architectural Style

Use a modular monolith with Hexagonal Architecture principles.

Prefer small, explicit interfaces over unnecessary abstraction layers.

## Logical Architecture

```text
                        CLIENTS
              ┌────────────────────────┐
              │ Python SDK             │
              │ CLI                    │
              │ REST API               │
              │ Rastro Studio          │
              └────────────┬───────────┘
                           │
                 APPLICATION LAYER
              ┌────────────▼───────────┐
              │ Source Management      │
              │ Extraction Orchestrator│
              │ Recipe Lifecycle       │
              │ Sync Coordinator       │
              │ Monitoring Services    │
              └────────────┬───────────┘
                           │
                     CORE DOMAIN
        ┌──────────────────┼────────────────────┐
        │                  │                    │
  Intelligence         Quality              Memory
  - Planner            - Validators         - Recipes
  - Compiler           - Evidence           - State
  - Classification     - Drift              - History
  - Repair             - Regression         - Metrics
        │                  │                    │
        └──────────────────┼────────────────────┘
                           │
                         PORTS
              ┌────────────▼───────────┐
              │ AcquisitionPort        │
              │ ModelPort              │
              │ RecipeRepository       │
              │ JobRepository          │
              │ DataSink               │
              │ NotificationPort       │
              └────────────┬───────────┘
                           │
                       ADAPTERS
      ┌────────────────────┼────────────────────┐
      │                    │                    │
   Scrapling          Ollama / AI         SQLite / SQL
   HTTP APIs          Model adapters      Notifications
   Browser            Future models       External sinks
```

## Dependency Rule

The domain layer must not import:

- Scrapling.
- FastAPI.
- React-specific interfaces.
- Database drivers.
- Ollama-specific clients.

Concrete integrations belong in adapters.

---

# 11. Approved Technology Stack

| Responsibility | Technology | Status |
|---|---|---|
| Primary language | Python 3.12+ | Required |
| Build and dependencies | uv + Hatchling | Required |
| Acquisition/crawling | Scrapling | Primary adapter |
| Schema validation | Pydantic v2 | Required |
| Local inference | Ollama | Optional |
| Initial local model | Qwen family, configurable | Optional |
| Local state persistence | SQLite | Required |
| Advanced database | PostgreSQL | Later adapter |
| HTTP API | FastAPI | Studio release |
| Frontend | React + TypeScript + Vite | Studio release |
| UI styling | Tailwind CSS | Studio release |
| Data visualization | Recharts | Studio release |
| Scheduling | APScheduler | Sync release |
| Logging | structlog | Required |
| Metrics instrumentation | OpenTelemetry | Optional instrumentation |
| Test framework | pytest | Required |
| Async testing | pytest-asyncio | Required |
| Property-based testing | Hypothesis | Required |
| Synthetic data | Polyfactory | Mock Lab |
| Linting and formatting | Ruff | Required |
| Type checking | mypy strict | Required |
| Security audit | pip-audit | Required |
| CI | GitHub Actions | Required |
| Documentation | MkDocs Material | Required |
| Packaging | PyPI | Release delivery |
| Container deployment | Docker / Compose | Later delivery |

## Dependency Strategy

- Keep the core installation lightweight.
- Use optional dependency groups for browser, AI, server, Studio backend, and integrations where feasible.
- Pin or constrain compatible dependency versions.
- Avoid redundant crawling libraries.
- Avoid redundant AI orchestration frameworks.
- Prefer Python standard library functionality when sufficient.
- Audit transitive licenses and vulnerabilities.

## Tools Explicitly Not Required for Core

- Scrapy.
- Crawlee.
- Crawl4AI.
- Firecrawl.
- REA.
- Kev.
- Redis.
- Kubernetes.
- Distributed message brokers.
- Mandatory commercial LLM APIs.

They may be evaluated as future integrations only with a concrete documented benefit.

---

# 12. Technical Workflows

## 12.1 First-Time Extraction

```text
Input URL + Schema + Context
           │
           ▼
       Policy Check
           │
           ▼
    Acquisition Adapter
           │
           ▼
  Structured Data Discovery
           │
           ▼
   Deterministic Extraction
           │
     ┌─────┴─────┐
     │           │
  Verified     Insufficient
     │           │
     │           ▼
     │      Optional AI
     │           │
     │           ▼
     │     Candidate Recipe
     │           │
     └─────┬─────┘
           ▼
     Field Validation
           │
           ▼
   Evidence + Provenance
           │
           ▼
      Persist Result
```

## 12.2 Repeated Extraction

```text
Scheduled/Manual Trigger
          │
          ▼
      Fetch Source
          │
          ▼
   Load Active Recipe
          │
          ▼
 Deterministic Extraction
          │
          ▼
    Quality Validation
          │
          ▼
  Accept / Repair / Reject
          │
          ▼
    Update Job History
```

## 12.3 Repair Workflow

```text
Quality Degradation Detected
              │
              ▼
        Collect Evidence
              │
              ▼
      Generate Candidate
              │
              ▼
      Generate Test Cases
              │
              ▼
      Execute Regression
              │
        ┌─────┴─────┐
        │           │
      Pass         Fail
        │           │
        ▼           ▼
    Promotion    Quarantine
    Policy       + Alert
        │
        ▼
  Activate Version
  or Require Review
```

## 12.4 Synchronization Workflow

```text
Source Schedule
       │
       ▼
  Acquire Pages
       │
       ▼
 Extract Records
       │
       ▼
 Validate Snapshot
       │
       ▼
 Resolve Identity
       │
       ▼
 Compare Previous State
       │
       ▼
 Generate Change Events
       │
       ▼
 Persist + Notify
```

## 12.5 High-Volume Acquisition

Rastro must use bounded concurrency.

Requirements:

- Concurrent crawling through Scrapling.
- Domain-level rate limits.
- Global concurrency limits.
- Bounded memory usage.
- Configurable timeouts.
- Backpressure between acquisition and processing.
- Independent limits for local model inference.
- Checkpoint-based recovery.
- Explicit failed-job accounting.

A single source must not monopolize the execution pool.

## 12.6 Access Restriction Handling

On access denial or rate limiting:

1. Classify the restriction.
2. Stop or defer affected requests.
3. Respect `Retry-After` when present.
4. Apply bounded backoff where permitted.
5. Store safe diagnostic evidence.
6. Update source health.
7. Notify according to alert policy.

Rastro must not promise CAPTCHA bypass, invisibility, or unrestricted access.

---

# 13. Core Data Models

## Source

Represents a configured data origin.

Fields include:

- ID.
- Name.
- URL patterns.
- Domain.
- Schema reference.
- Regional context.
- Acquisition policy.
- Schedule.
- Status.
- Created and updated timestamps.

## Recipe

Represents a reusable extraction strategy.

Fields include:

- ID.
- Source reference.
- Schema fingerprint.
- Page/template fingerprint.
- Version.
- Extraction methods.
- Field mappings.
- Fallbacks.
- Validation rules.
- Lifecycle state.
- Health metrics.

## ExtractionJob

Represents a single execution.

Fields include:

- Job ID.
- Source ID.
- Requested URLs.
- Started/completed timestamps.
- Status.
- Counters.
- Failure classification.
- Context.
- Performance metrics.

## ExtractionRecord

Represents a structured observation.

Fields include:

- Record ID.
- Source identity.
- Extracted values.
- Field evidence.
- Schema version.
- Recipe version.
- Observation timestamp.
- Validation status.

## ChangeEvent

Represents a change between accepted observations.

Fields include:

- Event ID.
- Record identity.
- Change type.
- Previous state reference.
- Current state reference.
- Detection timestamp.

## Incident

Represents an operational or quality problem.

Fields include:

- Incident ID.
- Source.
- Severity.
- Failure type.
- Evidence references.
- Detection timestamp.
- Current status.
- Resolution timestamp.

---

# 14. Public Developer Experience

## Design Target

The Python API should make simple use cases simple while preserving explicit configuration for advanced workloads.

Illustrative target:

```python
from pydantic import BaseModel
from rastro import Rastro, Source

class Product(BaseModel):
    name: str
    price: float | None = None
    currency: str | None = None

rastro = Rastro.local(
    database="sqlite:///rastro.db",
    model="ollama:qwen",
)

source = Source(
    url="https://example.com/products",
    locale="es-UY",
)

results = await rastro.extract(
    source=source,
    schema=Product,
)
```

This API is a design target, not an existing implementation.

## CLI Design Target

```bash
rastro extract https://example.com --schema product.json
rastro sources list
rastro recipes list
rastro jobs list
rastro jobs inspect JOB_ID
rastro doctor
```

Later releases:

```bash
rastro watch
rastro studio
rastro mock generate
rastro recipes test
rastro hub search
```

Every CLI command must provide predictable exit codes and machine-readable output options.

---

# 15. Engineering Standards

## Architecture

- Apply SOLID principles where useful.
- Use explicit dependency inversion.
- Prefer composition over inheritance.
- Keep domain logic independent from infrastructure.
- Do not introduce abstractions without a practical use case.
- Prefer a modular monolith over premature microservices.

## Design Patterns

Use only where appropriate:

- Strategy: extraction and acquisition selection.
- Adapter: external tool integration.
- Repository: persistence.
- State Machine: job and recipe lifecycle.
- Circuit Breaker: repeated failures.
- Chain of Responsibility: ordered extraction strategies.
- Observer/Event Publisher: telemetry and synchronization events.

## Python Quality

- Strict static typing.
- Clear public interfaces.
- Small focused modules.
- Immutable value objects where appropriate.
- Explicit domain exceptions.
- No hidden global mutable state.
- No blocking I/O inside asynchronous workflows.
- Resource cleanup through context managers.
- Structured logging.
- No secret leakage.
- Deterministic tests.

## Required Tooling

```text
ruff check
ruff format --check
mypy
pytest
pip-audit
```

## Testing Standards

Include:

- Unit tests.
- Integration tests.
- Contract tests.
- Regression fixtures.
- Property-based tests.
- Asynchronous concurrency tests.
- Failure-injection tests.
- Security boundary tests.

Core tests must run without:

- Internet access.
- API credentials.
- Installed AI models.
- Production databases.

Optional integration suites may require external dependencies.

## Code Review Requirements

Every meaningful feature must document:

1. Requirement identifiers.
2. Architectural changes.
3. Public API changes.
4. Test coverage.
5. Security implications.
6. Performance implications.
7. Known limitations.

---

# 16. Security, Privacy & Responsible Acquisition

## SEC-001 — MUST

Enforce permitted schemes and destination validation to prevent SSRF.

## SEC-002 — MUST

Prevent unauthorized local network, loopback, and metadata endpoint access in server-managed fetching, except where explicitly allowed by trusted administrators.

## SEC-003 — MUST

Respect configured crawling policies and applicable access restrictions.

## SEC-004 — MUST

Never store credentials in recipe packages or diagnostic snapshots.

## SEC-005 — MUST

Treat external HTML, JSON, JavaScript, and model outputs as untrusted.

## SEC-006 — MUST

Sanitize source content before exposing it in Studio.

## SEC-007 — MUST

Do not execute code from community recipes.

## SEC-008 — MUST

Bound memory, browser, request, and model resource consumption.

## SEC-009 — MUST

Provide data retention and deletion controls.

## SEC-010 — MUST

Disable public access to local management interfaces by default.

## SEC-011 — SHOULD

Support explicit secret references from environment variables or managed secret providers.

---

# 17. Evaluation & Benchmarks

## Benchmark Philosophy

Rastro must be evaluated against a competent baseline, not an intentionally fragile implementation.

## Baseline

Use Scrapling with conventional selectors, Pydantic validation, and reasonable custom adaptation.

## Dataset

Create a reproducible benchmark corpus containing:

- Static HTML pages.
- JavaScript-rendered pages.
- Structured-data-rich pages.
- Reordered DOM layouts.
- Renamed classes.
- Missing fields.
- Ambiguous values.
- Broken selectors.
- Regional variations.
- Rate-limited responses.
- Large paginated listings.

Use local snapshots and fixtures for deterministic comparisons.

## Required Metrics

### Extraction Quality

- Field-level precision.
- Field-level recall.
- Complete-record correctness.
- False success rate.
- Silent error detection rate.

### Operational Reliability

- Job success rate.
- Recovery success rate.
- Mean time to detect degradation.
- Mean time to restore extraction.
- Checkpoint recovery correctness.

### Cost & Performance

- Median and p95 latency.
- Pages per minute.
- Records per minute.
- Browser utilization.
- Model calls per successful record.
- Inference time.
- Peak memory consumption.
- Engineering effort for source integration.

### Developer Experience

- Time to first extraction.
- Lines of configuration.
- Time to add a source.
- Time to diagnose a broken source.

## Release Evidence

Every release must publish documented benchmark methodology and results.

Claims of higher accuracy or lower cost must include the tested baseline, environment, and dataset.

---

# 18. Product Roadmap

## Release v0.1 — Rastro Engine

### Objective

Deliver a usable open source Python package for verifiable, schema-first web extraction.

### Scope

- Public repository and package structure.
- Scrapling acquisition adapter.
- Typed Python SDK.
- Basic CLI.
- Pydantic schemas.
- Deterministic extraction.
- Optional Ollama-assisted recipe generation.
- SQLite recipe repository.
- Field provenance.
- Structured logs and basic execution metrics.
- Core security policies.
- Offline tests.
- Documentation.
- Scrapling comparison baseline.

### Acceptance Criteria

- Installs in a clean supported environment.
- Runs without an AI model.
- Can use Ollama when configured.
- Persists and reuses extraction recipes.
- Rejects invalid results explicitly.
- Tracks acquisition and processing timings.
- Passes unit and integration tests.
- Includes a functioning end-to-end example.

---

## Release v0.2 — Rastro Quality

### Objective

Make extraction maintenance measurable, testable, and safer.

### Scope

- MCP agent interface (AGENT-002).
- Scrapling adaptive relocation as a repair-candidate source (SCR-002).
- Instant Mock Lab.
- Structural mutation fixtures.
- Recipe CI.
- Recipe lifecycle and versioning.
- Candidate repair workflow.
- Rollback.
- Structural drift metrics.
- Explainable degradation risk.
- Semantic consistency checks.
- Repair reports.

### Acceptance Criteria

- A broken recipe produces an identifiable degraded state.
- A candidate repair is tested before activation.
- Failed candidates cannot silently replace active recipes.
- Mock generation is reproducible.
- Regression tests demonstrate recovery scenarios.
- Drift indicators have documented calculation methods.

---

## Release v0.3 — Rastro Sync

### Objective

Maintain datasets over time with operational control.

### Scope

- Term-based source discovery with approval (DISC-001/002).
- Recurring source configuration.
- Scheduling.
- Dataset synchronization.
- Record identity and deduplication.
- Historical changes.
- Adaptive freshness rules.
- Job recovery.
- Webhooks.
- Alerts.
- Domain and URL performance aggregation.
- Source-level health and freshness metrics.
- Optional PostgreSQL adapter.

### Acceptance Criteria

- A configured source runs repeatedly.
- Interrupted jobs can be recovered.
- Partial failures do not cause false deletions.
- Changes produce explicit events.
- Alerts distinguish failures from unchanged data.
- Performance metrics are queryable by source and domain.

---

## Release v0.4 — Rastro Studio

### Objective

Make Rastro accessible through a visual operational interface.

### Scope

- Job editor for sites, research terms and wanted data (JOB-003).
- FastAPI management API.
- React application.
- Source dashboard.
- Sources & Performance.
- Data Explorer.
- Visual Selector.
- Recipe Inspector.
- Errors & Alerts.
- Real-time execution updates.
- Mock Lab interface.
- Local deployment package.

### Acceptance Criteria

- Users can configure a source through the interface.
- Users can inspect extraction performance per URL.
- Users can review field provenance.
- Users can see current source health and incidents.
- Users can visually generate and validate a recipe candidate.
- Studio uses the same application services as the SDK.

---

## Release v0.5 — Rastro Hub

### Objective

Enable safe reuse and distribution of community extraction recipes.

### Scope

- Declarative recipe package format.
- Recipe export/import.
- Version compatibility.
- Git-based public recipe registry.
- Automated recipe validation.
- Package metadata and trust indicators.
- Studio integration.

### Acceptance Criteria

- Recipes can be shared without credentials.
- Imported recipes cannot execute arbitrary code.
- Packages contain reproducible tests.
- Local execution does not require a hosted Hub.
- Public and private recipes are explicitly separated.

---

# 19. Delivery Strategy

## Repository Structure Target

```text
rastro/
├── src/
│   └── rastro/
│       ├── domain/
│       ├── application/
│       ├── ports/
│       ├── adapters/
│       │   ├── acquisition/
│       │   ├── intelligence/
│       │   ├── persistence/
│       │   └── notifications/
│       ├── recipes/
│       ├── extraction/
│       ├── quality/
│       ├── synchronization/
│       ├── observability/
│       ├── api/
│       └── cli/
├── studio/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contracts/
│   ├── regression/
│   └── fixtures/
├── benchmarks/
├── examples/
├── docs/
├── .github/
├── pyproject.toml
├── AGENTS.md
├── RASTRO_MASTER_SPEC.md
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
└── README.md
```

Only create directories when their release scope requires them.

Do not generate empty architectural scaffolding solely to match the target tree.

## Iterative Implementation

Each release should be broken into milestones with:

- Defined responsibilities.
- Dependencies.
- Acceptance criteria.
- Tests.
- Documentation updates.

Avoid implementing several major subsystems before verifying the first vertical slice.

---

# 20. Instructions for AI Coding Agents

## Primary Objective

Implement Rastro incrementally according to this specification.

The current active release must be explicitly identified before implementation.

If no release is specified, begin with v0.1.

## Execution Protocol

1. Read this specification.
2. Inspect the current repository.
3. Identify implemented capabilities.
4. Compare the current state against active release requirements.
5. Create a milestone implementation plan.
6. Identify dependencies and technical risks.
7. Implement the smallest viable vertical slice.
8. Add automated tests.
9. Run code quality checks.
10. Validate acceptance criteria.
11. Update relevant documentation.
12. Report progress and remaining requirements.

## Non-Negotiable Rules

- Do not introduce additional frameworks without justification.
- Do not reimplement existing Scrapling functionality unnecessarily.
- Do not require a paid service for local core functionality.
- Do not silently modify public API contracts.
- Do not claim tests passed unless they were executed.
- Do not generate placeholder implementations presented as working features.
- Do not mark requirements complete without evidence.
- Do not implement future releases prematurely.
- Do not weaken access controls to improve scraping success.
- Do not confuse confidence estimates with verified correctness.
- Do not introduce avoidable vendor lock-in.

## Progress Reporting

Every implementation milestone must report:

```text
Milestone:
Release:
Requirements addressed:
Files created or modified:
Tests executed:
Results:
Known limitations:
Architecture decisions:
Next milestone:
```

## Definition of Done

A requirement is complete when:

- Its intended behavior works.
- The implementation follows architecture rules.
- Tests cover expected behavior and relevant failure modes.
- Type checking and linting pass.
- Documentation matches the implementation.
- Security boundaries are respected.
- Acceptance criteria are verified.
- Known limitations are documented.

---

# 21. Risks & Trade-offs

## Risk: Excessive Scope

Rastro includes several potentially independent products.

Mitigation: strict release boundaries and vertical-slice implementation.

## Risk: Scrapling Dependency

A breaking change in the acquisition engine could affect Rastro.

Mitigation: adapter boundary, dependency constraints, integration tests.

## Risk: Incorrect AI-Generated Recipes

Models may propose plausible but incorrect mappings.

Mitigation: deterministic validation, source evidence, regression testing, controlled promotion.

## Risk: False Predictive Alerts

Structural variation does not always imply impending failure.

Mitigation: explainable indicators, measured thresholds, calibration, clear uncertainty labeling.

## Risk: Community Supply Chain

Shared recipes may be malicious, incorrect, or outdated.

Mitigation: declarative format, validation, isolation, provenance, review, no arbitrary executable code.

## Risk: Operational Complexity

A distributed design could overwhelm early maintainers.

Mitigation: modular monolith and local-first architecture.

## Risk: Competitive Convergence

Existing scraping frameworks may implement similar features.

Mitigation: focus on integrated lifecycle value, verification, developer experience, and measurable outcomes.

---

# 22. Explicitly Out of Scope for v0.1

Do not implement:

- Full visual Studio.
- Community Hub.
- Distributed orchestration.
- Hosted cloud platform.
- Multi-tenant billing.
- Proprietary proxy networks.
- CAPTCHA-solving infrastructure.
- General-purpose autonomous browsing agents.
- Custom browser engines.
- Custom HTML parsers.
- Kubernetes deployment.
- Complex ML drift prediction.
- Mandatory external model providers.
- Commercial monetization systems.

These exclusions protect delivery focus.

---

# 23. Strategic Success Criteria

Rastro will be considered successful when developers can use it to maintain structured web datasets with less ongoing engineering effort than a comparable manually integrated solution.

The long-term success criteria are:

1. Low-friction developer onboarding.
2. High-quality structured extraction.
3. Demonstrable reduction in silent extraction errors.
4. Reliable reuse of extraction knowledge.
5. Controlled and measurable self-repair.
6. Efficient recurring synchronization.
7. Clear operational observability.
8. Accessibility through both code and visual configuration.
9. Healthy open source contributor adoption.
10. Sustainable long-term maintainability.

Success must be measured through real usage and reproducible evaluations.

---

# 24. Final Product Invariants

These invariants apply to every release.

1. Rastro must remain open source.
2. Core functionality must not require a paid API.
3. AI must remain optional.
4. Successful extraction must not be inferred merely from the absence of exceptions.
5. Invalid data must not be silently accepted.
6. Repairs must be validated before replacing active recipes.
7. Every execution must have an identifiable outcome.
8. Important extracted values must support provenance.
9. Regional context must be explicit.
10. Source restrictions and access policies must be respected.
11. The public SDK and visual interface must use the same engine.
12. User-controlled recipes must not be shared automatically.
13. Community recipes must not execute arbitrary code.
14. The architecture must remain modular and testable.
15. Technical claims must be supported by evidence.
16. The project must remain usable without Studio or a hosted service.
17. No release may sacrifice data correctness solely to increase throughput.
18. Dependencies must provide demonstrable value.
19. The system must clearly distinguish verified, estimated, missing, and uncertain information.
20. Every release must deliver usable functionality rather than architectural scaffolding alone.

---

# 25. Final Direction

Rastro is not intended to become another generic scraping framework.

It is intended to become an open source platform that connects changing web sources to trustworthy, continuously maintained structured datasets.

Its core differentiation is the combination of:

**Persistent Intelligence + Verifiable Extraction + Controlled Self-Repair + Continuous Synchronization + Operational Visibility.**

The technical architecture must serve this objective.

The product should be simple to start using, powerful enough for production, transparent in its decisions, and accessible to a broader audience through Rastro Studio.

**Build the smallest reliable version first. Prove its value. Expand without compromising its principles.**
