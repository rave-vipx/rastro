# Changelog

All notable user-facing changes are recorded here. The project follows [Semantic Versioning](docs/stability.md) from 0.6.0. Versions before 0.6.0 were never published to a package registry.

## 0.6.0 — 2026-11-12

First public release of `rastro-engine` on PyPI: one-call typed scraping with `rastro.scrape`, selectors suggested from example values, verified self-repair, a complete MCP server for AI agents and a local Studio GUI.

### Added

- `Issue.message` (optional) gives Pydantic's built-in error type and message for `validation_error` issues, never the input value or custom validator text; it is omitted from serialized output when absent, and the job-run and job-load JSON Schemas gain the field (P-005).
- Tag-triggered release workflow: builds the sdist and wheel, checks them with `twine check --strict`, verifies the tag matches the package version, smoke-tests the installed wheel, publishes to PyPI through Trusted Publishing with attestations and creates the GitHub release from this changelog (P-009, Release Evidence).
- CI `package` job installs the built wheel with and without the `http`, `mcp` and `studio` extras and runs the CLI smoke checks (P-006, Release Evidence).
- `docs/stability.md` defines the public surface and the deprecation policy; `docs/ai-agents.md` and a root `llms.txt` describe how AI agents use Rastro (AGENT-001, AGENT-002, AGENT-003).
- Studio is a complete local GUI: a new-scraper wizard (pick a saved page, give example values, review the suggested recipe with per-field confidence, create the job file), Extract (writes nothing) and Load into dataset with recipe origin and version per source, health with stale status and optional drift, repair proposals with an old-versus-new selector diff and gate results, promotion with a recorded reason (overriding failed gates warns and records it), writing the serving recipe back to the job file, and a paginated dataset browser with stale markers and JSON or HTML download. JSON job files are listed next to TOML ones. Every new action is a token-protected, exact-origin POST confined to the Studio folder; file edits carry the file digest. The app is split into per-area routers and the page into dependency-free ES modules, with light and dark themes (P-008, INT-002, JOB-001, DATA-001, DATA-004, REPAIR-002-005, REC-006).
- `rastro.scrape(target, schema, ...)` and `rastro.scrape_async` extract HTML text, a file or a URL into validated Pydantic models in one call; `rastro.css("...")` declares a field's selector on the model, `__records__` or `records=` selects records, and `ExtractionResult.items` lists the validated instances (INT-001, INT-002, REC-003).
- `scrape(..., memory="recipes.db")` remembers the working recipe and a sanitized success sample; when the remembered recipe stops matching, `repair="propose"` (default) stores gated offline repair candidates and `repair="auto"` promotes one that passes every gate and returns its result. `ExtractionResult.repair` describes what happened; it is omitted from serialized output when no repair ran, and `job-run.schema.json` gains the optional field. Before promotion, relocated fields must keep their retained values' type and shape and must not take another field's values (`value_shape_changed`, `value_type_changed`, `value_matches_other_field`); an identical stored candidate is reused instead of adding a version (REC-001, REC-006, REPAIR-001-005, SCR-002).
- `docs/quickstart.md`, `examples/scrape.py` and `examples/self_repair.py` (INT-001).
- `rastro.suggest(html, schema, *, examples, url=None)` builds a verified CSS recipe from a saved page and one or two example values per field, deterministically and without AI; it returns `Suggestion` (`recipe`, `preview`, per-field `confidence`) or raises `SuggestionError` naming the examples it could not reproduce (INT-002, DISC-003, P-003).
- `rastro suggest page.html --example FIELD=VALUE ...` prints the suggested recipe as JSON or a preview table; `rastro new job.toml --from page.html --example ...` writes a complete job file with conservatively typed fields, never overwriting an existing file (JOB-001, DISC-003).
- A `css` recipe field prefixed `expand:` turns a card with several values into one record per value, with per-value provenance and `max_records` enforced; relocation repair refuses it (DATA-004, DATA-005, JOB-002).
- `rastro recipe prune --store FILE --older-than DAYS [--dry-run]` deletes old recipe versions but never a serving one; `prune` always keeps the newest version of each key so version numbers are never reused (REC-005).
- A JSON-LD recipe field may list 1 to 4 JSON pointers; the first that resolves wins and the locator records it; a JSON null falls through to the next pointer (INT-002, DATA-005).
- Public Python API for datasets and repair: `rastro.load_job_file`, `rastro.repair_job_source`, `rastro.promote_job_recipe` and `rastro.apply_job_recipe`, with the `RepairReport`, `SourceRepair` and `RecipeSelection` result types (DATA-001, REPAIR-002–006, REC-006).
- `rastro recipe apply job.toml --source N` writes the serving recipe from `recipe_memory` back into the job file; TOML text outside that recipe's tables is kept, the write is atomic and refuses symlinks (REC-006).
- `rastro recipe promote --override-gates --reason "..."` activates a quarantined candidate; the lifecycle history records `gates overridden: <reason>`. Rejected candidates are never activated (REPAIR-005).
- Job run and load reports state per source whether the job file's recipe or the serving recipe in memory was used (`recipes` / `recipe_origin`, `recipe_version`); the published job-run and job-load JSON Schemas are updated (REC-006).
- `rastro job preview`, Studio preview and the MCP `job_preview` tool work for jobs with `recipe_memory`: the store is opened read-only (never created or upgraded) and a stored serving recipe newer than the job file's recipe is used (DATA-002, REC-001, P-008, AGENT-002).
- `SQLiteRecipeRepository(path, read_only=True)` reads an existing store without creating, migrating or writing it (DATA-002).
- `rastro recipe promote` adopts an imported candidate on a store with nothing serving, so export, import, promote and `job load` work on a fresh store; the candidate gate still runs on the job's own page (HUB-001, REC-006).
- The MCP server covers the whole job lifecycle: `job_extract` (like `rastro job run`, no dataset write), `job_load` (with `drift`), `drift_status`, `recipe_history`, `recipe_repair` (proposes; promotes only when the job sets `[repair] auto_promote = true`), `recipe_promote` (requires a reason), `recipe_apply`, `suggest_recipe`, `job_new` (never overwrites) and `scrape_file` (one-off extraction from a workspace file, no network). Tools declare bounded JSON Schema inputs and read-only/destructive/idempotent hints, outputs are bounded with `truncated`, and the server sends workflow `instructions` (AGENT-002, AGENT-003, REPAIR-002-005, REC-006, INT-002, JOB-001).
- `rastro new --recipe-memory FILE`, MCP `job_new(recipe_memory=...)` and the Studio wizard's "Recipe memory file" add `recipe_memory` to the new job, so a job created without the command line can be repaired; MCP `job_new` accepts `identity` as a field name or a list (JOB-001, REC-006, AGENT-003, P-008).
- `rastro new --allow-partial` writes a job even when the recipe preview is only a partial success; `Suggestion.status` reports the preview outcome (JOB-001, INT-002).
- `rastro recipe show job.toml --source N [--version V]` prints a stored recipe version as JSON, read-only; `recipe compare` reports `changed_settings` (such as `record_selector`); `rastro recipe --help` describes every subcommand (REC-006).
- Rejected repair candidates report `blocking` issues: record index, field and issue code, never values (REPAIR-002).

### Security

- Studio refuses a `POST` without an `Origin` header even with a valid token; GET requests are unchanged (P-008, SEC-008).
- Studio requires the session token on every `/api` request, reads included (job and page lists, connect commands); the page and its static files stay public. Saving a selector and writing a serving recipe compare the expected digest with the bytes being replaced inside the write step, so a file changed after the check fails with `file_changed` instead of being overwritten. Dataset pages and downloads cut field and identity values longer than 2,000 characters and mark the record `truncated` (P-008, SEC-008, DATA-004, REC-006).

### Changed

- The MCP server reports Rastro's package version as its `serverInfo.version` instead of the MCP library's (AGENT-002).
- Package classifier is `Development Status :: 4 - Beta`; README rewritten for external users.
- MCP `recipe_repair` declares `destructiveHint: true`, because a job with `[repair] auto_promote = true` lets it change the serving recipe; `dataset_records` output is bounded like the other tools, and every MCP output also caps objects at 100 keys with `truncated` (AGENT-002).
- With `recipe_memory`, `job run`, `job load`, `run_job` and `run_job_file` use the serving recipe in memory when its version is higher than the job file's recipe version, so a promoted repair applies to the next run. Previously `job run` kept the stale file recipe and `job load` used memory whenever it had a serving recipe; see ADR 0021 (REC-006).
- Repair completeness counts optional fields only as often as the stored success samples filled them, so an optional field that is legitimately empty in some records no longer quarantines a correct repair (QUAL-006).
- Code that imported `rastro.cli_dataset.load_job_file`, `rastro.cli_repair_context` or `rastro.cli_repair_storage` should use `rastro.load_job_file` and the other public functions; those internal modules moved under `rastro.application`.
- `rastro recipe import` reuses a stored version whose recipe equals the bundle's, and its JSON output gains `"reused": true|false` (HUB-001).
- Studio lists only job files (`.toml` or `.json` with a top-level `format_version`); repair reports and other files are no longer shown (P-008).
- MCP `job_run` is renamed `job_load`, matching `rastro job load` (it writes the dataset); `job_run` stays as a deprecated alias until 0.7.0. Use `job_extract` for the `rastro job run` behaviour. MCP tool errors, including argument validation errors, are always `{"error", "message", "location", "hint"}`; the `mcp` extra now requires `mcp>=1.10` (AGENT-002, AGENT-003).
- `rastro studio` and `rastro mcp` report a missing optional extra as `{"error": "missing_extra", "message", "location": null, "hint": "pip install 'rastro-engine[<extra>]'"}` (P-008, AGENT-002).
- A job `[dataset]` without `match_fields` now matches records on its `identity` fields only and flags no uncertain matches; previously it assumed event fields named `title`, `venue` and `start_time`. To keep near matching, declare `match_fields` explicitly. Errors in `match_fields`, `identity` and `approve` now report their TOML key as `location` (DATA-004, AGENT-001).
- `rastro new` refuses (exit 2, listing the preview issues) when the suggested recipe's preview is not a full success; pass `--allow-partial` to write it anyway (JOB-001).
- `rastro.scrape("page.html", ...)` raises `ConfigurationError` asking for `pathlib.Path(...)` instead of parsing the filename as HTML (INT-001).
- `rastro recipe import` reuses only an equal candidate or serving version; an equal retired version gets a new candidate version (HUB-001, REC-005).
- The recipe-version conflict error names the next free version to set in the job file (REC-006, ADR 0021).
- `RepairPolicy` is no longer exported from `rastro`; repair thresholds stay in the job's `[repair]` table. The `suggest` test seam is now the private `_suggester` keyword.
- `recipe repair` and `recipe promote` share one evaluator: hard validation, replay or consistency failures reject before ambiguity is considered, and a rejection from either command retires the version with a `Recipe CI: reject` lifecycle event. `--override-gates` bypasses quarantine thresholds only; on a rejected candidate it exits 2 with an error that says so (REPAIR-002, REPAIR-005).
- `recipe repair` reuses a stored candidate (or a still-rejected retired version) with the same definition instead of allocating a new version each run (REC-006).
- `recipe apply` on a job whose sources share the job-level recipe adds a `[sources.recipe]` table for the repaired source only, instead of refusing (REC-006).
- `recipe promote` retires a rejected candidate only when the current page is usable; on a page where neither the candidate nor the serving recipe finds records (consent wall, maintenance page) the candidate stays a candidate and the report notes `page_not_usable`. Promotion reports note `candidate_retired` when a version was retired (REPAIR-005).
- Repair reuse prefers the newest equal candidate over a retired twin and marks reused comparisons with `"reused": true` (REC-006).

### Fixed

- `rastro.suggest` tells a value the selector found but the schema type rejects (for example "59.90 EUR" for a `Decimal` field) apart from a value that was not reproduced: `SuggestionError` names only the failing field, its type and the page text's length and shape, and suggests a string field or a `BeforeValidator`; partial results note such fields in `confidence`. The comment written by `rastro new` says `rastro job run` previews without writing and, with a dataset, points to `rastro job load` (INT-002, INT-003, DISC-003).
- `rastro job load --drift` reports a total extraction failure (for example a record selector that no longer matches) on a source that succeeded before as `risk: high`, `score: 1.0` with an `extraction_failed` signal on field `*`, instead of `low`/`0.0` when the earlier success was loaded without `--drift`; `job-load.schema.json` gains the indicator (DRIFT-002, DRIFT-004).
- Self-repair (`rastro.scrape` and `rastro recipe repair`) relocates a renamed record container when some records lack an optional child, such as one card without a stock label: rows may omit children of the most common row shape, while extra children, other classes or other paths still count as different containers. Optional fields now need half the fill rate the success sample showed instead of the same rate (REPAIR-001, REPAIR-002, SCR-002, QUAL-006).
- `rastro.scrape` strips text in `rastro.css` fields like jobs do (NBSP included); a whitespace-only value in a required field is a validation issue instead of an empty string (QUAL-004).
- `rastro.scrape` raises `ConfigurationError` when `schema` is not a Pydantic model class (INT-001).
- `rastro job load` names a dataset folder that does not exist (`location: "dataset.path"`) instead of `Dataset operation failed`; the Studio wizard stores the dataset path relative to the job file and refuses a missing folder or a path outside the job's folder (DATA-001, P-008).
- The MCP tools reject an absolute path such as `/etc/passwd` as an absolute path instead of reporting a symlink (AGENT-002).
- Job field values are stripped of surrounding whitespace (NBSP included) before validation; a blank required value is a validation error instead of a successful `""` (QUAL-002, QUAL-004, JOB-001).
- `rastro job load` no longer exits 2 after writing rows when a field holds a relative URL: the retained repair fixture now replays exactly. A repair-evidence retention failure after the rows are committed is reported as `evidence_retention_failed` on that source, with the load's exit code unchanged (DATA-001, REPAIR-002, SCR-002).
- Every CLI error on `stderr` now has `error`, `message` and `location`, plus an optional `hint`. Commands other than `rastro job` used to print only `{"error": "invalid_input", "hint": ...}`. `rastro dataset export` and `status` on a missing file name the missing path. `rastro studio` and `rastro mcp` without their extra print `missing_extra` with the `pip install` command in `hint` (it used to be in `message` for `mcp`) (AGENT-001, DATA-003).
- `rastro demo` prints its price as an exact decimal string (`"12.5"`), like every other JSON output (AGENT-001).
- The HTTP `User-Agent` reports the installed package version instead of a fixed `0.1` (transparent contact metadata, ADR 0002).
- File targets of `rastro.scrape` are opened non-blocking, so a FIFO is refused instead of hanging (SEC-005).
- Job runs, loads and previews refuse a symlinked `recipe_memory` path; run and load open a current-schema store read-only while choosing recipes, and a pinned `recipe_version` is reported only when the store holds it (SEC-008, REC-006).
- `rastro recipe apply` refuses a symlink in any directory between the working directory (or, for a job outside it, the job's own directory) and the job file, and atomic writes sync the parent directory on POSIX (SEC-008).

## 0.5.0 — not published (2026-11-11)

Nothing from this project is published to PyPI. Versions 0.4 (Studio, MCP) and 0.5 (Hub step 1) land together; see [ADR 0020](docs/decisions/0020-studio-mcp-hub-minimal.md).

### Added

- `rastro mcp` serves job validate, preview and run, dataset records and recipe listing to agents over stdio MCP, behind the optional `mcp` extra and a workspace root (AGENT-002, AGENT-003, P-008).
- `rastro recipe export` and `rastro recipe import` share recipes as bundles (recipe, schema, sample, expected records, hashes) verified offline before storage (HUB-001; see `docs/hub.md`).
- `rastro studio` (optional `studio` extra) serves a loopback-only page to validate, preview, correct a selector, accept and copy connect commands for a job; see `docs/studio.md` (P-008).

## 0.3.0 — not published (2026-11-11)

Nothing from this project is published to PyPI.

### Added

- Localized date and datetime job fields (`localized = true`) parse Spanish listing text using the job region's timezone; a datetime without a time fails explicitly. Relative links resolve against the source URL (DATA-005, JOB-002).
- CSS field expressions may read one page-level node with the `document:` prefix (exactly one match, with provenance).
- `rastro dataset export --format html`, plus `--sort-field`, `--upcoming` and `--timezone`; daily scheduling examples in `docs/scheduling.md` (SCHED-007, DATA-004).
- Automatic repair relocates a renamed or moved record container from the last successful snapshot, proposes an "old, new" selector union, and quarantines heterogeneous matches; measured on `benchmarks/repair_suite` (REPAIR-001, SCR-002).
- The events example adds an offline three-venue job (`examples/uy-dance-agenda/sample/v03/`).

### Removed

- Live-site pilot material (the live job, pilot reports and the agenda publication) moved out of the repository; it is an internal use-case test, not part of the engine.

### Fixed

- Job loads gate approved observations on declared consistency rules before storage; inconsistent sources retain stale rows, report rule results and a repair action, and exit 3 (QUAL-005/006, SYNC-006).
- Acquisition falls back across validated DNS addresses on connection failure only, within the same timeout and request budget, and never through a proxy.
- Blank text nodes no longer count as field matches; uniqueness is decided on elements.

## 0.2.0rc1 — not published

### Added

- Measured opt-in dataset drift with named field evidence, deterministic risk and retained-table effects (DRIFT-001–006; #62; ADR 0018).
- Offline repair candidates from Scrapling relocation, fallbacks and discovery, plus explicitly enabled bounded models; recipe CI replays sanitized last-success and Mock Lab fixtures, checks schema, provenance and generic consistency rules, and promotes only a unique candidate meeting configured thresholds (REPAIR-001–005, SCR-002, QUAL-005/006).
- `rastro recipe repair|promote|compare|rollback`, machine-readable comparisons and per-run repair reports; failed/quarantined candidates keep the serving recipe and dataset records (REC-005/006, REPAIR-003–006).
- Optional job `[[consistency]]` and `[repair]` fields, published schemas, and latest-success relocation/sample tables in the dataset file with an independent version row (ADR 0019). Existing jobs without these fields keep their validation output.

- Separate SQLite dataset with approved fields, provenance, origins, stable identity, update/missing/uncertain outcomes and failure retention (DATA-001/004/005, SYNC-002/003/005/006; #56; ADR 0017).
- Declarative `[dataset]` configuration and read-only `rastro job preview` with observed values, provenance and approval decisions (DATA-002, JOB-002, AGENT-001; #55).
- Uruguay dance agenda pilot with dated live obstacle evidence and synthetic offline v1/v2 samples (COST-001; #59).
- `rastro job load`, per-source change counts and stale keys with last-success times, plus labeled `rastro dataset status` and JSON/JSONL export; shipped upcoming-events SQL and the executed README path (DATA-001/003, SYNC-002/006/008, COST-001; #61). The sample now includes synthetic stable performance IDs for repeat/reschedule proof; live jobs retain their original schema. Optional `dataset.match_fields` configures generic near-match field names.

- Recipe lifecycle states (`candidate`, `active`, `degraded`, `retired`), the `RecipeLifecycle` port (`save_candidate`, `transition`, `rollback`, `history`, `next_version`), and machine-readable lifecycle history. Candidates do not displace the serving version; rollback restores a previously served version (REC-005, REC-006, REPAIR-006; #46; ADR 0015).
- Mock Lab structural mutations: seeded, reproducible HTML and JSON operators for every MOCK-002 category (reordering, class changes, wrapping, missing fields, duplicates, empty containers, changed labels, pagination), each reporting machine-readable changes or an explicit no-target reason, composed by a versioned `MutationPlan` whose outputs carry the synthetic label. `lxml` and `cssselect`, already installed with Scrapling, are now declared dependencies (MOCK-002, MOCK-006; #48; ADR 0014).
- Synthetic failure scenarios with explicit outcomes and versioned labels (MOCK-003, MOCK-004; #40).
- Instant Mock Lab fixture bundles: labeled, sanitized, reproducible and replayable offline (MOCK-001, MOCK-004, MOCK-005, MOCK-006; #45; ADR 0014).
- Top-level Mock Lab fixture exports and support for extracted opaque product values while scrubbing structural attributes and URL paths (MOCK-001, MOCK-004, MOCK-005).

### Changed

- With recipe memory configured, `job load` uses the lifecycle's serving version after bootstrap, so promotion/rollback controls the next load; `job run` and SDK explicit-recipe precedence are unchanged. Dataset files gain additive `relocation_*` tables; no recipe-memory migration. The dance example's consistency rules now live in its job files instead of `consistency.toml` (REPAIR-006, QUAL-005/006).

- SQLite recipe memory migrates to schema 2 on first open through a forward-only transaction. Existing 0.1.0 payloads keep loading: the highest version per key remains active and older versions become retired rollback targets. Version 0.1.0 cannot read migrated files; back up before upgrading. `RecipeRepository` remains unchanged from 0.1.0 (REC-005, REC-006, REPAIR-006; #46; ADR 0015).

### Known limitations

- DIFF-002 product-task comparison: one offline synthetic task; human effort unmeasured; see docs/comparison-v02.md.
- DIFF-002 remains partial: human effort / minutes to a correct database were not measured (`human_minutes: null`); development-agent usage was not instrumented.
- DIFF-001 sealed-corpus recovery evaluation has not been run; no recovery result is claimed. The release candidate has not been tagged or promoted.
- The live pilot did not establish a complete two-source dataset; SODRE Spanish date text yielded zero live records, and sale dates can be confused with event dates. (Pilot report kept outside the repository.)
- Live-source repair, retention pruning, drift calibration and heterogeneous relocation remain future work.

## 0.1.0rc1 — 2026-11-03

Release candidate tagged on `main`; not published to any package registry. Final `0.1.0` waits on the release gates in issue #5.

### Changed

- `Recipe` is now a strategy-discriminated union of `CssRecipe` (`record_selector`), `JsonLdRecipe` (optional `item_type`) and `JsonRecipe` (`record_path`), all exported. `Recipe(...)`, `Recipe.model_validate(...)`, `Recipe.model_validate_json(...)` and Pydantic fields typed `Recipe` return the matching variant, so existing recipe files, `rastro validate-recipe` and stored payloads load unchanged. Each variant rejects other strategies' selectors. `null` selectors written by earlier releases mean absent. Fallbacks are typed per representation. Type-checked code should construct the variant classes, because static checkers see only the shared fields on `Recipe(...)` (REC-002, REC-003, INT-002, QUAL-001). See ADR 0009.
- Serialized recipes (`model_dump`, `learned_recipe`, new SQLite payloads) contain only the variant's own fields and no longer include `null` selectors for other strategies (REC-002).
- `StoredRecipe` separates the definition from operational history. `selected_index`, `created_at`, `last_success_at` and `successful_runs` moved to `StoredRecipe.metadata`, a new exported `RecipeMetadata`. `stored.recipe` and `stored.validated_recipe` are unchanged. This prepares REC-005 lifecycle states without changing definitions (REC-001, REC-003).
- `RecipeProposal` is a strategy union with `CssProposal` and `JsonLdProposal` variants derived from the HTML recipe variants. `proposal_schema()` is unchanged apart from the two variant titles (REC-004, AI-002).
- `AcquisitionResult.html` is renamed to `body` because it also carries JSON (INT-002).
- Custom transports and acquisition ports must accept the keywords Rastro now always passes: `HttpTransport.get(..., proxy=None | ProxyRoute)` and `AcquisitionPort.acquire(..., representation="html" | "json")` (INT-002, GEO-005).
- Internal: prepared extraction moved from `Rastro` into `application.service.ExtractionService`, with no behavior change. SQLite schema is unchanged and needs no migration; M3 and 0.1.0 payloads keep loading (REC-001, QUAL-004).
- Internal: `schema_fingerprint` caches canonical JSON Schema digests per model class (invalidating on `model_rebuild`) and `extract_document` computes `source.evidence_url` and validates shared `Evidence` metadata once per extraction, cutting unprofiled p50 latency on `static` (0.326 → 0.172 ms), `metadata` (0.251 → 0.118 ms) and `large` (12.033 → 9.135 ms; same-run gap over baseline 3.530 → 1.822 ms) with byte-identical serialized results, issues and provenance (P-010, DIFF-001).
- The README, getting-started and acquisition guides now construct `CssRecipe`, `JsonLdRecipe` and `JsonRecipe` directly instead of `Recipe(strategy=...)`, so every documented example is the typed form (REC-002, QUAL-001).
- Internal: split `domain.models` (`sources`, `recipes`, `results` with `domain.models` re-exporting all contracts) and extracted cohesive `application` helpers (`http_policy`, `proposals`, `outcomes`, `batch`) from `acquisition`, `intelligence`, `service` and `engine` so every module stays within the 250-line budget with unchanged public imports, SQLite payloads, schema fingerprints and serialized results (P-006, REC-001, REC-002, INT-003, QUAL-004).
- `docs/roadmap.md` records the merged pre-v0.2 hardening slices (#21–#25, #28–#32), the work still in progress (declarative jobs and JSON CLI, module splits for the clean-code size budget) and the maintainer-owned gates (issues #2–#5, promotion to `main`) (P-010, DIFF-001, INT-002, JOB-001/002, AGENT-001).

### Added

- Declarative job files (`format_version = 1`) in TOML or JSON with closed field types, deterministic dynamic `JobRecord` schemas and fingerprints, path containment and environment-only credential references, non-widening HTTP host allowlists, `rastro job validate|run|schema` with distinct exit codes (`0`/`1`/`2`/`3`) and `untrusted_source_data` labelling, and committed JSON Schemas under `docs/schemas/` (JOB-001, JOB-002, AGENT-001, AGENT-003, INT-001, P-008). See ADR 0013.
- Job files are parsed strictly (no string-to-bool or string-to-number coercion, including inside `acquisition`, `limits` and `inference_policy`), `string` patterns are checked with Pydantic's regex engine, `*_env` references must be uppercase, `openai_compatible` `base_url_env` requires `allow_remote = true`, `rastro job validate` redacts `*_env` values, and every `rastro job` configuration or usage error is one JSON object `{"error", "message", "location"}` on stderr with exit code `2` (JOB-002, AGENT-001, AGENT-003, SEC-004, SEC-011).
- Deterministic discovery also matches exact declared Pydantic field aliases (`Field(alias=...)`, string `validation_alias` and `AliasChoices`) and an opt-in typed `discovery_aliases` per-field map for JSON-LD, microdata and JSON responses; recipes still map the model field name, so provenance shows the matched source key, and an aliased schema no longer raises `ConfigurationError` (INT-002).
- Frozen v0.2 recovery benchmark protocol (`docs/recovery-benchmark.md`, ADR 0011) with exact detection, restoration, false-promotion, silent-error, model-call and human-approval metrics, pass criteria for a DIFF-001 recovery claim and anti-tuning rules, plus hashed synthetic development and sealed corpora of epoch sequences (`benchmarks/recovery/`) and an offline integrity test. No executor or repair code, no measured result, and no public SDK change (DIFF-001, REPAIR-001–006, DRIFT-001–005, QUAL-005/006, MOCK-002/003).
- `OpenAICompatibleModel`, a Chat Completions `ModelPort` strategy for OpenAI, OpenRouter, vLLM, LM Studio, llama.cpp server and Ollama's `/v1`, behind a new `openai` extra. It sends `json_schema` structured outputs (or explicit `response_format="json_object"`), never tools; loopback endpoints work without opt-in while any other host requires `allow_remote=True` and HTTPS; API keys are referenced only through `api_key_env`; `InferencePolicy` deadline, context, response-byte, token and concurrency budgets are enforced in the adapter and failures map to `ModelError` categories (AI-001–005, SEC-004/005/011, P-001, P-007). See ADR 0010.
- `rastro.OllamaModel` and `rastro.OpenAICompatibleModel` lazy public exports; core `import rastro` still does not import HTTPX, and a missing extra raises an `ImportError` naming it (P-002).
- `OpenAICompatibleModel(token_limit="max_completion_tokens", temperature=None)` for OpenAI reasoning models that reject `max_tokens` and sampling parameters (AI-005, AI-007).
- "Choosing or writing a model provider" documentation with provider examples and the custom `ModelPort` contract.

## Before 0.1.0rc1 — prepared as 0.1.0 (2026-10-30), never tagged

These entries were drafted as `0.1.0` and shipped in the `0.1.0rc1` tag above; `0.1.0` itself
was never tagged. First Rastro Engine release candidate: schema-first, verifiable extraction with
optional policy-aware HTTP acquisition, structured JSON responses, explicit egress
profiles, SQLite recipe memory and optional bounded Ollama assistance. A release
date, tag and registry publication await maintainer approval.

### Added

- Held-out DIFF-001 evaluation (`python -m benchmarks.heldout`): five new synthetic sources with 24 structural mutations, measuring source-specific configuration, silent errors when re-running working integrations, scripted-model calls per successful record with SQLite memory, an observed visibility checklist and latency against a schema-generic baseline with a structured-data adapter. Results show partial DIFF-001 evidence and no silent-error or latency advantage (DIFF-001, QUAL-003, OBS-001).
- Multi-model live Ollama harness (`--model` repeatable, `--trials`, `--keep-previous`) with recorded `qwen3:4b` and `llama3.2:3b` runs, a versioned report that keeps earlier runs, and a `--probe` mode that records raw proposals for diagnosis (AI-001–004, REC-001, REC-004).
- Permitted structured JSON responses: a declarative `json` recipe strategy with an RFC 6901 `record_path` and relative field pointers, `Rastro.extract_json`/`extract_json_async`, and opt-in `representation="json"` HTTP acquisition that sends `Accept: application/json` and rejects non-JSON content types as permanent failures (INT-001–004, SEC-001/005).
- Strict JSON budgets (`max_json_bytes`, `max_json_depth`, `max_json_nodes`), duplicate-key rejection, `json` evidence with absolute pointer locators, explicit `invalid_json`/`invalid_record`/`invalid_record_path`/`record_path_not_found` issues, and the public `Representation` type (QUAL-001–004, SEC-008).
- Deterministic JSON recipe discovery by exact field names over the root object and arrays of objects. JSON recipe memory uses representation-isolated keys, and existing HTML keys and legacy payloads are unchanged (INT-002, REC-001–004). Includes the offline `examples/structured_api.py` and ADR 0007.
- Explicit network egress profiles: `AcquisitionPolicy.egress_profiles` declares named HTTP(S) proxies selected per `Source.egress`, with environment-variable credential references, pre-DNS configuration failures, unchanged target validation, no rotation or fallback, egress-isolated recipe memory and `metrics.egress` (GEO-001–003, GEO-005, SEC-001–005, SEC-008, SEC-011). `Source` rejects `geolocation` explicitly.

- Opt-in live Ollama harness and recorded `qwen3:4b` evidence: cross-process learning and zero-inference reuse, plus labelled live/controlled failure scenarios (AI-001–004, REC-004).
- Explicit ordered language preferences with descending Accept-Language weights, currency, timezone and direct-egress context through Pydantic's supported `model_validate` path; early rejection of invalid headers and unsupported egress (GEO-001/002).
- Regional recipe isolation by language order, currency and egress with unchanged default canonical keys for existing SQLite memory (GEO-003, REC-001).

- Reproducible offline comparison against an independent Scrapling + Pydantic baseline: synthetic corpus, multiset quality scoring, raw timing samples and source hashes (DIFF-001, QUAL-003).
- Public demo-site end-to-end example with persistent recipe reuse and offline regression coverage; recorded live results, runtime license inventory and explicit v0.1 release gates (OBS-001, REC-001–003, specification sections 17–18).

- Opt-in deterministic recipe discovery, asynchronous supplied-HTML extraction and optional local Ollama assistance through `ModelPort`.
- Structured model proposals, bounded calls/corrections/deadline/context/response/concurrency, and source/schema validation before activation (AI-001–004, REC-004, INT-002).
- Learned recipes retain required-field evidence checks across restarts; M3 payloads remain reusable without false version conflicts.
- Inference metrics, attempt origins, an inspectable learned recipe, offline example, adapter/attack/budget tests and a separate `ollama` dependency extra.

- Optional SQLite recipe memory with exact source/template/schema/region identity, immutable versions, success metadata, retention and deletion.
- Deterministic reuse across restarts in offline and asynchronous SDKs, with worker-thread storage and explicit persistence failures.
- Bounded explicit fallback recipes; only the alternative validated successfully becomes reusable.
- Recipe version/reuse/attempt metadata, storage timings, a repeated-extraction example and persistence/security/concurrency tests.

- Optional policy-aware HTTP SDK with exact host allowlists, DNS pinning, robots rules, redirect checks, bounded retries and host pacing.
- Asynchronous single-source and ordered batch extraction, cancellation, request/body budgets, and explicit network outcomes.
- Acquisition metrics, regional Accept-Language, HTTP example, and offline transport/security regression tests.

- Offline HTML extraction with explicit JSON-LD and CSS recipes through Scrapling.
- Typed Pydantic results, field provenance, safe diagnostics, resource limits, and processing timings.
- CLI demo, environment diagnostics, and declarative recipe validation.
- Offline regression and property tests, strict typing, package checks, and documentation.
- Public contribution and security policies and an incremental v0.1 roadmap.
- Release evidence records built wheel/sdist contents and a bundled native notice review for `lxml` and `curl-cffi` on macOS arm64 (spec section 18 release gates).

### Fixed

- JSON-LD and JSON decoding now reject numbers that overflow or underflow a finite float (for example `1e999`, `-1e999`, `1e-999`) and integers over 4,300 digits. These are explicit invalid outcomes instead of `inf` or `0.0` records that report success (QUAL-004, SEC-005).
- `missing_evidence` issues now name each field that lacks evidence, for all strategies (QUAL-003, INT-004).
- The Ollama adapter disables thinking. Capable models no longer spend the output budget and deadline on hidden reasoning (AI-003).
- The decoding schema now requires `record_selector` for CSS proposals and forbids it for JSON-LD, matching recipe validation (AI-002, REC-004).

### Known limitations

- DIFF-001 is partially demonstrated: configuration ties an adapted Scrapling + Pydantic baseline, visibility and memory-backed inference use improve, but accuracy and silent-error detection are equal and latency is higher. See `docs/comparison.md`.
- Live Ollama evidence covers two small local models on synthetic inputs; both fail safely on a page with distractors. Broader model quality remains unmeasured.
- HTTP supports public standard-port HTML and JSON sources with direct or explicit HTTP(S) proxy egress; no JavaScript rendering, browser geolocation, authentication, SOCKS, or discovery crawling. Proxied connections cannot use the local DNS pin.
- Recipe memory requires explicit template/region/validator revisions and stores successful-run metadata, not a durable job history (v0.3). Lifecycle, repair and drift belong to v0.2.
- Pydantic models with a custom `__init__` retain Pydantic's constructor semantics and must handle validation context themselves; Rastro does not bypass user constructors.
- Schema aliases, API endpoint discovery, JSON embedded in HTML, automatic stored-recipe repair, and cross-document synchronization are not supported. Discovery is limited to exact JSON-LD names, simple microdata and exact JSON field names.
