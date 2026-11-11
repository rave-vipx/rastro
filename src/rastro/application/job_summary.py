"""The JSON summary of a validated job, shared by `rastro job validate` and the MCP server."""

from __future__ import annotations

from typing import Any

from rastro.domain.jobs import Job
from rastro.domain.models import schema_fingerprint


def job_validation_summary(job: Job) -> dict[str, Any]:
    """Describe a valid job without values from the sources it would read."""
    return {
        "valid": True,
        "format_version": job.format_version,
        "schema_fingerprint": schema_fingerprint(job.build_schema()),
        "source_count": len(job.sources),
        "fields": [field.name for field in job.fields],
        "job": job.redacted_dump(),
    }
