# Page steps

Some pages only show their records after a click: a "load more" button, a tab, a filter,
a link to the full list. A CSS recipe can carry **page steps** that run in the browser
page before extraction. Steps are data, not code. Later runs replay them without a model.

```toml
[acquisition]
allowed_hosts = ["shop.example"]
render = "browser"

[[sources]]
url = "https://shop.example/list"

[recipe]
strategy = "css"
record_selector = "article"

[[recipe.steps]]
action = "click"
selector = "button.more"
wait_for = "article:nth-of-type(21)"

[recipe.fields]
name = "h2::text"
```

## Actions

| Action | Keys | What it does |
| --- | --- | --- |
| `click` | `selector`, optional `wait_for` | Clicks the first match, then waits until `wait_for` is visible. |
| `type` | `selector`, `text` (up to 256 characters) | Types `text` as written. Nothing is read from the environment or expanded. |
| `select` | `selector`, `value` | Chooses the option with this value in a `<select>`. |
| `scroll` | `times` (1 to 20), `settle_ms` (0 to 5000) | Scrolls to the bottom `times` times. |
| `wait` | optional `selector`, `state`, `timeout_ms` (up to 15000) | Waits for an element state, or for a fixed time. |
| `follow` | `selector` ending in `::attr(href)` | Opens the link the selector reads. |

A recipe has at most 20 steps. There is no script action, and any other key is refused
when the job is loaded.

## Limits and safety

- **Selectors are CSS.** A selector cannot name another selector engine (`xpath=`,
  `text=`) or chain one (`>>`). Step selectors follow the same cost policy as the recipe's
  other selectors: no `:has()`, no sibling combinators (`~`, `+`), bounded chains.
- **Followed links stay inside the job.** The link is resolved against the page and checked
  against `allowed_hosts`, HTTPS and port rules before the browser moves. The browser's
  network guard checks it again, and the page it lands on must pass robots.
- **Time and requests are bounded.** Each browser call waits at most 10 seconds and all
  steps of a page share one deadline (at most 120 seconds). Requests the steps cause spend
  the source's request budget. A `scroll` step also spends a crawl's page cap.
- **Capability.** Steps request the `page_actions` capability. Your own jobs have it; a
  shared bundle, a registry recipe, an agent or a model needs an explicit grant.
- **Browser only.** Steps need `render = "browser"` or `"stealth"`. On an HTTP source the
  source fails with `step_failed` rather than extracting a page the steps never touched.

Steps run once per source, on its first page. Pagination and `[scroll]` continue from
the page the steps leave. In a `[spider]` crawl, steps run on the start page only; linked
pages are extracted as they render, without steps. Fallback recipes cannot carry steps.

## Let a model find the steps

When the first page hides records behind a button or a form, a model can look for the
steps once:

```bash
rastro new --explore job.toml -o explored.toml --expect 40
```

The job must render in a browser. Every step the model proposes is checked against the
action vocabulary above and kept only if a fresh run of the job's first source, without the
model, then shows more records. The new file has the job's fields, sources, hosts and
dataset, the kept steps and `render = "browser"`; `job.toml` is not changed. In Python,
`rastro.explore_job_file(job, output, model=...)` does the same and `rastro.explore_job`
returns the result without writing. Exploration starts only from the command line or
Python, never from an agent tool, a registry recipe, a bundle or model output.

## When a step stops matching

If a step's selector no longer matches, the source fails with `invalid_data` and the issue
`step_selector_missing`, whose message names the step (`step 2 (click)`). This is the
same structural signal as a recipe whose selectors stopped matching: drift alerts fire and
repair can propose a new recipe. Other step failures report `step_failed` or the network
reason (for example `host_not_allowed`).
