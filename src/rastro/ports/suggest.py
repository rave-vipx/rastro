"""Bounded example-driven CSS recipe proposals, verified later by the application."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from rastro.domain.models import CssRecipe, Limits
from rastro.domain.suggest import Example, FieldConfidence


@dataclass(frozen=True)
class RecipeCandidate:
    recipe: CssRecipe
    confidence: dict[str, FieldConfidence]


class RecipeSuggester(Protocol):
    def suggest(
        self,
        html: str,
        fields: tuple[str, ...],
        examples: Sequence[Example],
        limits: Limits,
        *,
        records_min: int,
        max_candidates: int,
        base_url: str | None = None,
    ) -> tuple[RecipeCandidate, ...]:
        """Propose CSS recipes, most specific first; never fetch or execute anything.

        ``base_url`` lets a relative ``href``/``src`` match an absolute example URL.
        Raises ``SuggestionError`` naming the examples that could not be located.
        Fields without examples are mapped only from exact microdata names, else unmapped.
        """
        ...
