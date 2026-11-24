"""Nested ticket tiers with no custom adapter: run a job, print its report, read records.

Run with `uv run python examples/event-agenda/read_agenda.py`. The page is a synthetic file
under `sample/tiers/`; nothing touches the network. The job file declares each event's
`tiers` as a list of objects (tier name and price), the recipe reads them with one nested
mapping, and `rastro.read_records` gives them back from code with provenance.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import rastro

SAMPLE = Path(__file__).parent / "sample" / "tiers"


def summary(report: rastro.RunReport) -> str:
    totals = report.totals
    return (
        f"{report.status}: {totals.sources} source, {totals.records_accepted} accepted, "
        f"{totals.records_rejected} rejected"
    )


def run(folder: Path) -> list[rastro.ReadRecord]:
    """Copy the sample into ``folder`` (state stays outside the tree), load it, read it back."""
    shutil.copytree(SAMPLE, folder / "tiers")
    job = folder / "tiers" / "job.toml"
    loaded = rastro.load_job_file(job)
    print(summary(loaded.report))
    return list(rastro.read_records(job).records)


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        for record in run(Path(folder).resolve()):
            tiers = ", ".join(f"{t['tier']} {t['price']}" for t in record.fields["tiers"])
            print(f"{record.fields['title']}: {tiers}")
            print("  title provenance:", record.provenance["title"].locator)


if __name__ == "__main__":
    main()
