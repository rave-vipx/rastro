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
    "RASTRO_NOTIFY": (
        "Comma list of notification channels (email, slack, telegram, webhook) for job "
        "load, run or watch.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_WEBHOOK_URL": (
        "Secret https URL the webhook channel posts the content-free message to.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SLACK_WEBHOOK_URL": (
        "Secret Slack incoming webhook URL for the slack channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_TELEGRAM_TOKEN": (
        "Secret Telegram bot token for the telegram channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_TELEGRAM_CHAT_ID": (
        "Telegram chat id or @channel the telegram channel writes to.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SMTP_HOST": (
        "SMTP server for the email channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SMTP_PORT": (
        "SMTP port for the email channel (default 587, or 465 with ssl).",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SMTP_SECURITY": (
        "starttls (default) or ssl for the email channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SMTP_USERNAME": (
        "SMTP login name for the email channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_SMTP_PASSWORD": (
        "Secret SMTP password for the email channel.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_EMAIL_FROM": (
        "Sender address of notification email.",
        "docs/operations.md#notifications",
    ),
    "RASTRO_NOTIFY_EMAIL_TO": (
        "Comma list of up to 10 notification email recipients.",
        "docs/operations.md#notifications",
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
