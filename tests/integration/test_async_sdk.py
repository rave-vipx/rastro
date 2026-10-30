import asyncio
import builtins
import logging

import pytest
from pydantic import BaseModel, ValidationInfo, field_validator

from rastro import (
    AcquisitionPolicy,
    ConfigurationError,
    Limits,
    Outcome,
    Rastro,
    Recipe,
    ResourceLimitError,
    Source,
)
from rastro.domain.acquisition import AcquisitionResult

pytestmark = pytest.mark.asyncio
POLICY = AcquisitionPolicy(allowed_hosts=("example.com",), max_concurrency=2, max_sources=4)


class Acquirer:
    def __init__(self, html):
        self.html = html
        self.active = 0
        self.peak = 0
        self.completed = 0
        self.representations = []

    async def acquire(self, source, *, max_bytes, representation):
        self.representations.append(representation)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            self.completed += 1
            return AcquisitionResult(
                "https://example.com/final?q=secret",
                self.html,
                Outcome.SUCCESS,
                None,
                http_status=200,
                requests=2,
                bytes_received=100,
                network_seconds=0.1,
                acquisition_seconds=0.2,
            )
        finally:
            self.active -= 1


async def test_web_extraction_uses_the_same_validation_and_provenance(
    html, schema, source, recipe, caplog
):
    acquirer = Acquirer(html)
    engine = Rastro(acquisition=acquirer, policy=POLICY)
    with caplog.at_level(logging.INFO, logger="rastro"):
        result = await engine.extract(source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.SUCCESS
    assert acquirer.representations == ["html"]  # Every port receives the representation.
    assert result.source_url == "https://example.com/final"
    assert result.requested_url == "https://example.com/products"
    assert result.http_status == 200
    assert result.locale == "es-UY" and result.timezone == "UTC"
    assert result.metrics.acquisition_method == "http"
    assert result.metrics.request_count == 2
    assert result.metrics.network_seconds == 0.1
    assert all(
        e.source_url == result.source_url for r in result.records for e in r.evidence.values()
    )
    assert "secret" not in caplog.text and "private" not in caplog.text
    assert result.job_id in caplog.text


async def test_acquired_extraction_preserves_regional_validation_context_after_redirect():
    class Regional(BaseModel):
        name: str

        @field_validator("name")
        @classmethod
        def validate_region(cls, value: str, info: ValidationInfo) -> str:
            assert info.context == {
                "locale": "pt-BR",
                "languages": ("pt-BR", "en"),
                "timezone": "America/Sao_Paulo",
                "currency": "BRL",
                "egress": "direct",
            }
            return value

    result = await Rastro(
        acquisition=Acquirer('<script type="application/ld+json">{"name":"Livro"}</script>'),
        policy=POLICY,
    ).extract(
        source=Source(
            url="https://example.com/original",
            locale="pt-BR",
            languages=("pt-BR", "en"),
            timezone="America/Sao_Paulo",
            currency="BRL",
        ),
        schema=Regional,
        recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
    )
    assert result.status == Outcome.SUCCESS


async def test_failures_are_results_with_no_false_success(schema, source, recipe):
    class Restricted:
        async def acquire(self, source, *, max_bytes, representation):
            return AcquisitionResult(source.url, None, Outcome.ACCESS_RESTRICTION, "robots_denied")

    result = await Rastro(acquisition=Restricted(), policy=POLICY).extract(
        source=source, schema=schema, recipe=recipe
    )
    assert result.status == Outcome.ACCESS_RESTRICTION and not result.records
    assert result.issues[0].code == "robots_denied"
    result = await Rastro(
        acquisition=Acquirer("too big"), policy=POLICY, limits=Limits(max_html_bytes=1)
    ).extract(source=source, schema=schema, recipe=recipe)
    assert result.status == Outcome.PERMANENT_FAILURE
    assert result.issues[0].code == "extraction_limit"
    result = await Rastro(acquisition=Acquirer(None), policy=POLICY).extract(
        source=source, schema=schema, recipe=recipe
    )
    assert result.issues[0].code == "invalid_acquisition_result"


async def test_batch_is_bounded_ordered_and_rejects_excess(html, schema, source, recipe):
    acquisition = Acquirer(html)
    engine = Rastro(acquisition=acquisition, policy=POLICY)
    sources = [source.model_copy(update={"url": f"https://example.com/{i}"}) for i in range(4)]
    results = await engine.extract_many(sources, schema=schema, recipe=recipe)
    assert [r.requested_url for r in results] == [s.url for s in sources]
    assert acquisition.peak == 2 and acquisition.completed == 4
    with pytest.raises(ResourceLimitError):
        await engine.extract_many([source] * 5, schema=schema, recipe=recipe)
    assert await engine.extract_many([], schema=schema, recipe=recipe) == ()


async def test_batch_cancellation_cancels_every_worker(schema, source, recipe):
    entered = asyncio.Event()

    class Paused(Acquirer):
        async def acquire(self, source, *, max_bytes, representation):
            self.active += 1
            if self.active == 2:
                entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.active -= 1

    acquisition = Paused("")
    task = asyncio.create_task(
        Rastro(acquisition=acquisition, policy=POLICY).extract_many(
            [source] * 4, schema=schema, recipe=recipe
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert acquisition.active == 0


async def test_config_errors_happen_before_fetching(html, schema, source, recipe):
    with pytest.raises(ConfigurationError):
        Rastro(acquisition=Acquirer(html))
    with pytest.raises(ConfigurationError):
        await Rastro().extract(source=source, schema=schema, recipe=recipe)
    with pytest.raises(ConfigurationError):
        await Rastro().extract_many([source], schema=schema, recipe=recipe)
    recipe.fields["not_a_field"] = "/oops"
    acquirer = Acquirer(html)
    with pytest.raises(ConfigurationError):
        await Rastro(acquisition=acquirer, policy=POLICY).extract(
            source=source, schema=schema, recipe=recipe
        )
    assert acquirer.completed == 0


async def test_web_factory_and_optional_dependency_error(monkeypatch):
    assert isinstance(Rastro.web(POLICY), Rastro)
    original = builtins.__import__

    def missing(name, *args, **kwargs):
        if name == "rastro.adapters.http":
            raise ModuleNotFoundError(name="curl_cffi")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ConfigurationError, match=r"rastro-engine\[http\]"):
        Rastro.web(POLICY)

    def unrelated(name, *args, **kwargs):
        if name == "rastro.adapters.http":
            raise ModuleNotFoundError(name="unrelated")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unrelated)
    with pytest.raises(ModuleNotFoundError):
        Rastro.web(POLICY)
