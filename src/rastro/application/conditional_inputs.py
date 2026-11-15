"""Digest of the inputs that shape a source's extraction, bound to its HTTP validators.

A ``304`` only proves the page is unchanged. If the job, the recipe or the schema changed
since the validators were stored, the dataset must be extracted again (SYNC-004).
"""

import json
from hashlib import sha256
from pathlib import Path

from rastro.application.active_recipes import serving_recipe_digests
from rastro.domain.jobs import Job, UrlJobSource
from rastro.domain.sources import schema_fingerprint


def extraction_inputs(
    job: Job, *, base_dir: Path | None, allow_absolute_paths: bool
) -> dict[str, str]:
    """Source URL -> sha256 of the effective job, schema and serving recipes for that URL."""
    serving = serving_recipe_digests(
        job, base_dir=base_dir, allow_absolute_paths=allow_absolute_paths
    )
    shared = {
        "job": job.model_dump(mode="json"),
        "schema": schema_fingerprint(job.build_schema()),
    }
    recipes: dict[str, list[str | None]] = {}
    for item, digest in zip(job.sources, serving, strict=True):
        if isinstance(item, UrlJobSource):
            recipes.setdefault(item.url, []).append(digest)
    return {
        url: sha256(
            json.dumps({**shared, "serving": found}, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        for url, found in recipes.items()
    }
