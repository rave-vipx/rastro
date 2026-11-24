"""The Hub's "allow only for this site" choice: registry input for that site, nothing else."""

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx2")

from starlette.testclient import TestClient

from rastro.application.capability_gate import require
from rastro.application.job_loader import load_job
from rastro.application.operator_trust import current_grants, entered_from
from rastro.domain.models import ConfigurationError
from rastro.domain.registry import TrustIndicators
from rastro.studio import routes_registry, routes_run
from rastro.studio.app import create_app
from rastro.studio.hub_actions import record_decision, site_scope
from rastro.studio.web import Studio
from tests.integration.test_studio import serving
from tests.integration.test_studio_registry import JOB

pytestmark = pytest.mark.usefixtures("offline_cli_loop")
OTHER = JOB.replace("agenda.example", "other.example")
BOTH = JOB.replace(
    'url = "https://agenda.example/espectaculos/"',
    'url = "https://agenda.example/espectaculos/"\n[[sources]]\npath = "page.html"\n'
    'url = "https://other.example/"',
)
INSTALL = {"registry": "registry", "spec": "shop@1.0.0"}


def _fake_install(*args: Any, **kwargs: Any) -> Any:
    """A hub recipe that asks for page actions, checked as registry input like the real one."""
    with entered_from("registry"):
        require(frozenset({"page_actions"}))
    meta = SimpleNamespace(state="candidate")
    stored = SimpleNamespace(recipe=SimpleNamespace(version=1), metadata=meta)
    imported = SimpleNamespace(stored=stored, reused=False)
    trust = TrustIndicators(replay_verified=True, days_since_verified=0, has_sample=True, format=1)
    return SimpleNamespace(
        id="shop", version="1.0.0", imported=imported, trust=trust, published_trust=trust
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    for name, text in (("job.toml", JOB), ("other.toml", OTHER), ("both.toml", BOTH)):
        (tmp_path / name).write_text(text)
    (tmp_path / "page.html").write_text("<article class='item'><h2>Evento</h2></article>")
    (tmp_path / "registry").mkdir()
    monkeypatch.setattr(routes_registry, "install_from_registry", _fake_install)
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as http:
        http.headers["X-Studio-Token"] = token
        yield http


def _install(client: TestClient, file: str, **extra: object) -> Any:
    return client.post("/api/registry/install", json={**INSTALL, "file": file, **extra})


def test_allowed_site_installs_and_other_sites_stay_refused(client: TestClient) -> None:
    refused = _install(client, "job.toml")
    assert refused.status_code == 400 and refused.json()["error"] == "capability_refused"
    allowed = _install(client, "job.toml", allow=["page_actions"], site="agenda.example")
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["allowed_for_site"] == ["page_actions"]
    assert _install(client, "job.toml").status_code == 200  # held for this site, this run
    for other in ("other.toml", "both.toml"):  # another site, or a job that also reads one
        assert _install(client, other).json()["error"] == "capability_refused"
    wrong = _install(client, "other.toml", allow=["page_actions"], site="agenda.example")
    assert wrong.json()["error"] == "capability_refused"  # a decision for A never covers B
    assert not current_grants().allows("page_actions", "registry")  # nothing leaked


def test_studio_runs_get_the_grant_only_for_that_site(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[bool, bool]] = []

    def fake_run(job: Any, **kwargs: Any) -> Any:
        grants = current_grants()
        seen.append(
            (grants.allows("page_actions", "registry"), grants.allows("page_actions", "mcp"))
        )
        raise ConfigurationError("stop here")

    monkeypatch.setattr(routes_run, "run_job", fake_run)
    assert (
        _install(client, "job.toml", allow=["page_actions"], site="agenda.example").status_code
        == 200
    )
    for file in ("job.toml", "other.toml", "both.toml"):
        client.post("/api/extract", json={"file": file})
    assert seen == [(True, False), (False, False), (False, False)]


def test_site_scope_needs_a_decision_for_every_site(tmp_path: Path) -> None:
    (tmp_path / "job.toml").write_text(JOB)
    (tmp_path / "page.html").write_text("")
    studio, job = Studio(tmp_path), load_job(tmp_path / "job.toml")
    with site_scope(studio, job):
        assert not current_grants().allows("stealth", "registry")
    record_decision(studio, ("agenda.example", frozenset({"stealth"})))
    with site_scope(studio, job):
        assert current_grants().allows("stealth", "registry")
        assert not current_grants().allows("stealth", "bundle")
    assert not current_grants().allows("stealth", "registry")
    other = Studio(tmp_path)  # decisions belong to one Studio run
    with site_scope(other, job):
        assert not current_grants().allows("stealth", "registry")
