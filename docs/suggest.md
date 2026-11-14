# Recipes from examples

You know which data you want from a page but not how to write CSS selectors.
Save the page, give one or two example values per field, and Rastro builds a
recipe, runs it on the same page and shows you what it extracts. It works
offline, gives the same result every time and uses no AI model.

## From Python

```python
from pydantic import BaseModel

import rastro


class Offer(BaseModel):
    title: str
    salary: int


html = open("careers.html", encoding="utf-8").read()
s = rastro.suggest(
    html,
    Offer,
    examples=[{"title": "Senior Dev", "salary": "120"}],
    url="https://acme.example/careers",
)
s.recipe  # CssRecipe: record_selector and one selector per field
s.preview  # ExtractionResult of running s.recipe on the page with Offer
s.confidence  # per field: how it was mapped, examples matched, records with a value
```

`schema` is a Pydantic model or a list of job field definitions, for example
`[rastro.StringField(name="title"), {"name": "salary", "type": "integer"}]`.
Example values are strings. Give several example records to make the result
more robust: `examples=[{"title": "Senior Dev"}, {"title": "Analyst"}]`.

The returned recipe is a normal `CssRecipe`. Pass it to
`Rastro().extract_html(..., recipe=s.recipe)`, save it as JSON, or put it in a
job file.

## From the command line

```bash
rastro suggest careers.html --url https://acme.example/careers \
  --example title="Senior Dev" --example salary=120
```

The command prints JSON with the recipe, the preview status, per-field notes and
up to 50 preview records. `--output table` prints the recipe followed by a
plain table of the preview. Every field is treated as text.

Each `--example FIELD=VALUE` adds a value to the current example record.
Repeating a field starts the next record:

```bash
rastro suggest careers.html \
  --example title="Senior Dev" --example salary=120 \
  --example title=Analyst --example salary=95
```

### Create a job file

```bash
rastro new careers.toml --from careers.html --url https://acme.example/careers \
  --example title="Senior Dev" --example salary=120 \
  --dataset offers.sqlite --identity title --recipe-memory recipes.sqlite
```

`rastro new` writes a complete [job file](jobs.md) with the suggested recipe, a
file source pointing at the saved page and one field per example key. It never
overwrites an existing file, and the page must be in the same directory as the
job file or below it. `--dataset` needs at least one `--identity` field.
`--recipe-memory FILE` adds a recipe store (relative to the job, in a folder that
already exists), which `rastro recipe repair`, `promote` and `apply` need.

Field types are inferred from the example values, conservatively:

| Example values | Field type |
| --- | --- |
| `120`, `-3` (no leading zeros, no separators) | `integer` |
| `9.50`, or integers mixed with decimals | `decimal` |
| `2026-03-01` | `date` |
| `https://...` | `url` |
| anything else, including `1,200`, `007` and `$120` | `string` |

A field keeps its inferred type only if every value on the page validates with
it; otherwise it stays a `string`. The new job passes `rastro job validate` and
runs with `rastro job run`. Review it, then edit types and constraints as you
need.

## With a model

Give a model and the examples become optional:

```python
s = rastro.suggest(html, Offer, model="env")  # RASTRO_MODEL, or any ModelPort
```

```bash
rastro suggest careers.html --model env --field title --field salary
rastro new job.toml --from careers.html --model env --field title --field salary
```

The model proposes a CSS recipe from the field names, types and descriptions (at most
two calls by default). It is verified exactly like an example-built one: it must
extract at least `--records-min` records that pass the schema, and reproduce every
example you did give. With examples, the deterministic suggester runs first and the
model is asked only when it finds nothing. `confidence[field].method` is `model` for
its fields. `--model` takes a spec for `rastro.model` or `env` (`RASTRO_MODEL`);
remote models need `--allow-remote`. Review the preview: without examples, only the
schema checks the values.

## How it works

1. **Find the examples.** Each example value is looked up in the page text,
   ignoring extra spaces and non-breaking spaces. Exact text wins over a value
   contained in longer text. Then the attributes `href`, `src`, `content`,
   `datetime` and `data-*` are checked; a relative link matches an absolute
   example URL when you pass `url`. Script, style and template content is never
   searched.
2. **Find the repeating record.** Starting from the elements that hold one
   example record, Rastro climbs to the smallest element that contains all of
   them and repeats on the page at least `records_min` times (default 2). It
   prefers short selectors made of tag names and class names, and never uses
   ids or class names that contain digits or look generated (such as `css-1x2y`
   or `jsx-381`).
3. **Pick a selector per field.** For each field it chooses the shortest
   selector, relative to the record, that finds at most one element in every
   record and reproduces the example value. Values come from `::text` or
   `::attr(name)`.
4. **Fields without examples.** A field without an example is mapped only
   when each record has at most one microdata `itemprop` with exactly the
   field name. Otherwise an optional field stays unmapped, with a note, and a
   required field is an error.
5. **Verify.** Each candidate recipe runs through the normal extraction
   pipeline with your schema. The first one that reproduces every example and
   accepts every record wins; one that rejects some records is used only if
   nothing better exists. If no candidate works, `SuggestionError` lists the
   examples that were not found or not reproduced.

## Errors and limits

`SuggestionError` (a `ConfigurationError`) is raised when an example value is
not on the page, when only one matching record exists and `records_min` is 2
or more, or when no candidate reproduces every example. Pass `records_min=1`
(`--records-min 1`) for a page that holds a single record.

Work is bounded: the page must fit `Limits.max_html_bytes`, the document may
hold at most 50,000 elements, and the number of matches, candidate selectors
and records scored are capped. At most 20 example records of up to 1,000
characters per value are accepted. Your example values are never used as
regular expressions or selectors.

Known limits: a table whose header row is not inside `<thead>` gives a recipe
that also matches the header row, so its preview is `partial_success`. Values
split across several text nodes of one element (for example
`Senior <em>Dev</em>`) cannot be matched as one value; give the part inside a
single element instead.
