"""SCR-003 amended: opt-in real Chromium rendering against a synthetic response."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RASTRO_LIVE_BROWSER") != "1", reason="Set RASTRO_LIVE_BROWSER=1"
)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["browser", "stealth"])
async def test_javascript_dom(mode, monkeypatch, socket_enabled):
    from rastro.adapters.playwright_browser import NetworkGuard, PlaywrightBrowser
    from rastro.adapters.scrapling_stealth import ScraplingStealth
    from rastro.domain.acquisition import AcquisitionPolicy, PinnedTarget

    original = NetworkGuard.setup

    async def synthetic(self, page):
        await original(self, page)
        await page.route(
            "https://example.com/",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body=(
                    "<html><body><p id='item'></p><script>"
                    "document.getElementById('item').textContent='Rendered item'"
                    "</script></body></html>"
                ),
            ),
        )

    monkeypatch.setattr(NetworkGuard, "setup", synthetic)
    target = PinnedTarget("https://example.com/", "example.com", 443, "93.184.216.34")
    renderer = PlaywrightBrowser() if mode == "browser" else ScraplingStealth()
    result = await renderer.render(
        target,
        policy=AcquisitionPolicy(allowed_hosts=("example.com",), render=mode),
        max_bytes=4096,
        targets=(target,),
    )
    assert '<p id="item">Rendered item</p>' in result.body
    assert result.http_status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["browser", "stealth"])
async def test_redirect_cannot_leave_allowlist(mode, monkeypatch, socket_enabled):
    from rastro.adapters.playwright_browser import NetworkGuard, PlaywrightBrowser
    from rastro.adapters.scrapling_stealth import ScraplingStealth
    from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, PinnedTarget

    original = NetworkGuard.setup

    async def redirect(self, page):
        await original(self, page)
        await page.route(
            "https://example.com/",
            lambda route: route.fulfill(
                status=302,
                headers={"location": "https://127.0.0.1/forbidden"},
                body="",
            ),
        )

    monkeypatch.setattr(NetworkGuard, "setup", redirect)
    target = PinnedTarget("https://example.com/", "example.com", 443, "93.184.216.34")
    renderer = PlaywrightBrowser() if mode == "browser" else ScraplingStealth()
    with pytest.raises(AcquisitionError, match="destination_denied"):
        await renderer.render(
            target,
            policy=AcquisitionPolicy(allowed_hosts=("example.com",), render=mode),
            max_bytes=4096,
            targets=(target,),
        )
