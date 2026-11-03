"""Regressions for the PR #45 review: each test uses the reviewer's failing input."""

import hashlib
import json
import os
from decimal import Decimal

import pytest
from pydantic import BaseModel, Field

from rastro import CssRecipe, JsonLdRecipe, JsonRecipe, Rastro, Recipe, Source
from rastro.mocklab import MockLabError, SanitizationError, build_fixture, load_fixture

HEX32 = "9f8a7b6c5d4e3f2a1b0c9d8e7f6a5b4c"
SOURCE = Source(url="https://shop.example.com/products")
PRODUCTS = (
    '<article class="product"><h2>Notebook</h2><span data-price="12.50"></span></article>'
    '<article class="product"><h2>Pen</h2><span data-price="2.00"></span></article>'
)
CSS = CssRecipe(
    record_selector="article.product",
    fields={"name": "h2::text", "price": "span::attr(data-price)"},
)


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0)


class Named(BaseModel):
    name: str = Field(min_length=1)


def written_text(tmp_path, html, recipe=CSS, schema=Product, source=SOURCE):
    result = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    bundle = build_fixture(html, source=source, schema=schema, recipe=recipe, result=result)
    bundle.write(tmp_path)
    load_fixture(tmp_path)
    return "\n".join(path.read_text() for path in sorted(tmp_path.iterdir()))


def assert_absent(text, *values):
    for value in values:
        assert value not in text, value


def test_h1_structural_attribute_values_are_not_kept_verbatim(tmp_path):
    html = (
        f'<div id="sess-{HEX32}" class="user-jane-doe-1987 acct-445566" lang="PRIVATE-Note-Jane">'
        f'<a name="ssn-123-45-6789" rel="me jane.doe.private">x</a>'
        f'<label for="tok-{HEX32[:28]}">y</label>{PRODUCTS}</div>'
    )
    text = written_text(tmp_path, html)
    assert_absent(text, HEX32[:28], "jane", "445566", "123-45-6789", "PRIVATE", "Jane")
    assert 'class="product"' in text


def test_h2_itemtype_query_is_scrubbed(tmp_path):
    html = f'<div itemtype="https://schema.org/Person?key=SuperSecretValue42">{PRODUCTS}</div>'
    assert_absent(written_text(tmp_path, html), "SuperSecretValue42", "key=", "Person")


@pytest.mark.parametrize(
    "href",
    [
        "/u/jane.doe%40corp.example/settings",
        "/u/jane.doe\uff20corp.example/x",
        "/reset/Kx9QpL2vN8rT4wZ1",
    ],
)
def test_h3_encoded_emails_and_short_tokens_in_urls_are_scrubbed(tmp_path, href):
    text = written_text(tmp_path, f'<a href="{href}">r</a>{PRODUCTS}')
    assert_absent(text, "jane.doe", "corp.example", "Kx9QpL2vN8rT4wZ1")


def test_h4_linked_data_subtrees_survive_only_as_the_recipe_item_type(tmp_path):
    block = {
        "@context": {"internal": "Project Bluebird merger memo"},
        "@graph": [
            {
                "@type": "Product",
                "name": "Notebook",
                "review": {"@type": "Jane Doe private complaint"},
            },
            {"@type": ["Patient-John-Smith"], "name": "Chart"},
        ],
    }
    html = f'<script type="application/ld+json">{json.dumps(block)}</script>'
    recipe = JsonLdRecipe(item_type="Product", fields={"name": "/name"})
    text = written_text(tmp_path, html, recipe=recipe, schema=Named)
    assert_absent(text, "Bluebird", "Jane Doe", "Patient", "John", "internal", "Chart")
    assert '"Product"' in text and "Notebook" in text


def test_m1_json_keys_off_retained_paths_are_replaced(tmp_path):
    payload = {
        "users": {"Jane Doe (DOB 1980-02-03)": {"tier": "gold"}},
        "sessions": {HEX32: True},
        "data": {"products": [{"name": "Pen", "offer": {"price": "1.20"}}]},
    }
    text = json.dumps(payload)
    recipe = JsonRecipe(
        record_path="/data/products", fields={"name": "/name", "price": "/offer/price"}
    )
    result = Rastro().extract_json(text, source=SOURCE, schema=Product, recipe=recipe)
    build_fixture(text, source=SOURCE, schema=Product, recipe=recipe, result=result).write(tmp_path)
    written = "\n".join(path.read_text() for path in sorted(tmp_path.iterdir()))
    assert_absent(written, "Jane", "1980", HEX32, "users", "sessions", "tier")
    body = json.loads((tmp_path / "body.json").read_text())
    assert body["data"]["products"][0]["offer"]["price"] == "1.20"


def test_m2_source_url_paths_with_tokens_or_personal_data_are_refused():
    source = Source(url=f"https://shop.example.com/orders/{HEX32}/u/jane.doe%40corp.example")
    result = Rastro().extract_html(PRODUCTS, source=source, schema=Product, recipe=CSS)
    with pytest.raises(SanitizationError, match="source URL"):
        build_fixture(PRODUCTS, source=source, schema=Product, recipe=CSS, result=result)


@pytest.mark.parametrize(
    "value",
    ["123-45-6789", "4111 1111 1111 1111", "dXNlcjpwYXNzd29yZA==", "GB82 WEST 1234 5698 7654 32"],
)
def test_m3_extracted_ssn_card_base64_and_iban_values_stop_generation(value):
    html = f'<article class="product"><h2>{value}</h2><span data-price="1"></span></article>'
    result = Rastro().extract_html(html, source=SOURCE, schema=Product, recipe=CSS)
    with pytest.raises(SanitizationError, match="'name'"):
        build_fixture(html, source=SOURCE, schema=Product, recipe=CSS, result=result)


def test_m4_empty_jsonld_blocks_do_not_shift_block_indexes(tmp_path):
    html = (
        '<script type="application/ld+json"></script>'
        '<script type="application/ld+json">{"@type": "Product", "name": "Notebook"}</script>'
    )
    recipe = JsonLdRecipe(item_type="Product", fields={"name": "/name"})
    assert "Notebook" in written_text(tmp_path, html, recipe=recipe, schema=Named)


@pytest.mark.parametrize("depth", [300, 1500])
def test_m5_deep_unread_jsonld_fails_explicitly_not_with_recursion_error(depth):
    html = f'<script type="application/ld+json">{"[" * depth}{"]" * depth}</script>{PRODUCTS}'
    recipe = CssRecipe(
        record_selector="article.product",
        fields={"name": "h2::text", "price": "span::attr(data-price)"},
        fallbacks=(Recipe(strategy="jsonld", fields={"name": "/name"}),),
    )
    result = Rastro().extract_html(html, source=SOURCE, schema=Product, recipe=recipe)
    with pytest.raises(SanitizationError, match="depth bound"):
        build_fixture(html, source=SOURCE, schema=Product, recipe=recipe, result=result)


@pytest.mark.parametrize("seed", [-1, 2**64, True])
def test_l1_invalid_seeds_raise_mocklab_errors(seed):
    result = Rastro().extract_html(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS)
    with pytest.raises(MockLabError, match="seed"):
        build_fixture(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS, result=result, seed=seed)


def resign(directory, name, text):
    (directory / name).write_text(text)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["digests"][name] = hashlib.sha256(text.encode()).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def bundle_dir(tmp_path):
    result = Rastro().extract_html(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS)
    bundle = build_fixture(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS, result=result)
    return bundle.write(tmp_path / "bundle")


def test_l2_invalid_or_deep_file_contents_raise_mocklab_errors(bundle_dir):
    label = json.loads((bundle_dir / "records.json").read_text())["__rastro_synthetic__"]
    deep = json.dumps({"__rastro_synthetic__": label, "records": []})[:-1]
    resign(bundle_dir, "records.json", deep + ', "x": ' + "[" * 100_000 + "]" * 100_000 + "}")
    with pytest.raises(MockLabError, match="invalid"):
        load_fixture(bundle_dir)
    resign(bundle_dir, "records.json", json.dumps({"__rastro_synthetic__": label, "records": 1}))
    with pytest.raises(MockLabError, match="contents are invalid"):
        load_fixture(bundle_dir)


def test_l3_write_refuses_symlinks_and_leaves_targets_untouched(tmp_path, bundle_dir):
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    (bundle_dir / "body.html").unlink()
    (bundle_dir / "body.html").symlink_to(victim)
    result = Rastro().extract_html(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS)
    fixture = build_fixture(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS, result=result)
    with pytest.raises(MockLabError, match="symbolic link"):
        fixture.write(bundle_dir)
    assert victim.read_text() == "original"
    fixture.write(tmp_path / "fresh")
    assert not [p for p in (tmp_path / "fresh").iterdir() if p.name.endswith(".tmp")]


def test_l3_write_failures_are_explicit_and_leave_no_temporary_files(tmp_path, monkeypatch):
    result = Rastro().extract_html(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS)
    fixture = build_fixture(PRODUCTS, source=SOURCE, schema=Product, recipe=CSS, result=result)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / f".body.html.{os.getpid()}.tmp").mkdir()
    with pytest.raises(MockLabError, match="cannot be written"):
        fixture.write(blocked)

    def refuse(source, destination):
        raise OSError("read-only")

    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(MockLabError, match="cannot be written"):
        fixture.write(tmp_path / "readonly")
    assert not list((tmp_path / "readonly").iterdir())
