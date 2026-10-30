# API and result contracts

## `Rastro`

```python
Rastro(*, parser=None, limits=None, acquisition=None, policy=None, repository=None,
       model=None, inference_policy=None, discover_recipes=False, discovery=None)
Rastro.web(policy, *, limits=None, repository=None, model=None,
           inference_policy=None, discover_recipes=False)
Rastro.extract_html(html, *, source, schema, recipe=None, context=None, recipe_version=None)
Rastro.extract_json(text, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract_html_async(html, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract_json_async(text, *, source, schema, recipe=None, context=None, recipe_version=None)
await Rastro.extract(*, source, schema, recipe=None, context=None, recipe_version=None,
                     representation="html")
await Rastro.extract_many(sources, *, schema, recipe=None, context=None, recipe_version=None,
                          representation="html")
```

`html` is a UTF-8-compatible Python string. `schema` is a Pydantic `BaseModel` subclass with declared fields. `recipe.fields` maps model field names to extraction expressions. Aliases and root models are outside this milestone's supported schema contract. Custom validators run as trusted caller code. Rastro supplies the source's regional context through Pydantic's `model_validate` context path, subject to Pydantic's constructor semantics described below.

The `extract_html` call is synchronous, does no fetching, and returns `ExtractionResult[YourModel]`. Persistence is opt-in through `repository`; omitting `recipe` then reuses an exact-context validated version. See [recipe memory](memory.md). Asynchronous methods optionally learn cold-context recipes when `discover_recipes=True` or `model=` is configured; see [optional intelligence](intelligence.md). Parser injection is an infrastructure seam for trusted adapters implementing `DocumentParser`.

`extract_json` and `extract_json_async` accept supplied JSON text and the `json` recipe strategy; see [structured JSON responses](#structured-json-responses). `Representation` is the public `Literal["html", "json"]` type for the `representation` argument.

For HTTP acquisition, construct `Rastro.web(AcquisitionPolicy(...))` and use the asynchronous methods. An injected `AcquisitionPort` is trusted infrastructure; it must enforce its own acquisition policy. See the [HTTP guide](acquisition.md) for budgets, outcomes, cancellation and transport controls.

## Inputs

| Object | Fields and behavior |
| --- | --- |
| `Source` | HTTP(S) `url`; `locale` (`und`); ordered `languages` (`()`); `timezone` (`UTC`); optional three-letter uppercase `currency`; and `egress` (`direct`, or the name of a policy egress profile). Credentials, invalid language tags/currency codes/egress names and a `geolocation` value (no browser adapter in v0.1) are rejected; an egress name missing from the policy fails before DNS. Explicit languages form Accept-Language with descending quality weights; otherwise a defined locale remains the compatible fallback. |
| `Recipe` | `strategy` (`jsonld`, `css` or `json`), required `fields`, positive `version` (default `1`), optional `item_type` for JSON-LD, required `record_selector` for CSS, required `record_path` for JSON, up to three flat `fallbacks` sharing its version and document representation. `require_evidence` (default `False`) requires observations for required fields before validation; learned recipes set it to `True`. Version maximum: 2,147,483,647. |
| `Limits` | `max_html_bytes` (2,000,000), `max_records` (1,000), `max_jsonld_blocks` (100), `max_json_bytes` (2,000,000), `max_json_depth` (32), `max_json_nodes` (100,000). |
| `AcquisitionPolicy` | Exact `allowed_hosts`, request budgets and timeouts (see [policy defaults](acquisition.md#policy-defaults)), plus `egress_profiles`: up to 16 names mapped to an `EgressProfile` (`proxy_url`, optional `username_env`/`password_env` environment-variable references) or a bare `http(s)://host[:port]` string. See [network egress](acquisition.md#network-egress). |

CSS fields must end in `::text` or `::attr(name)` and match at most one value in each record. Multiple matches reject the record as ambiguous, even if the values happen to be equal. Missing matches omit the field; Pydantic decides whether the omission is allowed. CSS `::text` follows Scrapling's direct-text semantics; it does not join descendant text automatically.

JSON-LD type matching is exact and accepts an `@type` string or array. Nested field values are traversed only by explicit JSON pointers. The object count budget includes filtered-out objects to bound scanning. Malformed blocks, duplicate JSON keys, and nonstandard `NaN`/`Infinity` values are reported as invalid JSON-LD.

## Structured JSON responses

A `json` recipe reads one JSON document: supplied text, or an HTTP response requested with `representation="json"` (see the [HTTP guide](acquisition.md#json-responses)).

```python
recipe = Recipe(
    strategy="json",
    record_path="/data/products",  # RFC 6901 pointer; "" selects the whole document
    fields={"name": "/name", "price": "/offer/price"},  # relative to each record
)
result = Rastro().extract_json(text, source=source, schema=Product, recipe=recipe)
```

- `record_path` must resolve to an object (one record) or an array. Each array element must be an object; other elements are rejected individually as `invalid_record`.
- Field pointers are absolute RFC 6901 pointers evaluated from each record. Array indexes are decimal without leading zeros; `-` never matches. A pointer selects exactly one value, so JSON extraction has no ambiguous matches. A missing pointer omits the field and Pydantic decides whether that is allowed.
- There is no JSONPath, wildcard, filter, or expression language, and nothing is evaluated.
- Input is decoded strictly. Duplicate object keys, `NaN`/`Infinity`, malformed text, an empty body and a byte-order mark reject the whole document as `invalid_json`. Rastro never picks one of several duplicate values.
- `max_json_bytes`, `max_json_depth` (container nesting below the root) and `max_json_nodes` (every value, including containers) are checked before records are selected. `max_records` bounds the selected records. A breach raises `ResourceLimitError` for supplied text and becomes `extraction_limit` for HTTP.

Evidence uses method `json` with locator `json#<absolute pointer>`, for example `json#/data/products/1/offer/price`. JSON recipes require JSON input and HTML recipes require HTML input: a mismatched explicit recipe raises `ConfigurationError` before any request. Recipe memory keys include the representation. HTML keys keep their existing canonical form, so a stored JSON recipe never answers an HTML request for the same source (or the reverse).

With `discover_recipes=True`, `extract_json_async` and JSON HTTP extraction discover recipes deterministically. Candidates are the root object and every array whose elements are all objects, up to `max_records` elements. In up to 20 sampled records, a field maps only when its exact name occurs once per record, and every sampled record containing it agrees on its pointer. Synonyms, case folding and array traversal are not attempted. Candidates are ranked by mapped field count, then document order. Like other learned recipes, they require evidence for required fields and must validate successfully before they are persisted. Recipe proposals from a model cover only HTML strategies, so a configured model is not consulted for JSON documents. If no discovered candidate validates, the result is `insufficient_evidence` with `no_valid_recipe`.

The pipeline order is memory, then JSON-LD, then structured JSON, then DOM selectors, then the optional model. Each step applies only to the representation it reads, and Rastro never fetches a second representation of the same source. An HTML request runs memory, JSON-LD, DOM and the model. A JSON request runs memory and structured JSON.

Recipes serialize using `recipe.model_dump_json()` and load with `Recipe.model_validate_json(text)`. Loading a recipe never evaluates Python. Schema compatibility is checked before parsing.

Rastro calls `schema.model_validate(..., context=...)`, so validators supported by
that Pydantic path receive `locale`, `languages`, `timezone`, `currency`, and
`egress` in `ValidationInfo.context` for explicit recipes, persisted recipes, cold
discovery/model learning, and acquired pages. A model with a custom `__init__`
follows Pydantic's constructor semantics and may validate without that context;
Rastro does not bypass or replace the constructor. Such a constructor must arrange
context propagation as documented by Pydantic. Rastro does not infer a currency or
convert values. Validator transformations do not create source evidence.

## Results

`ExtractionResult` contains:

- A unique `job_id`, start/completion timestamps, and `status`.
- Source URL with query/fragment removed, locale, and timezone metadata.
- A SHA-256 fingerprint of the schema's canonical JSON Schema.
- `records`: validated Pydantic models with a dictionary of evidence keyed by observed field name.
- `issues`: safe categories, candidate indices, and field names where applicable.
- `metrics`: `acquisition_method` (`provided_html`, `provided_json`, or `http`), input bytes, candidate/accepted/rejected and validated-field counts, extraction/validation/total seconds, plus HTTP acquisition/transfer metrics, `inference_calls` and `inference_seconds`.
- `recipe_version`, `recipe_reused`, and ordered `recipe_attempts` (index/origin/outcome), plus `learned_recipe` for newly validated successful recipes.
- For HTTP: sanitized `requested_url`, last `http_status`, optional `retry_after_seconds`, and `metrics.egress` (the egress profile name; never the proxy URL or credentials).

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

No raw HTML or excerpt is retained. User values and custom validator messages are omitted from diagnostics. Accepted data is intentionally returned to the caller and may be sensitive. URLs can also contain sensitive information in paths. Apply your own access and retention policy.

### Exceptions

`ConfigurationError` reports invalid selector syntax, unknown schema fields, or unsupported aliases. `ResourceLimitError` rejects oversized input or too many candidates/blocks. `RecipeNotFoundError` reports a missing stored recipe; `RecipeStorageError` reports persistence failures and `RecipeConflictError` reports changed content at an existing key/version. All derive from `RastroError`. Invalid public input models raise Pydantic `ValidationError`. Exceptions do not produce a truncated successful result.

Programmer errors in custom validators or adapters propagate. This service does not hide arbitrary application failures.

## Logging

Rastro uses a standard `logging` logger named `rastro` with structlog JSON serialization. It does not configure global logging on import. Enable the logger in your host application, or use CLI `--verbose`.

Extraction completion events contain a correlation `job_id`, status, accepted/rejected counts, processing time, recipe version/reuse/attempt count, and storage time. HTTP also emits `acquisition_completed` with the same job ID, request/retry counts, and acquisition duration. `recipe_learning_completed` includes job ID, outcome, inference calls, model-phase duration and attempt count after any successful activation write. Records, source URLs, prompts and model responses are not logged.

## Boundaries

HTML recovery follows Scrapling/libxml behavior; content discarded by its parser is not independently recovered or verified. Input budgets are not a hostile-code sandbox or a wall-clock deadline. Optional discovery and model use have separate bounded policies. Browser rendering/geolocation, discovering API endpoints from pages, JSON embedded in HTML scripts, model-proposed JSON recipes, SOCKS or rotating egress, automatic repair of stored recipes and durable execution history remain pending.
