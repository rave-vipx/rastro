"""Bounded spider policy and its content-free page report (N3; SEC-001, SEC-003, SEC-008).

A job may follow links from the pages it names. The links are read declaratively (a CSS
expression or one of the recipe's own fields), never by a callback, and every cap is
a hard number. The table carries no grants: robots, private networks, ports and stealth
stay with the operator (SECURITY.md), and the start URLs are the job's ``[[sources]]``.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from rastro.domain.job_contract import RedactedContract
from rastro.domain.job_sources import UrlJobSource
from rastro.domain.recipes import CssRecipe
from rastro.domain.selector_policy import check_untrusted_recipe
from rastro.domain.sources import ConfigurationError

LINK_EXPRESSION = re.compile(r"^.+::attr\([A-Za-z_][A-Za-z0-9_:-]{0,63}\)$", re.DOTALL)


class SpiderPolicy(RedactedContract):
    """The job's ``[spider]`` table. ``mode = "list"`` (the default) is today's behaviour."""

    mode: Literal["list", "spider"] = "list"
    # "sitemap": each start URL is a sitemap whose <loc>s are the depth-1 links (N16).
    seed: Literal["page", "sitemap"] = "page"
    max_depth: StrictInt = Field(default=2, ge=1, le=4)
    max_pages: StrictInt = Field(default=25, ge=1, le=100)
    same_host: StrictBool = True
    allow: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    deny: tuple[StrictStr, ...] = Field(default=(), max_length=32)
    links: StrictStr | None = Field(default=None, min_length=1, max_length=1024)
    link_field: StrictStr | None = Field(default=None, min_length=1, max_length=128)

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

    @model_validator(mode="after")
    def check_link_source(self) -> Self:
        if self.mode == "list":
            return self
        if self.links is not None and self.link_field is not None:
            raise ValueError("Spider mode takes one of links or link_field, not both")
        if self.seed == "page" and self.links is None and self.link_field is None:
            raise ValueError("Spider mode needs links or link_field unless seed is sitemap")
        if self.links is not None:
            if not LINK_EXPRESSION.fullmatch(self.links):
                raise ValueError("links must be a CSS expression ending in ::attr(name)")
            try:  # the same cost policy as any job recipe (SECURITY.md)
                check_untrusted_recipe(self.link_probe())
            except ConfigurationError as error:
                raise ValueError(str(error)) from None
        return self

    def link_probe(self) -> CssRecipe:
        """A recipe whose every match becomes one record holding one link."""
        assert self.links is not None
        return CssRecipe(record_selector="html", fields={"link": f"expand:{self.links}"})

    def check_against(self, declared: set[str], sources: tuple[object, ...]) -> None:
        """Raise ``ValueError`` when the table cannot run against this job."""
        if self.mode == "list":
            return
        if not all(isinstance(source, UrlJobSource) for source in sources):
            raise ValueError("Spider mode follows links from URL sources only")
        if self.link_field is not None and self.link_field not in declared:
            raise ValueError("link_field must name a declared field")
