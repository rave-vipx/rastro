"""Render the engine capability map as ``docs/engines.md``."""

from __future__ import annotations

from rastro.domain.engine_map import EngineArea, EngineFeature

__all__ = ["render_markdown"]

_AREA_TITLES: dict[EngineArea, str] = {
    "parser": "Parser and selectors",
    "adaptive": "Adaptive relocation and selector generation",
    "http": "HTTP sessions",
    "browser": "Browser sessions and controls",
    "proxy": "Proxy egress and rotation",
    "crawl": "Spider and crawl engine",
    "convert": "Content conversion",
    "mcp": "Agent browser tools (MCP)",
    "stealth": "Stealth rendering",
    "cli": "Command line",
}


def render_markdown(features: tuple[EngineFeature, ...]) -> str:
    """Render ``docs/engines.md`` from the engine capability map."""
    lines = [
        "# Acquisition and parsing engines",
        "",
        "<!-- Generated from src/rastro/engine_coverage.py; edit SCRAPLING_FEATURES instead. -->",
        "",
        "Rastro fetches, renders, parses, relocates and crawls through engines behind strategy",
        "interfaces (`rastro.ports`). Scrapling is the default engine today; another engine can",
        "implement the same ports without changing jobs, recipes, Studio or the public Python API.",
        "",
        "## Scrapling capability map",
        "",
        "Every public feature of Scrapling is mapped below to the Rastro surface where users",
        "meet it, marked `internal` when Rastro drives it behind a port, `planned` while its",
        "surface is landing, or `not_exposed` with the security or architecture reason.",
        "A continuous-integration test inspects the installed Scrapling package and fails if an",
        "upgrade adds an unmapped feature.",
        "",
    ]
    for area, title in _AREA_TITLES.items():
        rows = [f for f in features if f.area == area]
        if not rows:
            continue
        lines += [
            f"### {title}",
            "",
            "| Engine feature | Surface | Rastro location or reason | Docs |",
            "| --- | --- | --- | --- |",
        ]
        for row in rows:
            default_note = "Used internally behind Rastro ports."
            detail = f"`{row.where}`" if row.where else (row.reason or default_note)
            target = row.docs.removeprefix("docs/")
            doc_link = f"[{target}]({target})" if row.docs else "-"
            cell = detail.replace("|", "\\|")
            lines.append(f"| `{row.name}` | `{row.surface}` | {cell} | {doc_link} |")
        lines.append("")
    return "\n".join(lines)
