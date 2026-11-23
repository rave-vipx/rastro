"""Capability catalog entries."""

from __future__ import annotations

from rastro.capabilities_extract import Capability, _names

OPS_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        task="Keep records up to date in SQLite or PostgreSQL",
        use='`[dataset] path = "data.sqlite"`, `identity = ["field"]`; `backend = "postgres"` '
        "with `dsn_env`. Loads track new, changed, unchanged and missing records",
        python=_names("load_job_file forget_job_source JobLoadResult LoadedSource JobRecordData"),
        cli=(
            "rastro job load",
            "rastro dataset status",
            "rastro dataset migrate",
            "rastro job forget-source",
        ),
        mcp=_names("job_load dataset_records"),
        limits="Only approved fields with evidence are stored; partial runs never delete rows",
        instead_of="your own upsert or dedup-by-key table for scraped records",
        docs="docs/dataset.md#load-inspect-and-export-a-job",
    ),
    Capability(
        task="Export a dataset as JSON, JSONL, CSV, XLSX or an HTML table, or list what changed",
        use="`rastro dataset export data.sqlite --format json|jsonl|csv|xlsx|html`; "
        "`rastro dataset changes data.sqlite --against older.sqlite`",
        cli=("rastro dataset export", "rastro dataset changes"),
        limits="`--upcoming` needs an ISO date `--sort-field`",
        instead_of="your own HTML view or CSV dump of scraped records",
        docs="docs/scheduling.md",
    ),
    Capability(
        task="Run sources on a schedule",
        use='`[schedule] every = "6h"` (or `every = "1d"`, `at = "08:00"`) plus '
        "`rastro job watch job.toml` (or `--once` from cron); "
        '`rastro job schedule "every day at 8:00"` reads words',
        cli=("rastro job watch", "rastro job schedule"),
        limits="Lock file prevents overlapping runs; jitter at most a quarter of `every`",
        instead_of="your own scheduler loop or overlap lock",
        docs="docs/operations.md#scheduler",
    ),
    Capability(
        task="Alert when a run fails, drifts or goes stale",
        use="`--notify email|slack|telegram|webhook` or `RASTRO_NOTIFY`, or `--on-failure CMD`, "
        "on `job load`/`run`/`watch`; `rastro notify test CHANNEL` checks a channel; "
        "`rastro health` exits 4 when someone must act",
        python=_names("job_health JobExitCode"),
        cli=("rastro health", "rastro job stats", "rastro notify test"),
        limits="Messages and the hook carry the run report only, no records or secrets; "
        "channels and secrets come from the operator, never from a job file",
        instead_of="parsing logs or exit codes to build your own alerting",
        docs="docs/operations.md#failure-hook",
    ),
    Capability(
        task="Give a person a local UI",
        use="`rastro studio --root DIR`: projects are jobs with a dataset; paste a URL, name "
        "fields, preview, load, see results with provenance, repair",
        cli=("rastro studio",),
        limits="`studio` extra; loopback only",
        instead_of="building your own admin page for scrapers",
        docs="docs/studio.md",
    ),
    Capability(
        task="Find a local AI model and see what a call costs",
        use="`rastro model detect` lists models on loopback ports; `rastro model test --model "
        "SPEC` makes one call and prints the cost; the run report totals show model calls and cost",
        cli=("rastro model detect", "rastro model test"),
        limits="detect probes 127.0.0.1 only; hosted models still need `--allow-remote`",
        instead_of="guessing which local servers run or what a model costs",
        docs="docs/ai.md#find-a-local-model-and-test-it",
    ),
    Capability(
        task="Drive Rastro from an AI agent",
        use="`rastro mcp --root DIR` (MCP tools), the JSON CLI, `rastro capabilities` and "
        "`rastro init --agent claude|codex|generic`",
        cli=("rastro mcp", "rastro capabilities", "rastro init", "rastro doctor"),
        limits="MCP paths stay inside the workspace; the server adds no network access",
        instead_of="importing Rastro internals or scraping without the contract",
        docs="docs/capabilities.md",
    ),
    Capability(
        task="See every configuration option before writing one",
        use="`rastro config` (JSON, or `--output markdown`) lists every job key, keyword "
        "argument, operator grant, environment variable and CLI flag with type, default and "
        "bounds; `rastro job template --all` prints an annotated job file; MCP resource "
        "`rastro://configuration`",
        cli=("rastro config", "rastro job template"),
        limits="Generated from the code of the installed version; unknown job keys are "
        "rejected with the closest valid key",
        instead_of="guessing option names, or hand-building what an option already does",
        docs="docs/configuration.md",
    ),
)
