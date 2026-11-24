"""An app adapter on Rastro's public API only: fetch, extract, load your own records.

Run with `uv run python examples/adapter.py`. All data is synthetic and nothing touches the
network: the detail page is inline HTML (in production it is `rastro.fetch(url).body`), and
the one real `rastro.fetch` call targets a host outside the policy, which is refused before
any request.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel

import rastro

LISTING = "https://parties.example/agenda"
DETAIL = "https://parties.example/fiesta/noche-de-candombe"
DETAIL_HTML = """
<article class="party">
  <h1>Noche de candombe</h1>
  <time datetime="2026-11-21T23:00:00-03:00">sábado 21</time>
  <span class="venue">Sala Synthetic</span>
  <span class="price">450</span>
</article>
"""
JOB = f"""
format_version = 1
[recipe]
strategy = "css"
record_selector = ".party"
[recipe.fields]
slug = "h1::text"
title = "h1::text"
starts_at = "time::attr(datetime)"
venue = ".venue::text"
price = ".price::text"
[[fields]]
name = "slug"
type = "string"
[[fields]]
name = "title"
type = "string"
[[fields]]
name = "starts_at"
type = "datetime"
[[fields]]
name = "venue"
type = "string"
[[fields]]
name = "price"
type = "decimal"
required = false
[[sources]]
url = "{LISTING}"
[acquisition]
allowed_hosts = ["parties.example"]
[dataset]
name = "parties"
path = "parties.sqlite"
identity = ["slug"]
"""


class Party(BaseModel):
    __records__: ClassVar[str] = "article.party"
    title: Annotated[str, rastro.css("h1::text")]
    starts_at: Annotated[datetime, rastro.css("time::attr(datetime)")]
    venue: Annotated[str, rastro.css(".venue::text")]
    price: Annotated[Decimal | None, rastro.css(".price::text")] = None


def fetch_refused_offline(policy: rastro.AcquisitionPolicy) -> rastro.Fetched:
    """A host outside the allowlist is an explicit outcome, not an exception."""
    refused = rastro.fetch("https://elsewhere.example/", policy=policy)
    assert refused.status != rastro.Outcome.SUCCESS and refused.body is None
    return refused


def build_records(html: str) -> list[dict[str, object]]:
    """Extract with Rastro, then apply the app's own rules (here: a slug from the URL)."""
    result = rastro.scrape(html, Party, url=DETAIL)
    assert result.status == rastro.Outcome.SUCCESS
    return [
        {"slug": DETAIL.rsplit("/", 1)[-1], **party.model_dump(mode="json")}
        for party in result.items
    ]


def stored_records(dataset: Path) -> list[dict[str, object]]:
    """Read the dataset back with the public CLI (`rastro dataset export`)."""
    search = os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    command = shutil.which("rastro", path=search)
    if command is None:
        raise SystemExit("The rastro command is not installed in this environment")
    output = subprocess.run(
        [command, "dataset", "export", str(dataset), "--format", "json"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    records: list[dict[str, object]] = json.loads(output)["records"]
    return records


def run(folder: Path) -> list[dict[str, object]]:
    job = folder / "parties.toml"
    job.write_text(JOB, encoding="utf-8")
    refused = fetch_refused_offline(rastro.AcquisitionPolicy(allowed_hosts=("parties.example",)))
    print("fetch outside the policy:", refused.status.value, refused.issues[0].code)
    records = build_records(DETAIL_HTML)
    observed = datetime(2026, 11, 15, 12, tzinfo=UTC)
    loaded = rastro.load_records(job, records, source_url=LISTING, observed_at=observed)
    print("load:", loaded.status, {k.value: v for k, v in loaded.sources[0].counts.items() if v})
    return stored_records(folder / "parties.sqlite")


def main() -> None:
    with tempfile.TemporaryDirectory() as folder:
        for record in run(Path(folder).resolve()):
            evidence = record["provenance"]["title"]  # type: ignore[index]
            print(record["fields"])
            print(
                "title provenance:", evidence["method"], evidence["locator"], evidence["source_url"]
            )


if __name__ == "__main__":
    main()
