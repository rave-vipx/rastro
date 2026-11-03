import json

import pytest

from rastro.mocklab import SanitizationError
from rastro.mocklab.html_sanitizer import sanitize_html
from rastro.mocklab.json_scrubber import scrub_json
from rastro.mocklab.retention import RetentionPlan
from rastro.mocklab.sanitize import allowed_headers, assert_clean, sanitize_body
from rastro.mocklab.seeds import DeterministicStream


def stream():
    return DeterministicStream(0, "test")


def test_html_keeps_structure_retained_text_and_selector_values():
    plan = RetentionPlan(texts=frozenset({"Keep me"}), selector_text='[data-testid="card"]')
    html = (
        '<div data-testid="card" class="c" hidden><b>Keep me</b><i>drop me</i>'
        '<br/><img src="https://cdn.example.com/x.png?sig=1" alt="secret alt"></div>'
    )
    out = sanitize_html(html, plan, stream())
    assert '<div data-testid="card" class="' in out and 'class="c"' not in out
    assert " hidden>" in out
    assert "<b>Keep me</b>" in out and "drop me" not in out
    assert "<br />" in out
    assert 'src="https://cdn.example.com/' in out and "x.png" not in out and "sig" not in out
    assert "secret alt" not in out


def test_html_escapes_retained_text_and_drops_processing_and_unknown_markup():
    plan = RetentionPlan(texts=frozenset({"A & B"}))
    out = sanitize_html("<?xml x?><!DOCTYPE html><p>A &amp; B</p>", plan, stream())
    assert out == "<!doctype html><p>A &amp; B</p>"


def test_structural_attribute_with_secret_is_replaced():
    out = sanitize_html('<p class="someone@example.org">x</p>', RetentionPlan(), stream())
    assert "someone" not in out and "example.org" not in out


def test_secret_url_attribute_is_scrubbed():
    html = '<a href="https://example.com/p?api_key=abcd1234">x</a>'
    out = sanitize_html(html, RetentionPlan(), stream())
    assert 'href="https://example.com/' in out and "api_key" not in out


def test_jsonld_blocks_are_scrubbed_kept_in_place_or_dropped():
    html = (
        '<script type="application/ld+json">{bad</script>'
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "<\\/script>", "sku": "private", "n": 4, "ok": true}'
        "</script>"
    )
    plan = RetentionPlan(jsonld_pointers={1: frozenset({"/name"})}, selector_text="Product")
    out = sanitize_html(html, plan, stream())
    assert '<script type="application/ld+json">{</script>' in out
    assert '"name": "<\\/script>"' in out and "private" not in out and ": true" in out
    assert '"@type": "Product"' in out and '"sku"' not in out
    dropped = sanitize_html(html, RetentionPlan(), stream())
    assert dropped.count('<script type="application/ld+json"></script>') == 2


def test_scrub_json_keeps_only_keys_and_values_on_retained_paths():
    payload = {
        "data": [{"name": "Keep", "price": 3.5, "flag": None}],
        "api_key=abcd1234": "x",
        "users": {"Jane Doe (DOB 1980-02-03)": 1},
    }
    out = scrub_json(payload, frozenset({"/data/0/name"}), stream())
    assert isinstance(out, dict)
    assert out["data"][0]["name"] == "Keep"
    record = out["data"][0]
    assert set(record) - {"name"} and "price" not in record and None in record.values()
    text = json.dumps(out)
    assert "api_key" not in text and "Jane" not in text and "users" not in text


def test_json_body_sanitization_is_canonical_and_validates_input():
    plan = RetentionPlan(json_pointers=frozenset({"/a"}))
    out = sanitize_body('{"a": "keep", "b": "drop"}', "json", plan, seed=1)
    assert json.loads(out)["a"] == "keep" and out.endswith("\n")
    assert out == sanitize_body('{"a": "keep", "b": "drop"}', "json", plan, seed=1)
    with pytest.raises(SanitizationError):
        sanitize_body("{not json", "json", plan, seed=1)


def test_headers_use_an_allowlist_and_refuse_secret_values():
    assert allowed_headers(
        {"Content-Type": "application/json", "Set-Cookie": "sid=1", "Authorization": "x"}
    ) == {"content-type": "application/json"}
    assert allowed_headers(None) == {}
    with pytest.raises(SanitizationError):
        allowed_headers({"Content-Language": "token=abcdef123"})


def test_assert_clean_names_file_and_category_but_not_value():
    with pytest.raises(SanitizationError) as caught:
        assert_clean({"a.json": b"ok", "b.html": b"mail someone@example.org"})
    assert "b.html" in str(caught.value) and "email" in str(caught.value)
    assert "someone" not in str(caught.value)
    assert_clean({"a.json": b"clean"})


def test_only_exact_selector_comparisons_keep_attribute_values():
    plan = RetentionPlan(selector_text="article.product[data-kind=card] >> h2::text")
    out = sanitize_html('<p data-kind="card" data-x="p" data-y="">x</p>', plan, stream())
    assert 'data-kind="card"' in out and 'data-x="p"' not in out and 'data-y=""' in out


def test_short_or_empty_keys_get_unique_placeholders():
    payload = {chr(code): code for code in range(ord("a"), ord("z") + 1)} | {"": 0, " ": 1}
    out = scrub_json(payload, frozenset(), stream())
    assert isinstance(out, dict) and len(out) == len(payload)
    assert not set(out) & set(payload)


def test_key_placeholders_fail_closed_when_they_cannot_be_unique(monkeypatch):
    monkeypatch.setattr(DeterministicStream, "placeholder", lambda self, text: "same")
    with pytest.raises(SanitizationError, match="collide"):
        scrub_json({"a": 1, "b": 2}, frozenset(), stream())


def test_linked_data_non_string_types_are_scrubbed():
    out = scrub_json({"@type": {"x": "Jane"}}, frozenset(), stream(), linked_data=True)
    assert "Jane" not in json.dumps(out) and "@type" in out
