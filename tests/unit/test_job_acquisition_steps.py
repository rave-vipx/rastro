"""Page steps reach the browser client through the job acquisition router."""

import pytest

from rastro.browser import _JobAcquisition
from rastro.domain.models import ConfigurationError


class _Browser:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def runs_steps(self, url: str) -> bool:
        return True

    async def run_steps(self, source, steps, *, max_bytes):  # type: ignore[no-untyped-def]
        self.calls.append((source.url, max_bytes))
        return "result"


class _Http:
    pass


class _Source:
    url = "https://shop.example/list"


@pytest.mark.asyncio
async def test_browser_source_forwards_steps_to_its_client() -> None:
    browser = _Browser()
    router = _JobAcquisition({_Source.url: "browser"}, {"browser": browser, "http": _Http()})  # type: ignore[dict-item]
    assert router.runs_steps(_Source.url)
    assert await router.run_steps(_Source(), (), max_bytes=10) == "result"  # type: ignore[arg-type]
    assert browser.calls == [(_Source.url, 10)]


@pytest.mark.asyncio
async def test_http_source_does_not_run_steps() -> None:
    router = _JobAcquisition({_Source.url: "http"}, {"http": _Http()})  # type: ignore[dict-item]
    assert not router.runs_steps(_Source.url)
    with pytest.raises(ConfigurationError):
        await router.run_steps(_Source(), (), max_bytes=10)  # type: ignore[arg-type]
