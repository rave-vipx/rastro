# ADR 0013: declarative jobs and machine-readable CLI contracts

- **Status:** accepted for pre-v0.2; JOB-001, JOB-002 and AGENT-001 pulled forward by ADR 0012.
- **Requirements:** JOB-001, JOB-002, AGENT-001, AGENT-003, INT-001, P-008, SEC-001–011, QUAL-001–004.

## Context

Until now, running Rastro required writing Python to declare a Pydantic `BaseModel`, construct
`Source` and `AcquisitionPolicy`, wire SQLite memory or a model provider, and call `Rastro`.
ADR 0012 established that non-programmers, AI agents and a future Studio GUI (v0.4) must share a
single validated job contract rather than inventing separate CLI flags, agent payloads and UI
files (P-008). Agents also need deterministic JSON input/output schemas, distinct exit codes and
explicit untrusted-data framing before an MCP adapter is added in v0.2 (AGENT-001, AGENT-003).

## Decision

### Versioned data-only job contract (`format_version = 1`)

A job is a strict Pydantic contract (`Job` in `rastro.domain.jobs`, split across focused modules
`job_fields`, `job_sources`, `job_providers` and `job_results`) loadable from UTF-8 TOML
(`tomllib`) or strict JSON (`parse_job`, `load_job`) up to 256 KiB. Unknown keys, UTF-8 BOMs,
duplicate JSON keys, non-finite numbers and unquoted TOML dates/times are rejected. No Python
module is imported and no expression is evaluated.

Both representations normalize into the same `Job` model and produce identical dynamic schemas
and fingerprints.

### Closed field set and deterministic dynamic model

Target fields (`fields`) form a discriminated union (`JobField`) over eight closed types:
`string`, `integer`, `number`, `boolean`, `decimal`, `date`, `datetime` and `url`, each with
`required` (default `true`) and type-appropriate declarative constraints (`min_length`,
`max_length`, `pattern`, `ge`, `le`). `number` and `decimal` forbid `NaN` and `Infinity`
(`allow_inf_nan=False`). Field names must be valid public identifiers that do not shadow Python
keywords or `BaseModel` attributes.

`build_job_schema` constructs `pydantic.create_model("JobRecord", ...)` with fields sorted by
name. Because the model class name and sorted property definitions are canonical,
`schema_fingerprint` is deterministic across runs, formats (TOML vs JSON) and field declaration
orders, keeping SQLite recipe memory keys stable.

### Security and containment boundaries

- **Path containment.** Relative `sources[].path` and `recipe_memory` entries may not contain
  `..`, NUL bytes, control characters or `:memory:`. At load/run time, paths resolve against the
  job file's directory (`Path.resolve()`) and must remain inside it, blocking symlink escapes.
  Absolute paths are rejected unless `--allow-absolute-paths` (`allow_absolute_paths=True`) is
  passed explicitly. Local file sources without an explicit `url` receive a synthetic
  `https://local.invalid/...` provenance URL; absolute paths hash the parent directory so host
  filesystem paths never appear in outputs.
- **No policy widening.** Any HTTP(S) `UrlJobSource` requires an explicit `acquisition` policy
  whose `allowed_hosts` already contain the source host, whose `allow_http` permits `http://`
  when used, and whose `max_sources` covers the HTTP batch. Running a job never adds hosts or
  relaxes transport rules (SEC-001, AGENT-003).
- **Secrets by environment name only.** Proxy credentials (`AcquisitionPolicy.egress_profiles`)
  and model keys/endpoints (`api_key_env`, `base_url_env`) accept `[A-Z_][A-Z0-9_]{0,127}` variable
  names only. Validation uses `RedactedContract` (`hide_input_in_errors=True`) so accidental
  inline secrets are never echoed in Pydantic errors, stdout, stderr or logs (SEC-004, SEC-011).

### Shared SDK execution path

`rastro.application.jobs` executes `Job` exclusively through the public `Rastro` / `Rastro.web`
SDK (`extract_html`, `extract_json`, `extract_html_async`, `extract_json_async`, `extract`,
`extract_many`) without changing engine or service internals (P-008). Offline model-free file
jobs run synchronously without opening sockets or event loops; discovery, model providers and
HTTP batches run asynchronously, using `extract_many` when all sources share an HTTP
representation and recipe configuration.

### Machine-readable CLI, exit codes and published schemas

`rastro job` adds three subcommands:

- `rastro job validate <file> [--allow-absolute-paths]` prints validation metadata (`valid`,
  `format_version`, `schema_fingerprint`, `source_count`, `fields`) as JSON.
- `rastro job run <file> [--output json] [--allow-absolute-paths]` prints `JobRunResult`
  (`schema_version = 1`, `data_trust = "untrusted_source_data"`, aggregated `JobSummary`, and
  full `ExtractionResult` payloads with records, provenance, issues and metrics).
- `rastro job schema [--target {job,run}]` prints the canonical JSON Schema for `Job` or
  `JobRunResult`. Both schemas are committed under [`docs/schemas/`](../schemas/job.schema.json)
  and verified against code in unit tests.

Exit codes distinguish full success (`0`), extraction failure with zero accepted records (`1`),
configuration/validation/usage errors (`2`), and partial success where records were accepted
alongside issues or failed sources (`3`).

Every `rastro job` configuration or usage failure, including argparse usage errors, writes one
JSON object `{"error": <stable code>, "message": <safe text>, "location": <field path or null>}`
to stderr. Messages come from Rastro or from redacted Pydantic errors and never echo job values,
environment contents or page text. Unexpected exceptions map to `internal_error` with exit code
`2` instead of a traceback, so they never collide with the extraction-failure code `1`.

### Strict job input, lax extracted values

Job files are parsed strictly: `RedactedContract` rejects string or number coercion into
`bool`, `int` and `float` fields, and applies the same check recursively to embedded SDK
contracts (`AcquisitionPolicy`, `Limits`, `InferencePolicy`), which stay lax for Python callers.
Pydantic's global strict mode was not used because it also rejects JSON arrays for tuple
fields. `string` patterns are compiled with Pydantic's Rust regex engine at validation time.
Extracted record values keep Pydantic's lax mode because scraped text always arrives as strings
([coercion table](../jobs.md#strict-job-definitions-lax-extracted-values)).

## Consequences

- Non-Python users and AI agents can validate and run extractions end-to-end from TOML or JSON
  while preserving field-level provenance, explicit failure outcomes and recipe memory.
- Custom Python validators cannot be expressed inside a declarative job file; callers who need
  arbitrary domain normalization continue to use the Python SDK directly.
- `Job` (`format_version = 1`) and `JobRunResult` (`schema_version = 1`) are public contracts
  tracked by committed JSON Schemas; incompatible changes require incrementing the version.
