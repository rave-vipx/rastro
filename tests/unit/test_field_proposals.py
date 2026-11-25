"""Unit tests for observed field proposals from page structure."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest
from cssselect import SelectorError

import rastro.application.field_proposals as field_proposals
from rastro.adapters.field_discovery import ScraplingFieldDiscoverer
from rastro.application.field_proposals import (
    FieldProposal,
    _clean_ident,
    _field_name,
    propose_fields,
)
from rastro.domain.intelligence import InferencePolicy, ModelRequest
from rastro.domain.models import ConfigurationError, Limits, ResourceLimitError
from rastro.ports.field_discovery import DiscoveredField, DiscoveredRecords, FieldDiscoverer

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

CATALOGUE_HTML = """
<html>
  <body>
    <section class="events">
      <article class="card">
        <h2 class="title">Puerto Claro Tango</h2>
        <span class="price">450</span>
        <span class="price">450</span>
        <time class="starts_on" datetime="2026-11-20">20 Nov</time>
        <a class="ticket" href="/events/1">Buy</a>
      </article>
      <article class="card">
        <h2 class="title">Feria de Libros</h2>
        <span class="price">0</span>
        <span class="price">0</span>
        <time class="starts_on" datetime="2026-11-21">21 Nov</time>
        <a class="ticket" href="/events/2">Buy</a>
      </article>
      <article class="card">
        <h2 class="title">Jazz en el Parque</h2>
        <span class="price">300</span>
        <span class="price">300</span>
        <time class="starts_on" datetime="2026-11-22">22 Nov</time>
        <a class="ticket" href="/events/3">Buy</a>
      </article>
    </section>
  </body>
</html>
"""

CLASSLESS_HTML = """
<html><body><ul>
  <li><h3>Item A</h3><a href="https://a.example/1">Go</a><time datetime="2026-01-01">Jan</time></li>
  <li><h3>Item B</h3><a href="https://a.example/2">Go</a><time datetime="2026-01-02">Jan</time></li>
</ul></body></html>
"""


class NeverCalledModel:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        raise AssertionError("Model should not be called when deterministic structure succeeds")


class FakeSuggestModel:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls = 0

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        self.calls += 1
        return self.payload


def test_propose_fields_discovers_repeated_structure_without_model():
    """Observed repeated structures produce fields with examples and selectors."""
    model = NeverCalledModel()
    proposals = propose_fields(CATALOGUE_HTML, url="https://agenda.example/events", model=model)
    assert model.calls == 0
    assert len(proposals) >= 3
    assert all(isinstance(p, FieldProposal) for p in proposals)
    by_name = {p.name: p for p in proposals}
    assert by_name["title"].example == "Puerto Claro Tango"
    assert by_name["title"].selector.endswith("::text")
    assert by_name["starts_on"].type == "date"
    assert by_name["starts_on"].example == "2026-11-20"
    assert by_name["ticket"].type == "url"
    assert "::attr(href)" in by_name["ticket"].selector

    classless = propose_fields(CLASSLESS_HTML)
    assert {p.name for p in classless} >= {"title", "url", "date"}
    assert _clean_ident("123") is None
    assert _clean_ident("class") == "class_field"
    assert _field_name((), "span", None) == "span"


def test_propose_fields_with_requested_field_names_and_model_fallback():
    """Requested --fields map deterministically first, or fall back to model if needed."""
    model = NeverCalledModel()
    proposals = propose_fields(
        CATALOGUE_HTML,
        url="https://agenda.example/events",
        fields=("title", "date", "url"),
        model=model,
    )
    assert model.calls == 0
    assert [p.name for p in proposals] == ["title", "date", "url"]
    assert proposals[1].type == "date"
    assert proposals[2].type == "url"

    fallback_model = FakeSuggestModel(
        '{"strategy": "css", "record_selector": "article.card", '
        '"fields": {"custom_label": "h2.title::text"}}'
    )
    via_model = propose_fields(CATALOGUE_HTML, fields=("custom_label",), model=fallback_model)
    assert fallback_model.calls == 1
    assert via_model[0].name == "custom_label"
    assert via_model[0].example == "Puerto Claro Tango"


def test_propose_fields_raises_when_invalid_or_no_records_observed():
    """Invalid field names or pages without repeating records raise ConfigurationError."""
    with pytest.raises(ConfigurationError, match="valid identifiers"):
        propose_fields(CATALOGUE_HTML, fields=("123-bad",))
    with pytest.raises(ConfigurationError):
        propose_fields("   ")
    with pytest.raises(ConfigurationError):
        propose_fields("<html><body><p>Empty</p></body></html>")


def _letters(n: int) -> str:
    """Letter-only (stable) class names: digits would be rejected as generated-looking."""
    return "".join(chr(97 + (n // 26**i) % 26) for i in range(4))


def _hostile_page(parents: int) -> str:
    rows = (
        f'<div class="{_letters(i)}"><span class="c">a</span><span class="c">b</span></div>'
        for i in range(parents)
    )
    return f"<html><body>{''.join(rows)}</body></html>"


def test_many_distinct_candidate_selectors_fail_fast_with_a_reported_limit():
    """Candidate record selectors are capped; no quadratic scan."""

    def elapsed(parents: int) -> float:
        html = _hostile_page(parents)
        started = time.perf_counter()
        with pytest.raises(ResourceLimitError, match="candidate record selectors"):
            propose_fields(html)
        return time.perf_counter() - started

    small, large = elapsed(500), elapsed(5000)
    # 10x input: linear work grows ~10x, a quadratic scan ~100x. A ratio, not a wall-clock
    # bound, so slow CI runners do not fail it.
    assert large < max(small, 0.02) * 40


def test_candidate_cap_boundary_still_proposes_below_the_cap():
    """A page within the candidate budget is still analysed."""
    noisy = CATALOGUE_HTML.replace("<body>", "<body>" + _hostile_page(10))
    assert propose_fields(noisy).recipe.record_selector


def test_discovery_runs_behind_a_port_and_the_application_stays_adapter_free():
    """Dependency rule: propose_fields calls a FieldDiscoverer; no lxml or private parser."""
    seen: list[tuple[int, Limits]] = []

    class Fake:
        def discover(self, html: str, records_min: int, limits: Limits):
            seen.append((records_min, limits))
            field = DiscoveredField("h2", ("title",), None, "h2.title::text", ("A", "B", "C"))
            return (DiscoveredRecords("article.card", (field,)),)

    fake: FieldDiscoverer = Fake()
    found = propose_fields(CATALOGUE_HTML, discoverer=fake, records_min=3)
    assert seen and seen[0][0] == 3
    assert [(f.name, f.selector, f.example) for f in found] == [("title", "h2.title::text", "A")]
    assert found.record_selector == "article.card"

    tree = ast.parse(Path(field_proposals.__file__).read_text())
    text = Path(field_proposals.__file__).read_text()
    assert "_parse_html" not in text and "_root" not in text
    top = [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    assert not [n for n in top if "adapters" in ast.unparse(n)]


def test_discovery_skips_only_selector_errors(monkeypatch: pytest.MonkeyPatch):
    """Invalid candidate selectors are skipped; unexpected errors are not swallowed."""
    from scrapling.parser import Selector

    adapter = ScraplingFieldDiscoverer()
    assert adapter.discover(CATALOGUE_HTML, 2, Limits())

    def selector_error(self, *_a, **_k):
        raise SelectorError("bad")

    monkeypatch.setattr(Selector, "css", selector_error)
    assert adapter.discover(CATALOGUE_HTML, 2, Limits()) == ()

    def boom(self, *_a, **_k):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(Selector, "css", boom)
    with pytest.raises(RuntimeError, match="unexpected"):
        adapter.discover(CATALOGUE_HTML, 2, Limits())
