"""Offers for "this page loads its data from an API": captured JSON a recipe can read.

An offer names a captured response by host and path (never the query), its size, where its
records seem to be, and the anchored ``capture`` pattern a recipe would use. Building the
recipe is ordinary JSON discovery over the response body, with ``capture`` set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.json import escape_token, strict_json
from rastro.domain.models import Limits
from rastro.domain.recipes import JsonRecipe
from rastro.ports.discovery import RecipeDiscovery
from rastro.ports.engine import CapturedResponse

__all__ = ["CaptureOffer", "capture_offers", "offer_policy", "recipe_from_capture"]

_SEARCH_DEPTH = 4


@dataclass(frozen=True)
class CaptureOffer:
    """One capturable JSON response: no body, no query."""

    host: str
    path: str
    status: int
    size: int
    capture: str
    record_path: str | None
    records: int


def offer_policy(policy: AcquisitionPolicy) -> AcquisitionPolicy:
    """``policy`` capturing every JSON response path, to list offers for a page."""
    browser = policy.browser.model_copy(update={"capture_network": (r"^/",)})
    return policy.model_copy(update={"browser": browser})


def capture_offers(responses: tuple[CapturedResponse, ...]) -> tuple[CaptureOffer, ...]:
    """An offer per successful response whose body is JSON."""
    offers = []
    for response in responses:
        if not 200 <= response.status < 300:
            continue
        try:
            payload = strict_json(response.body)
        except (ValueError, RecursionError):
            continue
        record_path, records = _records(payload)
        offers.append(
            CaptureOffer(
                response.host,
                response.path,
                response.status,
                len(response.body),
                _anchored(response.path),
                record_path,
                records,
            )
        )
    return tuple(offers)


def recipe_from_capture(
    response: CapturedResponse,
    fields: tuple[str, ...],
    *,
    discovery: RecipeDiscovery,
    limits: Limits,
    max_candidates: int = 3,
) -> tuple[JsonRecipe, ...]:
    """JSON recipes over ``response`` for ``fields``, each reading it through ``capture``."""
    body = response.body.decode("utf-8", errors="replace")
    found = discovery.discover(body, fields, limits, max_candidates=max_candidates)
    return tuple(
        JsonRecipe.model_validate({**item.model_dump(), "capture": _anchored(response.path)})
        for item in found
        if isinstance(item, JsonRecipe)
    )


def _anchored(path: str) -> str:
    return f"^{re.escape(path)}$"


def _records(payload: object) -> tuple[str | None, int]:
    """The pointer to the largest list of objects near the top, and its length."""
    best: tuple[str | None, int] = (None, 0)
    level: list[tuple[str, object]] = [("", payload)]
    for _ in range(_SEARCH_DEPTH):
        following: list[tuple[str, object]] = []
        for pointer, value in level:
            if isinstance(value, list):
                count = sum(isinstance(item, dict) for item in value)
                if count > best[1]:
                    best = (pointer, count)
            elif isinstance(value, dict):
                following.extend((f"{pointer}/{escape_token(k)}", v) for k, v in value.items())
        level = following
    return best
