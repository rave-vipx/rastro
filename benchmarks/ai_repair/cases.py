"""Named breakage per base layout, with the recipe a correct repair needs (QUAL-001).

Each case pairs a base layout (``before``) with a mutated one (``after``). ``fix`` is the
correct recipe for the mutated page, as overrides of the base recipe; the scripted
oracle answers with it and the adversarial oracle with a value-swapping variant of it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from benchmarks.ai_repair.layouts import BASE_RECIPES, LAYOUTS, Layout

Kind = Literal["structural", "trap"]


@dataclass(frozen=True)
class Case:
    layout: str
    mutation: str
    kind: Kind
    after: Layout
    fix: dict[str, Any]

    @property
    def name(self) -> str:
        return f"{self.layout}-{self.mutation}"

    @property
    def before(self) -> Layout:
        return LAYOUTS[self.layout]

    def recipe(self) -> dict[str, Any]:
        """The base (pre-breakage) recipe."""
        base = BASE_RECIPES[self.layout]
        return {"record_selector": base["record_selector"], "fields": dict(base["fields"])}

    def correct_recipe(self) -> dict[str, Any]:
        recipe = self.recipe()
        recipe["fields"].update(self.fix.get("fields", {}))
        if "record_selector" in self.fix:
            recipe["record_selector"] = self.fix["record_selector"]
        return recipe


def _rename_record(layout: Layout, cls: str) -> Layout:
    return replace(layout, record=(layout.record[0], cls))


def _selector(layout: str, old: str, new: str) -> str:
    return str(BASE_RECIPES[layout]["record_selector"]).replace(old, new)


def _swap(layout: Layout, tag: str) -> Layout:
    """Title and performer share one tag and class, and the performer comes first."""
    parts = {p.field: p for p in layout.parts}
    first = replace(parts["performer"], tag=tag, cls="meta")
    second = replace(parts["title"], tag=tag, cls="meta")
    rest = tuple(p for p in layout.parts if p.field not in {"title", "performer"})
    return replace(layout, parts=(first, second, *rest))


def _swap_fix(tag: str) -> dict[str, Any]:
    return {
        "fields": {
            "performer": f"{tag}.meta:nth-of-type(1)::text",
            "title": f"{tag}.meta:nth-of-type(2)::text",
        }
    }


def _attribute(layout: Layout) -> Layout:
    parts = tuple(p for p in layout.parts if p.field != "starts_at")
    return replace(layout, parts=parts, value_attrs=("starts_at",))


@dataclass(frozen=True)
class Spec:
    """The layout-specific names a common mutation needs."""

    record_cls: str
    new_record_cls: str
    title_cls: str
    swap_tag: str


SPECS = {
    "cards": Spec("card", "event-card", "card-title", "span"),
    "table": Spec("row", "event-row", "col-title", "td"),
    "dlist": Spec("event", "show", "name", "dd"),
    "grid": Spec("tile", "event-tile", "tile-title", "span"),
}

type Builder = Callable[[str, Layout, Spec], tuple[Kind, Layout, dict[str, Any]]]


def _common() -> dict[str, Builder]:
    def class_rename(name: str, base: Layout, s: Spec) -> tuple[Kind, Layout, dict[str, Any]]:
        sel = _selector(name, f".{s.record_cls}", f".{s.new_record_cls}")
        return "structural", _rename_record(base, s.new_record_cls), {"record_selector": sel}

    def field_rename(name: str, base: Layout, s: Spec) -> tuple[Kind, Layout, dict[str, Any]]:
        title = BASE_RECIPES[name]["fields"]["title"].replace(s.title_cls, "headline")
        return "structural", base.with_part("title", cls="headline"), {"fields": {"title": title}}

    def to_attribute(name: str, base: Layout, s: Spec) -> tuple[Kind, Layout, dict[str, Any]]:
        fix = {"fields": {"starts_at": "::attr(data-starts-at)"}}
        return "structural", _attribute(base), fix

    def decoys(name: str, base: Layout, s: Spec) -> tuple[Kind, Layout, dict[str, Any]]:
        after = replace(_rename_record(base, s.new_record_cls), decoys=True)
        sel = "main > " + _selector(name, f".{s.record_cls}", f".{s.new_record_cls}")
        return "trap", after, {"record_selector": sel}

    def semantic_swap(name: str, base: Layout, s: Spec) -> tuple[Kind, Layout, dict[str, Any]]:
        return "trap", _swap(base, s.swap_tag), _swap_fix(s.swap_tag)

    return {
        "class_rename": class_rename,
        "field_rename": field_rename,
        "field_to_attribute": to_attribute,
        "decoy_blocks": decoys,
        "semantic_swap": semantic_swap,
    }


def _restructured() -> dict[str, tuple[Layout, dict[str, Any]]]:
    cards, table, dlist, grid = (LAYOUTS[n] for n in ("cards", "table", "dlist", "grid"))
    cells = tuple(
        replace(p, tag="div", wrap=None) if p.field != "url" else replace(p, wrap=None)
        for p in table.parts
    )
    spans = tuple(
        replace(p, tag="span" if p.field != "url" else p.tag, label=None, wrap=None)
        for p in dlist.parts
    )
    return {
        "cards": (
            replace(cards, outer=(("ul", "events-list"),), record=("li", "event")),
            {"record_selector": "ul.events-list > li.event"},
        ),
        "table": (
            replace(table, outer=(("div", "listing"),), record=("div", "row"), parts=cells),
            {
                "record_selector": "div.listing > div.row",
                "fields": {
                    f: f"div.col-{c}::text"
                    for f, c in (
                        ("title", "title"),
                        ("performer", "artist"),
                        ("starts_at", "date"),
                        ("price", "price"),
                    )
                },
            },
        ),
        "dlist": (
            replace(dlist, outer=(("ul", "agenda"),), record=("li", "event"), parts=spans),
            {
                "record_selector": "ul.agenda > li.event",
                "fields": {
                    f: f"span.{c}::text"
                    for f, c in (
                        ("title", "name"),
                        ("performer", "cast"),
                        ("starts_at", "when"),
                        ("price", "cost"),
                    )
                },
            },
        ),
        "grid": (
            replace(grid, outer=(("div", "grid"),), record_wrap=()),
            {"record_selector": "div.grid > div.tile"},
        ),
    }


def _extras() -> list[Case]:
    cards, table, grid = LAYOUTS["cards"], LAYOUTS["table"], LAYOUTS["grid"]
    # Recipe CI expects an optional field in 75% of records when samples always had it:
    # the table loses it in two of five records (below), the grid in one (above).
    thin, thin_one = frozenset({"ev-2", "ev-4"}), frozenset({"ev-3"})
    return [
        Case(
            "cards",
            "tag_change",
            "structural",
            cards.with_part("title", tag="h2"),
            {"fields": {"title": "h2.card-title::text"}},
        ),
        Case(
            "cards",
            "wrapper_added",
            "structural",
            replace(
                cards,
                outer=(("div", "container"), *cards.outer),
                record_wrap=(("div", "slot"),),
            ),
            {"record_selector": "section.events article.card"},
        ),
        Case(
            "table",
            "optional_thinned",
            "structural",
            replace(_rename_record(table, "event-row"), thinned=thin),
            {"record_selector": "table.listing tr.event-row"},
        ),
        Case(
            "grid",
            "optional_thinned",
            "structural",
            replace(_rename_record(grid, "event-tile"), thinned=thin_one),
            {"record_selector": "div.grid div.col > div.event-tile"},
        ),
    ]


def build_cases() -> tuple[Case, ...]:
    cases: list[Case] = []
    restructured = _restructured()
    for name, base in LAYOUTS.items():
        for mutation, builder in _common().items():
            kind, after, fix = builder(name, base, SPECS[name])
            cases.append(Case(name, mutation, kind, after, fix))
        after, fix = restructured[name]
        cases.append(Case(name, "container_restructured", "structural", after, fix))
    cases.extend(_extras())
    return tuple(sorted(cases, key=lambda c: c.name))


CASES = build_cases()
