# Quickstart

This page takes about five minutes. You declare the data you want as a Pydantic
model, point Rastro at a page, and get validated objects back. Then you turn on
recipe memory and watch Rastro repair itself when the page changes.

```sh
pip install rastro-engine
# To scrape URLs directly, add the HTTP extra:
pip install 'rastro-engine[http]'
```

## 1. Declare the model with its selectors

Put the CSS selector next to each field with `rastro.css(...)`. Each selector
ends in `::text` or `::attr(name)`. `__records__` selects one element per record;
leave it out and the whole page is a single record.

```python
from typing import Annotated, ClassVar

from pydantic import BaseModel, HttpUrl

import rastro


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3::text")]
    url: Annotated[HttpUrl, rastro.css("a::attr(href)")]
    salary: Annotated[int | None, rastro.css(".salary::text")] = None
```

The selectors do not change the model's JSON Schema. Before validation, a
marked field's text is stripped of surrounding whitespace (non-breaking spaces
included), exactly as in declarative jobs. Blank text in a field that cannot be
`None` (such as `title: str`) is a validation issue, never an empty string; a
field typed `str | None` keeps `""` when the element is present but blank. A URL
field also resolves `/path`, `./path` and `../path` links against the page URL.

## 2. Scrape

```python
html = """
<ul>
  <li class="job"><h3>Platform engineer</h3><a href="/careers/1">Apply</a>
    <span class="salary">5200</span></li>
  <li class="job"><h3>Product designer</h3><a href="/careers/2">Apply</a></li>
</ul>
"""

result = rastro.scrape(html, Offer, url="https://acme.example/careers")
print(result.status)  # Outcome.SUCCESS
for offer in result.items:  # validated Offer instances
    print(offer.title, offer.url, offer.salary)
```

`target` can be:

- an HTML string;
- a `pathlib.Path` to a UTF-8 file (symlinks are refused and the size is capped by `Limits`);
- an `http://` or `https://` URL, which needs the `http` extra. Rastro fetches it
  once, allows only that URL's host (redirects to other hosts are refused) and
  honours `robots.txt`.

`url=` is the page address used for relative links and provenance. For strings
and files it defaults to `https://localhost/`; for a URL target it is the target.

Failures stay explicit. If a required field is missing or blank, `result.status` says
so (for example `invalid_data`), `result.items` is empty and `result.issues`
names the field. Rastro never returns an empty success.

The full example is [`examples/scrape.py`](https://github.com/rave-vipx/rastro/blob/main/examples/scrape.py).

### Other ways to say what to extract

- `records="article"` and `fields={"title": "h2::text"}` override or add to the
  model's selectors for one call.
- `recipe=` passes any recipe unchanged, including `JsonLdRecipe` and `JsonRecipe`.
- With no selectors at all, Rastro tries JSON-LD and microdata whose names match
  your field names exactly (or their declared aliases). If nothing matches it
  raises `ConfigurationError` telling you to declare `rastro.css(...)`, pass
  `fields=`, or use `rastro.suggest`.

## 3. Remember the recipe and repair it offline

Pass `memory=` and Rastro stores the recipe that worked, keyed by page,
model and selectors. It also keeps one sanitized copy of the last successful
page in a file next to it (`recipes.samples.db` for `recipes.db`). That sample
is what a repair is tested against.

```python
result = rastro.scrape(
    html, Offer, url="https://acme.example/careers", memory="recipes.db", repair="auto"
)
```

When a remembered recipe stops matching (records disappear or a required field
is missing), `repair` decides what happens:

| `repair` | Behaviour |
| --- | --- |
| `"off"` | Return the failure as is. |
| `"propose"` (default) | Build repair candidates from the current page, test each one against the retained sample, store them as candidates and return the failure with `result.repair` describing them. Nothing is promoted. |
| `"auto"` | Same, but if a candidate passes every gate it becomes the serving recipe, the page is extracted again with it and that result is returned with `result.repair.promoted == True`. If any gate fails, it behaves like `"propose"`. |

Repair is deterministic: it relocates the old elements by their saved
fingerprints, tries declared fallbacks and exact-name structured data. It never
calls an AI model and never fetches anything beyond the page you asked for.

### Demo: a renamed class heals itself

Version 1 of a page uses `<h3 class="title">`. Version 2 renames it to
`<h2 class="heading">`.

```python
class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3.title::text")]
    city: Annotated[str, rastro.css(".city::text")]


first = rastro.scrape(version_1, Offer, url=URL, memory="recipes.db", repair="auto")
second = rastro.scrape(version_2, Offer, url=URL, memory="recipes.db", repair="auto")

second.status  # Outcome.SUCCESS
second.repair.promoted  # True
second.recipe_version  # 2
second.repair.candidates[0].changed_fields  # ('title',)
```

Run it with `python examples/self_repair.py`. See
[`examples/self_repair.py`](https://github.com/rave-vipx/rastro/blob/main/examples/self_repair.py).

### What `"auto"` guarantees

A candidate is promoted only when all of these hold:

- it yields complete, valid records on the current page, with evidence for
  every required field and no rejected records;
- replayed on the retained sample, it reproduces the sample's records exactly;
- for every field whose selector changed, the new values keep the field's
  shape: the same JSON types; for text, a length within 3x of the sample's
  median, a similar mix of digits, letters and other characters, and the same
  use of an `http(s)://` prefix;
- those values are not taken only from another field's sample values (a title
  relocated onto the city text fails with `value_matches_other_field`);
- it is the only candidate that passes.

A failed check is listed in `result.repair.candidates[i].reasons`
(`value_shape_changed`, `value_type_changed`, `value_matches_other_field`,
`insufficient_history`, ...) and `"auto"` then behaves like `"propose"`.

The limits: the checks compare shapes, not meaning. A relocated field can
still pick a different element whose values look alike (one product name
instead of another), and a field missing from the sample is not shape-checked.
Use `"propose"` when a wrong value costs more than a failed run, and review
candidates with the [recipe lifecycle](recipe-lifecycle.md) commands.

A candidate identical to one already stored is reused under its existing
version, so repeated failing runs do not pile up versions. Running `"auto"`
later promotes that stored candidate if it now passes every check.

Editing a field's selector in your model starts a fresh recipe history, so your
own changes always win over a remembered repair. To start over, delete
`recipes.db` and `recipes.samples.db`.

## With a model

Everything above works without AI. A model makes the difference where rules give up,
and it never supplies the data: it proposes **recipes** and **judgements**, and every
proposal passes the same deterministic checks as a relocated one.

```python
port = rastro.model()  # reads RASTRO_MODEL; any ModelPort object works too
result = rastro.scrape(page, Offer, memory="recipes.db", repair="auto", model=port)
result.repair.origin  # "relocation" or "model": where the candidates came from
result.repair.model_calls  # proposal and judge calls this repair made
```

- **No selectors.** A model without `rastro.css` markers, on a page without matching
  structured data, gets a recipe proposed from the field names, types and descriptions;
  it is kept only if extraction and the schema validate, and `memory=` remembers it.
- **Repair.** Relocation runs first. Only when none of its candidates would pass does
  the model propose (at most two calls by default). Rastro prefixes each changed
  selector with the remembered one (`"old, new"`), so the retained sample still parses,
  and gates the result like any candidate.
- **Meaning.** Before `"auto"` promotes any candidate (a model candidate must first
  pass the value checks: no values borrowed from another field), the model judges
  whether each changed field's new values match the field (name, type, description, earlier values). A `mismatch`
  quarantines the candidate (`semantic_mismatch`); `unsure` promotes only when every
  deterministic gate is at its strictest; a failed or malformed answer quarantines
  (`semantic_judge_failed`). Without a model nothing changes.

`model` also accepts a spec string for `rastro.model` or `"env"` (the `RASTRO_MODEL`
variable). Sending a page to a model that is not on this machine requires opting in
with `allow_remote=True` when you build the port. Values sent to the judge are clipped
and labelled as untrusted page data.

## Async code

`rastro.scrape` runs its own event loop. Inside async code (including Jupyter),
use `await rastro.scrape_async(...)`, which takes the same arguments. Calling
`rastro.scrape` inside a running event loop raises `ConfigurationError`.

## Next steps

- [Declarative jobs](jobs.md): scheduled runs, datasets and the CLI without Python.
- [Recipe memory](memory.md) and [recipe lifecycle](recipe-lifecycle.md).
- [API and outcomes](api.md): every status and issue code.
