import asyncio
import logging
import threading

import pytest
from pydantic import BaseModel, Field, field_validator

from rastro import (
    AcquisitionPolicy,
    ConfigurationError,
    Outcome,
    Rastro,
    Recipe,
    RecipeContext,
    RecipeKey,
    RecipeNotFoundError,
    RecipeStorageError,
    Source,
    SQLiteRecipeRepository,
    schema_fingerprint,
)
from rastro.domain.acquisition import AcquisitionResult

POLICY = AcquisitionPolicy(allowed_hosts=("example.com",), max_concurrency=2)


def test_repeat_extraction_reuses_exact_version_and_retains_evidence(
    tmp_path, source, schema, recipe, html
):
    path = tmp_path / "recipes.db"
    repo = SQLiteRecipeRepository(path)
    first = Rastro(repository=repo).extract_html(html, source=source, schema=schema, recipe=recipe)
    assert first.status == Outcome.SUCCESS and not first.recipe_reused
    repeated = Rastro(repository=SQLiteRecipeRepository(path)).extract_html(
        html, source=source, schema=schema
    )
    assert repeated.status == Outcome.SUCCESS and repeated.recipe_reused
    assert repeated.recipe_version == 1
    assert repeated.records[0].data == first.records[0].data
    assert repeated.records[0].evidence["name"].recipe_version == 1
    assert repeated.metrics.storage_seconds > 0
    assert repo.get(RecipeKey.for_source(source, schema)).successful_runs == 2
    v2 = recipe.model_copy(update={"version": 2})
    engine = Rastro(repository=repo)
    engine.extract_html(html, source=source, schema=schema, recipe=v2)
    assert engine.extract_html(html, source=source, schema=schema).recipe_version == 2
    assert (
        engine.extract_html(html, source=source, schema=schema, recipe_version=1).recipe_version
        == 1
    )
    with pytest.raises(RecipeNotFoundError):
        engine.extract_html(html, source=source, schema=schema, recipe_version=3)


def test_pre_regional_sqlite_row_is_reused_after_restart(tmp_path, source, schema, recipe, html):
    import json
    import sqlite3
    from contextlib import closing

    path = tmp_path / "legacy.db"
    key = RecipeKey.for_source(source, schema)
    legacy_key = key.model_dump(mode="json")
    for field in ("languages", "currency", "egress"):
        legacy_key.pop(field)
    SQLiteRecipeRepository(path).get(key)
    with closing(sqlite3.connect(path)) as db:
        db.execute(
            "INSERT INTO recipes VALUES (?, 1, ?, 0, ?, ?, 1)",
            (
                json.dumps(legacy_key, sort_keys=True, separators=(",", ":")),
                recipe.model_dump_json(),
                "2026-01-01T00:00:00.000000+00:00",
                "2026-01-01T00:00:00.000000+00:00",
            ),
        )
        db.commit()

    result = Rastro(repository=SQLiteRecipeRepository(path)).extract_html(
        html, source=source, schema=schema
    )

    assert result.status == Outcome.SUCCESS and result.recipe_reused


@pytest.mark.parametrize(
    "dimension",
    [
        "host",
        "path",
        "query",
        "locale",
        "languages",
        "language_order",
        "timezone",
        "currency",
        "schema",
        "template",
        "region",
        "revision",
    ],
)
def test_incompatible_context_never_reuses(tmp_path, source, schema, recipe, html, dimension):
    if dimension == "language_order":
        source = source.model_copy(update={"languages": ("es-UY", "en")})
    repo = SQLiteRecipeRepository(tmp_path / "recipes.db")
    engine = Rastro(repository=repo)
    engine.extract_html(html, source=source, schema=schema, recipe=recipe)
    context = RecipeContext()
    updates = {
        "host": {"url": "https://other.example/products"},
        "path": {"url": "https://example.com/other"},
        "query": {"url": "https://example.com/products?token=different"},
        "locale": {"locale": "pt-BR"},
        "languages": {"languages": ("pt-BR",)},
        "language_order": {"languages": ("en", "es-UY")},
        "timezone": {"timezone": "America/Montevideo"},
        "currency": {"currency": "UYU"},
    }
    if dimension in updates:
        source = source.model_copy(update=updates[dimension])
    elif dimension == "schema":

        class Changed(BaseModel):
            name: str

        schema = Changed
    else:
        field = "schema_revision" if dimension == "revision" else dimension
        context = RecipeContext(**{field: "different"})
    with pytest.raises(RecipeNotFoundError):
        engine.extract_html(html, source=source, schema=schema, context=context)


def test_custom_validator_revision_and_runtime_validation(tmp_path):
    def model(minimum):
        class Amount(BaseModel):
            amount: int

            @field_validator("amount")
            @classmethod
            def check(cls, value):
                if value < minimum:
                    raise ValueError("private validation detail")
                return value

        return Amount

    old, new = model(0), model(20)
    assert schema_fingerprint(old) == schema_fingerprint(new)
    source = Source(url="https://example.com/")
    recipe = Recipe(strategy="jsonld", fields={"amount": "/amount"})
    html = '<script type="application/ld+json">{"amount": 10}</script>'
    repo = SQLiteRecipeRepository(tmp_path / "db")
    engine = Rastro(repository=repo)
    engine.extract_html(html, source=source, schema=old, recipe=recipe)
    with pytest.raises(RecipeNotFoundError):
        engine.extract_html(
            html, source=source, schema=new, context=RecipeContext(schema_revision="2")
        )
    # Even if a caller forgets to bump the revision, reuse still runs today's validators.
    result = engine.extract_html(html, source=source, schema=new)
    assert result.status == Outcome.INVALID_DATA
    assert repo.get(RecipeKey.for_source(source, old)).successful_runs == 1


def test_only_success_is_remembered_and_failed_versions_do_not_replace(
    tmp_path, source, schema, recipe, html
):
    repo = SQLiteRecipeRepository(tmp_path / "db")
    engine = Rastro(repository=repo)
    key = RecipeKey.for_source(source, schema)
    assert (
        engine.extract_html("", source=source, schema=schema, recipe=recipe).status
        == Outcome.MISSING_DATA
    )
    assert repo.get(key) is None
    engine.extract_html(html, source=source, schema=schema, recipe=recipe)
    bad = recipe.model_copy(update={"version": 2, "fields": {"name": "/missing"}})
    assert (
        engine.extract_html(html, source=source, schema=schema, recipe=bad).status
        != Outcome.SUCCESS
    )
    assert repo.get(key).recipe.version == 1
    partial = html.replace(
        "</head>",
        '<script type="application/ld+json">{"@type":"Product","name":"broken"}</script></head>',
    )
    result = engine.extract_html(
        partial, source=source, schema=schema, recipe=recipe.model_copy(update={"version": 3})
    )
    assert result.status == Outcome.PARTIAL_SUCCESS
    assert repo.get(key, version=3) is None


def test_fallback_candidates_preserved_but_only_successful_alternative_reused(
    tmp_path, source, schema, recipe, html
):
    repo = SQLiteRecipeRepository(tmp_path / "db")
    primary = Recipe(
        strategy="css", record_selector="missing", fields={"name": "p::text"}, fallbacks=(recipe,)
    )
    engine = Rastro(repository=repo)
    result = engine.extract_html(html, source=source, schema=schema, recipe=primary)
    assert [a.status for a in result.recipe_attempts] == [Outcome.MISSING_DATA, Outcome.SUCCESS]
    stored = repo.get(RecipeKey.for_source(source, schema))
    assert stored.recipe == primary and stored.validated_recipe == recipe
    assert stored.selected_index == 1
    repeated = engine.extract_html(html, source=source, schema=schema)
    assert len(repeated.recipe_attempts) == 1 and repeated.recipe_attempts[0].index == 1
    failed = engine.extract_html("", source=source, schema=schema)
    assert len(failed.recipe_attempts) == 1 and failed.status == Outcome.MISSING_DATA
    assert repo.get(stored.key).successful_runs == 2


def test_partial_results_do_not_trigger_fallbacks(source, schema, recipe, html):
    partial = html.replace(
        "</head>",
        '<script type="application/ld+json">{"@type":"Product","name":"broken"}</script></head>',
    )
    result = Rastro().extract_html(
        partial,
        source=source,
        schema=schema,
        recipe=recipe.model_copy(update={"fallbacks": (recipe,)}),
    )
    assert result.status == Outcome.PARTIAL_SUCCESS and len(result.recipe_attempts) == 1


def test_configuration_errors_and_storage_failures_are_explicit(
    tmp_path, source, schema, recipe, html, caplog
):
    for kwargs in [
        {},
        {"recipe": recipe, "context": RecipeContext()},
        {"recipe": recipe, "recipe_version": 1},
    ]:
        with pytest.raises(ConfigurationError):
            Rastro().extract_html(html, source=source, schema=schema, **kwargs)

    class Failing:
        def save_success(self, *args, **kwargs):
            raise RecipeStorageError("Unavailable")

    with caplog.at_level(logging.INFO, logger="rastro"), pytest.raises(RecipeStorageError):
        Rastro(repository=Failing()).extract_html(html, source=source, schema=schema, recipe=recipe)
    assert "extraction_completed" not in caplog.text


@pytest.mark.parametrize("mismatch", ["key", "version"])
def test_repository_cannot_return_incompatible_recipe(
    tmp_path, source, schema, recipe, html, mismatch
):
    repo = SQLiteRecipeRepository(tmp_path / "db")
    Rastro(repository=repo).extract_html(html, source=source, schema=schema, recipe=recipe)
    stored = repo.get(RecipeKey.for_source(source, schema))
    if mismatch == "key":
        stored = stored.model_copy(
            update={"key": stored.key.model_copy(update={"locale": "fr-FR"})}
        )

    class Incompatible:
        def get(self, *args, **kwargs):
            return stored

    with pytest.raises(RecipeStorageError, match="incompatible"):
        Rastro(repository=Incompatible()).extract_html(
            html, source=source, schema=schema, recipe_version=2 if mismatch == "version" else None
        )


class Acquirer:
    def __init__(self, html):
        self.html = html
        self.calls = 0

    async def acquire(self, source, *, max_bytes):
        self.calls += 1
        await asyncio.sleep(0)
        return AcquisitionResult("https://example.com/final", self.html, Outcome.SUCCESS, None)


@pytest.mark.asyncio
async def test_async_memory_uses_workers_and_requested_identity_after_redirect(
    tmp_path, source, schema, recipe, html, monkeypatch
):
    loop_thread = threading.get_ident()
    import sqlite3

    connect = sqlite3.connect

    def guarded_connect(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    repo = SQLiteRecipeRepository(tmp_path / "async.db")
    engine = Rastro(acquisition=Acquirer(html), policy=POLICY, repository=repo)
    first = await engine.extract(source=source, schema=schema, recipe=recipe)
    second = await engine.extract(source=source, schema=schema)
    assert first.status == second.status == Outcome.SUCCESS
    assert second.recipe_reused and second.source_url == "https://example.com/final"
    assert second.requested_url == source.evidence_url
    sources = [source.model_copy(update={"url": f"https://example.com/{i}"}) for i in range(6)]
    await engine.extract_many(sources, schema=schema, recipe=recipe)
    repeated = await engine.extract_many(sources, schema=schema)
    assert all(item.recipe_reused for item in repeated)
    assert [item.requested_url for item in repeated] == [s.url for s in sources]


@pytest.mark.asyncio
async def test_missing_memory_fails_before_acquisition(tmp_path, source, schema, html):
    acquirer = Acquirer(html)
    with pytest.raises(RecipeNotFoundError):
        await Rastro(
            acquisition=acquirer, policy=POLICY, repository=SQLiteRecipeRepository(tmp_path / "db")
        ).extract(source=source, schema=schema)
    assert acquirer.calls == 0


@pytest.mark.asyncio
async def test_cancellation_keeps_loop_responsive_and_finishes_started_transaction(
    tmp_path, source, schema, recipe, html
):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class SlowRepository(SQLiteRecipeRepository):
        def save_success(self, *args, **kwargs):
            entered.set()
            try:
                assert release.wait(timeout=5)
                return super().save_success(*args, **kwargs)
            finally:
                finished.set()

    path = tmp_path / "db"
    engine = Rastro(acquisition=Acquirer(html), policy=POLICY, repository=SlowRepository(path))
    task = asyncio.create_task(engine.extract(source=source, schema=schema, recipe=recipe))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
    stored = await asyncio.to_thread(
        SQLiteRecipeRepository(path).get, RecipeKey.for_source(source, schema)
    )
    assert stored.successful_runs == 1
    path.unlink()


def test_schema_defaults_and_examples_are_not_persisted(tmp_path):
    class Data(BaseModel):
        name: str
        token: str = Field(default="PRIVATE_DEFAULT", examples=["PRIVATE_EXAMPLE"])

    path = tmp_path / "db"
    result = Rastro(repository=SQLiteRecipeRepository(path)).extract_html(
        '<script type="application/ld+json">{"name":"PRIVATE_RECORD"}</script>',
        source=Source(url="https://example.com/?token=PRIVATE_URL"),
        schema=Data,
        recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
    )
    assert result.status == Outcome.SUCCESS
    assert b"PRIVATE_" not in path.read_bytes()
