"""A careful hand-written Scrapling + Pydantic scraper with generic fallbacks (DIFF-002).

It knows the base selectors and, when they find nothing, tries the fallbacks a
defensive scraper author typically adds. It refuses when a record fails validation or
no record is found, and serves whatever validates otherwise. No Rastro code runs here.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AwareDatetime, BaseModel, Field, HttpUrl, ValidationError
from scrapling.parser import Selector

from benchmarks.ai_repair.cases import Case
from benchmarks.ai_repair.executors import Verdict, judge
from benchmarks.ai_repair.layouts import render

Text = Annotated[str, Field(min_length=1)]

FALLBACKS: dict[str, tuple[str, ...]] = {
    "id": ("::attr(data-id)",),
    "title": ("h2::text", "h3::text", "h4::text", "[class*=title]::text", "[class*=name]::text"),
    "performer": ("[class*=artist]::text", "[class*=performer]::text", "[class*=cast]::text"),
    "starts_at": ("time::attr(datetime)", "[class*=date]::text", "[class*=when]::text"),
    "price": ("[class*=price]::text", "[class*=cost]::text"),
    "url": ("a::attr(href)",),
}
RECORD_FALLBACK = "[data-id]"


class Event(BaseModel):
    id: Text
    title: Text
    performer: Text
    starts_at: AwareDatetime
    url: HttpUrl
    price: Decimal | None = Field(default=None, ge=0)


def _first(node: Selector, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        value = node.css(selector).get()
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def scrape(html: str, recipe: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Records as JSON values, or ``None`` when the scraper refuses."""
    page = Selector(html, adaptive=False, huge_tree=False)
    nodes = page.css(recipe["record_selector"]) or page.css(RECORD_FALLBACK)
    records: list[dict[str, Any]] = []
    for node in nodes:
        row = {
            name: _first(node, (selector, *FALLBACKS[name]))
            for name, selector in recipe["fields"].items()
        }
        try:
            records.append(Event.model_validate(row).model_dump(mode="json"))
        except ValidationError:
            return None
    return records or None


def run_baseline(case: Case) -> Verdict:
    started = time.perf_counter()
    served = scrape(render(case.after), case.recipe())
    if served is None:
        verdict, detail = "refused", "validation_failed"
    else:
        verdict, detail = judge(served, case), "served"
    return Verdict(case.name, case.kind, verdict, detail, 0, time.perf_counter() - started)
