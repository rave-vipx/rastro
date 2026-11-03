"""MOCK-003: synthetic failures remain explicit through the public API."""

import pytest
from pydantic import BaseModel

from rastro import AcquisitionPolicy, Outcome, Rastro, Recipe, Source
from rastro.application.acquisition import WebAcquirer
from rastro.mocklab.failures import SCENARIOS


class Product(BaseModel):
    name: str
    price: str


class Resolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        return ("8.8.8.8",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("http_403", Outcome.ACCESS_RESTRICTION),
        ("http_429", Outcome.ACCESS_RESTRICTION),
        ("timeout", Outcome.TRANSIENT_FAILURE),
        ("empty_page", Outcome.MISSING_DATA),
        ("invalid_structured_response", Outcome.INVALID_DATA),
        ("missing_required_fields", Outcome.INVALID_DATA),
    ],
)
async def test_synthetic_failures_have_explicit_outcomes(name: str, expected: Outcome) -> None:
    scenario = next(item for item in SCENARIOS if item.name == name)
    assert scenario.synthetic is True
    policy = AcquisitionPolicy(
        allowed_hosts=("example.com",), min_interval_seconds=0, max_retries=0
    )
    engine = Rastro(
        acquisition=WebAcquirer(policy, transport=scenario.build_transport(), resolver=Resolver()),
        policy=policy,
    )
    recipe = Recipe(strategy="jsonld", fields={"name": "/name", "price": "/price"})
    result = await engine.extract(
        source=Source(url="https://example.com/product"), schema=Product, recipe=recipe
    )
    assert result.status == expected
    assert not result.records
    if name == "http_429":
        assert result.retry_after_seconds == 60
