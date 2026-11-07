"""ADR 0019: atomic additive retention, no recipe-memory migrations."""

import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from rastro.adapters.scrapling import ScraplingParser
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.adapters.sqlite_relocation import SQLiteRelocationStore
from rastro.application.extraction import extract_document
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.models import CssRecipe, Limits, Source, schema_fingerprint
from rastro.mocklab.fixtures import build_fixture


class Item(BaseModel):
    title: str


SOURCE = Source(url="https://example.com/")
RECIPE = CssRecipe(record_selector="article", fields={"title": "h2::text"}, require_evidence=True)


def fixture():
    body = "<article><h2>Title</h2></article>"
    result = extract_document(
        body, source=SOURCE, schema=Item, recipe=RECIPE, parser=ScraplingParser(), limits=Limits()
    )
    return build_fixture(
        body, source=SOURCE, schema=Item, recipe=RECIPE, result=result, synthetic_count=0
    )


NOW = datetime(2026, 11, 7, tzinfo=UTC)


def test_retention_is_idempotent_rejects_foreign_versions_and_is_read_only(tmp_path):
    path = tmp_path / "dataset.sqlite"
    store = SQLiteRelocationStore(path)
    assert store.latest("items", SOURCE, schema_fingerprint(Item), RECIPE) is None
    assert not path.exists()
    bundle = fixture()
    store.save("items", SOURCE, bundle, observed_at=NOW)
    store.save("items", SOURCE, bundle, observed_at=NOW)
    assert store.latest("items", SOURCE, schema_fingerprint(Item), RECIPE)[1] == bundle
    assert SQLiteDatasetStore(path).records("items") == ()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM relocation_samples").fetchone() == (1,)
        assert db.execute("PRAGMA user_version").fetchone() == (1,)
        db.execute("UPDATE relocation_schema SET version=999")
    before = path.read_bytes()
    with pytest.raises(DatasetStorageError):
        store.save("items", SOURCE, bundle, observed_at=NOW)
    assert path.read_bytes() == before
    with pytest.raises(DatasetStorageError):
        store.latest("items", SOURCE, schema_fingerprint(Item), RECIPE)


def test_failed_initial_snapshot_migration_leaves_no_file(tmp_path, monkeypatch):
    import rastro.adapters.sqlite_relocation as module

    real = module._schema

    def abort(db, *, create=False):
        real(db, create=create)
        raise ValueError("injected migration failure")

    monkeypatch.setattr(module, "_schema", abort)
    path = tmp_path / "dataset.sqlite"
    with pytest.raises(DatasetStorageError):
        SQLiteRelocationStore(path).save("items", SOURCE, fixture(), observed_at=NOW)
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []


def test_snapshot_store_refuses_symlink_parent_and_budget(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(DatasetStorageError):
        SQLiteRelocationStore(link / "data.sqlite").save(
            "items", SOURCE, fixture(), observed_at=NOW
        )
    with pytest.raises(DatasetStorageError):
        SQLiteRelocationStore(target / "small.sqlite", limits=Limits(max_json_bytes=100)).save(
            "items", SOURCE, fixture(), observed_at=NOW
        )
    assert not list(target.iterdir())
