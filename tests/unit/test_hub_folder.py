"""The in-repository hub: default URL, local-registry loading, replay tooling and its workflow."""

from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from rastro.application.hub_search import hub_candidates
from rastro.application.registry_url import DEFAULT_HUB_URL, parse_registry_url

ROOT = Path(__file__).parents[2]
HUB = ROOT / "hub"


def _load(name: str):
    sys.dont_write_bytecode = True  # keep hub/ free of __pycache__
    spec = importlib.util.spec_from_file_location(f"hub_{name}", HUB / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_url_points_at_the_in_repo_hub() -> None:
    assert DEFAULT_HUB_URL == "https://raw.githubusercontent.com/rave-vipx/rastro/main/hub/"
    assert parse_registry_url(DEFAULT_HUB_URL).base == DEFAULT_HUB_URL


@pytest.mark.parametrize(
    ("url", "starter"),
    [
        ("https://sitemap-crawl.example/sitemap.xml", "sitemap-crawl"),
        ("https://shopify-products.example/products.json", "shopify-products"),
        ("https://generic-crawl.example/", "generic-crawl"),
    ],
)
def test_hub_folder_loads_as_a_local_registry(url: str, starter: str) -> None:
    offers = hub_candidates(url, registry=HUB)
    assert [offer.entry.id for offer in offers] == [starter]
    assert offers[0].card is not None and offers[0].health is not None
    assert offers[0].health.passed


def test_summaries_are_current_and_every_package_replays() -> None:
    assert _load("build").check(HUB) == []


def test_stale_index_is_reported(tmp_path: Path) -> None:
    copy = tmp_path / "hub"
    shutil.copytree(HUB, copy)
    (copy / "index.json").write_bytes(b"[]\n")
    assert any("index.json is stale" in item for item in _load("build").check(copy))


def test_a_broken_package_fails_the_replay(tmp_path: Path) -> None:
    copy = tmp_path / "hub"
    shutil.copytree(HUB, copy)
    sample = copy / "recipes/sitemap-crawl/1.0.0/sample.html"
    sample.write_text("<main>changed</main>\n")
    assert any("sitemap-crawl@1.0.0" in item for item in _load("build").check(copy))


def test_layout_check_accepts_the_hub_and_refuses_strays(tmp_path: Path) -> None:
    layout = _load("check_layout")
    assert layout.problems(HUB) == []
    copy = tmp_path / "hub"
    shutil.copytree(HUB, copy)
    (copy / "stray.txt").write_text("x")
    script = copy / "recipes/generic-crawl/1.0.0/sample.html"
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    (copy / "link.json").symlink_to(copy / "index.json")
    found = " ".join(layout.problems(copy))
    assert "stray.txt: outside" in found
    if os.name != "nt":  # Windows has no executable bit to detect
        assert "sample.html: executable" in found
    assert "link.json: symbolic" in found


def test_workflow_is_least_privilege_pinned_and_path_filtered() -> None:
    text = (ROOT / ".github/workflows/hub.yml").read_text()
    assert text.count("paths: ['hub/**']") == 2
    assert "\npermissions:\n  contents: read\n" in text
    assert "write" not in text.replace("- run:", "")
    uses = [line.split("uses:")[1].split()[0] for line in text.splitlines() if "uses:" in line]
    assert uses and all("@" in item and len(item.split("@")[1]) == 40 for item in uses)
    assert "hub/build.py --check" in text and "hub/check_layout.py" in text
