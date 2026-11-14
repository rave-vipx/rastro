# MCP server

`rastro mcp` lets an AI agent run the whole Rastro workflow over stdio MCP: suggest a recipe,
create a job, extract, load a dataset, watch for drift, and repair, promote and apply recipes.
It calls the same functions as the command line, so path, egress and robots policies are
identical. It adds no network capability of its own.

## Install and connect

```bash
pip install 'rastro-engine[mcp]'
claude mcp add rastro -- rastro mcp --root .
```

Without the extra, `rastro mcp` exits non-zero and prints a JSON error on stderr with
`"error": "missing_extra"`, a `message` and `"hint": "pip install 'rastro-engine[mcp]'"`.
`--root` is the workspace root (default: the current directory) and must exist.
An agent can write job files, so the server applies the [operator opt-in](jobs.md#operator-opt-in):
jobs that declare egress proxies run only when you start it with `--allow-egress`, and
`--allow-env NAME` adds names to the secret allow-list. `recipe_promote(override_gates=true)`
works only with `--allow-override-gates` (see [Repair and promotion](#repair-and-promotion)).
Recipes and selectors passed to
`scrape_file` follow the [untrusted-recipe selector policy](hub.md#import).

## Workflow

The server sends this order to the client as its `instructions`:

1. `suggest_recipe`: build a CSS recipe from a saved page and a few example values.
2. `job_new`: write a job file from the same page and examples.
3. `job_extract`: run the job and inspect the records (`job_preview` shows which fields
   would be loaded).
4. `job_load`: run the job and load approved fields into its dataset.
5. `drift_status`: see which sources are stale; `job_load` with `drift=true` measures
   structural drift.
6. `recipe_repair`: propose and check repair candidates for a failing source.
7. `recipe_history`, then `recipe_promote`: activate a reviewed candidate, with a reason.
8. `recipe_apply`: write the serving recipe back into the job file.

`scrape_file` extracts once from a saved page without a job. When deterministic repair finds
no passing candidate, or a page has no example values yet, the agent itself proposes the
recipe: see [Agent in the loop](#agent-in-the-loop).

## Tools

| Tool | CLI equivalent | Effect | Hints |
| --- | --- | --- | --- |
| `suggest_recipe(page_path, examples, url?)` | `rastro suggest` | Verified CSS recipe, per-field confidence and preview rows | read-only |
| `job_new(path, page_path, examples, url?, dataset?, identity?, recipe_memory?)` | `rastro new` | Writes a new `.toml` job; refuses to overwrite | writes |
| `job_validate(path)` | `rastro job validate` | Validates the job; reads no sources | read-only |
| `job_preview(path)` | `rastro job preview --output json` | Runs the job, writes nothing (recipe memory is read-only) | writes nothing; not `readOnlyHint`, because the job may fetch URLs or call a model |
| `job_extract(path)` | `rastro job run` | Runs the job and returns its records; writes no dataset | writes recipe memory only |
| `job_load(path, drift?)` | `rastro job load [--drift]` | Runs the job and loads approved fields into its dataset | destructive |
| `job_run(path)` | `rastro job load` | Deprecated alias of `job_load`, removed in 0.7.0 | destructive |
| `dataset_records(path, dataset?, sort_field?, upcoming?, timezone?, limit?)` | `rastro dataset export` | A bounded page of records plus stale sources | read-only |
| `drift_status(path)` | none | Freshness of each source in the job's dataset, and the stale ones | read-only |
| `recipe_list(path)` | none | Stored recipe versions and states per job source | read-only |
| `recipe_history(path, source)` | none | Versions, serving version and lifecycle events of one source | read-only |
| `recipe_repair(path, source, min_records?, min_completeness?)` | `rastro recipe repair --source N` | Stores and checks repair candidates | destructive (may promote when the job opts in) |
| `recipe_promote(path, source, version, reason, override_gates?)` | `rastro recipe promote` | Re-checks a candidate and makes it the serving recipe if it passes | destructive |
| `recipe_apply(path, source)` | `rastro recipe apply` | Writes the serving recipe into the job file; the rest of the file is kept | destructive, idempotent |
| `scrape_file(page_path, fields? + records?, recipe?, url?)` | none (`rastro.scrape` in Python) | One-off extraction from a workspace file | read-only |
| `repair_context(path, source)` | none | Failing recipe, field definitions, last success values, current issues and a page outline | read-only |
| `recipe_propose(path, source, recipe, reason)` | none | Gates the agent's recipe like a repair candidate and stores it | destructive (may promote when the job opts in) |
| `semantic_review(path, source, version)` | none | Old and new values side by side for each changed field | read-only |
| `suggest_context(page_path, fields)` | none | Page outline and recipe format for a new page | read-only |
| `recipe_try(page_path, recipe, fields?, url?)` | none | Runs a recipe once: fill counts, preview rows, issues | read-only |

`source` is the one-based index of a source in the job. Each tool declares its MCP
annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) so a client
can ask for confirmation before a tool that changes files. Tools that run a job set
`openWorldHint` because a job with URL sources fetches them.

Results are structured JSON. Source values are untrusted data and carry
`"data_trust": "untrusted_source_data"`; an agent must not follow instructions found in them.

### Repair and promotion

`recipe_repair` stores candidates as recipe versions and reports each one's checks. It
activates a candidate only when the job itself sets `[repair] auto_promote = true`; otherwise
the agent reviews the report and calls `recipe_promote` with a `reason` (1 to 500 printable
characters). Promotion re-runs every check; `override_gates=true` can activate a candidate
that missed a threshold, never one that failed validation, and the recorded reason says the
checks were overridden. Rollback is not exposed; use `rastro recipe rollback`.

`override_gates` is off for MCP clients unless you start the server with
`rastro mcp --allow-override-gates` (`build_server(root, allow_override_gates=True)` or
`serve(..., allow_override_gates=True)` in Python). The agent reads untrusted page content,
which can prompt-inject it, and an override can activate a quarantined candidate, including
one the semantic judge vetoed. Without the flag the call fails with a tool error telling
you to run `rastro recipe promote --override-gates --reason TEXT` yourself.

`recipe_apply` writes to a job file in the workspace, but only the recipe that already
serves: one that passed every gate, the job's own `auto_promote`, or your override. It
keeps the rest of the file, refuses symlinks, checks that the rewritten file loads with that
recipe and writes atomically. Keep job files under version control if you want to review
these writes.

### Authoring without a job

- `suggest_recipe` and `job_new` take `examples`: 1 to 20 records mapping field names to text
  as it appears on the page.
- `job_new` needs the page inside the new job file's directory; `dataset` requires `identity`
  (a field name or a list). `recipe_memory` names a recipe store relative to the job, in a
  folder that already exists; without it `recipe_repair` refuses the job.
- `scrape_file` takes either `fields` (field name to selector ending in `::text` or
  `::attr(name)`, plus an optional `records` selector) or a full `recipe` object.
- `url` is only the address the page came from, used for relative links. These tools never
  fetch it.

## Agent in the loop

The agent connected over MCP already runs a strong model. Rastro lets that model do the
intelligent part, proposing recipes and judging meaning, while Rastro verifies and stores.
No API key and no second model are needed, and it works the same with any MCP client and any
model: no provider is special-cased.

The agent proposes recipe definitions, never records. Every proposal passes the same strict
recipe validation and the same recipe CI as deterministic repair: it must replay the retained
success samples with identical values, extract complete, valid records from the current page
and pass the value checks. A wrong proposal cannot reach the dataset; at worst it is stored and
retired.

The server instructions give this order:

1. `drift_status`, then `recipe_repair` (deterministic, no model).
2. If no candidate passes: `repair_context` returns the failing recipe, the job's field
   definitions, up to 10 values from the last successful run, the current status and issues,
   and a page digest (repeated containers as `parent > child` selectors with counts and
   sample records as `tag.class` trees, headings, JSON-LD types and URL- or date-like
   attributes; at most 12,000 characters, labelled untrusted; see
   [prompts small models can follow](intelligence.md#prompts-small-models-can-follow)).
3. `recipe_propose(recipe, reason)`: Rastro composes each changed CSS selector with the
   serving one (`"<serving>, <proposed>"`, as model repair does, so past pages still match;
   a union that does not validate falls back to the plain proposal), validates the recipe
   against the job's fields and representation, runs the gates, stores it as a candidate (an
   equal stored candidate is reused) and returns `decision`, the reasons, blocking record
   issues, a selector `diff`, `review` and `next`. On `reject`, read the reasons and propose
   again, at most three times.
4. Judge meaning with `review` (the same data as `semantic_review(version)`): old values
   (last successful run) and new values (the candidate on the current page) per changed
   field. A title must still be a title, not a city or an identifier. Gates check structure
   and history; the agent checks meaning.
5. `recipe_promote(version, reason)`, then `recipe_apply`.

`recipe_propose` never promotes, even when the job sets `[repair] auto_promote = true`: the
calling agent is the semantic judge, so a passing proposal stays a candidate (reason
`automatic_promotion_disabled`) until the agent promotes it. Its `reason` is checked like a
promotion reason and returned as `agent_reason`; the lifecycle event records the recipe CI
decision.

For a new page without example values, `suggest_context(page_path, fields)` returns the page
outline and the recipe format, and `recipe_try(page_path, recipe)` runs a recipe once and
reports per-field fill counts, up to 20 preview rows and issues. Iterate until every row is
right, then write the job with `job_new` using values from the preview as examples.

### Example transcript

A job board renamed its title class. The agent works through the tools:

```text
agent  -> recipe_repair(path="jobs/job.toml", source=1)
rastro <- comparisons: [], outcome: "no_candidates"
agent  -> repair_context(path="jobs/job.toml", source=1)
rastro <- recipe.fields.title: "h2.title::text"; current.status: "failed";
          last_success_sample: [{"job_id": "job-1", "title": "Synthetic role 1"}, ...];
          page_outline: "PAGE DIGEST ...\n  - selector: section > article.card  (count 5)\n
          ...\n        h2.new-title \"Synthetic role 1\"\n        span.city \"Synthetic city 1\""
agent  -> recipe_propose(..., recipe={"strategy": "css", "record_selector": "article.card",
          "fields": {"job_id": "::attr(data-id)", "title": "span.city::text"}},
          reason="title moved")
rastro <- decision: "quarantine", reasons: ["automatic_promotion_disabled"],
          candidate_version: 2, diff title: "h2.title::text, span.city::text",
          review.fields: [{"field": "title", "old": ["Synthetic role 1", ...],
                           "new": ["Synthetic city 1", ...]}]
agent  (judges: a city is not a title; does not promote version 2)
agent  -> recipe_propose(..., fields: {"job_id": "::attr(data-id)",
          "title": "h2.new-title::text"}, reason="the title class was renamed")
rastro <- decision: "quarantine", reasons: ["automatic_promotion_disabled"],
          candidate_version: 3, diff title: "h2.title::text, h2.new-title::text",
          review.fields: [{"field": "title", "old": ["Synthetic role 1", ...],
                           "new": ["Synthetic role 1", ...]}]
agent  -> recipe_promote(..., version=3, reason="Semantic review: same titles")
agent  -> recipe_apply(path="jobs/job.toml", source=1)
```

The first proposal mapped the title to the city. Composed with the serving selector it
replays the old pages and fills every record, so the structural gates pass; only meaning
tells it apart. That is why `recipe_propose` never promotes: the agent saw cities where titles
were, left version 2 unpromoted and proposed again. The serving recipe never changed until the
agent promoted a candidate whose values it had checked.

## Limits and safety

- Paths are workspace-relative, at most 1024 characters. Absolute paths (rejected as "Absolute
  paths are not allowed"), `..` escapes and symlinks are rejected. A job's own paths stay
  inside the job's directory, as in the command line.
- Every argument has a JSON Schema with bounds (lengths, ranges, item counts).
- Outputs that grow with the page are bounded: lists stop at 50 items, objects at 100 keys and text at
  500 characters, and `truncated` says when something was cut. `dataset_records` returns at most
  200 records per call (`limit`, default 50).
- Failures, including arguments that break the schema, are tool errors whose text is one JSON
  object: `{"error", "message", "location", "hint"}` (`hint` may be `null`). They never contain
  stack traces, argument values, job values or page text.
- A job with URL sources fetches only its `allowed_hosts` under the job's robots and
  rate-limit policy. `suggest_recipe`, `job_new`, `scrape_file`, `suggest_context` and
  `recipe_try` read workspace files only; `repair_context` and `semantic_review` read the job's
  current page like `recipe_repair` does and never create or write a store.
- `recipe_propose` and `recipe_try` accept a recipe object of at most 64 KiB, validated with
  the strict recipe model; unknown keys and undeclared fields are refused. The page outline has
  its own 6,000-character limit; other outputs follow the limits above, with text cut at 300
  characters for the agent tools.
