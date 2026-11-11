# Local Studio

Studio is the proven job path done in a browser: pick a job file, validate it, preview
sample records with per-field provenance, correct one selector, accept the fix, and copy the
commands that connect your app (P-008). It calls the same application services as the CLI and
SDK; it adds no extraction logic.

## Install and start

```bash
pip install "rastro-engine[studio]"
rastro studio --root path/to/jobs --port 8765
```

`--root` defaults to the current directory and `--port` to 8765. Studio always binds
`127.0.0.1`. Without the extra, `rastro studio` prints a `missing_extra` error and exits with
code 2. The page is one HTML file plus one script and one stylesheet shipped in the package;
there is no Node build.

## The six steps

1. **Job file.** Lists `.toml` and `.json` files below the root (four levels deep, symlinks
   skipped).
2. **Validate.** Parses and validates the job with the same rules as `rastro job validate`.
3. **Preview.** Runs `rastro job preview` logic: sample records, and for every field the method,
   locator, validation status and whether it would be loaded. Rejected records show their
   reasons. A preview needs a `[dataset]` block and writes nothing. A job with URL sources
   fetches them under its own acquisition policy, exactly as the CLI does.
4. **Correct a match.** Choose a field mapping from a recipe, edit its selector and preview again.
   Nothing is written yet.
5. **Accept.** Writes the corrected job back to its file. The whole edited job is validated first,
   the write goes through a temporary file in the same directory and an atomic replace, and the
   request carries the digest of the file you previewed: if the file changed meanwhile, accept
   fails with `file_changed`. TOML comments and layout are kept because only the one selector
   text is replaced; if that text is not unique in the file, Studio refuses and asks you to edit
   by hand. JSON jobs are rewritten with two-space indentation.
6. **Connect your app.** Shows the `rastro job run`, `job load` and `dataset export` commands and
   the `run_job_file` SDK call for that job. Run them from the root directory.

## Security model

Studio is a local tool, not a server to expose.

- Loopback only. Requests whose `Host` is not `127.0.0.1` or `localhost` are rejected (this
  also blocks DNS rebinding), and so is any request whose `Origin` is not local.
- A random token is generated on every start and embedded in the served page. Every `POST`
  needs it in `X-Studio-Token`.
- Paths are relative to the root, may not contain `..`, backslashes or NUL, must end in `.toml`
  or `.json`, and may not pass through symlinks. Files the job itself references follow the same
  containment rules as the CLI.
- Source content is shown with plain text content only; the script never parses markup. A
  Content-Security-Policy allows only same-origin script and style, and no inline code.
- Request bodies are limited to 16 KiB, job files to 256 KiB, and selectors to 1024 characters.

## Limitations

Studio edits one selector per accept, and only recipe field mappings (not the field schema,
dataset or acquisition blocks). It is single-user and has no authentication beyond the
per-run token.
