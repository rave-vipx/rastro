"""ADR 0037: stealth from untrusted input without a grant never builds the browser;
the operator's own jobs build it without any flag."""

import pytest

from rastro.application.jobs import run_job
from rastro.application.operator_trust import input_origin, operator_trust
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

SCROLL = {"max_steps": 2, "settle_ms": 100}


def job(*, render="stealth", scroll=None, solve=False, per_source=None):
    source = {"url": "https://example.com/"}
    if per_source:
        source["render"] = per_source
    data = {
        "format_version": 1,
        "sources": [source],
        "fields": [{"name": "title", "type": "string"}],
        "recipe": {"strategy": "css", "record_selector": "p", "fields": {"title": "::text"}},
        "acquisition": {
            "allowed_hosts": ["example.com"],
            "min_interval_seconds": 0,
            "render": render,
            "solve_challenges": solve,
        },
    }
    if scroll:
        data["scroll"] = scroll
    return Job.model_validate(data)


@pytest.fixture
def constructed(monkeypatch):
    """Record every ScraplingStealth / PlaywrightBrowser construction; neither touches a browser."""
    import rastro.adapters.playwright_browser as plain
    import rastro.adapters.scrapling_stealth as stealth

    monkeypatch.delenv("RASTRO_ALLOW_STEALTH", raising=False)
    built: list[str] = []
    monkeypatch.setattr(stealth.ScraplingStealth, "__init__", lambda self: built.append("stealth"))
    monkeypatch.setattr(plain.PlaywrightBrowser, "__init__", lambda self: built.append("browser"))
    return built


@pytest.mark.parametrize(
    "spec",
    [
        job(),
        job(per_source="stealth", render="http"),
        job(render="browser", per_source="stealth"),
        job(render="browser", solve=True),
    ],
    ids=["default", "per-source", "mixed", "solve-challenges"],
)
def test_no_grant_never_builds_the_stealth_browser(spec, constructed, tmp_path):
    from rastro.browser import job_acquisition

    with input_origin("mcp"):
        with pytest.raises(ConfigurationError, match="not granted for mcp input"):
            job_acquisition(spec, None)
        with pytest.raises(ConfigurationError, match="not granted for mcp input"):
            run_job(spec, base_dir=tmp_path)
    assert constructed == []
    assert job_acquisition(spec, None) is not None  # the operator's own job: no flag
    assert constructed


def test_load_job_file_refuses_before_building_the_stealth_browser(constructed, tmp_path):
    import rastro.application.job_dataset as module

    path = tmp_path / "job.toml"
    path.write_text(
        'format_version = 1\n[acquisition]\nallowed_hosts = ["example.com"]\n'
        'min_interval_seconds = 0\nrender = "stealth"\n'
        '[[sources]]\nurl = "https://example.com/"\n'
        '[sources.recipe]\nversion = 1\nstrategy = "css"\nrecord_selector = "p"\n'
        '[sources.recipe.fields]\ntitle = "::text"\n'
        '[[fields]]\nname = "title"\ntype = "string"\n'
        '[dataset]\nname = "t"\npath = "t.sqlite"\nidentity = ["title"]\n',
        encoding="utf-8",
    )
    with (
        input_origin("bundle"),
        pytest.raises(ConfigurationError, match="for bundle input: stealth"),
    ):
        module.load_job_file(path)
    assert constructed == []


@pytest.mark.filterwarnings("ignore:allow_stealth= is deprecated:DeprecationWarning")
def test_the_deprecated_grant_lets_untrusted_input_build(constructed):
    from rastro.browser import job_acquisition

    with operator_trust(allow_stealth=True), input_origin("mcp"):
        client = job_acquisition(job(), None)
    assert client is not None
    assert constructed == ["stealth"]


def test_plain_browser_and_scroll_never_need_or_build_the_stealth_browser(constructed):
    from rastro.browser import job_acquisition

    spec = job(render="browser", scroll=SCROLL)
    assert job_acquisition(spec, None) is not None
    assert constructed == ["browser"]


def test_direct_construction_refuses_before_touching_the_renderer():
    from rastro.application.browser_acquisition import BrowserAcquirer

    class Untouchable:
        def __getattr__(self, name):
            raise AssertionError("the renderer was touched")

    policy = AcquisitionPolicy(allowed_hosts=("example.com",), render="stealth")
    with input_origin("model"), pytest.raises(ConfigurationError, match="for model input: stealth"):
        BrowserAcquirer(
            policy,
            renderer=Untouchable(),
            transport=None,
            resolver=None,  # type: ignore[arg-type]
        )
    solving = AcquisitionPolicy(
        allowed_hosts=("example.com",), render="browser", solve_challenges=True
    )
    with (
        input_origin("model"),
        pytest.raises(ConfigurationError, match="for model input: solve_challenges"),
    ):
        BrowserAcquirer(
            solving,
            renderer=Untouchable(),
            transport=None,
            resolver=None,  # type: ignore[arg-type]
        )
