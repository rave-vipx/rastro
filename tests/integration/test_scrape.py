"""One-call scraping: targets, items, discovery, failures (INT-001, INT-002, SEC-001)."""

import asyncio
import json
import os
import sys
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, HttpUrl

import rastro
import rastro.scrape_target as scrape_target
from rastro import (
    ConfigurationError,
    JsonLdRecipe,
    Limits,
    Outcome,
    ResourceLimitError,
)
from rastro.domain.acquisition import AcquisitionResult

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

HTML = """<ul>
<li class="job"><h3>Engineer</h3><a href="/jobs/1">Apply</a><span class="salary">100</span></li>
<li class="job"><h3>Designer</h3><a href="/jobs/2">Apply</a></li>
</ul>"""


class Offer(BaseModel):
    __records__: ClassVar[str] = "li.job"
    title: Annotated[str, rastro.css("h3::text")]
    url: Annotated[HttpUrl, rastro.css("a::attr(href)")]
    salary: Annotated[int | None, rastro.css(".salary::text")] = None


class Product(BaseModel):
    name: str
    sku: str


def test_items_are_validated_model_instances_with_resolved_links():
    result = rastro.scrape(HTML, Offer, url="https://acme.example/careers")
    assert result.status == Outcome.SUCCESS
    assert all(type(item) is Offer for item in result.items)
    assert [(i.title, str(i.url), i.salary) for i in result.items] == [
        ("Engineer", "https://acme.example/jobs/1", 100),
        ("Designer", "https://acme.example/jobs/2", None),
    ]
    assert result.repair is None


def test_default_url_is_localhost_and_whole_document_is_one_record():
    class Page(BaseModel):
        heading: Annotated[str, rastro.css("h1::text")]
        link: Annotated[HttpUrl, rastro.css("a::attr(href)")]

    result = rastro.scrape("<h1>Hi</h1><a href='/x'>x</a>", Page)
    assert [(p.heading, str(p.link)) for p in result.items] == [("Hi", "https://localhost/x")]


def test_failures_keep_explicit_status_and_empty_items():
    result = rastro.scrape("<ul><li class='job'><h3>Only title</h3></li></ul>", Offer)
    assert result.status == Outcome.INVALID_DATA
    assert result.items == []
    assert {issue.field for issue in result.issues} == {"url"}


def test_items_and_absent_repair_stay_out_of_serialization():
    result = rastro.scrape(HTML, Offer, url="https://acme.example/careers")
    dumped = json.loads(result.model_dump_json())
    assert "items" not in dumped and "repair" not in dumped
    assert "repair" not in result.model_dump()


def test_records_and_fields_arguments_override_the_model():
    html = "<article><h2>Engineer</h2><a href='/j'>x</a></article>"
    result = rastro.scrape(html, Offer, records="article", fields={"title": "h2::text"})
    assert [item.title for item in result.items] == ["Engineer"]


def test_recipe_argument_passes_any_recipe_unchanged():
    html = '<script type="application/ld+json">{"name": "Lamp", "id": "L-1"}</script>'
    recipe = JsonLdRecipe(fields={"name": "/name", "sku": "/id"})
    result = rastro.scrape(html, Product, recipe=recipe)
    assert [(p.name, p.sku) for p in result.items] == [("Lamp", "L-1")]
    with pytest.raises(ConfigurationError, match="recipe= replaces"):
        rastro.scrape(html, Product, recipe=recipe, fields={"name": "b::text"})


def test_without_selectors_exact_name_discovery_is_tried():
    html = '<script type="application/ld+json">{"name": "Lamp", "sku": "L-1"}</script>'
    assert [p.sku for p in rastro.scrape(html, Product).items] == ["L-1"]


def test_without_selectors_or_structured_data_the_hint_is_explicit():
    with pytest.raises(ConfigurationError, match=r"rastro\.css.*fields=.*rastro\.suggest"):
        rastro.scrape("<p>Lamp</p>", Product)


def test_path_targets_are_bounded_utf8_regular_files(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(HTML)
    assert len(rastro.scrape(page, Offer).items) == 2
    with pytest.raises(ResourceLimitError):
        rastro.scrape(page, Offer, limits=Limits(max_html_bytes=10))
    page.write_bytes(b"\xff\xfe<h3>")
    with pytest.raises(ConfigurationError, match="UTF-8"):
        rastro.scrape(page, Offer)
    with pytest.raises(ConfigurationError, match="cannot be opened"):
        rastro.scrape(tmp_path / "missing.html", Offer)
    with pytest.raises(ConfigurationError, match="regular file"):
        rastro.scrape(tmp_path, Offer)


def test_path_targets_refuse_symlinks(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(HTML)
    link = tmp_path / "link.html"
    os.symlink(page, link)
    with pytest.raises(ConfigurationError, match="symlink"):
        rastro.scrape(link, Offer)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"repair": "always"}, "repair must be"),
        ({"url": "ftp://acme.example/"}, "absolute http"),
        ({"url": "https://user:secret@acme.example/"}, "no credentials"),
    ],
)
def test_invalid_arguments_raise_configuration_errors(kwargs, reason):
    with pytest.raises(ConfigurationError, match=reason) as error:
        rastro.scrape(HTML, Offer, **kwargs)
    assert "secret" not in str(error.value)


def test_unsupported_target_types_are_rejected():
    with pytest.raises(ConfigurationError, match="target must be"):
        rastro.scrape(b"<h3>x</h3>", Offer)  # type: ignore[arg-type]


def test_sync_scrape_inside_an_event_loop_points_to_scrape_async():
    async def inside() -> None:
        with pytest.raises(ConfigurationError, match="scrape_async"):
            rastro.scrape(HTML, Offer)
        result = await rastro.scrape_async(HTML, Offer, url="https://acme.example/")
        assert len(result.items) == 2

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(inside())


class FakeAcquirer:
    def __init__(self, result: AcquisitionResult) -> None:
        self.result = result
        self.calls = 0

    async def acquire(self, source, *, max_bytes, representation):
        self.calls += 1
        return self.result


def _fake_web(monkeypatch, result):
    seen = {}
    acquirer = FakeAcquirer(result)

    def build(policy):
        seen["policy"] = policy
        return acquirer

    monkeypatch.setattr(scrape_target, "web_acquirer", build)
    return seen, acquirer


def test_url_targets_fetch_once_under_a_single_host_policy(monkeypatch):
    fetched = AcquisitionResult(
        url="https://acme.example/careers", body=HTML, status=Outcome.SUCCESS, issue_code=None
    )
    seen, acquirer = _fake_web(monkeypatch, fetched)
    result = rastro.scrape("https://acme.example/careers", Offer)
    assert seen["policy"].allowed_hosts == ("acme.example",)
    assert seen["policy"].allow_http is False
    assert acquirer.calls == 1
    assert result.metrics.acquisition_method == "http"
    assert str(result.items[0].url) == "https://acme.example/jobs/1"


def test_url_target_failures_return_explicit_outcomes(monkeypatch):
    blocked = AcquisitionResult(
        url="http://acme.example/",
        body=None,
        status=Outcome.ACCESS_RESTRICTION,
        issue_code="robots_disallowed",
    )
    seen, _ = _fake_web(monkeypatch, blocked)
    for schema in (Offer, Product):
        result = rastro.scrape("http://acme.example/", schema)
        assert result.status == Outcome.ACCESS_RESTRICTION
        assert result.items == []
        assert [issue.code for issue in result.issues] == ["robots_disallowed"]
    assert seen["policy"].allow_http is True


def test_url_targets_reject_a_different_url_argument(monkeypatch):
    _fake_web(monkeypatch, None)
    with pytest.raises(ConfigurationError, match="URL target"):
        rastro.scrape("https://acme.example/", Offer, url="https://other.example/")


def test_url_targets_without_the_http_extra_name_the_install(monkeypatch):
    for name in [m for m in sys.modules if m.startswith(("curl_cffi", "rastro.adapters.http"))]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "curl_cffi", None)
    with pytest.raises(ImportError, match=r"pip install 'rastro-engine\[http\]'"):
        rastro.scrape("https://acme.example/", Offer)
