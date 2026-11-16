"""N10, HUB-005, SEC-001: ``recipe search|install --registry-url`` end to end (offline).

The curl transport and the system resolver are replaced; the CLI, the acquisition policy and
the install gates are the real ones.
"""

import json
import shutil
from pathlib import Path

import pytest

from rastro.cli import main
from rastro.domain.acquisition import HttpResponse

EXAMPLE = Path(__file__).parents[2] / "examples/registry"
URL = "https://registry.example.org/"

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.fixture
def serve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "served"
    shutil.copytree(EXAMPLE, root)
    state = {"address": "8.8.8.8", "seen": []}

    class Transport:
        async def get(self, target, *, headers, timeout, max_bytes, proxy):
            state["seen"].append(target.url)
            path = target.url.split("://", 1)[1].split("/", 1)[1]
            file = root / path
            if path == "robots.txt" or not file.is_file():
                return HttpResponse(404, {}, b"", 0)
            body = file.read_bytes()
            return HttpResponse(200, {"content-type": "application/json"}, body, len(body))

    class Resolver:
        async def resolve(self, host, port):
            return (state["address"],)

    monkeypatch.setattr("rastro.adapters.http.CurlTransport", Transport)
    monkeypatch.setattr("rastro.adapters.dns.SystemResolver", Resolver)
    return state


def test_search_by_url(serve, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["recipe", "search", "sodre", "--registry-url", URL]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in out["results"]] == ["uy-sodre-agenda"]


def test_install_by_url_is_a_candidate_and_never_touches_jobs(
    serve, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    job = tmp_path / "job.toml"
    job.write_text("format_version = 1\n[repair]\nauto_promote = true\n", encoding="utf-8")
    store = tmp_path / "recipes.sqlite"
    argv = ["recipe", "install", "uy-sodre-agenda", "--registry-url", URL, "--store", str(store)]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state"] == "candidate" and out["package_version"] == "1.0.0"
    assert job.read_text(encoding="utf-8").endswith("auto_promote = true\n")


def test_registry_and_registry_url_are_mutually_exclusive(
    serve, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    both = ["--registry", str(EXAMPLE), "--registry-url", URL]
    assert main(["recipe", "search", "sodre", *both]) != 0
    assert "not allowed with" in capsys.readouterr().err
    assert main(["recipe", "search", "sodre"]) != 0
    assert "usage_error" in capsys.readouterr().err


def test_unsafe_url_and_private_address_fail_with_a_stable_error(
    serve, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["recipe", "search", "x", "--registry-url", "http://registry.example.org/"]) != 0
    assert serve["seen"] == []
    serve["address"] = "10.0.0.5"
    assert main(["recipe", "search", "x", "--registry-url", URL]) != 0
    assert serve["seen"] == []
    captured = capsys.readouterr()
    assert "10.0.0.5" not in captured.out + captured.err
    granted = ["--allow-private-network", "recipe", "search", "sodre", "--registry-url", URL]
    assert main(granted) == 0
