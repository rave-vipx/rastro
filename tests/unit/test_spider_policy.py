"""The `[spider]` job table and the pure link rules (N3; SEC-001, SEC-003)."""

import pytest
from pydantic import ValidationError

from rastro.application.spider_links import classify_link, path_matches
from rastro.domain.jobs import parse_job
from rastro.domain.models import Recipe
from rastro.domain.spider import SpiderPolicy

BASE = """
format_version = 1

[acquisition]
allowed_hosts = ["shop.test"]

[recipe]
strategy = "css"
record_selector = "article"
fields = { name = "h2::text" }

[[sources]]
url = "https://shop.test/list"

[[fields]]
name = "name"
type = "string"
"""


def policy(**values):
    return SpiderPolicy.model_validate({"mode": "spider", "links": "a.item::attr(href)", **values})


@pytest.mark.parametrize(
    "values",
    [
        {"max_depth": 0},
        {"max_depth": 5},
        {"max_pages": 0},
        {"max_pages": 101},
        {"max_pages": "5"},
        {"same_host": "yes"},
        {"allow": ["item"]},
        {"deny": ["/a?x=1"]},
        {"allow": ["/a#b"]},
        {"mode": "crawl"},
        {"links": "a.item"},
        {"links": "a::text"},
        {"links": "a:has(b)::attr(href)"},
        {"links": "p ~ p ~ b::attr(href)"},
        {"link_field": "url"},  # exactly one of links / link_field
        {"ignore_robots": ["shop.test"]},
        {"start_urls": ["https://evil.test/"]},
        {"domain": "evil.test"},
        {"url": "https://evil.test/"},
        {"allow_private_network": True},
    ],
)
def test_policy_rejects_invalid_or_privileged_input(values):
    with pytest.raises(ValidationError):
        policy(**values)


def test_spider_mode_needs_a_declarative_link_source():
    with pytest.raises(ValidationError):
        SpiderPolicy.model_validate({"mode": "spider"})


def test_defaults_and_list_mode():
    assert SpiderPolicy().mode == "list"
    spider = policy()
    assert (spider.max_depth, spider.max_pages, spider.same_host) == (2, 25, True)


def test_a_recipe_or_bundle_cannot_carry_a_spider_or_start_url():
    for extra in ({"spider": {"mode": "spider"}}, {"start_url": "https://evil.test/"}):
        with pytest.raises(ValidationError):
            Recipe.model_validate(
                {"strategy": "css", "record_selector": "a", "fields": {"n": "b::text"}, **extra}
            )


def test_job_file_cannot_grant_ignore_robots_through_spider():
    text = BASE + '\n[spider]\nmode = "spider"\nlinks = "a::attr(href)"\nignore_robots = ["x"]\n'
    with pytest.raises(ValidationError):
        parse_job(text, format="toml")


def test_job_parses_spider_and_checks_link_field_and_sources():
    ok = BASE + '\n[spider]\nmode = "spider"\nlinks = "a.item::attr(href)"\nmax_depth = 3\n'
    assert parse_job(ok, format="toml").spider.max_depth == 3
    with pytest.raises(ValidationError, match="link_field"):
        parse_job(BASE + '\n[spider]\nmode = "spider"\nlink_field = "missing"\n', format="toml")
    with pytest.raises(ValidationError, match="URL"):
        parse_job(
            BASE.replace('url = "https://shop.test/list"', 'path = "page.html"')
            + '\n[spider]\nmode = "spider"\nlinks = "a::attr(href)"\n',
            format="toml",
        )


def test_a_job_without_spider_serialises_as_before():
    job = parse_job(BASE, format="toml")
    assert job.spider is None
    assert "spider" not in job.model_dump(mode="json", exclude_none=True)


@pytest.mark.parametrize(
    ("path", "prefix", "expected"),
    [
        ("/item", "/item", True),
        ("/item/1", "/item", True),
        ("/items/2", "/item", False),
        ("/item-x", "/item", False),
        ("/anything", "/", True),
        ("/item/1", "/item/", True),
    ],
)
def test_prefixes_match_whole_segments(path, prefix, expected):
    assert path_matches(path, (prefix,)) is expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/item/2#frag", "https://shop.test/item/2"),
        ("../item/2", "https://shop.test/item/2"),
        ("HTTPS://SHOP.test./item/2?b=1&a=2", "https://shop.test/item/2?b=1&a=2"),
        ("//shop.test/x", "https://shop.test/x"),
    ],
)
def test_links_are_normalised_without_dropping_the_query(href, expected):
    assert classify_link("https://shop.test/list/", href).url == expected


@pytest.mark.parametrize(
    "href",
    [
        "javascript:alert(1)",
        "mailto:a@b.test",
        "https://u:p@shop.test/x",
        "/a%2fb",
        "/a/%2e%2e/b",
        "",
    ],
)
def test_unusable_links_are_invalid(href):
    link = classify_link("https://shop.test/list", href)
    assert link.refusal == "invalid_url"
    assert link.host == "" and link.path == ""
