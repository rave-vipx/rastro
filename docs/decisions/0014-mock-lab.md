# ADR 0014: Mock Lab fixture format, synthetic labels and fail-closed sanitization

- **Status:** accepted for v0.2 (wave 1, Mock Lab core).
- **Requirements:** MOCK-001, MOCK-004, MOCK-005, MOCK-006; SEC-004 and SEC-011 for diagnostics.

## Context

v0.2 adds an Instant Mock Lab: test artifacts generated from real extraction sessions. That raises
three questions. How can a fixture stay replayable as Rastro evolves? How can a generated artifact
never be mistaken for observed source data? How can it be safe to commit a fixture derived from a
page that may contain cookies, tokens, personal data or confidential text? Mutations (MOCK-002)
and failure scenarios (MOCK-003) are built in parallel slices and need the same label.

## Decision

### Versioned bundle with fixed file names (`format_version = 1`)

`build_fixture` produces a `FixtureBundle`, and `FixtureBundle.write` writes four files:

| File | Contents |
| --- | --- |
| `manifest.json` | `FixtureManifest`: format version, label, representation, query-free `Source`, recipe, schema fingerprint, allowlisted response headers, SHA-256 digest per file |
| `body.html` or `body.json` | Sanitized body with the label embedded |
| `expected.json` | `ExpectedExtraction`: status, record data, evidence (method, locator, recipe version, source URL), issues, candidate counts |
| `records.json` | `SyntheticRecords`: schema-valid synthetic records and any resampled field names |

The manifest never names paths, so loading cannot be redirected outside the bundle directory.
`load_fixture` rejects digest mismatches, missing files, invalid JSON, non-UTF-8 files, files over
25 MB and labels that differ from the manifest's. `ExpectedExtraction` drops timestamps and timings
so replays compare deterministically.

Before `build_fixture` returns, it scans every rendered file and replays the labeled body through
`Rastro` offline with the manifest's recipe and source. If the expected extraction does not
reproduce, it raises `FixtureMismatchError` instead of returning a fixture that silently tests
nothing.

### `SyntheticLabel`: one small, stable label (MOCK-004)

```python
class SyntheticLabel(Contract):
    synthetic: Literal[True] = True
    generator: Literal["rastro.mocklab"] = "rastro.mocklab"
    label_version: Literal[1] = 1
    kind: Literal["fixture", "mutation", "failure"]
    seed: int  # 0..2**63-1
```

- HTML artifacts begin with `<!-- rastro-synthetic {...} -->`. Parsers ignore the comment and
  readers cannot miss it. The sanitizer removes every source comment, so the label is the only one.
- JSON artifacts carry it as the first key, `"__rastro_synthetic__"`. A JSON body whose root is not
  an object cannot carry a key, so it is refused rather than written unlabeled.
- The manifest stores the same label, and loading requires every file's label to match it.

Slices B (mutations) and C (failures) reuse this model with their own `kind`. Adding a field
requires a new `label_version`.

### Fail-closed sanitization with no off switch (MOCK-005)

The sanitizer keeps only what the recipe extracts, rather than trying to recognize every possible
secret. A retention plan built from parser candidates for the recipe and its fallbacks records:

- CSS: the extracted strings;
- JSON-LD and JSON: the exact pointers of extracted values.

Everything else is replaced:

- **Text** outside retained values becomes deterministic lowercase placeholder text of the same
  length, with whitespace preserved. This covers titles, form text, emails and phone numbers in
  copy, and confidential paragraphs.
- **Comments, processing instructions, `<style>` and non-JSON-LD `<script>` contents** are
  removed. JSON-LD blocks are kept only when a recipe reads JSON-LD. They are scrubbed like JSON
  bodies; `@graph` and `@type` keys stay, but an `@type` value stays only when it equals a token
  of the recipe (its `item_type`), at any depth. `@context` is scrubbed. An invalid block stays
  invalid, and blocks are indexed as the parser enumerates them (empty blocks take no index).
- **Attributes:** event handlers, `style`, `srcdoc`, `srcset`, `nonce` and `integrity` are
  dropped. The `value` of `password` and `hidden` inputs is always dropped. Every other value,
  including `class`, `id`, `name`, `for`, `lang` and `rel`, survives token by token only when an
  identifier token of the recipe's selectors names it (`.product` keeps class `product`,
  `[data-kind=card]` keeps `card`); other tokens become placeholders. Extracted values survive
  whole. URL attributes (`href`, `src`, `action`, `itemtype`, `itemid`...) keep scheme and host;
  credentials, query and fragment are removed, non-HTTP schemes become `#`, and each path segment
  survives only when the recipe names it and it does not look sensitive.
- **JSON** keys survive only on the path to a retained value or a JSON recipe's `record_path`;
  every other key becomes a unique placeholder. Unretained strings become placeholders, numbers
  become small deterministic integers, and booleans and null stay. Nesting deeper than 256 levels
  raises `SanitizationError`.
- **Response headers** keep only `content-type` and `content-language`. `Set-Cookie`,
  `Authorization` and API-key headers never qualify.
- **Source URL** is stored without its query string or fragment, which is also what evidence uses.
  Evidence records this URL, so it cannot be rewritten: when a path segment looks like a token,
  opaque identifier or personal data, generation fails with `SanitizationError`.

Pattern detectors are a second line of defense: JWTs, bearer/basic tokens, common API-key
formats, credential assignments, secret-looking URL parameters, emails, phone numbers, US social
security numbers, Luhn-valid card numbers, mod-97-valid IBANs, padded base64, long hex strings and
long letter-digit tokens. Text is checked as written and after percent-decoding and NFKC
normalization. If an extracted value, a retained structural
value or allowlisted header matches one, or any finished file still does, generation raises
`SanitizationError`. Diagnostics name the file or field and the category, never the matched value.

Placeholders come from a SHA-256 counter stream keyed by seed and namespace, never by the original
content. They reveal length and whitespace layout, nothing else, and are not open to dictionary
attacks.

### Determinism (MOCK-006)

The seed is in the manifest and in every label. Output has no timestamps, keys are emitted in a
stable order, and the random streams do not depend on Python's `random` implementation. Equal
inputs and seed produce byte-identical files.

### Synthetic records

Records are generated from field annotations and declared bounds for `str`, `int`, `float`,
`Decimal`, `bool`, `date`, `datetime`, URL types, `Literal`, `Enum` and optional variants, then
validated by the model. Faker is not added. A field the generator cannot satisfy (a `pattern`, a
nested type or a model validator) is resampled from the fixture's own expected values, which are
already in the bundle, and listed in `resampled_fields`. If no valid value exists, generation fails
explicitly.

## Consequences

- Fixtures from authenticated or personalized pages are safe by default but less realistic.
  Selectors that depend on unretained attribute values (for example `a[href*="?id="]`) fail the
  replay check and must be rewritten to use structure. This is reported, never hidden.
- Extracted values that look like personal data (an email field, say) block generation. Synthetic
  substitution of extracted values is future work.
- Hostnames are kept. A confidential hostname would survive unless it matches a detector.
- Extracted values that look like long identifiers (UUIDs, opaque SKUs of 20+ mixed characters)
  block generation, a deliberate fail-closed false positive.
- Source URLs whose paths carry identifiers (`/orders/<id>`) cannot become fixtures until
  evidence URLs can be rewritten consistently.
- Mock Lab composes adapters itself (`ScraplingParser`, `JsonParser`, `Rastro`) at the edge, as the
  public facade does. Domain code remains free of I/O and Scrapling.
- Structural mutations (MOCK-002) edit HTML trees with lxml and cssselect directly, both declared
  dependencies (they were already installed with Scrapling). Scrapling's parser port returns
  candidates, not editable trees, so routing edits through it would add indirection without
  removing lxml. This code stays in `rastro.mocklab.mutations`, outside domain code, and
  byte-identical HTML output is guaranteed only for a given lxml and libxml2 version.
- Bundles with JSON array roots are refused until a label convention for them is agreed.

## Alternatives considered

- **Keep structural attributes (`class`, `id`, `name`...) verbatim.** Rejected after review:
  values such as `id="sess-<hex>"` or `class="user-jane-doe"` leak; replay needs only the tokens the
  recipe names.
- **Detect and redact secrets, keep everything else.** Rejected: no pattern set covers
  confidential prose or new token formats, and a miss leaks silently.
- **Placeholders derived from hashes of the original text.** Rejected: short secrets such as PINs
  could be recovered by dictionary attack, and equal values would be linkable.
- **Faker for synthetic records.** Rejected: it is a new dependency, its output changes between
  versions and locales, and it does not follow Pydantic constraints.
- **Label JSON in a sidecar file only.** Rejected: a body copied out of the bundle would lose its
  label.
