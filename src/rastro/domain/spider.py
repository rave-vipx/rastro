"""Spider policy and its content-free page report.

A job may follow links from the pages it names. The links are read declaratively (a CSS
expression or one of the recipe's own fields), never by a callback. A crawl is capped by
a page count, or, with ``max_pages = "unbounded"``, by ``time_budget_seconds``; an unbounded
crawl requests the ``unbounded_crawl`` capability. The table carries no grants: robots,
private networks, ports and stealth stay with the operator (SECURITY.md), and the start
URLs are the job's ``[[sources]]``.
"""

from __future__ import annotations

import difflib
import re
from typing import Annotated, Any, Literal, Self

from pydantic import (
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from rastro.domain.capability_grants import Capability
from rastro.domain.job_contract import RedactedContract
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.linear_regex import check_linear_pattern
from rastro.domain.recipes import CssRecipe
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.domain.sources import ConfigurationError

LINK_EXPRESSION = re.compile(r"^.+::attr\([A-Za-z_][A-Za-z0-9_:-]{0,63}\)$", re.DOTALL)
type LinkExpression = Annotated[StrictStr, Field(min_length=1, max_length=1024)]
type SeedOrder = Literal["document", "reverse", "lastmod"]
_SEED_ORDERS: tuple[str, ...] = ("document", "reverse", "lastmod")


class SpiderPolicy(RedactedContract):
    """The job's ``[spider]`` table. ``mode = "list"`` (the default) is today's behaviour."""

    mode: Literal["list", "spider"] = "list"
    # "sitemap": each start URL is a sitemap whose <loc>s are the depth-1 links.
    seed: Literal["page", "sitemap"] = "page"
    seed_order: SeedOrder = "document"
    max_depth: StrictInt = Field(default=2, ge=1, le=4)
    max_pages: Annotated[StrictInt, Field(ge=1, le=100)] | Literal["unbounded"] = Field(
        default=25,
        description='Most pages fetched per start URL (1-100), or "unbounded" to crawl until '
        "the time budget runs out and resume from a checkpoint on the next run.",
    )
    time_budget_seconds: StrictInt = Field(
        default=1800,
        ge=60,
        le=86400,
        description="Seconds a crawl may run per start URL; an unbounded crawl saves its "
        "checkpoint when the budget runs out.",
    )
    same_host: StrictBool = True
    allow: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    deny: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    allow_pattern: StrictStr | None = Field(default=None, min_length=1, max_length=512)
    deny_pattern: StrictStr | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="Reject matching decoded paths before the page cap (linear-time regex).",
    )
    links: (
        LinkExpression
        | Annotated[tuple[LinkExpression, ...], Field(min_length=1, max_length=8)]
        | None
    ) = Field(
        default=None,
        description="One CSS attribute expression or a list (up to eight); no comma groups.",
    )
    link_field: StrictStr | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("seed_order", mode="before")
    @classmethod
    def check_seed_order(cls, value: Any) -> Any:
        if isinstance(value, str) and value not in _SEED_ORDERS:
            match = difflib.get_close_matches(value.lower(), _SEED_ORDERS, n=1, cutoff=0.6)
            hint = f'; did you mean "{match[0]}"?' if match else ""
            raise ValueError(f"seed_order must be one of {', '.join(_SEED_ORDERS)}{hint}")
        return value

    @field_validator("allow", "deny")
    @classmethod
    def check_prefixes(cls, prefixes: tuple[str, ...]) -> tuple[str, ...]:
        for prefix in prefixes:
            if (
                not prefix.startswith("/")
                or len(prefix) > 512
                or any(ord(char) < 33 or char in "?#\\" for char in prefix)
            ):
                raise ValueError("Path prefixes start with / and carry no query or fragment")
        return tuple(dict.fromkeys(prefixes))

    @field_validator("allow_pattern", "deny_pattern")
    @classmethod
    def check_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            check_linear_pattern(value)
        return value

    @model_validator(mode="after")
    def check_link_source(self) -> Self:
        if self.mode == "list":
            return self
        if self.seed != "sitemap" and self.seed_order != "document":
            raise ValueError('seed_order applies only when seed is "sitemap"')
        if self.links is not None and self.link_field is not None:
            raise ValueError("Spider mode takes one of links or link_field, not both")
        if self.seed == "page" and self.links is None and self.link_field is None:
            raise ValueError("Spider mode needs links or link_field unless seed is sitemap")
        for expression in self.link_expressions():
            if _has_link_group(expression):
                raise ValueError("link_group_refused: use a list of CSS attribute expressions")
            if not LINK_EXPRESSION.fullmatch(expression):
                raise ValueError("links must be CSS expressions ending in ::attr(name)")
            try:  # the same cost policy as any job recipe (SECURITY.md)
                check_untrusted_recipe(self.link_probe(expression))
            except ConfigurationError as error:
                raise ValueError(str(error)) from None
        return self

    @property
    def unbounded(self) -> bool:
        """Whether this crawl is capped by its time budget instead of a page count."""
        return self.mode == "spider" and self.max_pages == "unbounded"

    def requested_capabilities(self) -> frozenset[Capability]:
        """An unbounded crawl requests ``unbounded_crawl``; any other table requests nothing."""
        return frozenset({"unbounded_crawl"}) if self.unbounded else frozenset()

    def link_expressions(self) -> tuple[str, ...]:
        return (self.links,) if isinstance(self.links, str) else self.links or ()

    def link_probe(self, expression: str) -> CssRecipe:
        """A recipe whose every match becomes one record holding one link."""
        return CssRecipe(record_selector="html", fields={"link": f"expand:{expression}"})

    def check_against(self, declared: set[str], sources: tuple[object, ...]) -> None:
        """Raise ``ValueError`` when the table cannot run against this job."""
        if self.mode == "list":
            return
        if not all(isinstance(source, UrlJobSource) for source in sources):
            raise ValueError("Spider mode follows links from URL sources only")
        if self.link_field is not None and self.link_field not in declared:
            raise ValueError("link_field must name a declared field")


def _has_link_group(expression: str) -> bool:
    """Commas inside quoted attributes or pseudo-classes are not selector groups."""
    depth, quote, escaped = 0, "", False
    for char in expression:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = ""
        elif char in "\"'":
            quote = char
        elif char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        elif char == "," and depth <= 0:
            return True
    return False
