"""Distributed event samples use reserved hosts and invented source identities (COST-001)."""

import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("case", ["venue-cards", "jsonld-events", "list-events"])
def test_repair_samples_have_synthetic_source_names(case: str) -> None:
    import tomllib

    job = ROOT / "benchmarks/repair_suite/cases" / case / "job.toml"
    data = tomllib.loads(job.read_text(encoding="utf-8"))
    assert all(urlsplit(source["url"]).hostname.endswith(".example") for source in data["sources"])


def test_registry_agenda_uses_a_reserved_host() -> None:
    path = ROOT / "examples/registry/recipes/teatro-central-agenda/1.0.0/bundle.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["id"] == "teatro-central-agenda"
    assert urlsplit(manifest["source"]["url"]).hostname == "agenda.example"
