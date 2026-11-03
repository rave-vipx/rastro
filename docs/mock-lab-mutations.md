# Instant Mock Lab: structural mutations

`rastro.mocklab.mutations` changes the structure of an HTML or JSON document in controlled,
reproducible ways, so you can check offline which layout or API drift a recipe survives
(MOCK-002, MOCK-006). Mutated documents carry the Mock Lab synthetic label from
[ADR 0014](decisions/0014-mock-lab.md) (MOCK-004).

## Apply a plan

```python
from rastro import CssRecipe, Rastro, Source
from rastro.mocklab.mutations import HtmlChangeClasses, HtmlReorderChildren, MutationPlan

page = (
    "<div class='product'><h2>Notebook</h2><p class='price'>12.50</p></div>"
    "<div class='product'><h2>Pen</h2><p class='price'>2.00</p></div>"
)
plan = MutationPlan(
    seed=7,
    operators=(
        HtmlReorderChildren(selector=".product"),
        HtmlChangeClasses(selector="p", rename={"price": "amount"}),
    ),
)
result = plan.apply(page)
print(result.status)  # applied
for step in result.steps:
    print(step.operator, [change.model_dump() for change in step.changes])
print(result.label)  # synthetic=True ... kind='mutation' seed=7
```

Feed `result.document` to `Rastro().extract_html(...)` with your recipe and compare the outcome
with the unmutated page. With `CssRecipe(record_selector=".product", fields={"name": "h2::text",
"price": ".price::text"})`, reordering alone still returns `success`; the class rename makes the
required price disappear, and the result becomes `invalid_data`.

## Operators

Each MOCK-002 category has one HTML and one JSON operator. Every operator is a typed, frozen
Pydantic model, identified by its `operator` name.

| Category | HTML | JSON |
| --- | --- | --- |
| DOM reordering | `HtmlReorderChildren`: permute element children | `JsonReorder`: permute array items or object keys |
| Class changes | `HtmlChangeClasses`: `rename`, `remove`, `add`, `hash_suffix` | `JsonChangeType`: map discriminator strings (`@type`, `kind`) |
| Element wrapping | `HtmlWrapElement`: new parent element | `JsonWrapValue`: nest the value under `wrapper_key` |
| Missing fields | `HtmlRemoveField`: remove the element or one `attribute` | `JsonRemoveField`: delete the key or set it to `null` |
| Duplicate elements | `HtmlDuplicateElement`: `copies` after the original | `JsonDuplicateItem`: copy array items |
| Empty containers | `HtmlEmptyContainer`: drop children and text | `JsonEmptyContainer`: clear an array or object |
| Changed labels | `HtmlChangeLabel`: element text or an attribute such as `aria-label` | `JsonRenameKey`: rename a key, keeping order |
| Modified pagination | `HtmlModifyPagination`: `rewrite_href`, `disable`, `remove` | `JsonModifyPagination`: `rewrite_cursor`, `end`, `shift_page` |

HTML operators target a CSS `selector`. JSON container operators target a `pointer`; JSON field
operators edit `field` (a pointer inside each record) in every object found at `records`, which
points at one object or an array of objects. `max_targets` limits how many eligible targets change;
the seed decides which ones.

## Explicit outcomes

An operator never reports success without a change. Each `MutationResult` is either:

- `applied`, with one or more `MutationChange` records: the configured `target`, `path_before` and
  `path_after` (XPath for HTML, RFC 6901 pointers for JSON; `path_after` is `null` when the node was
  removed) and `before`/`after` summaries (JSON values are JSON-encoded); or
- `no_applicable_target`, with no changes, the document unchanged and a `reason`:
  `empty_document`, `invalid_document`, `target_not_found` (nothing matched) or
  `no_eligible_target` (matches exist but the edit would change nothing, for example removing a
  class that is absent or emptying an empty container).

A plan is `applied` when at least one step applied. Otherwise its `document` is the input, unchanged
and unlabeled, because no artifact was produced.

## Reproducibility

- `MutationPlan` has `format_version = 1`, a `seed` (0 to 2^63 - 1) and up to 64 operators of one
  representation. `to_json()` is canonical (sorted keys, no whitespace), `from_json()` restores it,
  and `fingerprint` is the SHA-256 of that text.
- Each step draws from its own SHA-256 stream keyed by seed, step index and operator name. Output
  does not depend on Python's `random` module, `PYTHONHASHSEED` or on the other steps' choices, so
  equal plans and inputs give byte-identical documents.
- Configurations are strict: unknown keys, wrong types, invalid selectors or pointers, and operators
  without an effect (`HtmlChangeClasses` with nothing to do) are rejected. In Python, pass tuples
  for sequence fields; JSON arrays are accepted by `from_json`.

## Labels

When a plan applies, its document carries `SyntheticLabel(kind="mutation", seed=plan.seed)`: a
leading HTML comment, or the first JSON key `"__rastro_synthetic__"`. A label already present on the
input, such as a fixture body's, is removed before mutating so operators never see it. JSON
documents whose root is not an object cannot carry a label, so applying a plan that changes one
raises `MockLabError`. The label does not include the plan; store `plan.to_json()` or
`result.plan_fingerprint` beside the artifact to trace it.

## Limits

- HTML is parsed and serialized with lxml (installed with Scrapling). Serialization normalizes the
  markup: a fragment gains `<html><body>`, and the doctype and attribute quoting are rewritten.
- Inputs above `Limits().max_html_bytes` or `max_json_bytes`, or JSON beyond `max_json_depth` or
  `max_json_nodes`, raise `ResourceLimitError`.
- Operators edit document structure, not text content. Use fixtures (see
  [Mock Lab fixtures](mock-lab.md)) for sanitized source data.
