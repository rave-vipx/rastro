"""The engine map: every feature of an acquisition engine and how it reaches users.

Each row names one engine feature and its surface: a job key, a grant, a recipe strategy,
Studio, an agent tool, the Python API, internal use, or not exposed (with a reason). A
release ships with no row still ``planned``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal, Self

from pydantic import Field, model_validator

from rastro.domain.sources import Contract

__all__ = ["EngineArea", "EngineFeature", "Surface", "planned_features"]

type EngineArea = Literal[
    "parser", "http", "browser", "stealth", "proxy", "crawl", "adaptive", "convert", "mcp", "cli"
]
type Surface = Literal[
    "job_key",
    "grant",
    "recipe_strategy",
    "studio",
    "mcp_tool",
    "python_api",
    "internal",
    "not_exposed",
    "planned",
]
"""Where users meet a feature; ``internal`` and ``planned`` have no user-facing location."""

_UNLOCATED: frozenset[str] = frozenset({"internal", "not_exposed", "planned"})
_NAME = r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
_DOCS = r"^(?:docs/[a-z0-9][a-z0-9_/-]{0,120}\.md(?:#[a-z0-9][a-z0-9-]{0,80})?)?$"


class EngineFeature(Contract):
    """One engine feature, such as ``Selector.find_similar``, and where Rastro exposes it."""

    engine: Literal["scrapling"]
    area: EngineArea
    name: str = Field(max_length=128, pattern=_NAME)
    surface: Surface
    where: str = Field(default="", max_length=200)
    """The user-facing location, for example ``[acquisition.browser] block_ads``."""
    docs: str = Field(default="", pattern=_DOCS)
    """The guide that documents the feature, as a repository-relative anchor."""
    reason: str = Field(default="", max_length=500)
    """Why the feature is not exposed; required for ``not_exposed``."""

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if self.surface == "not_exposed" and not self.reason.strip():
            raise ValueError("a not_exposed feature needs a reason")
        if self.surface not in _UNLOCATED and not self.where.strip():
            raise ValueError("an exposed feature needs where it is exposed")
        return self


def planned_features(features: Iterable[EngineFeature]) -> tuple[EngineFeature, ...]:
    """The rows still ``planned``: each one blocks a release."""
    return tuple(feature for feature in features if feature.surface == "planned")
