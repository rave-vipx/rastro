# Local Studio

Studio is Rastro in a browser. Without writing code you can create a scraper from a saved page,
run it, look after its health, repair it when a site changes, and browse the records it stored.
It calls the same services as the command line and the Python API; it adds no extraction logic.

## Install and start

```bash
pip install "rastro-engine[studio]"
rastro studio --root path/to/jobs --port 8765
```

`--root` defaults to the current directory and `--port` to 8765. Studio always binds
`127.0.0.1`. Without the extra, `rastro studio` exits with code 2 and prints a JSON error on
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
`--allow-remote`; Studio then says on the page and in the terminal that page content may leave
the machine. The model receives the field names and a bounded page outline (tags, ids,
classes and short text, at most 6,000 characters), never the whole page, keys or tokens.

- **New scraper:** name the fields (example values are optional) and press **Ask AI**. The
  model proposes a recipe; Studio keeps it only if it extracts valid records from the page,
  retrying within the model budget. The verified recipe and preview are shown and the example
  values are filled from the first record, so **Create job** re-checks the recipe the usual
  deterministic way.
- **Health and repair:** when **Propose repair** finds no usable candidate, **Ask AI for a
  repair** asks the model. Each proposal is gated like any repair candidate and stored; Studio
  never promotes it. Check that the new values mean the same as before, then promote with a
  reason.

## Walkthrough

The page has two views, **Jobs** and **New scraper**, switched with the buttons at the top.

### Create a scraper (New scraper)

1. **Saved page.** Save the web page as HTML somewhere inside the Studio folder and pick it.
   Studio lists `.html` and `.htm` files with the same rules as the job list below. Enter the
   address the page came from, so links resolve and the job knows its source.
2. **Fields and one example each.** Give each field a name (letters, digits and `_`, starting
   with a letter) and copy one value exactly as the page shows it. Press **Suggest**: Studio
   builds a CSS recipe, checks that it reproduces your examples on the page, and shows the
   recipe, a confidence note per field (how it was matched and how many records carry a value)
   and a preview table of up to 50 records. Nothing is fetched or written. If no recipe
   reproduces your examples, the error names the examples it could not find.
3. **Create the job.** Name the job file (for example `pages/events.toml`). It must be in an
   existing folder, next to the page, and must not exist yet: Studio never overwrites a file.
   To store records, give a dataset file name and tick at least one **Record key** field.
   To repair the recipe later, also give a **Recipe memory file** (for example
   `recipes.sqlite`).
   Studio then opens the new job in the Jobs view.

### Work with a job (Jobs)

1. **Job file.** Lists the `.toml` and `.json` job files below the root (four levels deep):
   files with a top-level `format_version`, valid or not, so a broken job still shows its
   errors. Repair reports, `pyproject.toml` and other TOML or JSON files are not listed.
   Symlinks, hidden directories and vendor directories (`node_modules`, `__pycache__`, `venv`,
   `dist`, `build`, `site`) are skipped; at most 500 files are listed and the page says when
   the list is cut. **Open** validates the job with the same rules as `rastro job validate`.
2. **Preview and correct.** Runs `rastro job preview` logic: sample records, and for every field
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
3. **Run.** **Extract** runs the job and reports status, record counts and issues per source,
   plus which recipe each source used (the job file's, or a newer one in recipe memory, with
   its version). It writes nothing, not even the recipe store. **Load into dataset** runs
   `rastro job load`: approved fields go into the dataset and the result shows the changes per
   source, the recipe used and any stale sources. Tick **Measure drift** to also compare each
   page's structure with its last good run.
4. **Health and repair.** **Check health** shows, per source, the serving recipe, whether the
   source is stale in the dataset (and why), and the stored recipe versions. With
   `recipe_memory` in the job, two actions appear:
   - **Propose repair** runs `rastro recipe repair` for that source. Each candidate shows its
     old and new selectors side by side (changed fields in bold), the decision (`promote`,
     `quarantine` or `reject`), every gate with pass or fail, and the reasons. When the job's
     `[repair]` block enables automatic promotion, a candidate that passes every gate starts
     serving at once.
   - **Promote** a candidate with a reason (1 to 500 printable characters; it is recorded in
     the recipe history). Promotion re-checks the gates. **Override failed gates** activates a
     quarantined candidate anyway; the page warns first and the history records the reason
     with a `gates overridden:` prefix. A rejected candidate is never activated.
   - **Write to job file** copies the serving recipe from recipe memory into the job file,
     as `rastro recipe apply` does, keeping the rest of the file. It carries the file digest
     like saving a selector.
5. **Dataset.** Shows the stored records 25 at a time with **Previous page** and **Next page**,
   when each was last seen, and a stale marker for records from stale sources. Values longer than
   2,000 characters are shortened and the record is marked `truncated`, on the page and in
   downloads. **Download JSON** and **Download HTML** produce the same records as
   `rastro dataset export` (with that shortening), for datasets of up to 5,000 records (use the command line for
   larger ones). Values come from the source pages: treat them as untrusted data.
6. **Connect your app.** Shows the `rastro job run`, `job load` and `dataset export` commands and
   the `run_job_file` call for that job. Run them from the root directory.

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
- Every action that changes something (create, save, load, repair, promote, write) is a `POST`
  with the token and this Studio's exact origin. Actions that edit a job file carry its digest.
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
  deterministic verification; it never becomes records directly and is never promoted by Studio.

## Limitations

Studio edits one selector per save, and only recipe field mappings (not the field schema,
dataset or acquisition blocks). The wizard takes one example record; use `rastro new` with
several `--example` records when one is not enough. Repair needs `recipe_memory` and a
`[dataset]` in the job. Long-running actions (a URL job, a repair) keep the button disabled
until they finish; there is no progress bar. It is single-user and has no authentication beyond the
per-run token.
