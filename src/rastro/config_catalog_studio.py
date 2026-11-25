"""Configuration catalog entries."""

from __future__ import annotations

CONFIG_RULE = (
    "Before writing an option, check `rastro config` or docs/configuration.md; never invent "
    "one. Job files reject unknown keys, and `rastro job validate` names the closest valid key."
)

STUDIO_RESULTS_RULE = (
    "To see results in Studio, keep them in a job's dataset: let `rastro job load` write them, "
    "or call `rastro.load_records(job_path, records, source_url=..., observed_at=...)` for "
    "records your own code built. Then run `rastro studio --root <project folder>` (extra "
    "`studio`) and open the project. Do not create your own database or HTML table just to "
    "inspect results: Studio lists only datasets that a job declares, with provenance, "
    "per-source status and alerts."
)
