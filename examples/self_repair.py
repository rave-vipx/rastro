"""Heal a renamed CSS class offline with recipe memory and repair="auto". No network needed.

Version 1 of the synthetic page uses ``h3.title``; version 2 renames it to
``h2.heading``. The first run remembers the recipe and a success sample; the
second run finds the old selector broken, relocates the field, checks the
candidate against the retained sample and promotes it.
"""

import argparse
import tempfile
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import BaseModel

import rastro

VERSION_1 = """
<main><ul>
  <li class="job"><h3 class="title">Platform engineer</h3><span class="city">Rivera</span></li>
  <li class="job"><h3 class="title">Product designer</h3><span class="city">Salto</span></li>
</ul></main>
"""
VERSION_2 = """
<main><ul>
  <li class="job"><h2 class="heading">Platform engineer</h2><span class="city">Rivera</span></li>
  <li class="job"><h2 class="heading">Product designer</h2><span class="city">Salto</span></li>
  <li class="job"><h2 class="heading">Data analyst</h2><span class="city">Paysandu</span></li>
</ul></main>
"""


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3.title::text")]
    city: Annotated[str, rastro.css(".city::text")]


def run(memory: Path) -> list[rastro.ExtractionResult[Offer]]:
    results = []
    for html in (VERSION_1, VERSION_2):
        result = rastro.scrape(
            html, Offer, url="https://acme.example/careers", memory=memory, repair="auto"
        )
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, help="recipe memory file (default: temporary)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        memory = args.database or Path(folder) / "recipes.db"
        first, second = run(memory)
    print("version 1:", first.status.value, [offer.title for offer in first.items])
    print("version 2:", second.status.value, [offer.title for offer in second.items])
    if second.repair is not None:
        print("repaired:", second.repair.promoted, "now serving version", second.recipe_version)
        for candidate in second.repair.candidates:
            print(" candidate", candidate.version, candidate.decision, candidate.changed_fields)


if __name__ == "__main__":
    main()
