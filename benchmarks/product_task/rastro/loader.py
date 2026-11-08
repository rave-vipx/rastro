"""Generic manual candidate staging; activation still runs the real recipe CI."""

import json
from datetime import UTC, datetime
from pathlib import Path

from rastro.cli_repair_context import RepairContext


def stage_candidate(job: Path, edit: Path) -> tuple[int, int]:
    """Stage source one's prewritten edit; return version and changed-entry count."""
    edits = json.loads(edit.read_text(encoding="utf-8"))
    context = RepairContext.load(job, False)
    _, key, previous = context.source(1)
    candidate = previous.model_copy(update={**edits, "version": previous.version + 1})
    context.repository.save_candidate(key, candidate, selected_index=0, at=datetime.now(UTC))
    return candidate.version, sum(getattr(previous, name) != value for name, value in edits.items())
