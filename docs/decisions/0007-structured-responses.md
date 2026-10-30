# ADR 0007: permitted structured JSON responses

- **Status:** accepted for the v0.1 structured-response slice (INT-002 step 3).
- **Requirements:** INT-001–004, QUAL-001–004, REC-001–004, SEC-001, SEC-005,
  SEC-008 and specification section 12.1.

## Context

INT-002 puts permitted structured responses and APIs between JSON-LD and DOM
heuristics. Rastro could only read HTML: JSON-LD inside `<script>` blocks, or CSS
selectors. A caller with a permitted JSON endpoint had to wrap the JSON in HTML or
bypass validation and provenance.

Structured responses widen the untrusted input surface. JSON can be deeply nested,
very wide, or ambiguous because of duplicate keys. Expression languages such as
JSONPath, JMESPath and jq add wildcards, filters and sometimes functions, which
conflicts with REC-002 (declarative recipes) and SEC-008 (bounded resources). An HTTP
client that silently accepted any content type could also misinterpret an HTML error
page as data, or the reverse.

## Decision

Add a `json` recipe strategy. It has a `record_path` (RFC 6901 pointer to an object
or an array of objects; `""` selects the whole document) and field pointers relative
to each record. Pointers are the only addressing mechanism. There are no wildcards,
filters, recursive descent or evaluation. JSON-LD already uses RFC 6901, so the
pointer resolver moves to the domain layer and both strategies share it.

Parse JSON with the existing strict decoder. Duplicate keys, non-finite constants, and numbers
that overflow or underflow a finite float or have more than 4,300 integer digits
reject the entire document as `invalid_json`. JSON-LD shares the decoder. Rastro does not choose between
duplicate values. New `Limits` fields bound bytes, nesting depth and total nodes, and
`max_records` still bounds selected records. Python's own recursion guard is mapped to
the depth limit. Each breach raises `ResourceLimitError`, as with HTML budgets. Every
selected record goes through the same Pydantic path, regional context, evidence check
and outcome mapping as HTML candidates. Evidence uses method `json` and the absolute
pointer as its locator.

JSON parsing and discovery are stdlib-only adapters (`JsonParser`, `JsonDiscovery`)
behind the existing `DocumentParser` and `RecipeDiscovery` ports. They are not
injectable in the public constructor, which keeps the API small. Callers opt in
explicitly with `extract_json`, `extract_json_async`, or
`representation="json"` on HTTP methods:

- The `Accept` header changes to `application/json` only when JSON is requested.
- Only `application/json` and `application/*+json` responses are accepted.
  Anything else is a permanent `unsupported_content_type` failure, and no second
  representation is fetched.
- Non-UTF-8 charsets are rejected (RFC 8259).
- A recipe and its fallbacks share one representation. A mismatch with the
  requested input raises `ConfigurationError` before any request.

`RecipeKey` gains `representation`. Like ADR 0006's regional fields, the canonical
key omits it for HTML, so every existing SQLite row keeps its exact key. JSON
recipes get distinct keys and never answer an HTML request for the same URL. The
new optional `Recipe.record_path` defaults to `None`, so stored M3/M4 payloads still
validate, and conflict detection compares validated definitions.

Discovery for JSON considers the root object and arrays whose elements are all
objects. A field maps only when its exact name occurs once in each sampled record
(up to 20 records) and all samples agree on one pointer. Candidates are ranked by
mapped fields, then document order, and validated before activation. The model
port's proposal schema covers only HTML strategies, so models are not consulted for
JSON. The INT-002 order is therefore memory → JSON-LD → structured JSON → DOM →
model, with each step applied only to the representation it can read.

## Consequences

- Permitted JSON APIs have the same outcomes, provenance and memory guarantees as
  HTML, with no new dependency, table migration or executable recipe content.
- Pointers cannot express "every item's price under any key". Callers needing that
  must choose a precise `record_path`. Field values inside arrays are reached only
  by explicit indexes. Discovery does not traverse arrays within a record.
- Rejecting duplicate keys may refuse documents that lenient parsers accept. This
  is deliberate: the alternative silently picks one value.
- A byte-order mark is rejected as invalid JSON rather than stripped.
- HTML pages that embed JSON in `<script type="application/json">` blocks, API
  endpoint discovery from pages, and model-proposed JSON recipes are out of scope.
- `AcquisitionResult.html` holds decoded JSON text for JSON requests. The field
  name was kept to avoid churn in the acquisition boundary, which is changing in
  parallel.

## Verification

Offline tests cover pointer escaping and indexing (property-based), recipe shape and
legacy payload loading, byte/depth/node/record budgets, malformed and duplicate-key
input, per-record and document-level outcomes, absolute locators, discovery ranking
and ambiguity rejection, memory precedence and reuse across a restart, model
exclusion, representation-isolated keys, and JSON HTTP acquisition through the fake
transport. That includes the Accept header, `+json` types, HTML and `text/json`
rejection, non-UTF-8 charsets, budget breaches and batches. The
`examples/structured_api.py` example runs in a test across two processes.
