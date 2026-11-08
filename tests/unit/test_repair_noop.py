"""REPAIR-001/REC-006: unchanged proposals cannot create lifecycle versions."""

import pytest
from pydantic import BaseModel

from rastro import CssRecipe, JsonRecipe, Limits, Source
from rastro.adapters.structured import JsonDiscovery
from rastro.application.repair_candidates import RepairCandidateGenerator


class Event(BaseModel):
    title: str


@pytest.mark.asyncio
async def test_json_discovery_skips_unchanged_active_recipe():
    report = await RepairCandidateGenerator(discovery=JsonDiscovery()).generate(
        '{"title": "Dance"}',
        source=Source(url="https://example.com"),
        schema=Event,
        recipe=JsonRecipe(record_path="", fields={"title": "/title"}, require_evidence=True),
        limits=Limits(),
    )
    assert report.candidates == ()
    assert report.outcome == "no_candidates"
    assert report.issues == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["fallback", "discovery", "model"])
async def test_unchanged_proposals_compare_normalized_baseline(origin):
    proposal = CssRecipe(record_selector="article", fields={"title": "h2::text"})

    class Discovery:
        def discover(self, *args, **kwargs):
            return (proposal,) if origin == "discovery" else ()

    class Model:
        async def generate(self, request, policy):
            return proposal.model_dump_json(include={"strategy", "record_selector", "fields"})

    recipe = proposal.model_copy(
        update={
            "version": 3,
            "require_evidence": False,
            "fallbacks": (proposal.model_copy(update={"version": 3}),)
            if origin == "fallback"
            else (),
        }
    )
    report = await RepairCandidateGenerator(discovery=Discovery(), model=Model()).generate(
        "<article><h2>Dance</h2></article>",
        source=Source(url="https://example.com"),
        schema=Event,
        recipe=recipe,
        limits=Limits(),
        model_enabled=origin == "model",
    )
    assert report.candidates == ()
    assert report.outcome == "no_candidates"
    assert report.issues == ()
