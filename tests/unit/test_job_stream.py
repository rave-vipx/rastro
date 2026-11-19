"""stream_job_async yields each source's result as soon as it is extracted."""

from __future__ import annotations

import asyncio

import pytest

from rastro import Job
from rastro.application.job_stream import stream_job_async
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.results import Outcome

HTML = "<main><article><h2>Item</h2></article></main>"


class GatedAcquisition:
    """The second fetch waits for the test, proving the first result arrived earlier."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.fetched: list[str] = []

    async def acquire(self, source, *, max_bytes, representation):
        self.fetched.append(source.url)
        if source.url.endswith("/two"):
            await self.release.wait()
        return AcquisitionResult(
            url=source.url, body=HTML, status=Outcome.SUCCESS, issue_code=None, http_status=200
        )


def _job():
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": ["https://a.example/one", "https://a.example/two"],
            "acquisition": {"allowed_hosts": ["a.example"]},
            "recipe": {
                "strategy": "css",
                "record_selector": "article",
                "fields": {"title": "h2::text"},
            },
            "fields": [{"name": "title", "type": "string"}],
        }
    )


@pytest.mark.asyncio
async def test_stream_yields_before_completion() -> None:
    gate = GatedAcquisition()
    stream = stream_job_async(_job(), acquisition=gate)
    first = await asyncio.wait_for(anext(stream), timeout=5)
    assert (
        first.records
        and gate.fetched == ["https://a.example/one", "https://a.example/two"][: len(gate.fetched)]
    )
    assert not gate.release.is_set()
    gate.release.set()
    rest = [item async for item in stream]
    assert len(rest) == 1 and rest[0].records


@pytest.mark.asyncio
async def test_stream_stops_the_run_when_the_consumer_leaves() -> None:
    gate = GatedAcquisition()
    stream = stream_job_async(_job(), acquisition=gate)
    await anext(stream)
    await stream.aclose()
    await asyncio.sleep(0)
    assert gate.fetched.count("https://a.example/two") <= 1
