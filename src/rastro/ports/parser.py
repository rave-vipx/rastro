"""The application consumes candidates, never Scrapling implementation types."""

from dataclasses import dataclass
from typing import Protocol

from rastro.domain.models import Issue, Limits, Recipe


@dataclass(frozen=True)
class Candidate:
    values: dict[str, object]
    locators: dict[str, str]
    issues: tuple[Issue, ...] = ()


@dataclass(frozen=True)
class ParsedDocument:
    candidates: tuple[Candidate, ...]
    issues: tuple[Issue, ...] = ()


class DocumentParser(Protocol):
    def parse(self, html: str, recipe: Recipe, limits: Limits) -> ParsedDocument: ...
