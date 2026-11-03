"""MOCK-001/MOCK-006: bundles replay offline through Rastro and are byte-reproducible."""

import json
from pathlib import Path

import pytest

from examples.structured_api import API
from examples.structured_api import Product as ApiProduct
from rastro import CssRecipe, JsonRecipe, Rastro, Recipe, Source
from rastro.mocklab import (
    FixtureMismatchError,
    MockLabError,
    SanitizationError,
    build_fixture,
    load_fixture,
    read_html_label,
    read_json_label,
    replay_fixture,
    verify_fixture,
)

EXAMPLE_RECIPE = Recipe.model_validate_json(Path("examples/product.recipe.json").read_text())
CSS = CssRecipe(
    record_selector="article.product",
    fields={"name": "h2::text", "price": "span::attr(data-price)"},
    fallbacks=(Recipe(strategy="jsonld", fields={"name": "/name"}),),
)
API_SOURCE = Source(url="https://api.example.com/v1/products?key=private", locale="en-US")
API_RECIPE = JsonRecipe(
    record_path="/data/products", fields={"name": "/name", "price": "/offer/price"}
)


def html_bundle(html, source, schema, recipe, **options):
    result = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    return build_fixture(
        html, source=source, schema=schema, recipe=recipe, result=result, **options
    )


def api_bundle(**options):
    result = Rastro().extract_json(API, source=API_SOURCE, schema=ApiProduct, recipe=API_RECIPE)
    return build_fixture(
        API, source=API_SOURCE, schema=ApiProduct, recipe=API_RECIPE, result=result, **options
    )


@pytest.mark.parametrize("recipe", [EXAMPLE_RECIPE, CSS], ids=["jsonld", "css"])
def test_html_bundle_written_and_loaded_replays_the_same_extraction(
    tmp_path, html, source, schema, recipe
):
    original = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    bundle = html_bundle(html, source, schema, recipe, seed=42)
    loaded = load_fixture(bundle.write(tmp_path))
    assert loaded == bundle
    replayed = verify_fixture(loaded, schema)
    assert [r.data for r in replayed.records] == [r.data for r in original.records]
    assert read_html_label((tmp_path / "body.html").read_text()).seed == 42
    for name in ("expected.json", "records.json"):
        assert read_json_label(json.loads((tmp_path / name).read_text())).kind == "fixture"
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["format_version"] == 1 and manifest["label"]["synthetic"] is True
    for record in loaded.records.records:
        schema.model_validate(record)


def test_json_bundle_replays_and_labels_the_response(tmp_path):
    bundle = api_bundle(seed=3, synthetic_count=2)
    loaded = load_fixture(bundle.write(tmp_path))
    assert verify_fixture(loaded, ApiProduct).metrics.accepted == 2
    body = json.loads((tmp_path / "body.json").read_text())
    assert read_json_label(body).seed == 3
    assert "meta" not in body and "private" not in json.dumps(body)
    assert body["data"]["products"][1]["name"] == "Pen"
    assert len(loaded.records.records) == 2


def test_same_inputs_and_seed_are_byte_identical_and_seeds_differ(tmp_path, html, source, schema):
    first = html_bundle(html, source, schema, CSS, seed=9).write(tmp_path / "a")
    second = html_bundle(html, source, schema, CSS, seed=9).write(tmp_path / "b")
    other = html_bundle(html, source, schema, CSS, seed=10).write(tmp_path / "c")
    names = sorted(path.name for path in first.iterdir())
    assert all((first / n).read_bytes() == (second / n).read_bytes() for n in names)
    assert (first / "body.html").read_bytes() != (other / "body.html").read_bytes()
    assert api_bundle(seed=1).render() == api_bundle(seed=1).render()


def test_unsuccessful_or_foreign_results_are_refused(html, source, schema, recipe):
    empty = Rastro().extract_html("<p></p>", source=source, schema=schema, recipe=recipe)
    with pytest.raises(MockLabError, match="successful"):
        build_fixture("<p></p>", source=source, schema=schema, recipe=recipe, result=empty)
    result = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    with pytest.raises(MockLabError, match="different schema"):
        build_fixture(html, source=source, schema=ApiProduct, recipe=recipe, result=result)
    with pytest.raises(MockLabError, match="synthetic_count"):
        build_fixture(
            html, source=source, schema=schema, recipe=recipe, result=result, synthetic_count=-1
        )


def test_result_from_another_recipe_fails_replay(html, source, schema, recipe):
    result = Rastro().extract_html(html, source=source, schema=schema, recipe=recipe)
    with pytest.raises(FixtureMismatchError):
        build_fixture(html, source=source, schema=schema, recipe=CSS, result=result)


def test_extracted_sensitive_values_stop_generation(source, schema):
    html = (
        '<article class="product"><h2>someone@example.org</h2>'
        '<span data-price="1"></span></article>'
    )
    with pytest.raises(SanitizationError, match=r"'name'.*email"):
        html_bundle(html, source, schema, CSS)


def test_array_json_roots_cannot_be_labeled():
    text = json.dumps([{"name": "Pen", "price": "1.20"}])
    recipe = JsonRecipe(record_path="", fields={"name": "/name", "price": "/price"})
    result = Rastro().extract_json(text, source=API_SOURCE, schema=ApiProduct, recipe=recipe)
    with pytest.raises(MockLabError, match="object root"):
        build_fixture(text, source=API_SOURCE, schema=ApiProduct, recipe=recipe, result=result)


def test_replay_requires_the_generating_schema(html, source, schema):
    bundle = html_bundle(html, source, schema, CSS)
    with pytest.raises(MockLabError, match="different schema"):
        replay_fixture(bundle, ApiProduct)


@pytest.fixture
def written(tmp_path):
    return api_bundle().write(tmp_path)


def rewrite(path, transform):
    path.write_text(transform(path.read_text()))


@pytest.mark.parametrize(
    ("damage", "message"),
    [
        (lambda d: rewrite(d / "body.json", lambda t: t.replace("Pen", "Pan")), "digest"),
        (lambda d: (d / "records.json").unlink(), "cannot be read"),
        (
            lambda d: rewrite(
                d / "manifest.json",
                lambda t: t.replace('"format_version": 1', '"format_version": 2'),
            ),
            "manifest is invalid",
        ),
        (
            lambda d: rewrite(
                d / "manifest.json", lambda t: t.replace('"records.json"', '"other.json"')
            ),
            "expected files",
        ),
        (lambda d: (d / "expected.json").write_bytes(b"\xff\xfe"), "UTF-8"),
    ],
)
def test_damaged_bundles_are_rejected(written, damage, message):
    damage(written)
    with pytest.raises(MockLabError, match=message):
        load_fixture(written)


def resign(directory, name, text):
    import hashlib

    (directory / name).write_text(text)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["digests"][name] = hashlib.sha256(text.encode()).hexdigest()
    (directory / "manifest.json").write_text(json.dumps(manifest))


def test_files_with_foreign_labels_or_invalid_json_are_rejected(written):
    body = json.loads((written / "body.json").read_text())
    body["__rastro_synthetic__"]["seed"] = 99
    resign(written, "body.json", json.dumps(body))
    with pytest.raises(MockLabError, match="label does not match"):
        load_fixture(written)
    resign(written, "body.json", "{not json")
    with pytest.raises(MockLabError, match="not valid JSON"):
        load_fixture(written)


def test_oversized_files_are_rejected(written, monkeypatch):
    monkeypatch.setattr("rastro.mocklab.bundle.MAX_FILE_BYTES", 10)
    with pytest.raises(MockLabError, match="exceeds"):
        load_fixture(written)
