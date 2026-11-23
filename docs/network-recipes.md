# Recipes from the site's own data

Many pages draw their content from a JSON API they call while loading. In browser mode,
Rastro can capture those responses and run a JSON recipe over one of them instead of the
page's markup. The job URL stays the page: robots, pacing, host rules and provenance are
the page's, and the page is what a person would open to check a record.

## Job file

```toml
format_version = 1

[acquisition]
allowed_hosts = ["shop.example"]
render = "browser"

[acquisition.browser]
capture_network = ['^/api/']        # path patterns; needs the network_capture grant

[[sources]]
url = "https://shop.example/list"
representation = "json"             # read a captured response, not the page

[sources.recipe]
strategy = "json"
capture = '^/api/items$'            # which captured response the recipe reads
record_path = "/data/items"
fields = { name = "/name", price = "/price" }

[[fields]]
name = "name"
type = "string"

[[fields]]
name = "price"
type = "number"
```

`capture_network` decides what may be captured at all; the recipe's `capture` picks one of
those responses. Without `capture`, the first successful captured response is read.

## What is captured

- XHR and fetch responses only, with a JSON media type (`application/json`, `text/json`
  or any `+json` type), from an allowed host, whose path matches a `capture_network`
  pattern.
- At most 16 responses per page render, 2 MB per response and 8 MB in total. A larger
  response is dropped, never truncated.
- The host, path, method, status and media type are kept with the body. The query, the
  fragment, request bodies and all headers are never kept, so a token in a URL stays out.
- Captured bodies live in memory for the run. Only the response the recipe reads becomes
  the source's document, like any fetched page.

A `capture` pattern is recipe data and is checked like untrusted input: at most 256
characters, a valid regular expression, and no repeated group that itself repeats.

When no captured response matches, the source fails with `capture_empty` (invalid data),
never an empty success. See [issues](issues.md).

## Drift

For JSON recipes, drift also records the shape of each field's values (number, string,
object keys, list item shapes), never the values. A price that turns from a number into
an object is structural drift even though every pointer still resolves.

## Finding the API from Python

`rastro.application.network_recipes.capturable_json` renders a page with every JSON path
captured (`offer_policy`) and returns offers: host, path, status, size, where the records
seem to be, and the anchored `capture` pattern. `recipe_from_capture` builds JSON recipes
over one captured response for the fields you name.

## Shadow DOM

With `pierce_shadow = true`, the plain browser serializes open shadow roots into the
document (as `<template shadowrootmode="open">` blocks), so CSS recipes can reach them.
The stealth browser hands the option to its engine.
