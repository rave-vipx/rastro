"""Configuration catalog entries."""

from __future__ import annotations

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
