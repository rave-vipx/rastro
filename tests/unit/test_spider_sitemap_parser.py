"""Sitemap parser and the sitemap seed policy."""

import gzip

import pytest
from pydantic import ValidationError

from rastro.application.sitemap import (
    MAX_SITEMAP_URLS,
    SitemapEntry,
    SitemapError,
    inflate,
    order_entries,
    parse_sitemap,
)
from rastro.domain.spider import SpiderPolicy

CAP = 1_000_000


@pytest.mark.parametrize(
    "xml",
    [
        b"<urlset><url><loc> /a </loc></url><url><loc>/b</loc></url></urlset>",
        b'<s:urlset xmlns:s="urn:x"><s:url><s:loc>/a</s:loc></s:url><s:url><s:loc>/b</s:loc>'
        b"<s:lastmod>2026</s:lastmod></s:url></s:urlset>",
    ],
    ids=["plain", "prefixed"],
)
def test_locs_are_namespace_agnostic_trimmed_and_in_document_order(xml):
    assert parse_sitemap(xml, CAP).locs == ("/a", "/b")


def test_only_url_loc_counts_and_blank_locs_are_dropped():
    xml = (
        b"<urlset><loc>/stray</loc><url><image><loc>/img</loc></image><loc>  </loc></url></urlset>"
    )
    assert parse_sitemap(xml, CAP).locs == ()


def test_index_is_flagged():
    sitemap = parse_sitemap(
        b"<sitemapindex><sitemap><loc>/s.xml</loc></sitemap></sitemapindex>", CAP
    )
    assert sitemap.index and sitemap.locs == ("/s.xml",)


@pytest.mark.parametrize(
    ("xml", "code"),
    [
        (b"", "sitemap_invalid"),
        (b"<feed/>", "sitemap_invalid"),
        (b"<urlset><url><loc>" + b"a" * 9000 + b"</loc></url></urlset>", "sitemap_invalid"),
        (b'<!DOCTYPE urlset SYSTEM "http://evil.test/x.dtd"><urlset/>', "sitemap_invalid"),
        (gzip.compress(b"<urlset/>", mtime=0)[:-6], "sitemap_invalid"),
        (b"\x1f\x8bnot gzip", "sitemap_invalid"),
        (
            b"<urlset>" + b"<url><loc>/a</loc></url>" * (MAX_SITEMAP_URLS + 1) + b"</urlset>",
            "sitemap_too_large",
        ),
    ],
    ids=["empty", "root", "long_loc", "external_dtd", "truncated_gz", "bad_gz", "too_many"],
)
def test_bad_input_fails_with_a_safe_code(xml, code):
    with pytest.raises(SitemapError) as caught:
        parse_sitemap(xml, 10 * CAP)
    assert caught.value.code == code


def test_inflate_never_exceeds_the_cap():
    with pytest.raises(SitemapError, match="sitemap_too_large"):
        inflate(gzip.compress(b"x" * 101), 100)
    assert inflate(gzip.compress(b"x" * 100), 100) == b"x" * 100
    assert inflate(b"<urlset/>", 1) == b"<urlset/>"  # plain XML: the fetch already capped it


def test_sitemap_seed_needs_no_link_source_but_a_page_seed_does():
    assert SpiderPolicy(mode="spider", seed="sitemap").links is None
    with pytest.raises(ValidationError, match="unless seed is sitemap"):
        SpiderPolicy(mode="spider")
    with pytest.raises(ValidationError, match="not both"):
        SpiderPolicy(mode="spider", seed="sitemap", links="a::attr(href)", link_field="url")
    with pytest.raises(ValidationError):
        SpiderPolicy(mode="spider", seed="robots")


def test_parser_captures_lastmod_in_any_child_order_and_ignores_overlong_lastmod():
    """<lastmod> is paired with <loc> for both urlset and sitemapindex."""
    xml = (
        b"<urlset>"
        b"<url><lastmod>2026-05-01</lastmod><loc>/a</loc></url>"
        b"<url><loc>/b</loc><lastmod>2026-06-01T12:00:00+02:00</lastmod></url>"
        b"<url><loc>/c</loc><lastmod>" + b"9" * 200 + b"</lastmod></url>"
        b"<url><loc>/d</loc></url>"
        b"</urlset>"
    )
    sitemap = parse_sitemap(xml, CAP)
    assert sitemap.entries == (
        SitemapEntry("/a", "2026-05-01"),
        SitemapEntry("/b", "2026-06-01T12:00:00+02:00"),
        SitemapEntry("/c", None),
        SitemapEntry("/d", None),
    )
    idx = parse_sitemap(
        b"<sitemapindex><sitemap><loc>/s1.xml</loc><lastmod>2026-01</lastmod></sitemap>"
        b"</sitemapindex>",
        CAP,
    )
    assert idx.index and idx.entries == (SitemapEntry("/s1.xml", "2026-01"),)


def test_order_entries_supports_document_reverse_and_lastmod():
    """Lastmod orders newest UTC first; ties and undated keep document order."""
    entries = (
        SitemapEntry("/undated-1", None),
        SitemapEntry("/older", "2026-05-01"),
        SitemapEntry("/tz-earlier", "2026-05-10T11:00:00+02:00"),  # 09:00Z
        SitemapEntry("/tz-later", "2026-05-10T10:00:00Z"),  # 10:00Z
        SitemapEntry("/bad-date", "not-a-date"),
        SitemapEntry("/tie-a", "2026-05-01T00:00:00Z"),
        SitemapEntry("/year-only", "2025"),
        SitemapEntry("/year-month", "2026-02"),
        SitemapEntry("/undated-2", ""),
    )
    assert [e.loc for e in order_entries(entries, "document")] == [e.loc for e in entries]
    assert [e.loc for e in order_entries(entries, "reverse")] == [e.loc for e in reversed(entries)]
    assert [e.loc for e in order_entries(entries, "lastmod")] == [
        "/tz-later",
        "/tz-earlier",
        "/older",
        "/tie-a",
        "/year-month",
        "/year-only",
        "/undated-1",
        "/bad-date",
        "/undated-2",
    ]


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        ("revers", "reverse"),
        ("reversed", "reverse"),
        ("last_mod", "lastmod"),
        ("LASTMOD", "lastmod"),
        ("documnt", "document"),
    ],
)
def test_unknown_seed_order_suggests_the_closest_valid_value(bad, expected):
    """Typos in seed_order are refused with the closest valid option."""
    with pytest.raises(ValidationError) as caught:
        SpiderPolicy(mode="spider", seed="sitemap", seed_order=bad)
    message = caught.value.errors(include_url=False, include_input=False)[0]["msg"]
    assert message == (
        f"Value error, seed_order must be one of document, reverse, lastmod; "
        f'did you mean "{expected}"?'
    )


def test_seed_order_and_allow_pattern_validation():
    """Seed_order needs seed='sitemap' and allow_pattern rejects ReDoS features."""
    assert SpiderPolicy(mode="spider", seed="sitemap").seed_order == "document"
    with pytest.raises(ValidationError, match="document, reverse, lastmod") as caught:
        SpiderPolicy(mode="spider", seed="sitemap", seed_order="SECRET_TOKEN_XYZ")
    assert "SECRET_TOKEN_XYZ" not in str(caught.value.errors(include_input=False))
    with pytest.raises(ValidationError, match='seed is "sitemap"'):
        SpiderPolicy(mode="spider", links="a::attr(href)", seed_order="reverse")
    assert SpiderPolicy(mode="spider", seed="sitemap", allow_pattern=r"/e-1\d\d$").allow_pattern
    for bad_pattern in (r"^(?=a)a", r"(a)\1", "[unclosed"):
        with pytest.raises(ValidationError, match="regular expression"):
            SpiderPolicy(mode="spider", seed="sitemap", allow_pattern=bad_pattern)
