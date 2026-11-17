"""N1/N3/N9, SEC-008: previews expire and bounded settings validate before writes."""

import json
from pathlib import Path

import pytest

from rastro.application.job_loader import load_job
from rastro.studio.app import create_app
from tests.integration.test_studio import post, serving
from tests.integration.test_studio_ops import EXAMPLES, PAGE, URL, FakeAcquirer

I18N = "src/rastro/studio/static/i18n.js"  # bounds.js labels come from the dictionary (N8)

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


@pytest.mark.parametrize(
    ("elapsed", "different", "count"),
    [(599, False, 1), (600, False, 2), (601, False, 2), (1, True, 2)],
)
def test_preview_expiry(tmp_path: Path, elapsed: int, different: bool, count: int) -> None:
    now = [0.0]
    other = URL + "?other=1"
    acquirer = FakeAcquirer({URL: PAGE, other: PAGE})
    app, token = create_app(tmp_path, acquisition=acquirer, clock=lambda: now[0])
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        data = {"file": "job.toml", "url": URL, "examples": EXAMPLES}
        assert post(client, "/api/suggest", data).status_code == 200
        now[0] = elapsed
        assert (
            post(client, "/api/create", {**data, "url": other if different else URL}).status_code
            == 200
        )
    assert len(acquirer.fetched) == count


def test_bounded_settings(tmp_path: Path) -> None:
    app, token = create_app(tmp_path, acquisition=FakeAcquirer({URL: PAGE}))
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        assert (
            post(
                client, "/api/create", {"file": "job.toml", "url": URL, "examples": EXAMPLES}
            ).status_code
            == 200
        )
        path = tmp_path / "job.toml"
        path.write_text(
            path.read_text(encoding="utf-8")
            + "\n# keep me\n[scroll] # scroll comment\nmax_steps = 2 # steps comment\n",
            encoding="utf-8",
        )
        for kind, policy, bad in [
            (
                "spider",
                {"mode": "spider", "links": "a::attr(href)", "max_depth": 4, "max_pages": 100},
                {"max_depth": 5},
            ),
            (
                "scroll",
                {"max_steps": 30, "settle_ms": 3000, "stop_when": "no_new_records"},
                {"max_steps": 31},
            ),
        ]:
            opened = post(client, "/api/validate", {"file": "job.toml"}).json()
            data = {"file": "job.toml", "sha256": opened["sha256"], "kind": kind, "policy": policy}
            before = path.read_bytes()
            refused = post(client, "/api/bounds", {**data, "policy": {**policy, **bad}})
            assert refused.status_code == 400
            assert path.read_bytes() == before
            saved = post(client, "/api/bounds", data)
            assert saved.status_code == 200, saved.text
        assert load_job(path).spider.max_pages == 100
        assert load_job(path).scroll.max_steps == 30
        assert "# keep me" in path.read_text(encoding="utf-8")
        assert "# steps comment" in path.read_text(encoding="utf-8")
        assert "# scroll comment" in path.read_text(encoding="utf-8")
        opened = post(client, "/api/validate", {"file": "job.toml"}).json()
        assert opened["bounds"]["scroll_disabled"] is True


def test_disable_scroll_and_reject_malformed_policy(tmp_path: Path) -> None:
    """N9/SEC-008: disabling removes the policy and invalid data never writes."""
    from tests.integration.test_job_api import JOB

    path = tmp_path / "job.toml"
    path.write_text(
        JOB + "\n# standalone\n[scroll] # header comment\nmax_steps = 2 # steps\n", encoding="utf-8"
    )
    app, token = create_app(tmp_path)
    with serving(app, "http://127.0.0.1:8765") as client:
        client.headers["X-Studio-Token"] = token
        sha = post(client, "/api/validate", {"file": "job.toml"}).json()["sha256"]
        data = {"file": "job.toml", "sha256": sha, "kind": "scroll"}
        before = path.read_bytes()
        for policy in (
            {"settle_ms": 99},
            {"settle_ms": 3001},
            {"max_steps": True},
            {"stop_when": "forever"},
        ):
            response = post(client, "/api/bounds", {**data, "policy": policy})
            assert response.status_code == 400
            assert "Invalid spider or scroll settings" in response.text
            assert path.read_bytes() == before
        assert post(client, "/api/bounds", {**data, "policy": None}).status_code == 200
        assert load_job(path).scroll is None
        for comment in ("# standalone", "# header comment", "# steps"):
            assert comment in path.read_text(encoding="utf-8")


def test_spider_result_line_only_uses_report_fields() -> None:
    """N3/SEC-008: result rendering excludes arbitrary page bodies."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the standalone JS assertion")
    source = Path("src/rastro/studio/static/bounds.js").read_text(encoding="utf-8")
    function = source[source.index("export function spiderLine") :].replace("export ", "")
    fill = source[source.index("let spider") : source.index("async function save")].replace(
        "export ", ""
    )
    from rastro.domain.jobs import Job
    from rastro.studio.bounds import controls

    limits = controls(Job.model_construct(sources=(), acquisition=None))["limits"]
    function += (
        "const limits = "
        + json.dumps(limits)
        + ";\n"
        + """
const nodes = {};
const $ = id => nodes[id] ??= {};
const show = (id, text) => { $(id).textContent = text; };
"""
        + f'const {{ t }} = await import("{Path(I18N).resolve().as_uri()}");\n'
        + fill
        + """
fillBounds({limits, scroll_disabled: true});
if (!$("scroll-controls").disabled) throw Error("HTTP scroll must be disabled");
if (!$("scroll-reason").textContent.includes("HTTP")) throw Error("Explain HTTP");
fillBounds({limits, scroll_disabled: false, scroll: {max_steps: 3, settle_ms: 200}});
if ($("scroll-controls").disabled) throw Error("Browser scroll must be editable");
"""
    )
    subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            function
            + """
import assert from 'node:assert/strict';
const line = spiderLine([{spider: {pages: [
  {outcome: 'fetched', host: 'example.test', path: '/items', body: 'SECRET PAGE'}
], body: 'SECRET PAGE'}}]);
assert.equal(line, 'fetched: example.test/items');
assert.equal(spiderLine([{}]), '');
""",
        ],
        check=True,
    )
