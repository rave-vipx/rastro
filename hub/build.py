"""Rebuild or check the hub's index.json and health.json by replaying every package offline.

    uv run python hub/build.py            # rewrite index.json and health.json
    uv run python hub/build.py --check    # fail when they are stale or a package fails

Packages are never changed: each ``recipes/<id>/<version>`` is verified (digests, selector
policy, offline replay against its synthetic sample) and the two summary files are derived
from the result. The rastro version in health.json is informative; --check ignores it.
"""

import argparse
import json
import sys
from pathlib import Path

from rastro import __version__
from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.structured import JsonParser
from rastro.application.recipe_hub import verify_bundle
from rastro.application.recipe_registry import _build_entry
from rastro.domain.recipe_bundle import RecipeBundleError, canonical_json
from rastro.domain.registry import RECIPES_DIR, semver_key
from rastro.ports.parser import DocumentParser

HUB = Path(__file__).parent


def parser_for(representation: str) -> DocumentParser:
    return JsonParser() if representation == "json" else ScraplingParser()


def rebuild(hub: Path) -> tuple[bytes, bytes, list[str]]:
    """Return index.json bytes, health.json bytes and one message per failing package."""
    entries, health, failures = [], [], []
    for package in sorted((hub / RECIPES_DIR).glob("*/*")):
        label = f"{package.parent.name}@{package.name}"
        try:
            manifest, files, _, _ = verify_bundle(package, parser_for)
            entries.append(_build_entry(manifest, files, today=manifest.last_verified))
            passed = True
        except (RecipeBundleError, ValueError, OSError) as error:
            failures.append(f"{label}: {error}")
            continue
        health.append(
            {
                "id": manifest.id,
                "version": manifest.version,
                "last_verified": manifest.last_verified.isoformat(),
                "rastro_version": __version__,
                "passed": passed,
            }
        )
    entries.sort(key=lambda item: (item.id, semver_key(item.version)))
    index = canonical_json([item.model_dump(mode="json") for item in entries])
    return index, canonical_json({"format": 1, "packages": health}), failures


def _stable(health: bytes) -> list[dict]:
    packages = json.loads(health)["packages"]
    return [{k: v for k, v in item.items() if k != "rastro_version"} for item in packages]


def check(hub: Path) -> list[str]:
    index, health, problems = rebuild(hub)
    problems = list(problems)
    if (hub / "index.json").read_bytes() != index:
        problems.append("index.json is stale; run hub/build.py")
    try:
        if _stable((hub / "health.json").read_bytes()) != _stable(health):
            problems.append("health.json is stale; run hub/build.py")
    except (OSError, ValueError, KeyError):
        problems.append("health.json is missing or invalid; run hub/build.py")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    parser.add_argument("--hub", type=Path, default=HUB, help="hub directory (default: here)")
    args = parser.parse_args()
    if args.check:
        problems = check(args.hub)
    else:
        index, health, problems = rebuild(args.hub)
        if not problems:
            (args.hub / "index.json").write_bytes(index)
            (args.hub / "health.json").write_bytes(health)
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
