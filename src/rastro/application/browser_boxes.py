"""Highlight boxes for a rendered page: one more render under the acquirer's own policy.

The page is fetched exactly like any other render (destination checks, robots, budget and
pacing), in a source session of its own, and measured before that session closes. A failed
render, a redirect to another host or a renderer without box support measures nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from rastro.application.browser_sessions import source_session
from rastro.domain.models import Limits, Outcome, Source
from rastro.ports.acquisition import AcquisitionPort
from rastro.ports.browser import BoxMeasuringSession

type Boxes = Mapping[str, Sequence[Mapping[str, float]]]


async def measure_rendered(
    acquirer: AcquisitionPort, renderer: object, url: str, selectors: Sequence[str]
) -> Boxes:
    """Boxes per selector on ``url`` as ``acquirer`` renders it; empty when unmeasurable."""
    async with source_session() as scope:
        result = await acquirer.acquire(
            Source(url=url), max_bytes=Limits().max_html_bytes, representation="html"
        )
        session = scope.last_rendered(renderer)
        if result.status != Outcome.SUCCESS or not isinstance(session, BoxMeasuringSession):
            return {}
        return await session.element_boxes(selectors)
