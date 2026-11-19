"""Configuration catalog entries."""

from __future__ import annotations

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
    "RASTRO_PROFILE_DIR": (
        "Directory holding the persistent browser profiles that browser.session_profile names.",
        "docs/browser.md#persistent-sessions",
    ),
    "RASTRO_SEARXNG_URL": (
        "SearXNG endpoint for `rastro new --goal --search searxng`.",
        "docs/jobs.md#start-from-a-goal",
    ),
}

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
