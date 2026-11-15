"""Unit tests for observed field proposals from page structure (DISC-003, P-002)."""

from __future__ import annotations

import pytest

from rastro.application.field_proposals import (
    FieldProposal,
    _clean_ident,
    _field_name,
    propose_fields,
)
from rastro.domain.intelligence import InferencePolicy, ModelRequest
from rastro.domain.models import ConfigurationError

pytestmark = pytest.mark.usefixtures("offline_cli_loop")

CATALOGUE_HTML = """
<html>
  <body>
    <section class="events">
      <article class="card">
        <h2 class="title">Montevideo Tango</h2>
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
    """DISC-003, P-002: observed repeated structures produce fields with examples and selectors."""
    model = NeverCalledModel()
    proposals = propose_fields(CATALOGUE_HTML, url="https://agenda.example/events", model=model)
    assert model.calls == 0
    assert len(proposals) >= 3
    assert all(isinstance(p, FieldProposal) for p in proposals)
    by_name = {p.name: p for p in proposals}
    assert by_name["title"].example == "Montevideo Tango"
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
    """DISC-003: requested --fields map deterministically first, or fall back to model if needed."""
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
    assert via_model[0].example == "Montevideo Tango"


def test_propose_fields_raises_when_invalid_or_no_records_observed():
    """DISC-003: invalid field names or pages without repeating records raise ConfigurationError."""
    with pytest.raises(ConfigurationError, match="valid identifiers"):
        propose_fields(CATALOGUE_HTML, fields=("123-bad",))
    with pytest.raises(ConfigurationError):
        propose_fields("   ")
    with pytest.raises(ConfigurationError):
        propose_fields("<html><body><p>Empty</p></body></html>")
