"""Page steps in a rendered page: a closed vocabulary, guarded navigation, bounded time.

A fake page stands in for the browser: it records each call and answers selectors from a
small synthetic document model. No browser and no network.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urljoin

import pytest
from pydantic import TypeAdapter

from rastro.adapters.browser_steps import STEP_TIMEOUT_MS, perform_steps
from rastro.application.page_steps import PageStepError
from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.models import Outcome
from rastro.domain.page_steps import PageSteps

STEPS = TypeAdapter(PageSteps)


class PlaywrightTimeout(Exception):
    """Named like Playwright's own timeout error, which the adapter recognises by name."""


PlaywrightTimeout.__name__ = "TimeoutError"


class Guard:
    def __init__(self, hosts=("shop.example",)):
        self.policy = AcquisitionPolicy(allowed_hosts=hosts)
        self.requests, self.issue = 0, None


class Page:
    def __init__(self, present=(), attrs=None, url="https://shop.example/list"):
        self.present, self.attrs, self.url = set(present), attrs or {}, url
        self.calls: list[tuple] = []

    def _find(self, selector, timeout):
        assert selector.startswith("css="), selector  # always the CSS engine, never another
        assert 0 < timeout <= STEP_TIMEOUT_MS
        if selector.removeprefix("css=") not in self.present:
            raise PlaywrightTimeout("waiting for selector")

    async def wait_for_selector(self, selector, *, state, timeout):
        self.calls.append(("wait", selector, state))
        if state in ("attached", "visible"):
            self._find(selector, timeout)

    async def click(self, selector, *, timeout):
        self._find(selector, timeout)
        self.calls.append(("click", selector))
        if selector == "css=button.more":
            self.present.add("article.late")

    async def fill(self, selector, text, *, timeout):
        self.calls.append(("fill", selector, text))

    async def select_option(self, selector, *, value, timeout):
        self.calls.append(("select", selector, value))

    async def get_attribute(self, selector, name, *, timeout):
        self.calls.append(("attr", selector, name))
        return self.attrs.get(selector.removeprefix("css="))

    async def goto(self, url, *, timeout, wait_until):
        self.calls.append(("goto", url))
        self.url = urljoin(self.url, url)

    async def evaluate(self, expression):
        self.calls.append(("evaluate", expression))

    async def wait_for_timeout(self, ms):
        self.calls.append(("sleep", ms))


async def run(page, steps, guard=None, timeout_ms=30_000):
    await perform_steps(
        page, STEPS.validate_python(steps), guard=guard or Guard(), timeout_ms=timeout_ms
    )


@pytest.mark.asyncio
async def test_click_load_more_waits_for_the_new_records():
    page = Page(present={"button.more"})
    await run(page, [{"action": "click", "selector": "button.more", "wait_for": "article.late"}])
    assert ("click", "css=button.more") in page.calls
    assert page.calls[-1] == ("wait", "css=article.late", "visible")


@pytest.mark.asyncio
async def test_type_select_and_scroll_use_literal_values_only():
    page = Page(present={"input#q", "select#size"})
    await run(
        page,
        [
            {"action": "type", "selector": "input#q", "text": "${HOME}"},
            {"action": "select", "selector": "select#size", "value": "m"},
            {"action": "scroll", "times": 2, "settle_ms": 10},
        ],
    )
    assert ("fill", "css=input#q", "${HOME}") in page.calls  # typed as written, never expanded
    assert ("select", "css=select#size", "m") in page.calls
    scripts = {call[1] for call in page.calls if call[0] == "evaluate"}
    assert len(scripts) == 1  # one fixed scroll expression; nothing from the recipe
    assert [c for c in page.calls if c[0] == "sleep"] == [("sleep", 10), ("sleep", 10)]


@pytest.mark.asyncio
async def test_step_selector_missing_names_the_step():
    page = Page(present={"button.more"})
    with pytest.raises(PageStepError) as caught:
        await run(
            page,
            [
                {"action": "click", "selector": "button.more"},
                {"action": "click", "selector": "button.gone"},
            ],
        )
    error = caught.value
    assert (error.code, error.outcome, error.step) == (
        "step_selector_missing",
        Outcome.INVALID_DATA,
        2,
    )


@pytest.mark.asyncio
async def test_follow_step_target_checked_by_allowlist():
    page = Page(present={"a.next"}, attrs={"a.next": "https://elsewhere.example/page"})
    with pytest.raises(PageStepError) as caught:
        await run(page, [{"action": "follow", "selector": "a.next::attr(href)"}])
    assert caught.value.code == "host_not_allowed"
    assert not [c for c in page.calls if c[0] == "goto"]  # refused before the browser moves


@pytest.mark.asyncio
@pytest.mark.parametrize("href", ["javascript:alert(1)", "data:text/html,x", "file:///etc/x"])
async def test_follow_step_refuses_non_web_links(href):
    page = Page(present={"a.next"}, attrs={"a.next": href})
    with pytest.raises(PageStepError):
        await run(page, [{"action": "follow", "selector": "a.next::attr(href)"}])
    assert not [c for c in page.calls if c[0] == "goto"]


@pytest.mark.asyncio
async def test_follow_step_opens_a_relative_link_on_an_allowed_host():
    page = Page(present={"a.next"}, attrs={"a.next": "/list?page=2"})
    await run(page, [{"action": "follow", "selector": "a.next::attr(href)"}])
    assert ("goto", "https://shop.example/list?page=2") in page.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", ["css=a >> xpath=//b", "xpath=//button", "text=More"])
async def test_selectors_never_switch_the_selector_engine(selector):
    page = Page(present={selector})
    with pytest.raises(PageStepError) as caught:
        await run(page, [{"action": "click", "selector": selector}])
    assert caught.value.code == "step_failed"
    assert page.calls == []


@pytest.mark.asyncio
async def test_the_total_time_of_all_steps_is_bounded():
    class Slow(Page):
        async def wait_for_timeout(self, ms):
            await asyncio.sleep(ms / 1000)

    page = Slow()
    started = asyncio.get_running_loop().time()
    with pytest.raises(PageStepError) as caught:
        await run(page, [{"action": "wait", "timeout_ms": 15_000}] * 3, timeout_ms=50)
    assert asyncio.get_running_loop().time() - started < 1
    assert (caught.value.code, caught.value.outcome) == ("step_failed", Outcome.TRANSIENT_FAILURE)


@pytest.mark.asyncio
async def test_a_wait_for_a_hidden_element_that_never_hides_is_a_step_failure():
    class Stubborn(Page):
        async def wait_for_selector(self, selector, *, state, timeout):
            raise PlaywrightTimeout("still visible")

    with pytest.raises(PageStepError) as caught:
        await run(Stubborn(), [{"action": "wait", "selector": "div.spinner", "state": "hidden"}])
    assert (caught.value.code, caught.value.step) == ("step_failed", 1)


@pytest.mark.asyncio
async def test_a_request_the_guard_refused_names_its_reason():
    class Refused(Page):
        async def goto(self, url, *, timeout, wait_until):
            guard.issue = "destination_denied"
            raise RuntimeError("net::ERR_BLOCKED_BY_CLIENT")

    guard = Guard()
    page = Refused(present={"a.next"}, attrs={"a.next": "/two"})
    with pytest.raises(PageStepError) as caught:
        await run(page, [{"action": "follow", "selector": "a.next::attr(href)"}], guard=guard)
    assert caught.value.code == "destination_denied"
