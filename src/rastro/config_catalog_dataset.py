"""Configuration catalog entries."""

from __future__ import annotations

PYTHON_MODELS: dict[str, str] = {"AcquisitionPolicy": "acquisition", "Limits": "limits"}

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
