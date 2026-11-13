"""Synthetic listing layouts rendered from records (QUAL-001, REPAIR-001).

A layout is data: the wrappers around the listing, the record element and an ordered
list of parts. Mutations replace parts of a layout, so the expected records of a
mutated page are known without parsing it. Every value is invented; no page is captured.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from html import escape
from typing import Any

Record = dict[str, Any]

RECORDS: tuple[Record, ...] = (
    {
        "id": "ev-1",
        "title": "Noche de Tango",
        "performer": "Orquesta Sintética",
        "starts_at": "2099-04-03T21:00:00-03:00",
        "url": "https://agenda.example/ev-1",
        "price": "1200",
    },
    {
        "id": "ev-2",
        "title": "Candombe en el Parque",
        "performer": "Comparsa Prueba",
        "starts_at": "2099-04-05T19:30:00-03:00",
        "url": "https://agenda.example/ev-2",
        "price": "800",
    },
    {
        "id": "ev-3",
        "title": "Jazz de Medianoche",
        "performer": "Trío Ficticio",
        "starts_at": "2099-04-08T23:00:00-03:00",
        "url": "https://agenda.example/ev-3",
        "price": "950",
    },
    {
        "id": "ev-4",
        "title": "Cuarteto de Cuerdas",
        "performer": "Ensamble Modelo",
        "starts_at": "2099-04-12T20:00:00-03:00",
        "url": "https://agenda.example/ev-4",
        "price": "1500",
    },
    {
        "id": "ev-5",
        "title": "Murga Joven",
        "performer": "Murga Ejemplo",
        "starts_at": "2099-04-15T21:30:00-03:00",
        "url": "https://agenda.example/ev-5",
        "price": "600",
    },
)

DECOYS: tuple[Record, ...] = (
    {
        "id": "promo-1",
        "title": "Abono de temporada",
        "performer": "Varios artistas",
        "starts_at": "2099-04-01T10:00:00-03:00",
        "url": "https://agenda.example/abono",
        "price": "9000",
    },
    {
        "id": "promo-2",
        "title": "Tarjeta regalo",
        "performer": "Agenda Ejemplo",
        "starts_at": "2099-04-01T10:00:00-03:00",
        "url": "https://agenda.example/regalo",
        "price": "2000",
    },
)


@dataclass(frozen=True)
class Part:
    """One field rendered as ``<tag class=cls>``; ``attr`` holds the value instead of text."""

    field: str
    tag: str
    cls: str
    attr: str | None = None
    label: str | None = None  # a preceding <dt> label in definition lists
    wrap: str | None = None  # an unclassed parent element, such as a table cell


@dataclass(frozen=True)
class Layout:
    name: str
    outer: tuple[tuple[str, str], ...]  # (tag, class) wrappers around the listing
    record: tuple[str, str]
    parts: tuple[Part, ...]
    record_wrap: tuple[tuple[str, str], ...] = ()  # wrappers around each record
    id_attr: str = "data-id"
    value_attrs: tuple[str, ...] = ()  # fields carried as data-* on the record element
    decoys: bool = False  # add complete-looking promo records outside the listing
    thinned: frozenset[str] = field(default_factory=frozenset)  # ids without a price

    def with_part(self, name: str, **changes: Any) -> Layout:
        parts = tuple(replace(p, **changes) if p.field == name else p for p in self.parts)
        return replace(self, parts=parts)


def _open(tag: str, cls: str, extra: str = "") -> str:
    return f'<{tag} class="{cls}"{extra}>' if cls else f"<{tag}{extra}>"


def _part(part: Part, value: str) -> str:
    label = f"<dt>{escape(part.label)}</dt>" if part.label else ""
    if part.attr is not None:
        text = "Entradas" if part.attr == "href" else "Ver detalle"
        extra = f' {part.attr}="{escape(value)}"'
        html = f"{_open(part.tag, part.cls, extra)}{text}</{part.tag}>"
    else:
        html = f"{_open(part.tag, part.cls)}{escape(value)}</{part.tag}>"
    if part.wrap:
        html = f"<{part.wrap}>{html}</{part.wrap}>"
    return label + html


def _record(layout: Layout, record: Record) -> str:
    extra = f' {layout.id_attr}="{escape(record["id"])}"'
    for name in layout.value_attrs:
        extra += f' data-{name.replace("_", "-")}="{escape(record[name])}"'
    body = "".join(
        _part(p, record[p.field])
        for p in layout.parts
        if not (p.field == "price" and record["id"] in layout.thinned)
    )
    html = f"{_open(*layout.record, extra)}{body}</{layout.record[0]}>"
    for tag, cls in reversed(layout.record_wrap):
        html = f"{_open(tag, cls)}{html}</{tag}>"
    return html


def _listing(layout: Layout, records: tuple[Record, ...]) -> str:
    html = "\n".join(_record(layout, r) for r in records)
    for tag, cls in reversed(layout.outer):
        html = f"{_open(tag, cls)}\n{html}\n</{tag}>"
    return html


def render(layout: Layout) -> str:
    """A whole page: chrome, the listing and, optionally, a promo block of decoys."""
    promos = ""
    if layout.decoys:
        aside = replace(layout, outer=(("aside", "promos"), *layout.outer), thinned=frozenset())
        promos = "\n" + _listing(aside, DECOYS)
    return (
        '<!DOCTYPE html>\n<html lang="es-UY"><head><meta charset="utf-8">'
        "<title>Agenda sintética</title></head>\n<body>\n"
        '<header class="site"><nav><a href="https://agenda.example/">Inicio</a></nav></header>\n'
        f"<main>\n{_listing(layout, RECORDS)}{promos}\n</main>\n"
        '<footer class="site"><p>Agenda Ejemplo</p></footer>\n</body></html>\n'
    )


def expected(layout: Layout) -> list[Record]:
    """The records a correct extraction of ``render(layout)`` returns."""
    return [{**r, "price": None if r["id"] in layout.thinned else r["price"]} for r in RECORDS]


def _card_parts(prefix: str) -> tuple[Part, ...]:
    return (
        Part("title", "h3", f"{prefix}-title"),
        Part("performer", "p", f"{prefix}-artist"),
        Part("starts_at", "time", f"{prefix}-date", "datetime"),
        Part("price", "span", f"{prefix}-price"),
        Part("url", "a", f"{prefix}-link", "href"),
    )


CARDS = Layout("cards", (("section", "events"),), ("article", "card"), _card_parts("card"))
TABLE = Layout(
    "table",
    (("table", "listing"), ("tbody", "")),
    ("tr", "row"),
    (
        Part("title", "td", "col-title"),
        Part("performer", "td", "col-artist"),
        Part("starts_at", "td", "col-date"),
        Part("price", "td", "col-price"),
        Part("url", "a", "col-link", "href", wrap="td"),
    ),
)
DLIST = Layout(
    "dlist",
    (("div", "agenda"),),
    ("dl", "event"),
    (
        Part("title", "dd", "name", label="Obra"),
        Part("performer", "dd", "cast", label="Elenco"),
        Part("starts_at", "dd", "when", label="Fecha"),
        Part("price", "dd", "cost", label="Precio"),
        Part("url", "a", "buy", "href", wrap="dd"),
    ),
)
GRID = Layout(
    "grid",
    (("div", "grid"), ("div", "row")),
    ("div", "tile"),
    _card_parts("tile"),
    record_wrap=(("div", "col"),),
)

BASE_RECIPES: dict[str, dict[str, Any]] = {
    "cards": {
        "record_selector": "section.events > article.card",
        "fields": {
            "id": "::attr(data-id)",
            "title": "h3.card-title::text",
            "performer": "p.card-artist::text",
            "starts_at": "time.card-date::attr(datetime)",
            "price": "span.card-price::text",
            "url": "a.card-link::attr(href)",
        },
    },
    "table": {
        "record_selector": "table.listing tr.row",
        "fields": {
            "id": "::attr(data-id)",
            "title": "td.col-title::text",
            "performer": "td.col-artist::text",
            "starts_at": "td.col-date::text",
            "price": "td.col-price::text",
            "url": "a.col-link::attr(href)",
        },
    },
    "dlist": {
        "record_selector": "div.agenda > dl.event",
        "fields": {
            "id": "::attr(data-id)",
            "title": "dd.name::text",
            "performer": "dd.cast::text",
            "starts_at": "dd.when::text",
            "price": "dd.cost::text",
            "url": "a.buy::attr(href)",
        },
    },
    "grid": {
        "record_selector": "div.grid div.col > div.tile",
        "fields": {
            "id": "::attr(data-id)",
            "title": "h3.tile-title::text",
            "performer": "p.tile-artist::text",
            "starts_at": "time.tile-date::attr(datetime)",
            "price": "span.tile-price::text",
            "url": "a.tile-link::attr(href)",
        },
    },
}
LAYOUTS = {layout.name: layout for layout in (CARDS, TABLE, DLIST, GRID)}
