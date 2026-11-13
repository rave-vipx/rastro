# API and result contracts

## `scrape`

```python
rastro.scrape(target, schema, *, records=None, fields=None, recipe=None, url=None,
              memory=None, repair="propose", locale="en-US", limits=None)
await rastro.scrape_async(...)  # same arguments
rastro.css(selector)            # Annotated[...] marker for model fields
```

`scrape` is the one-call entry point; [Quickstart](quickstart.md) walks through it.
It returns the same `ExtractionResult[YourModel]` as `Rastro`, and
`result.items` lists the validated model instances.

| Argument | Behavior |
| --- | --- |
| `target` | HTML text, a `pathlib.Path` (bounded UTF-8 read, symlinks refused) or an `http(s)://` URL. A URL needs the `http` extra (`ImportError` names `pip install 'rastro-engine[http]'` otherwise), is fetched once, and only its host is allowed. |
| `schema` | A Pydantic model class (anything else raises `ConfigurationError`). Fields annotated with `rastro.css("...::text")` or `rastro.css("...::attr(name)")` form a `CssRecipe`; their text is stripped (NBSP included) before validation, and blank text in a field that cannot be `None` is a validation issue, as in jobs. |
| `records` | Record selector; else the model's `__records__` class variable; else the whole document (`:root`) is one record. |
| `fields` | Per-field selectors that override or extend the model's markers. |
| `recipe` | Any `Recipe`, used unchanged; cannot be combined with `records` or `fields`. |
| `url` | Page URL for relative links and provenance. Default `https://localhost/` for text and files; a URL target is its own URL. |
| `memory` | Path to a recipe database. A successful recipe is remembered; a sanitized success sample goes to `<name>.samples<suffix>` next to it. |
| `repair` | `"off"`, `"propose"` or `"auto"`; applies only with `memory` when a remembered recipe fails structurally (no records with `missing_data`, `invalid_data` or `insufficient_evidence`). |
| `locale`, `limits` | `Source.locale` and `Limits` for this call. |

With no selectors and no `recipe`, exact-name JSON-LD and microdata discovery is
tried; if it finds nothing, `ConfigurationError` asks for `rastro.css(...)`,
`fields=` or `rastro.suggest`. Memory is keyed by page URL, regional context,
schema and the declared selectors, so editing a selector starts a fresh history.
`scrape` raises `ConfigurationError` inside a running event loop; use
`scrape_async` there.

## `Rastro`

```python
Rastro(*, parser=None, limits=None, acquisition=None, policy=None, repository=None,
       model=None, inference_policy=None, discover_recipes=False, discovery=None,
       discovery_aliases=None)
Rastro.web(policy, *, limits=None, repository=None, model=None,
           inference_policy=None, discover_recipes=False, discovery_aliases=None)
Rastro.extract_html(html, *, source, schema, recipe=None, context=None, recipe_version=None)
Rastro.extract_json(text, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract_html_async(html, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract_json_async(text, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract(*, source, schema, recipe=None, context=None, recipe_version=None,
                     representation="html")
await Rastro.extract_many(sources, *, schema, recipe=None, context=None, recipe_version=None,
                          representation="html")
```

`html` is a UTF-8-compatible Python string. `schema` is a Pydantic `BaseModel` subclass with declared fields. `recipe.fields` maps model field names to extraction expressions. Field aliases are accepted: a recipe still names the model field, and discovery may also match the declared alias or a configured `discovery_aliases` entry. Root models are outside this milestone's supported schema contract. Custom validators run as trusted caller code. Rastro supplies the source's regional context through Pydantic's `model_validate` context path, subject to Pydantic's constructor semantics described below.

The `extract_html` call is synchronous, does no fetching, and returns `ExtractionResult[YourModel]`. Persistence is opt-in through `repository`; omitting `recipe` then reuses an exact-context validated version. See [recipe memory](memory.md). Asynchronous methods optionally learn cold-context recipes when `discover_recipes=True` or `model=` is configured; see [optional intelligence](intelligence.md). Parser injection is an infrastructure seam for trusted adapters implementing `DocumentParser`.

`extract_json` and `extract_json_async` accept supplied JSON text and the `json` recipe strategy; see [structured JSON responses](#structured-json-responses). `Representation` is the public `Literal["html", "json"]` type for the `representation` argument.

For HTTP acquisition, construct `Rastro.web(AcquisitionPolicy(...))` and use the asynchronous methods. An injected `AcquisitionPort` is trusted infrastructure; it must enforce its own acquisition policy. See the [HTTP guide](acquisition.md) for budgets, outcomes, cancellation and transport controls.

## Inputs

| Object | Fields and behavior |
| --- | --- |
| `Source` | HTTP(S) `url`; `locale` (`und`); ordered `languages` (`()`); `timezone` (`UTC`); optional three-letter uppercase `currency`; and `egress` (`direct`, or the name of a policy egress profile). Credentials, invalid language tags/currency codes/egress names and a `geolocation` value (no browser adapter in v0.1) are rejected; an egress name missing from the policy fails before DNS. Explicit languages form Accept-Language with descending quality weights; otherwise a defined locale remains the compatible fallback. |
| `Recipe` | A union of `CssRecipe`, `JsonLdRecipe` and `JsonRecipe` discriminated by `strategy` (see [recipe variants](#recipe-variants)). All share required `fields`, positive `version` (default `1`), `require_evidence` (default `False`) and up to three flat `fallbacks` sharing its version and document representation. `require_evidence` requires observations for required fields before validation; learned recipes set it to `True`. Version maximum: 2,147,483,647. |
| `Limits` | `max_html_bytes` (2,000,000), `max_records` (1,000), `max_jsonld_blocks` (100), `max_json_bytes` (2,000,000), `max_json_depth` (32), `max_json_nodes` (100,000). |
| `AcquisitionPolicy` | Exact `allowed_hosts`, request budgets and timeouts (see [policy defaults](acquisition.md#policy-defaults)), plus `egress_profiles`: up to 16 names mapped to an `EgressProfile` (`proxy_url`, optional `username_env`/`password_env` environment-variable references) or a bare `http(s)://host[:port]` string. See [network egress](acquisition.md#network-egress). |

CSS fields must end in `::text` or `::attr(name)` and match at most one value in each record. Multiple matches reject the record as ambiguous, even if the values happen to be equal. Missing matches omit the field; Pydantic decides whether the omission is allowed. CSS `::text` follows Scrapling's direct-text semantics; it does not join descendant text automatically.

JSON-LD type matching is exact and accepts an `@type` string or array. Nested field values are traversed only by explicit JSON pointers. The object count budget includes filtered-out objects to bound scanning. Malformed blocks, duplicate JSON keys, nonstandard `NaN`/`Infinity` values, and numbers that overflow or underflow a finite float (such as `1e999` or `1e-999`) or have more than 4,300 integer digits are reported as invalid JSON-LD. Rastro never turns them into infinity, zero, or a successful empty record.

## Recipe variants

A recipe is a declarative **definition** of what to extract. `Recipe` is the shared
base of three variants, selected by `strategy`:

| Variant | `strategy` | Strategy-specific field | Field expressions |
| --- | --- | --- | --- |
| `CssRecipe` | `css` | required `record_selector` | CSS ending in `::text` or `::attr(name)` |
| `JsonLdRecipe` | `jsonld` | optional `item_type` | absolute RFC 6901 pointers within each JSON-LD object; a field may list 1 to 4 pointers and the first that resolves wins, recorded in the locator |
| `JsonRecipe` | `json` | required `record_path` (RFC 6901, `""` is the document) | absolute RFC 6901 pointers from each record |

```python
from rastro import CssRecipe, JsonLdRecipe, JsonRecipe, Recipe

css = CssRecipe(record_selector="article", fields={"name": "h2::text"})
jsonld = JsonLdRecipe(item_type="Product", fields={"name": "/name"})
api = JsonRecipe(record_path="/data/products", fields={"name": "/name"})

loaded = Recipe.model_validate_json(text)  # -> CssRecipe, JsonLdRecipe or JsonRecipe
same = Recipe(strategy="css", record_selector="article", fields={"name": "h2::text"})
assert isinstance(same, CssRecipe) and isinstance(same, Recipe)
```

- `Recipe(...)`, `Recipe.model_validate(...)`, `Recipe.model_validate_json(...)` and
  Pydantic fields annotated `Recipe` select the variant named by `strategy`. A missing
  or unknown strategy raises `ValidationError`. Prefer the variant constructors in
  type-checked code: static checkers see only the shared fields on `Recipe(...)`.
- Each variant rejects the other strategies' selectors: a `CssRecipe` with
  `item_type`, a `JsonLdRecipe` with `record_selector` or `record_path`, or a
  `JsonRecipe` with either HTML selector is invalid. An explicit `null` for another
  strategy's selector means "absent", so files and stored payloads written by 0.1.0
  (which serialized every selector) still load.
- Fallbacks are typed by representation: `CssRecipe` and `JsonLdRecipe` accept CSS and
  JSON-LD fallbacks; `JsonRecipe` accepts only JSON fallbacks.
- `recipe.representation` is `"html"` for CSS and JSON-LD and `"json"` for JSON.
- Serialization contains only the variant's own fields.

Operational history (creation time, last success, run counts, the validated
alternative) is never part of a definition; see `StoredRecipe.metadata` in
[recipe memory](memory.md#inspect-delete-and-retain).

## Structured JSON responses

A `json` recipe reads one JSON document: supplied text, or an HTTP response requested with `representation="json"` (see the [HTTP guide](acquisition.md#json-responses)).

```python
recipe = JsonRecipe(
    record_path="/data/products",  # RFC 6901 pointer; "" selects the whole document
    fields={"name": "/name", "price": "/offer/price"},  # relative to each record
)
result = Rastro().extract_json(text, source=source, schema=Product, recipe=recipe)
```

- `record_path` must resolve to an object (one record) or an array. Each array element must be an object; other elements are rejected individually as `invalid_record`.
- Field pointers are absolute RFC 6901 pointers evaluated from each record. Array indexes are decimal without leading zeros; `-` never matches. A pointer selects exactly one value, so JSON extraction has no ambiguous matches. A missing pointer omits the field and Pydantic decides whether that is allowed.
- There is no JSONPath, wildcard, filter, or expression language, and nothing is evaluated.
- Input is decoded strictly. Duplicate object keys, `NaN`/`Infinity`, numbers outside the finite float range (`1e999`, `-1e999`, or a nonzero value that underflows to zero, such as `1e-999`), integers longer than 4,300 digits, malformed text, an empty body and a byte-order mark reject the whole document as `invalid_json` (`invalid_data`). Rastro never picks one of several duplicate values.
- `max_json_bytes`, `max_json_depth` (container nesting below the root) and `max_json_nodes` (every value, including containers) are checked before records are selected. `max_records` bounds the selected records. A breach raises `ResourceLimitError` for supplied text and becomes `extraction_limit` for HTTP.

Evidence uses method `json` with locator `json#<absolute pointer>`, for example `json#/data/products/1/offer/price`. JSON recipes require JSON input and HTML recipes require HTML input: a mismatched explicit recipe raises `ConfigurationError` before any request. Recipe memory keys include the representation. HTML keys keep their existing canonical form, so a stored JSON recipe never answers an HTML request for the same source (or the reverse).

With `discover_recipes=True`, `extract_json_async` and JSON HTTP extraction discover recipes deterministically. Candidates are the root object and every array whose elements are all objects, up to `max_records` elements. In up to 20 sampled records, a field maps only when its exact name occurs once per record, and every sampled record containing it agrees on its pointer. Synonyms, case folding and array traversal are not attempted. A field also matches its declared Pydantic aliases (`Field(alias=...)`, a string `validation_alias`, or string `AliasChoices`) and any extra names in the typed `discovery_aliases` map (`{"currency": ("priceCurrency",)}`) passed when discovery is configured. Matching stays exact and case-sensitive, the model field name is always accepted, and more than one matching node for a field leaves that field unmapped as ambiguous. Recipes always map the model field name, so evidence locators show the matched source key. Candidates are ranked by mapped field count, then document order. Like other learned recipes, they require evidence for required fields and must validate successfully before they are persisted. Recipe proposals from a model cover only HTML strategies, so a configured model is not consulted for JSON documents. If no discovered candidate validates, the result is `insufficient_evidence` with `no_valid_recipe`.

The pipeline order is memory, then JSON-LD, then structured JSON, then DOM selectors, then the optional model. Each step applies only to the representation it reads, and Rastro never fetches a second representation of the same source. An HTML request runs memory, JSON-LD, DOM and the model. A JSON request runs memory and structured JSON.

Recipes serialize using `recipe.model_dump_json()` and load with `Recipe.model_validate_json(text)`, which returns the matching variant. Loading a recipe never evaluates Python. Schema compatibility is checked before parsing.

Rastro calls `schema.model_validate(..., context=...)`, so validators supported by
that Pydantic path receive `locale`, `languages`, `timezone`, `currency`, and
`egress` in `ValidationInfo.context` for explicit recipes, persisted recipes, cold
discovery/model learning, and acquired pages. A model with a custom `__init__`
follows Pydantic's constructor semantics and may validate without that context;
Rastro does not bypass or replace the constructor. Such a constructor must arrange
context propagation as documented by Pydantic. Rastro does not infer a currency or
convert values. Validator transformations do not create source evidence.

## Model providers

```python
from rastro import OllamaModel, OpenAICompatibleModel  # lazy; need an optional extra

OllamaModel(model, *, base_url="http://127.0.0.1:11434")                  # [ollama]
OpenAICompatibleModel(model, *, base_url="http://127.0.0.1:11434/v1",     # [openai]
                      api_key_env=None, allow_remote=False,
                      response_format="json_schema")
```

`model=` on `Rastro` and `Rastro.web` accepts any `ModelPort` strategy:
`async generate(request: ModelRequest, policy: InferencePolicy) -> str`, returning
JSON text for one recipe proposal or raising `ModelError`. Provider classes are
resolved lazily, so `import rastro` never imports HTTPX; accessing one without its
extra raises `ImportError` naming the extra. They are not in `rastro.__all__`.

`OpenAICompatibleModel` speaks the Chat Completions API. Non-loopback `base_url`
values raise `ConfigurationError` unless `allow_remote=True` and HTTPS are both
present. `api_key_env` names an environment variable that must be set at construction
and is read per request; the key is never stored, logged or shown by `repr`.
`response_format="json_object"` selects JSON mode for servers without JSON Schema
support. Constructor validation raises `ConfigurationError`; request failures raise
`ModelError`, which the learner turns into explicit outcomes. See
[choosing or writing a model provider](intelligence.md#choosing-or-writing-a-model-provider).

## Declarative jobs

```python
from rastro import (
    Job,
    JobExitCode,
    JobRunResult,
    build_job_schema,
    load_job,
    parse_job,
    run_job,
    run_job_async,
    run_job_file,
)
```

`Job` (`format_version = 1`) loads from UTF-8 TOML or strict JSON via `parse_job` / `load_job`, builds a deterministic dynamic `JobRecord` Pydantic model (`build_job_schema`), and executes through `Rastro` / `Rastro.web` (`run_job`, `run_job_async`, `run_job_file`), returning `JobRunResult` (`schema_version = 1`, `data_trust = "untrusted_source_data"`). Published JSON Schemas (`job_json_schema()`, `job_run_json_schema()`) and `rastro job {validate,run,schema}` expose the same contract without Python code; see [declarative jobs](jobs.md).

## Datasets and recipe repair

```python
from rastro import (
    JobLoadResult,
    RecipeSelection,
    RepairReport,
    apply_job_recipe,
    load_job_file,
    promote_job_recipe,
    repair_job_source,
)
```

| Function | Returns | What it does |
| --- | --- | --- |
| `load_job_file(path, *, drift=False, allow_absolute_paths=False)` | `JobLoadResult` | Runs the job and loads approved fields into its `[dataset]` (`rastro job load`). |
| `repair_job_source(path, source, *, min_records=None, min_completeness=None, auto_promote=None)` | `RepairReport` | Proposes and evaluates repair candidates for one source (`rastro recipe repair`). |
| `promote_job_recipe(path, source, version, *, reason, override_gates=False)` | `RepairReport` | Re-evaluates a stored candidate and activates it when it passes; `override_gates=True` activates a quarantined one (`rastro recipe promote`). |
| `apply_job_recipe(path, source)` | `Path` | Writes the serving recipe of a source back into the job file (`rastro recipe apply`). |

`source` is one-based. `RepairReport` has `schema_version = 1`, a `run_id` and one
`SourceRepair` per source with its `comparisons` (each a `RecipeComparison` with an
optional `CandidateEvaluation`) and `promoted_version`. Repair thresholds are set in the
job's `[repair]` table, not through a Python policy object. `JobRunResult.recipes`
lists one `RecipeSelection` (`source_index`, `recipe_origin`, `recipe_version`) per source;
`JobLoadResult` sources carry the same `recipe_origin` and `recipe_version`. See
[recipe repair](repair.md) for the gates and the rule that picks the recipe.

## Recipes from examples

```python
from rastro import FieldConfidence, Suggestion, SuggestionError, suggest
```

`suggest(html, schema, *, examples, url=None, records_min=2, limits=None)` builds a
`CssRecipe` from a saved page and example values, runs it through the normal extraction
pipeline and returns a `Suggestion` with `recipe`, `preview` (an `ExtractionResult`), `status` (the
preview outcome; check it is `success` before trusting the recipe) and
`confidence` (one `FieldConfidence` per schema field: `method` is `example`, `microdata` or
`unmapped`, plus `selector`, `examples_matched`, `examples_total`, `records_with_value`,
`ambiguous` and `note`). `schema` is a Pydantic model or a list of job field definitions.
It is deterministic and offline. `SuggestionError` (a `ConfigurationError`) lists the
examples that were not found or not reproduced. The CLI equivalents are `rastro suggest`
and `rastro new`; see [recipes from examples](suggest.md).

## Results

`ExtractionResult` contains:

- A unique `job_id`, start/completion timestamps, and `status`.
- Source URL with query/fragment removed, locale, and timezone metadata.
- A SHA-256 fingerprint of the schema's canonical JSON Schema.
- `records`: validated Pydantic models with a dictionary of evidence keyed by observed field name.
- `issues`: safe categories, candidate indices, and field names where applicable. A `validation_error` issue may carry `message`: Pydantic's error type and message, only for built-in types whose message is fixed or quotes schema constraints (a pattern, a bound, the allowed literals) (for example `int_parsing: Input should be a valid integer, unable to parse string as an integer`). It never contains the input value or a custom validator's text, and is omitted from serialized output when absent. `missing_evidence` is reported once for each field that lacks evidence: an observed value without a locator, or a missing required field when `require_evidence` is set.
- `metrics`: `acquisition_method` (`provided_html`, `provided_json`, or `http`), input bytes, candidate/accepted/rejected and validated-field counts, extraction/validation/total seconds, plus HTTP acquisition/transfer metrics, `inference_calls` and `inference_seconds`.
- `recipe_version`, `recipe_reused`, and ordered `recipe_attempts` (index/origin/outcome), plus `learned_recipe` for newly validated successful recipes.
- For HTTP: sanitized `requested_url`, last `http_status`, optional `retry_after_seconds`, and `metrics.egress` (the egress profile name; never the proxy URL or credentials).
- `repair`: `None` unless `scrape` ran a self-repair. Then a `SelfRepair` with `mode` (`propose` or `auto`), `previous_version`, `promoted`, `promoted_version` and `candidates`: one `RepairProposal` per stored candidate with its `version`, gate `decision` (`promote`, `quarantine` or `reject`), `reasons` and `changed_fields`. When `promoted` is true, the rest of the result comes from the promoted recipe. Promotion also requires relocated fields to keep the retained values' shape ([what auto guarantees](quickstart.md#what-auto-guarantees)); an identical stored candidate keeps its version. The key is omitted from serialized output when it is `None`.
- `items` (property, not serialized): the validated model instances from `records`, in order.

The schema fingerprint does not hash Python validators; custom validator changes require a new `RecipeContext.schema_revision` for memory isolation. Metrics count accepted and rejected **candidates**; malformed metadata blocks are reported separately and do not count as candidates. With explicit fallbacks, counts and parsing/validation timings sum all attempts; records/issues describe the final attempt. `storage_seconds` isolates repository I/O. Offline timings measure local processing. HTTP adds separate network and acquisition durations. Inference metrics include queue wait and model-candidate validation; see the [budget semantics](intelligence.md#budgets-and-outcomes).

### Outcomes currently emitted

| Outcome | Meaning |
| --- | --- |
| `success` | At least one record accepted and no reported issues. |
| `partial_success` | At least one record accepted and at least one reported issue. |
| `missing_data` | No matching candidates, no mapped values were observed, or a JSON `record_path` was absent (`record_path_not_found`). |
| `invalid_data` | No accepted records; malformed JSON-LD or JSON (`invalid_json`), a non-container record path or non-object record (`invalid_record_path`, `invalid_record`), or schema validation failed. |
| `insufficient_evidence` | No accepted records; ambiguous selection or missing field evidence. |

If all records fail and both ambiguity and validation issues exist, the summary is `insufficient_evidence`; all issues remain available. HTTP additionally emits `access_restriction`, `transient_failure`, and `permanent_failure`; see the [failure policy](acquisition.md#source-rules-and-failures). Optional model failures also emit `transient_failure`; exhausted or rejected recipe learning emits `insufficient_evidence` with a safe category. Model-free offline parsing does not emit network outcomes.

### Evidence and privacy

Each observed field has a sanitized source URL, observation timestamp, recipe version, extraction method, and locator. JSON-LD locators identify the script index and JSON pointer; JSON locators give the absolute pointer in the response; CSS locators identify the record selector, match index, and field selector. Pydantic defaults have no source evidence. A custom validator may normalize or transform a value; evidence refers to the original observation path, not an independent proof of the transformed value.

No raw HTML or excerpt is retained, except that `scrape(..., memory=...)` and job repair keep one sanitized copy of the last successful page for repair testing. User values and custom validator messages are omitted from diagnostics. Accepted data is intentionally returned to the caller and may be sensitive. URLs can also contain sensitive information in paths. Apply your own access and retention policy.

### Exceptions

`ConfigurationError` reports invalid selector syntax, unknown schema fields, or malformed `discovery_aliases`. `ResourceLimitError` rejects oversized input or too many candidates/blocks. `RecipeNotFoundError` reports a missing stored recipe; `RecipeStorageError` reports persistence failures and `RecipeConflictError` reports changed content at an existing key/version. All derive from `RastroError`. Invalid public input models raise Pydantic `ValidationError`. Exceptions do not produce a truncated successful result.

Programmer errors in custom validators or adapters propagate. This service does not hide arbitrary application failures.

## Logging

Rastro uses a standard `logging` logger named `rastro` with structlog JSON serialization. It does not configure global logging on import. Enable the logger in your host application, or use CLI `--verbose`.

Extraction completion events contain a correlation `job_id`, status, accepted/rejected counts, processing time, recipe version/reuse/attempt count, and storage time. HTTP also emits `acquisition_completed` with the same job ID, request/retry counts, and acquisition duration. `recipe_learning_completed` includes job ID, outcome, inference calls, model-phase duration and attempt count after any successful activation write. Records, source URLs, prompts and model responses are not logged.

## Boundaries

HTML recovery follows Scrapling/libxml behavior; content discarded by its parser is not independently recovered or verified. Input budgets are not a hostile-code sandbox or a wall-clock deadline. Optional discovery and model use have separate bounded policies. Browser rendering/geolocation, discovering API endpoints from pages, JSON embedded in HTML scripts, model-proposed JSON recipes, SOCKS or rotating egress, automatic repair of stored recipes and durable execution history remain pending.
