"""A new job offers navigation exploration when its first page looks like it hides records."""

from __future__ import annotations

from pathlib import Path

import pytest

from rastro.application.job_new_sites import new_job_from_sites
from rastro.application.navigation_offer import exploration_offer
from rastro.domain.acquisition import AcquisitionResult
from rastro.domain.models import Outcome, Representation, Source

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


def page(control: str = "", count: int = 3) -> str:
    items = "".join(
        f'<article class="event"><h2 class="title">Event {i}</h2>'
        f'<span class="venue">Hall {i}</span></article>'
        for i in range(count)
    )
    return f"<html><body><div class='list'>{items}</div>{control}</body></html>"


@pytest.mark.parametrize(
    ("control", "reason"),
    [
        ('<button class="x">Load more</button>', "load_more_control"),
        ('<a class="btn">Ver más</a>', "load_more_control"),
        ("<button>Cargar más eventos</button>", "load_more_control"),
        ('<a rel="next" href="/p/2">2</a>', "pagination_control"),
        ('<nav class="pagination"><a href="?page=2">2</a></nav>', "pagination_control"),
    ],
)
def test_controls_that_hide_records_are_detected(control, reason):
    offer = exploration_offer(page(control), records=3, expected=3)
    assert offer is not None and offer.reason == reason


def test_few_records_offer_and_quiet_page():
    assert exploration_offer(page(), records=1, expected=5).reason == "few_records"
    assert exploration_offer(page(), records=3, expected=3) is None
    hidden = "<!-- Load more --><script>var a = 'load more'</script>"
    assert exploration_offer(page(hidden), records=3, expected=3) is None  # not visible


class Acquirer:
    def __init__(self, body: str) -> None:
        self.body = body

    async def acquire(
        self, source: Source, *, max_bytes: int, representation: Representation = "html"
    ) -> AcquisitionResult:
        return AcquisitionResult(source.url, self.body, Outcome.SUCCESS, None, requests=1)


def test_new_job_reports_the_offer(tmp_path: Path):
    result = new_job_from_sites(
        tmp_path / "events.toml",
        sites=["https://events.example/list"],
        acquisition=Acquirer(page('<button class="more">Load more</button>')),
        fields=["title", "venue"],
    )
    assert result.created is not None
    assert result.exploration is not None
    assert result.exploration.reason == "load_more_control"
    assert result.exploration.url == "https://events.example/list"
    quiet = new_job_from_sites(
        tmp_path / "quiet.toml",
        sites=["https://events.example/list"],
        acquisition=Acquirer(page()),
        fields=["title", "venue"],
    )
    assert quiet.exploration is None
