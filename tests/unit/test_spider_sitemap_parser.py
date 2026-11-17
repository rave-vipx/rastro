"""Sitemap parser and the sitemap seed policy (N16; SEC-001, SEC-008)."""

import gzip

import pytest
from pydantic import ValidationError

from rastro.application.sitemap import MAX_SITEMAP_URLS, SitemapError, inflate, parse_sitemap
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
