"""Candidate generation never promotes or weakens validation (REPAIR-001, REC-006)."""

import pytest
from pydantic import BaseModel

from rastro import CssRecipe, Limits, Source
from rastro.application.repair_candidates import RepairCandidateGenerator
from rastro.domain.intelligence import InferencePolicy


class Event(BaseModel):
    title: str


class Discovery:
    def discover(self, html, fields, limits, *, max_candidates, aliases=None):
        return (CssRecipe(record_selector="article", fields={"title": "h3::text"}),)


class Model:
    calls = 0

    async def generate(self, request, policy):
        self.calls += 1
        return '{"strategy":"css","record_selector":"article","fields":{"title":"h4::text"}}'


@pytest.mark.asyncio
async def test_fallback_discovery_and_model_are_ordered_deduplicated_and_explicitly_gated():
    fallback = CssRecipe(record_selector="article", fields={"title": "h3::text"})
    recipe = CssRecipe(
        record_selector="article", fields={"title": "h2::text"}, fallbacks=(fallback,)
    )
    model = Model()
    generator = RepairCandidateGenerator(discovery=Discovery(), model=model)
    arguments = dict(
        source=Source(url="https://example.test"), schema=Event, recipe=recipe, limits=Limits()
    )
    report = await generator.generate("<article/>", **arguments)
    assert [c.origin for c in report.candidates] == ["fallback"]
    assert model.calls == report.inference_calls == 0
    assert report.candidates[0].recipe.require_evidence
    assert not report.candidates[0].recipe.fallbacks
    assert recipe.fallbacks == (fallback,)
    report = await generator.generate("<article/>", model_enabled=True, **arguments)
    assert [c.origin for c in report.candidates] == ["fallback", "model"]
    assert report.inference_calls == model.calls == 1


@pytest.mark.asyncio
async def test_invalid_model_output_is_explicit_and_bounded():
    class EmptyDiscovery(Discovery):
        def discover(self, *args, **kwargs):
            return ()

    class InvalidModel(Model):
        async def generate(self, request, policy):
            self.calls += 1
            return "private invalid response"

    model = InvalidModel()
    generator = RepairCandidateGenerator(
        discovery=EmptyDiscovery(), model=model, policy=InferencePolicy(max_calls=1)
    )
    report = await generator.generate(
        "<article/>",
        source=Source(url="https://example.test"),
        schema=Event,
        recipe=CssRecipe(record_selector="article", fields={"title": "h2::text"}),
        limits=Limits(),
        model_enabled=True,
    )
    assert report.outcome == "no_candidates"
    assert report.inference_calls == model.calls == 1
    assert report.issues[0].code == "invalid_model_candidate"
    assert "private" not in report.model_dump_json()


class EmptyDiscovery:
    def discover(self, *args, **kwargs):
        return ()


@pytest.mark.asyncio
async def test_model_configuration_and_budget_failures_remain_explicit():
    from rastro import ConfigurationError

    arguments = dict(
        source=Source(url="https://example.test"),
        schema=Event,
        recipe=CssRecipe(record_selector="article", fields={"title": "h2::text"}),
        limits=Limits(),
    )
    with pytest.raises(ConfigurationError, match="configured job model"):
        await RepairCandidateGenerator(discovery=EmptyDiscovery()).generate(
            "<article/>", model_enabled=True, **arguments
        )
    model = Model()
    report = await RepairCandidateGenerator(
        discovery=EmptyDiscovery(), model=model, policy=InferencePolicy(max_context_bytes=1)
    ).generate("<article/>", model_enabled=True, **arguments)
    assert report.issues[0].code == "model_context_limit"
    assert report.inference_calls == model.calls == 0
    report = await RepairCandidateGenerator(
        discovery=Discovery(), model=model, policy=InferencePolicy(max_discovery_candidates=1)
    ).generate("<article/>", model_enabled=True, **arguments)
    assert len(report.candidates) == 1
    assert report.inference_calls == model.calls == 0


@pytest.mark.asyncio
async def test_json_repairs_use_structured_discovery_without_model():
    from rastro import JsonRecipe
    from rastro.adapters.structured import JsonDiscovery

    model = Model()
    report = await RepairCandidateGenerator(discovery=JsonDiscovery(), model=model).generate(
        '[{"title":"Synthetic dance"}]',
        source=Source(url="https://example.test"),
        schema=Event,
        recipe=JsonRecipe(record_path="/outdated", fields={"title": "/title"}),
        limits=Limits(),
        model_enabled=True,
    )
    assert report.candidates[0].origin == "discovery"
    assert model.calls == 0


@pytest.mark.asyncio
async def test_provider_overflow_unknown_fields_and_mutated_recipes_fail_closed():
    from rastro import ConfigurationError, ResourceLimitError

    class Overflow(Discovery):
        def discover(self, *args, **kwargs):
            return (CssRecipe(record_selector="article", fields={"title": "h3::text"}),) * 33

    class Undeclared(Discovery):
        def discover(self, *args, **kwargs):
            return (CssRecipe(record_selector="article", fields={"unknown": "h3::text"}),)

    recipe = CssRecipe(record_selector="article", fields={"title": "h2::text"})
    arguments = dict(
        source=Source(url="https://example.test"), schema=Event, recipe=recipe, limits=Limits()
    )
    for discovery, error in ((Overflow(), ResourceLimitError), (Undeclared(), ConfigurationError)):
        with pytest.raises(error):
            await RepairCandidateGenerator(discovery=discovery).generate("<article/>", **arguments)
    recipe.fields["title"] = "invalid"
    with pytest.raises(ConfigurationError, match="Invalid repair input"):
        await RepairCandidateGenerator(discovery=EmptyDiscovery()).generate(
            "<article/>", **arguments
        )


@pytest.mark.asyncio
async def test_snapshot_binding_checks_source_query_locale_schema_and_adapter():
    from datetime import UTC, datetime

    from rastro import ConfigurationError
    from rastro.adapters.scrapling_relocation import ScraplingRelocation
    from rastro.domain.models import schema_fingerprint
    from rastro.domain.repair import RelocationSnapshot, digest, recipe_digest

    recipe = CssRecipe(record_selector="article", fields={"title": "h2::text"})
    source = Source(url="https://example.test?variant=1")
    token = "h" + "a" * 64
    snapshot = RelocationSnapshot(
        recipe_digest=recipe_digest(recipe),
        source_digest=digest(source.model_dump_json()),
        schema_fingerprint=schema_fingerprint(Event),
        observed_at=datetime.now(UTC),
        fields={"title": {"tag": token, "text": token, "path": (token,), "attributes": {}}},
    )
    arguments = dict(schema=Event, recipe=recipe, limits=Limits(), snapshot=snapshot)
    with pytest.raises(ConfigurationError, match="adapter"):
        await RepairCandidateGenerator(discovery=EmptyDiscovery()).generate(
            "<article/>", source=source, **arguments
        )
    generator = RepairCandidateGenerator(
        discovery=EmptyDiscovery(), relocation=ScraplingRelocation()
    )
    for changed_source in (
        Source(url="https://other.test"),
        Source(url="https://example.test?variant=2"),
        source.model_copy(update={"locale": "es-UY"}),
    ):
        with pytest.raises(ConfigurationError, match="another source or schema"):
            await generator.generate("<article/>", source=changed_source, **arguments)
    arguments["snapshot"] = snapshot.model_copy(update={"schema_fingerprint": "b" * 64})
    with pytest.raises(ConfigurationError, match="another source or schema"):
        await generator.generate("<article/>", source=source, **arguments)
