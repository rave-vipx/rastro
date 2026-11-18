"""Configuration points that are not job-file keys: Python keyword arguments, operator
grants, environment variables, plus where each part is documented (AGENT-001, N18).

Data only, no imports from Rastro: ``capabilities_render`` reads ``SECTIONS`` for the agent
snippet. Guard tests check that every parameter, grant flag and ``RASTRO_*`` name in the
code has an entry here.
"""

from __future__ import annotations

CONFIG_RULE = (
    "Before writing an option, check `rastro config` or docs/configuration.md; never invent "
    "one. Job files reject unknown keys, and `rastro job validate` names the closest valid key."
)

# How data reaches Studio; the Rave adoption built its own SQLite and Studio could not show it.
STUDIO_RESULTS_RULE = (
    "To see results in Studio, keep them in a job's dataset: let `rastro job load` write them, "
    "or call `rastro.load_records(job_path, records, source_url=..., observed_at=...)` for "
    "records your own code built. Then run `rastro studio --root <project folder>` (extra "
    "`studio`) and open the project. Do not create your own database or HTML table just to "
    "inspect results: Studio lists only datasets that a job declares, with provenance, "
    "per-source status and alerts."
)

# Top-level job key -> what it controls; the agent snippet's configuration map.
SECTIONS: dict[str, str] = {
    "sources": "the pages to read (URLs or local files)",
    "fields": "record fields and their types",
    "acquisition": "allowed hosts, JavaScript rendering, pacing, retries, budgets, proxies",
    "region": "locale, timezone, currency and egress profile",
    "recipe": "how records are found: css, json, jsonld or embedded_json, plus pagination",
    "limits": "page, JSON and record size limits",
    "context": "recipe-memory identity (with recipe_memory, recipe_version)",
    "model": "optional AI model (ollama, http, command) that proposes selectors",
    "inference_policy": "model call, size and time budgets",
    "dataset": "SQLite or PostgreSQL storage, identity, approved fields",
    "consistency": "semantic rules on extracted values",
    "repair": "gates a repaired recipe must pass",
    "schedule": "run interval for `rastro job watch`",
    "scroll": "scroll rendered pages for more records",
    "spider": "follow links to detail pages, or start from a sitemap",
}

# Job path prefix -> docs anchor; the longest matching prefix wins.
JOB_DOCS: dict[str, str] = {
    "": "docs/jobs.md#complete-toml-example",
    "sources": "docs/jobs.md#sources-recipes-memory-and-providers",
    "fields": "docs/jobs.md#target-fields-and-dynamic-schema",
    "acquisition": "docs/acquisition.md#policy-defaults",
    "acquisition.render": "docs/acquisition.md#javascript-pages",
    "acquisition.solve_challenges": "docs/acquisition.md#javascript-pages",
    "acquisition.session": "docs/acquisition.md#sessions",
    "acquisition.egress_profiles": "docs/acquisition.md#network-egress",
    "acquisition.auth": "docs/jobs.md#auth-header-from-the-environment",
    "region": "docs/acquisition.md#metrics-and-regional-behavior",
    "context": "docs/memory.md#identity-and-validation",
    "recipe_memory": "docs/memory.md#identity-and-validation",
    "recipe_version": "docs/memory.md#versions-and-explicit-fallbacks",
    "limits": "docs/api.md#inputs",
    "recipe": "docs/api.md#recipe-variants",
    "recipe.pagination": "docs/jobs.md#pagination",
    "model": "docs/ai.md#connect-a-model",
    "inference_policy": "docs/ai.md#cost-and-call-budgets",
    "dataset": "docs/jobs.md#dataset-block-and-preview",
    "consistency": "docs/consistency.md#rules",
    "repair": "docs/repair.md",
    "schedule": "docs/operations.md#scheduler",
    "scroll": "docs/jobs.md#scroll",
    "spider": "docs/jobs.md#spider",
    "spider.seed": "docs/jobs.md#start-from-a-sitemap",
}

# Python models whose keyword arguments are the same keys as a job section.
PYTHON_MODELS: dict[str, str] = {"AcquisitionPolicy": "acquisition", "Limits": "limits"}

# Public function -> (docs anchor, {parameter: description}).
PYTHON_PARAMS: dict[str, tuple[str, dict[str, str]]] = {
    "scrape": (
        "docs/api.md#scrape",
        {
            "target": "URL, file path or HTML string to read.",
            "schema": "Pydantic model of one record; `rastro.css(...)` defaults are selectors.",
            "records": "CSS selector of one record when the model has no selectors.",
            "fields": "Field name -> selector, instead of selectors on the model.",
            "recipe": "A full Recipe; replaces records and fields.",
            "url": "Provenance URL for a file or HTML target; nothing is fetched.",
            "memory": "SQLite file that remembers and versions the recipe.",
            "repair": 'What to do when the recipe breaks: "propose", "auto" or "off".',
            "locale": "Locale used to read numbers and dates.",
            "limits": "Size and count limits (`rastro.Limits`).",
            "model": 'Optional AI model: a spec string, "env" for RASTRO_MODEL, or a ModelPort.',
        },
    ),
    "fetch": (
        "docs/api.md#adapters-fetch-and-load-your-own-records",
        {
            "url": "URL to fetch; its host must be allowed by the policy.",
            "policy": "AcquisitionPolicy; defaults to allowing only the URL's host.",
            "representation": '"html" or "json": the body format expected.',
            "max_bytes": "Largest body read; defaults to the matching Limits value.",
            "locale": "Locale sent as Accept-Language.",
        },
    ),
    "load_records": (
        "docs/api.md#adapters-fetch-and-load-your-own-records",
        {
            "job": "Job file that supplies dataset, fields and identity.",
            "records": "Records your code built, one mapping per record.",
            "source_url": "URL the records came from, kept as provenance.",
            "observed_at": "When the records were observed; now when omitted.",
            "allow_absolute_paths": "Allow an absolute dataset path in the job.",
            "allow_remote": "Operator grant: allow job-declared remote endpoints.",
            "allow_env": "Operator grant: extra environment variables the job may read.",
        },
    ),
    "run_job_file": (
        "docs/api.md#declarative-jobs",
        {
            "path": "Job file (.toml or .json).",
            "allow_absolute_paths": "Allow absolute source, memory and dataset paths.",
            "acquisition": "Your own AcquisitionPort instead of the built-in fetcher.",
            "model": "A ModelPort that replaces the job's [model].",
            "allow_remote": "Operator grant: allow job-declared remote model endpoints.",
            "allow_egress": "Operator grant: use the job's egress proxies.",
            "allow_stealth": 'Operator grant: allow render = "stealth".',
            "allow_env": "Operator grant: extra environment variables the job may read.",
            "allow_private_network": "Operator grant: reach private addresses of allowed hosts.",
            "allow_ports": "Operator grant: ports allowed besides 80 and 443.",
            "ignore_robots": "Operator grant: hosts whose robots.txt is not enforced.",
            "max_model_cost": "Cost cap for model calls in this run.",
            "model_cost_per_call": "Cost counted per model call in this run.",
        },
    ),
}

# Operator grant -> (environment variable, Python keyword, what it allows, docs anchor).
# A job file can never grant these; only the person running Rastro can.
GRANTS: dict[str, tuple[str, str, str, str]] = {
    "--allow-remote": (
        "RASTRO_ALLOW_REMOTE",
        "allow_remote",
        "Job-declared models may use non-loopback endpoints; page content leaves the machine.",
        "docs/jobs.md#operator-opt-in",
    ),
    "--allow-stealth": (
        "RASTRO_ALLOW_STEALTH",
        "allow_stealth",
        'Allow render = "stealth" and challenge solving.',
        "docs/acquisition.md#javascript-pages",
    ),
    "--allow-egress": (
        "RASTRO_ALLOW_EGRESS",
        "allow_egress",
        "Use the egress proxies the job declares.",
        "docs/acquisition.md#network-egress",
    ),
    "--allow-env": (
        "RASTRO_ALLOWED_ENV",
        "allow_env",
        "Environment variables, besides RASTRO_*, that job keys and proxy credentials may read.",
        "docs/jobs.md#operator-opt-in",
    ),
    "--allow-private-network": (
        "RASTRO_ALLOW_PRIVATE_NETWORK",
        "allow_private_network",
        "Reach private, loopback and CGNAT addresses of allowed hosts.",
        "docs/acquisition.md#operator-permissions",
    ),
    "--allow-port": (
        "RASTRO_ALLOWED_PORTS",
        "allow_ports",
        "Ports allowed besides 80 and 443.",
        "docs/acquisition.md#operator-permissions",
    ),
    "--ignore-robots": (
        "RASTRO_IGNORE_ROBOTS",
        "ignore_robots",
        "Hosts whose robots.txt is not fetched or enforced; you assert permission.",
        "docs/acquisition.md#operator-permissions",
    ),
    "--allow-override-gates": (
        "",
        "allow_override_gates",
        "`rastro mcp`: let the agent promote a recipe that failed its gates.",
        "docs/mcp.md#agent-in-the-loop",
    ),
    "--override-gates": (
        "",
        "override_gates",
        "`rastro recipe promote`: promote a candidate that failed its gates (needs --reason).",
        "docs/repair.md#commands",
    ),
}

# Environment variables Rastro reads that are not grants.
ENV_VARS: dict[str, tuple[str, str]] = {
    "RASTRO_MODEL": (
        'Default model spec, such as "ollama:qwen3"; used by scrape(model="env"), Studio '
        "Ask AI, `new --search model` and [repair] model = true.",
        "docs/ai.md#connect-a-model",
    ),
    "RASTRO_MODEL_LOCAL": (
        "Set to 1 to declare that a RASTRO_MODEL command keeps content local.",
        "docs/ai.md#privacy",
    ),
    "RASTRO_ON_FAILURE": (
        "Command run with a JSON summary when job load, run or watch needs attention.",
        "docs/operations.md#failure-hook",
    ),
    "RASTRO_SEARXNG_URL": (
        "SearXNG endpoint for `rastro new --goal --search searxng`.",
        "docs/jobs.md#start-from-a-goal",
    ),
}

# CLI command (first word after `rastro`) -> docs anchor.
CLI_DOCS: dict[str, str] = {
    "": "docs/jobs.md#cli-commands-and-exit-codes",
    "job": "docs/jobs.md#cli-commands-and-exit-codes",
    "dataset": "docs/dataset.md#load-inspect-and-export-a-job",
    "health": "docs/operations.md#one-health-check",
    "recipe": "docs/repair.md#commands",
    "studio": "docs/studio.md#install-and-start",
    "suggest": "docs/suggest.md#from-the-command-line",
    "new": "docs/suggest.md#create-a-job-file",
    "mcp": "docs/mcp.md#install-and-connect",
    "capabilities": "docs/capabilities.md",
    "init": "docs/ai-agents.md",
    "config": "docs/configuration.md",
}
