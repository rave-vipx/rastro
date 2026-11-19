# Local Studio

Studio is Rastro in a browser. Without writing code you can create a scraper from a URL,
run it, look after its health, repair it when a site changes, and browse the records it stored.
It calls the same services as the command line and the Python API; it adds no extraction logic.

## Install and start

```bash
pip install "rastro-engine[studio,http]"
rastro studio --root path/to/jobs --port 8765
```

`--root` defaults to the current directory and `--port` to 8765. Studio always binds
`127.0.0.1`. Jobs that declare egress proxies run only with `--allow-egress`, and
`--allow-env NAME` extends the secret allow-list (see [operator opt-in](jobs.md#operator-opt-in));
selector edits follow the [untrusted-recipe selector policy](hub.md#import). Without the extra, `rastro studio` exits with code 2 and prints a JSON error on
stderr: `{"error": "missing_extra", "message": ..., "location": null, "hint": "pip install
'rastro-engine[studio]'"}`. The page is one HTML file plus a few scripts and one stylesheet shipped in the package;
there is no build step.

### Ask AI (optional)

Start Studio with a model to get **Ask AI** buttons:

```bash
rastro studio --model SPEC                  # or set RASTRO_MODEL=SPEC
rastro studio --model SPEC --allow-remote   # a hosted or non-loopback model
```

`SPEC` is resolved by `rastro.model`, so any supported provider works (for example an
OpenAI-compatible endpoint or a local command); Studio has no provider-specific code. Python
code can pass any `ModelPort` to `rastro.studio.app.create_app(root, model=...)`.
Without `--model` or `RASTRO_MODEL`, Ask AI stays hidden and nothing is sent anywhere.
Sending page content to a non-loopback endpoint or a hosted subscription requires
`--allow-remote` (or `RASTRO_ALLOW_REMOTE=1` with `RASTRO_MODEL`). Studio reads from the
model it built, not from the flag, whether page content may leave the machine, and says so
on the page and in the terminal. A `ModelPort` passed from Python that does not report it
(`remote` attribute) is treated as `create_app(..., model_remote=...)` declares. The model receives the field names and a bounded page outline (tags, ids,
classes and short text, at most 6,000 characters), never the whole page, keys or tokens.

- **New scraper:** name the fields (example values are optional) and press **Ask AI**. The
  model proposes a recipe; Studio keeps it only if it extracts valid records from the page,
  retrying within the model budget. The verified recipe and preview are shown and the example
  values are filled from the first record, so **Create job** re-checks the recipe the usual
  deterministic way.
- **Health and repair:** **Fix this site** already passes the configured model to repair.
  When it finds no usable candidate, **Ask AI for a repair** runs repair with the model (`repair_job_source(..., model=...)`): relocation first,
  then model proposals composed with the serving selectors, the same gates, and the semantic
  judge. A candidate becomes active on its own only when the job sets
  `[repair] auto_promote = true` and the judge agrees the values kept their meaning; otherwise
  check that the new values mean the same as before, then promote with a reason.

## Walkthrough

Studio opens on **Projects**: one card per project found in the folder, with its health in words
(*Up to date*, *With warnings*, *Failing*, *Status unknown*), last update, record count and sources
with problems, and **View results**. **Projects** in the header always comes back here.

- **Empty folder:** Studio says no projects were found and offers **Start from a URL** and **Connect
  an export** (JSON records loaded with `rastro.load_records`, or a Rastro SQLite dataset).
- **A database Studio doesn't recognize:** SQLite files under the folder that no job uses are listed
  by path with what to do; Studio does not open them.
- **Loading** is said while the list is read. With a Studio that has no project list endpoint, job
  files are listed with *Status unknown* and a note, never with an invented health.

### Create a scraper (New scraper)

Four steps, one decision each:

1. **URL.** Paste a page URL you have permission to scrape. Studio fetches the page
   through the acquisition boundary; public-address checks, robots and the source
   request budget apply. Saving HTML by hand is not required. A URL job fetches its URL on
   every later run. If the page builds its list with JavaScript, see the
   [browser extra and runtime](acquisition.md#javascript-pages).
2. **Fields.** Name each field (letters, digits and `_`, starting with a letter) and copy an
   example value exactly as the page shows it. Tick **Record key** on a field that identifies a
   record (needed to store records). With a configured model, **Ask AI** can propose selectors
   from names alone; proposals must extract valid records, the model does not supply them.
3. **Preview.** **Preview records** shows up to 50 records found on the page; how each field is
   read is folded under a technical detail. If a field comes out wrong, **Correct a field** goes
   back to paste another example.
4. **Name and save.** Give the project a name; the job, dataset and recipe-memory file names are
   filled from it (`book-prices.toml`, `book-prices.sqlite`,
   `book-prices-recipes.sqlite`) and can be changed under **File names (advanced)**. Studio
   never overwrites a file. **Save and open** opens the new project on Results.

### Details that apply to every project

1. **Open a job file (advanced),** at the bottom of Projects, lists the `.toml` and `.json` job files below the root (four levels deep):
   files with a top-level `format_version`, valid or not, so a broken job still shows its
   errors. Repair reports, `pyproject.toml` and other TOML or JSON files are not listed.
   Symlinks, hidden directories and vendor directories (`node_modules`, `__pycache__`, `venv`,
   `dist`, `build`, `site`) are skipped; at most 500 files are listed and the page says when
   the list is cut. **Open** validates the job with the same rules as `rastro job validate`.
2. **Correct a field by hand** (in Fix). Runs `rastro job preview` logic: sample records, and for every field
   the method, locator, validation status and whether it would be loaded. Rejected records show
   their reasons. A preview needs a `[dataset]` block and writes nothing; a job with
   `recipe_memory` reads its recipe store without changing it. Choose a field mapping, edit its
   selector and preview again; **Save to job file** writes the corrected job. The edited job is
   validated first, the write is atomic, and the request carries the digest of the file you
   opened: if the file changed meanwhile, even a moment before the write, saving fails with
   `file_changed` and the file is left as it is. TOML comments and
   layout are kept because only the one selector text is replaced; if that text is not unique
   in the file, Studio refuses and asks you to edit by hand. JSON jobs are rewritten with
   two-space indentation.
3. **Run** (in Results). **Extract** runs the job and reports status, record counts and issues per source,
   plus which recipe each source used (the job file's, or a newer one in recipe memory, with
   its version). It writes nothing, not even the recipe store. **Load into dataset** runs
   `rastro job load`: approved fields go into the dataset and the result shows the changes per
   source, the recipe used and any stale sources. Tick **Measure drift** to also compare each
   page's structure with its last good run.
4. **Health and repair** (Sources → *Technical detail*, and Fix). **Check health** shows, per source, the serving recipe, whether the
   source is stale in the dataset, its last success, why it is stale, open drift and the
   stored recipe versions. Above the table, plain-language alerts name failed or stale
   sources, open drift, quarantined recipes and the alerts of the last run of this job in
   this Studio session. Below them is the exact `rastro job load ... --on-failure` command
   for the README hook. With `recipe_memory` in the job, two actions appear:
   - **Fix this site** runs `rastro recipe repair` for that source, with the Studio model
     when one is configured (`--model` or `RASTRO_MODEL`). Each candidate shows its
     old and new selectors side by side (changed fields in bold), the decision (`promote`,
     `quarantine` or `reject`), every gate with pass or fail, and the reasons. When the job's
     `[repair] auto_promote` is enabled, a candidate starts serving only after the gates
     and semantic judge pass.
   - **Promote** a candidate with a reason (1 to 500 printable characters; it is recorded in
     the recipe history). Promotion re-checks the gates. Studio cannot override a failed
     gate: a request with `override_gates` is refused with `gate_override_refused`, and a
     candidate that fails is reported as refused while the current recipe keeps serving.
     A rejected candidate is never activated. The operator's own
     `rastro recipe promote --override-gates` stays available on the command line.
   - **Write to job file** copies the serving recipe from recipe memory into the job file,
     as `rastro recipe apply` does, keeping the rest of the file. It carries the file digest
     like saving a selector.
5. **Dataset** (Results). Shows the stored records with a stale marker for records from stale
   sources (see [Results](#results-n5)). Values longer than
   2,000 characters are shortened and the record is marked `truncated`, on the page and in
   downloads. **Download JSON** and **Download HTML** produce the same records as
   `rastro dataset export` (with that shortening), for datasets of up to 5,000 records (use the command line for
   larger ones). Values come from the source pages: treat them as untrusted data.
6. **Connect your app.** Shows the `rastro job run`, `job load` and `dataset export` commands and
   the `run_job_file` call for that job. Run them from the root directory.

## Layout, language and theme

Studio opens on **Projects**. The header holds **Projects** with the open project's name, the
language and theme choices, and, once a project is open, its views: **Results** (run and records),
**Sources**, **Alerts**, **Fix** (repair, the automatic switch, correcting a field by hand),
**Settings** (next page, link following, scroll, stealth, connecting your app, the recipe registry)
and **New scraper**.

- **Language:** English by default; Spanish (Rioplatense voseo) from the header. Every label and
  message Studio writes comes from one dictionary (`static/i18n.js`, tests keep both languages
  complete); lists, cards and panels redraw when the language changes. Sentences the server writes
  (run alerts above a result) and issue codes stay as the server sends them.
- **Narrow screens:** from 360 px. At 40rem and below the results and before/after tables become
  cards with a label per value, the provenance panel opens as a bottom sheet, and controls are 44 px.
- **Settings** are folded groups: next page, follow links, scroll, browser and stealth, network
  permissions, connect your app, and **Registry (URL)**.
- **Theme:** System (default), Light or Dark. Both choices are stored in this browser only; with
  storage blocked they last until reload.
- **Accessibility:** controls are at least 36 px (44 px on narrow screens), focus is a 3 px accent
  outline, and every text/background token pair meets WCAG AA (a test computes the ratios).

### Results

Opening a project loads its stored records into a table, 25, 50 or 100 per page ("Records 1–25 of
74"). Search matches any value; filters narrow by source (when there are several), status (upcoming,
live now, or from a failed source) and date (when a field holds dates). Studio reads up to 2,000
records in chunks of 100 for search and filters and says so when a dataset is larger; **JSON** and
**HTML** export always cover the whole dataset (up to 5,000 records).

An empty cell always says why: *Not published* (the source does not give it), *Source failed* (the
source could not be read in the last load) or *Empty, no reason reported* when the server sent no
reason. A value with several prices (a list, or text such as `Free / $ 160 / $ 199`) shows the first
one and the number of tiers. Free entries are bold. A value that is a link or an image address is
shown as text with an **Open** button; nothing from the data is loaded on its own.

Choose a record (click the row, or Tab to its first cell and press Enter) to open **Where it came
from**. For every field it shows the value, the page it came from, when it was observed, the
extraction method and locator, and the recipe version. Several prices are listed one per tier, with
availability and conditions when the data has them. A field flagged as *Looks like instructions*
(`instruction_like_content`) carries a marker in the table and an explanation in the panel: the text
is shown as data and never executed. Records written with `rastro.load_records` say *Imported
snapshot* with the import time and the original observation time, so they are not mistaken for a
recent web check. **Close** or Esc returns focus to the record.

### Sources and Alerts

**Sources** lists every site of the project with its status in words (*Up to date*, *Up to date,
with a warning*, *Cap reached*, *Access denied by the site (robots.txt)*, *The site returned
something that can't be read*, *The site's structure changed*, *Last load failed; earlier records
kept*, *Not read yet*), the last fetch, records read and rejected ("Read 20 records; cap 20. There
may be more."), warnings that did not stop the save, and one next step: **Retry** (runs Load into
dataset), **Fix this site**, **Raise the cap** (opens Settings) or **Run a load**. **Details**
shows the address and each issue code with its plain explanation. The previous health table
(serving recipe, stored versions, the `--on-failure` command) is folded below as *Technical detail*.

**Alerts** lists what happened in the runs, newest first and grouped by day: failures, site
changes, caps, warnings, quarantined fixes and refused fixes. Each one has a sentence written from
its kind and issue code in the chosen language, and one action. Both views read their own
endpoints (`/api/sources`, `/api/alerts`); when the endpoint is missing or fails, the view says so
instead of showing an empty list.

### Fix a site

Pick a site and press **Find a fix** (this runs `rastro recipe repair` for that source; the job needs
`recipe_memory`). Each proposal shows its checks in one row (the decision in words, records read
now, past samples that pass, each gate with pass or fail) and a before/after table: for every field,
how it is read now, how the proposal reads it, and whether that changed. Then decide:

- **Apply fix** needs a reason (1 to 500 characters, kept in the recipe history) and calls
  `/api/promote`, which re-runs every check. A failed check is reported as not applied and the
  current recipe keeps serving; Studio cannot override it. After applying, **Also write it to the
  job file** copies the serving recipe into the job file (`/api/apply`, digest-checked).
- **Discard** writes nothing: the current recipe keeps serving and the proposal stays stored, unused.
- A rejected proposal cannot be applied. With a configured model and no usable proposal, **Ask AI for
  a fix** runs repair with the model, under the same checks.

**Apply fixes automatically** is a separate switch in the same view, off by default; it writes only
`[repair] auto_promote` for this job. **Correct a field by hand** (folded below) keeps the
preview-and-save-a-selector flow.

### API for the views

These read-only endpoints feed Projects, Sources, Alerts and Results. They use the same guard
as every other route (loopback Host, exact Origin, the per-run token, bounded bodies), and
`file` is a job path relative to the root, refused like everywhere else when it escapes the root
or follows a link. Responses carry statuses, counts, dates and Rastro codes; the page writes
every sentence from the dictionary. A code read from a file that is not a Rastro code is shown as
`unknown`, and an alert names a field only when the job declares it.

| Route | Returns |
| --- | --- |
| `GET /api/projects` | `projects`: per job `file`, `name` (readable), `dataset` (the raw dataset name, `null` without one), `health` (`ok`, `warnings`, `failing`, `unknown`), `last_run`, `records`, `sources_total`, `sources_with_problems` (failed or stale); `other_databases`: `.sqlite` files no job uses; `truncated` |
| `POST /api/sources {file}` | `sources`: per current source `url`, `status` (`ok`, `warning`, `capped`, `failed`, `stale`, `never`), `issue_code`, `last_fetch`, `read`, `rejected`, `cap`, `warnings` (issue codes, `open_drift`, `quarantined_candidate`); `read`, `rejected` and `cap` are `null` when nothing recorded them |
| `POST /api/alerts {file}` | `alerts`, newest first, at most 100: `at`, `kind` (`failure`, `drift`, `cap`, `warning`, `quarantine`, `promote_refused`), `source`, `code`, `field`, `action` (`retry`, `fix`, `details`, `raise_cap`) |
| `POST /api/records` | The existing page, plus per record `provenance` (per field `source_url`, `observed_at`, `method`, `locator`, `recipe_version`), `empty` (declared fields without a value: `not_published`, or `source_failed` when the record's source is stale), `flags` (instruction-like values, see [ADR 0035](decisions/0035-prompt-injection-defense.md)), `origin` (`web` or `adapter`); and `imported_at` when the page shows adapter records |

A source is `failed` when its last attempt failed with an outcome such as `permanent_failure`,
`stale` when its last attempt did not refresh it for another reason (for example
`consistency_failed`), `capped` when its last load stopped at a collection cap (`issue_code` names
it, such as `pagination_max_pages` or `spider_page_limit`, and `cap` gives the configured value),
`warning` from the last Studio load, and `never` when nothing was recorded. A job is `failing`
when every attempted source failed or is stale.

`read`, `rejected`, `cap` and the `capped` status come from the dataset's run metrics (see
[Metrics and cost](operations.md#metrics-and-cost)), so loads started from the command line show
up too, as do failures, open drift and waiting candidates. A PostgreSQL dataset keeps no run
metrics: its counts are `null`, never 0. Other issue codes of a load and refused promotions are
not kept by the dataset: Studio writes them to a small run log, `.rastro-studio/runs/` under the
root (one JSON file per job, the last 20 runs, URLs, counts, Rastro codes and declared field
names only). Deleting that folder loses only those warnings.

A demo folder with three jobs in these states is built by
`python -m tests.fixtures.studio_demo.build DIR` from a source checkout; then run
`rastro studio --root DIR`.

## Collection, alerts and sharing

These controls sit in the Jobs view and call the existing services. Jobs with
`[spider]` and `[scroll]` can be edited through the bounded controls below. A visible control is not a grant.

- **Next page and repair settings.** Pick the job recipe or a source's recipe, type
  the next-link selector (for example `a.next::attr(href)`) and a page cap from 1 to 50,
  then **Save next page**. Studio writes the recipe's existing `pagination` fields
  ([ADR 0026](decisions/0026-bounded-recipe-pagination.md)); there is no second paginator. The
  selector passes the untrusted selector policy, the request carries the file digest,
  and the reparsed job must differ from the old one only in that recipe's pagination, or
  nothing is written. TOML keeps the rest of the file; JSON is rewritten with the same data.
  Clicking an element in the preview to pick it is not available yet: type the selector.
- **Auto-promote** (a switch, off by default) writes only `[repair] auto_promote` on the
  job you opened, with the same digest and reparse check. On, a repair candidate starts
  serving only after every gate and the semantic judge pass. The judge is a model: the
  Studio `--model`, the job's `[model]`, or `[repair] model = true` with `RASTRO_MODEL`.
  Without one, **Fix this site** stores the candidate, keeps the current recipe serving
  and says why, so you promote with a reason. Off, you always promote with a reason.
  Installing a registry package never changes it.
- **Allow stealth for this Studio session** (a switch, off by default) grants the running
  Studio process, like `--allow-stealth` or `RASTRO_ALLOW_STEALTH=1`, and applies to
  preview, extract, load, repair and promote. It is never written to a job or a bundle.
  When the process was started with that grant the switch shows on and is locked. A job
  asking for `render = "stealth"` without the grant fails with a refusal; Studio does not
  fall back to a plain browser. Browser rendering and scroll do not enable stealth.
- **Progress.** While an extract, load, repair or promote runs, the Run card shows which
  operation is running for which job file, then its real outcome. The engine does not
  report source, page or scroll steps yet, so Studio shows none and does not estimate
  them. The line never contains page content, URL queries or secrets. One operation runs
  at a time; a second one is refused with `operation_in_progress`. The responses stay
  synchronous.
- **Alerts.** When a run, repair or promotion ends in failure, open drift, a stale source,
  a quarantined candidate, a refused promotion or a pagination cap (`max_pages`, budget
  or record limit), Studio says so in a sentence above the result. Accepted records are
  kept; a cap means the run is incomplete, not empty.
- **Registry.** At the bottom of the Jobs view: search a registry directory under the
  Studio root, install a result as a candidate into the open job's `recipe_memory`, or
  publish an exported public bundle into a local registry directory. Install never
  promotes and never edits the job. Publishing does not run Git or push. An `https://`
  registry address uses the same policy-bound fetcher as `--registry-url`: public addresses
  and robots rules apply unless the operator grants private networking, an extra port
  or a robots override. Request data and bundles cannot grant those permissions. See [sharing](hub.md#host-a-static-registry-n10).
  No account or Rastro-operated server is involved.

## Security model

Studio is a local tool, not a server to expose.

- Loopback only. Requests whose `Host` is not `127.0.0.1` or `localhost` are rejected (this
  also blocks DNS rebinding), and so is any request whose `Origin` is not this Studio's.
  A `POST` without an `Origin` header is refused (browsers always send it).
- A random token is generated on every start and printed once as
  `http://127.0.0.1:PORT/#token=<token>`. The page reads it from the URL fragment (which the
  browser never sends to the server), keeps it in memory and clears it from the address bar.
  The served page does not contain it. Every `/api` request, reads included, needs it in
  `X-Studio-Token`; only the page itself and its scripts and stylesheet load without it. The token
  stops other local users only as long as the terminal output stays private. Without it the page
  asks you to open the URL printed in the terminal. Reloading the tab drops the token; reopen that URL.
- Every action that changes something (create, save, load, repair, promote, write, the
  pagination and auto-promote settings, the session stealth switch, registry install and
  publish) is a `POST` with the token and this Studio's exact origin. Actions that edit a
  job file carry its digest.
- Paths are relative to the root, may not contain `..`, backslashes or NUL, must end in `.toml`
  or `.json` (job files) or `.html`/`.htm` (saved pages), and may not pass through symlinks.
  New job names use only letters, digits, `_`, `.` and `-` per folder name. Files the job itself references follow the same
  containment rules as the CLI.
- Source content is shown with plain text content only; the scripts never parse markup. A
  Content-Security-Policy allows only same-origin script and style, and no inline code. The
  page loads nothing from other sites.
- Request bodies are limited to 16 KiB, job files to 256 KiB, and selectors to 1024 characters.
- Ask AI (`/api/ai`, `/api/ai/suggest`, `/api/ai/repair`) uses the same token, origin and path
  rules. Model output is untrusted: it is parsed as a strict recipe proposal and kept only after
  deterministic verification; it never becomes records directly, and a repair candidate is
  promoted automatically only under the job's own `auto_promote` with the judge's agreement.

## Limitations

Studio edits one selector per save, and only recipe field mappings (not the field schema,
dataset or acquisition blocks). The wizard takes one example record; use `rastro new` with
several `--example` records when one is not enough. Repair needs `recipe_memory` and a
`[dataset]` in the job. Long-running actions keep their button disabled until they finish; progress names the
running operation and its outcome, not a page count or an estimate of completion time. It is single-user and has no authentication beyond the
per-run token.

### Bounded links and scrolling

After creating a scraper, open its job and use **Follow links** to enable a
bounded spider. Set link depth (1–4), page cap (1–100), a link selector,
same-host restriction, and optional allowed or denied path prefixes.
Jobs already using a declared link field keep that field instead of a selector.
Other hosts still need the job's allowed-host list; these controls grant no
network permissions.

**Scroll for more records** sets a step cap (1–30) and wait (100–3000 ms),
stopping when no new records appear. The controls are disabled unless a source
uses browser rendering: HTTP ignores scroll and stealth does not support it.
Saving validates the domain policy before replacing the job atomically and
retains TOML comments. Unusual TOML layouts that cannot be safely edited are refused.
Extract results show each reported spider page's outcome, host and path, never
its page body.

URL previews are reused only for the exact requested URL and for less than ten
minutes. Creating after expiry fetches again under the operator's current grants.
