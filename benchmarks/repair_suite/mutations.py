"""Named breakage transforms per page (REPAIR-001-005). Each maps page HTML to new HTML."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

from rastro.mocklab.mutations.html_content import HtmlChangeClasses
from rastro.mocklab.mutations.html_structure import HtmlWrapElement
from rastro.mocklab.mutations.plan import MutationPlan

SALE = "2026-11-01T10:00:00-03:00"
VENUE = "Auditorio Nelly Goitiño"
Transform = Callable[[str], str]


@dataclass(frozen=True)
class Case:
    name: str
    kind: str  # "structural" or "trap"
    transform: Transform


def _plan(*operators: HtmlChangeClasses | HtmlWrapElement) -> Transform:
    return lambda html: MutationPlan(seed=42, operators=operators).apply(html).document


def _classes(selector: str, **kwargs: object) -> HtmlChangeClasses:
    return HtmlChangeClasses(selector=selector, **kwargs)  # type: ignore[arg-type]


def _wrap(selector: str) -> HtmlWrapElement:
    return HtmlWrapElement(selector=selector, wrapper_tag="div")


def _chain(*steps: Transform) -> Transform:
    def run(html: str) -> str:
        for step in steps:
            html = step(html)
        return html

    return run


def _sub(pattern: str, repl: str) -> Transform:
    return lambda html: re.sub(pattern, repl, html)


def _jsonld(edit: Callable[[list[dict]], list[dict]]) -> Transform:
    def run(html: str) -> str:
        match = re.search(r'(<script type="application/ld\+json">)(.*?)(</script>)', html, re.S)
        assert match
        body = json.dumps(edit(json.loads(match.group(2))), ensure_ascii=False, indent=2)
        return html[: match.start(2)] + "\n" + body + "\n" + html[match.end(2) :]

    return run


def _each(change: Callable[[dict], None]) -> Callable[[list[dict]], list[dict]]:
    def run(events: list[dict]) -> list[dict]:
        for event in events:
            change(event)
        return events

    return run


def _nest_place(event: dict) -> None:
    event["location"] = {"@type": "Place", "place": {"name": event["location"]["name"]}}


def _sale_date(event: dict) -> None:
    event["startDate"] = event["offers"]["validFrom"]


def _venue_as_name(event: dict) -> None:
    event["headline"], event["name"] = event["name"], event["location"]["name"]


def _promo(events: list[dict]) -> list[dict]:
    return [*events, {"@type": "Event", "name": "Abonos 2099", "identifier": "promo"}]


_EMPTY_MAIN: Transform = lambda html: re.sub(  # noqa: E731
    r"(<main[^>]*>).*?(</main>)", r"\1\2", html, flags=re.S
)
_EMPTY_LD = _jsonld(lambda _events: [])

SODRE_CARD = ".cyg--info.espectaculo"
SODRE = (
    Case(
        "container_class_rename",
        "structural",
        _plan(_classes(SODRE_CARD, rename={"espectaculo": "show-card"})),
    ),
    Case("wrapper_around_list", "structural", _plan(_wrap("main.cartelera"))),
    Case(
        "field_class_rename",
        "structural",
        _plan(_classes("h2.cyg--display-3", rename={"cyg--display-3": "cyg--titulo"})),
    ),
    Case(
        "field_moved_in_card",
        "structural",
        _chain(
            _plan(_wrap("time.cyg--dia")),
            _plan(_classes("time.cyg--dia", rename={"cyg--dia": "cyg--fecha"})),
        ),
    ),
    Case(
        "container_and_field_rename",
        "structural",
        _plan(
            _classes(SODRE_CARD, rename={"espectaculo": "show-card"}),
            _classes("time.cyg--dia", rename={"cyg--dia": "cyg--fecha"}),
        ),
    ),
    Case(
        "link_class_rename",
        "structural",
        _plan(_classes("a.cyg--btn", rename={"cyg--btn": "cyg--comprar"})),
    ),
    Case(
        "sale_date_in_event_path",
        "trap",
        _sub(r'(<time class="cyg--dia" datetime=")[^"]+', rf"\g<1>{SALE}"),
    ),
    Case(
        "title_selector_hits_venue",
        "trap",
        _chain(
            _plan(_classes("h2.cyg--display-3", rename={"cyg--display-3": "cyg--titulo"})),
            _sub(r"<address>", f'<address><span class="cyg--display-3">{VENUE}</span>'),
        ),
    ),
    Case(
        "container_matches_promos",
        "trap",
        _plan(
            _classes(".promo-wrap", add=("item",)),
            _classes(".cyg--promo", add=("cyg--info", "espectaculo")),
        ),
    ),
    Case("page_emptied", "trap", _EMPTY_MAIN),
)

ZITA = (
    Case(
        "container_class_rename",
        "structural",
        _plan(_classes("li.evento", rename={"evento": "show"})),
    ),
    Case("wrapper_around_list", "structural", _plan(_wrap("ul.agenda"))),
    Case(
        "field_class_rename",
        "structural",
        _plan(_classes("span.fecha", rename={"fecha": "fecha-evento"})),
    ),
    Case(
        "field_moved_in_card",
        "structural",
        _chain(
            _plan(_wrap("span.fecha")), _plan(_classes("span.fecha", rename={"fecha": "cuando"}))
        ),
    ),
    Case(
        "container_and_field_rename",
        "structural",
        _plan(
            _classes("li.evento", rename={"evento": "show"}),
            _classes("span.fecha", rename={"fecha": "cuando"}),
        ),
    ),
    Case(
        "link_class_rename",
        "structural",
        _chain(
            _sub(r"<a href=", '<a class="ver" href='),
            _plan(_classes("li.evento a", rename={"ver": "mas"})),
        ),
    ),
    Case("sale_date_in_event_path", "trap", _sub(r'(<span class="fecha">)[^<]+', rf"\g<1>{SALE}")),
    Case(
        "title_selector_hits_venue",
        "trap",
        _sub(r"<h3>([^<]+)</h3>", r"<h4>\1</h4><h3>Sala Zitarrosa</h3>"),
    ),
    Case("container_matches_promos", "trap", _plan(_classes(".evento-promo", add=("evento",)))),
    Case("page_emptied", "trap", _EMPTY_MAIN),
)

TICK = (
    Case("jsonld_key_moved", "structural", _jsonld(_each(_nest_place))),
    Case("sale_date_in_event_path", "trap", _jsonld(_each(_sale_date))),
    Case("title_selector_hits_venue", "trap", _jsonld(_each(_venue_as_name))),
    Case("container_matches_promos", "trap", _jsonld(_promo)),
    Case("page_emptied", "trap", _EMPTY_LD),
)

CASES = {"sodre": SODRE, "tickantel": TICK, "zitarrosa": ZITA}
