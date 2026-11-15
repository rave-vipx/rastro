"""Offline paginated agenda loading, retention and egress (SYNC-002, SEC-008)."""

from pathlib import Path
from urllib.parse import urlsplit

import pytest

from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.application.acquisition import WebAcquirer
from rastro.application.dataset_load import load_results
from rastro.application.jobs import run_job
from rastro.domain.acquisition import HttpResponse
from rastro.domain.jobs import Job
from rastro.domain.models import Outcome

pytestmark = pytest.mark.usefixtures("offline_cli_loop")


class Resolver:
    async def resolve(self, host, port):
        return ("93.184.216.34",)


class Transport:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    async def get(self, target, **kwargs):
        self.urls.append(target.url)
        path = urlsplit(target.url).path
        if path == "/robots.txt":
            return HttpResponse(404, {}, b"", 0)
        status, body = self.pages[path]
        if status == 302:
            return HttpResponse(302, {"location": body}, b"", 0)
        raw = body.encode()
        return HttpResponse(status, {"content-type": "text/html"}, raw, len(raw))


def page(*names, next=None):
    html = "<html><body>" + "".join(f"<article><h2>{name}</h2></article>" for name in names)
    return html + (f'<a rel="next" href="{next}">Next</a>' if next else "") + "</body></html>"


def job(*, pagination=True, max_pages=5, budget=30):
    recipe = {"strategy": "css", "record_selector": "article", "fields": {"name": "h2::text"}}
    if pagination:
        recipe["pagination"] = {"next": "a[rel=next]::attr(href)", "max_pages": max_pages}
    return Job.model_validate(
        {
            "format_version": 1,
            "sources": [{"url": "https://agenda.test/one"}],
            "fields": [{"name": "name", "type": "string"}],
            "recipe": recipe,
            "acquisition": {
                "allowed_hosts": ["agenda.test", "other.test"],
                "max_requests": budget,
                "min_interval_seconds": 0,
                "max_retries": 0,
            },
            "dataset": {"name": "agenda", "path": "agenda.sqlite", "identity": ["name"]},
        }
    )


def run(spec, pages):
    transport = Transport(pages)
    acquirer = WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver())
    return run_job(spec, acquisition=acquirer, base_dir=Path.cwd()), transport


def test_two_pages_deduplicate_and_update_table(tmp_path):
    spec = job()
    pages = {"/one": (200, page("A", "B", next="/two")), "/two": (200, page("B", "C"))}
    report, transport = run(spec, pages)
    result = report.results[0]
    assert [r.name for r in result.items] == ["A", "B", "C"]
    assert result.status == Outcome.SUCCESS
    assert result.pagination_stop == "no_next"
    assert result.records[-1].evidence["name"].page_url == "https://agenda.test/two"
    store = SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    for _ in range(2):
        load_results(spec.dataset, ["name"], report, store)
    assert len(store.records("agenda")) == 3
    assert transport.urls.count("https://agenda.test/two") == 1


@pytest.mark.parametrize(
    ("next", "max_pages", "stop", "count"),
    [
        ("/two", 1, "max_pages", 1),
        ("https://other.test/two", 5, "off_host", 1),
        ("http://agenda.test/two", 5, "https_downgrade", 1),
        ("javascript:secret()", 5, "invalid_next", 1),
    ],
)
def test_stops_without_acquiring_next(next, max_pages, stop, count):
    report, transport = run(job(max_pages=max_pages), {"/one": (200, page("A", next=next))})
    result = report.results[0]
    assert result.pagination_stop == stop
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert len(result.records) == count
    assert int(report.exit_code) == 3
    assert len(transport.urls) == 2  # robots + page one


def test_repeat_after_second_page():
    report, _ = run(
        job(), {"/one": (200, page("A", next="/two")), "/two": (200, page("B", next="/one"))}
    )
    assert report.results[0].pagination_stop == "repeat"
    assert report.results[0].status == Outcome.SUCCESS
    assert len(report.results[0].records) == 2


def test_page_two_failure_retains_previous_snapshot(tmp_path):
    spec = job()
    pages = {"/one": (200, page("A", next="/two")), "/two": (200, page("B"))}
    complete, _ = run(spec, pages)
    store = SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    load_results(spec.dataset, ["name"], complete, store)
    pages["/two"] = (500, "private server content")
    partial, _ = run(spec, pages)
    result = partial.results[0]
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert result.pagination_stop == "page_failure"
    assert [r.name for r in result.items] == ["A"]
    loaded = load_results(spec.dataset, ["name"], partial, store)
    assert loaded.sources[0].counts["missing"] == 0
    assert len(store.records("agenda")) == 2
    assert store.source_status("agenda")[0].reason == "partial_snapshot"


@pytest.mark.parametrize("budget", [2, 3])
def test_shared_budget_includes_robots_and_never_overshoots(budget):
    report, transport = run(
        job(budget=budget), {"/one": (200, page("A", next="/two")), "/two": (200, page("B"))}
    )
    result = report.results[0]
    assert result.pagination_stop == "budget"
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert [r.name for r in result.items] == ["A"]
    assert len(transport.urls) == budget
    assert result.metrics.request_count == budget


def test_page_two_redirect_cannot_escape_source_host():
    report, transport = run(
        job(),
        {
            "/one": (200, page("A", next="/two")),
            "/two": (302, "https://other.test/private?secret=value"),
        },
    )
    result = report.results[0]
    assert result.pagination_stop == "off_host"
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert all(urlsplit(url).hostname == "agenda.test" for url in transport.urls)
    assert "secret" not in result.model_dump_json()


def test_recipe_without_pagination_is_unchanged():
    report, transport = run(job(pagination=False), {"/one": (200, page("A", next="/two"))})
    assert report.results[0].status == Outcome.SUCCESS
    assert "pagination_stop" not in report.results[0].model_dump()
    assert len(transport.urls) == 2


@pytest.mark.parametrize("pagination", [False, True])
def test_same_page_duplicates_still_fail_storage(tmp_path, pagination):
    spec = job(pagination=pagination)
    report, _ = run(spec, {"/one": (200, page("A", "A"))})
    store = SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    loaded = load_results(spec.dataset, ["name"], report, store)
    assert loaded.status == "storage_error"
    assert len(report.results[0].records) == 2


def test_duplicate_in_later_page_still_fails_storage(tmp_path):
    spec = job()
    report, _ = run(spec, {"/one": (200, page("A", next="/two")), "/two": (200, page("A", "A"))})
    loaded = load_results(
        spec.dataset, ["name"], report, SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    )
    assert loaded.status == "storage_error"


def test_cross_page_conflicts_keep_first_and_report_count(tmp_path):
    spec = Job.model_validate(
        {
            **job().model_dump(),
            "fields": [{"name": "name", "type": "string"}, {"name": "title", "type": "string"}],
            "recipe": {
                **job().recipe.model_dump(),
                "fields": {"name": "h2::text", "title": "p::text"},
            },
        }
    )
    report, _ = run(
        spec,
        {
            "/one": (200, page("A", next="/two").replace("</article>", "<p>first</p></article>")),
            "/two": (200, page("A").replace("</article>", "<p>changed</p></article>")),
        },
    )
    assert report.results[0].pagination_duplicates_conflicting == 1
    assert [r.title for r in report.results[0].items] == ["first"]
    store = SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    load_results(spec.dataset, ["name", "title"], report, store)
    assert store.records("agenda")[0].fields["title"] == "first"


def test_query_cursors_are_distinct_but_provenance_is_redacted():
    transport = Transport({})

    async def get(target, **kwargs):
        transport.urls.append(target.url)
        if target.url.endswith("robots.txt"):
            return HttpResponse(404, {}, b"", 0)
        body = page("A", next="?cursor=secret#private") if "?" not in target.url else page("B")
        raw = body.encode()
        return HttpResponse(200, {"content-type": "text/html"}, raw, len(raw))

    transport.get = get
    spec = job()
    report = run_job(
        spec,
        base_dir=Path.cwd(),
        acquisition=WebAcquirer(spec.acquisition, transport=transport, resolver=Resolver()),
    )
    result = report.results[0]
    assert [r.name for r in result.items] == ["A", "B"]
    assert "secret" not in result.model_dump_json()
    assert "private" not in result.model_dump_json()
    assert [r.page_index for r in result.records] == [1, 2]


def test_first_page_budget_failure_stays_failure():
    report, transport = run(job(budget=1), {"/one": (200, page("A"))})
    assert report.results[0].status == Outcome.PERMANENT_FAILURE
    assert report.results[0].pagination_stop == "budget"
    assert report.results[0].records == ()
    assert len(transport.urls) == 1


def test_combined_record_limit_is_partial():
    spec = Job.model_validate({**job().model_dump(), "limits": {"max_records": 1}})
    report, _ = run(
        spec, {"/one": (200, page("A", next="/two")), "/two": (200, page("B", next="/three"))}
    )
    assert report.results[0].status == Outcome.PARTIAL_SUCCESS
    assert report.results[0].pagination_stop == "record_limit"
    assert [r.name for r in report.results[0].items] == ["A"]


def test_ambiguous_next_is_partial():
    body = page("A", next="/two").replace("</body>", '<a rel="next" href="/three">Next</a></body>')
    report, transport = run(job(), {"/one": (200, body)})
    assert report.results[0].pagination_stop == "invalid_next"
    assert report.results[0].status == Outcome.PARTIAL_SUCCESS
    assert len(transport.urls) == 2


def test_without_dataset_all_page_records_are_retained():
    spec = Job.model_validate({**job().model_dump(), "dataset": None})
    report, _ = run(spec, {"/one": (200, page("A", next="/two")), "/two": (200, page("A"))})
    assert [r.name for r in report.results[0].items] == ["A", "A"]


def test_next_cursor_is_normalized_before_repeat_check():
    report, transport = run(job(), {"/one": (200, page("A", next="https://AGENDA.TEST:443/one"))})
    assert report.results[0].pagination_stop == "repeat"
    assert len(transport.urls) == 2


@pytest.mark.parametrize(
    ("pages", "stop"),
    [
        ({"/one": (200, page("A", next="/two")), "/two": (200, page("B"))}, "no_next"),
        ({"/one": (200, page("A", next="/one#again"))}, "repeat"),
        ({"/one": (200, page("A", next="/two")), "/two": (200, page())}, "empty_page"),
    ],
)
def test_normal_end_of_list_is_complete_snapshot(pages, stop):
    report, _ = run(job(), pages)
    result = report.results[0]
    assert result.pagination_stop == stop
    assert result.status == Outcome.SUCCESS
    assert [r.name for r in result.items][:1] == ["A"]
    assert int(report.exit_code) == 0
    assert not any(issue.code.startswith("pagination_") for issue in result.issues)
    assert all(issue.code != "no_records" for issue in result.issues)


def test_end_of_list_loads_as_complete_and_marks_missing(tmp_path):
    spec = job()
    store = SQLiteDatasetStore(tmp_path / "agenda.sqlite")
    pages = {"/one": (200, page("A", next="/two")), "/two": (200, page("B"))}
    load_results(spec.dataset, ["name"], run(spec, pages)[0], store)
    pages["/two"] = (200, page())  # B vanished from the list: an honest complete snapshot
    loaded = load_results(spec.dataset, ["name"], run(spec, pages)[0], store)
    assert loaded.sources[0].counts["missing"] == 1


def test_empty_first_page_is_not_end_of_list():
    report, _ = run(job(), {"/one": (200, page())})
    assert report.results[0].status != Outcome.SUCCESS


def test_failed_page_two_is_not_end_of_list():
    report, _ = run(job(), {"/one": (200, page("A", next="/two")), "/two": (500, "")})
    assert report.results[0].status == Outcome.PARTIAL_SUCCESS
    assert report.results[0].pagination_stop == "page_failure"


@pytest.mark.parametrize("next", ["https://AGENDA.test./two", "https://agenda.test./two"])
def test_trailing_dot_next_host_is_the_same_host(next):
    report, _ = run(job(), {"/one": (200, page("A", next=next)), "/two": (200, page("B"))})
    assert report.results[0].pagination_stop == "no_next"
    assert [r.name for r in report.results[0].items] == ["A", "B"]


def test_other_host_with_trailing_dot_is_still_refused():
    report, _ = run(job(), {"/one": (200, page("A", next="https://other.test./two"))})
    assert report.results[0].pagination_stop == "off_host"
