"""Optional PostgreSQL config and credential boundaries."""

import sys

import pytest
from pydantic import ValidationError

from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.models import ConfigurationError


def test_postgres_job_config():
    spec = DatasetSpec(name="events", identity=("id",), backend="postgres", dsn_env="RASTRO_PG_DSN")
    assert spec.backend == "postgres" and spec.db_schema == "rastro"


@pytest.mark.parametrize("extra", [dict(schema="bad;sql"), dict(dsn_env=None), dict(path="x.db")])
def test_invalid_postgres_config(extra):
    with pytest.raises((ValidationError, ConfigurationError)):
        DatasetSpec.model_validate(
            dict(name="events", identity=["id"], backend="postgres", dsn_env="RASTRO_PG_DSN")
            | extra
        )


def test_missing_extra(monkeypatch):
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    # Contract tests may have cached the submodule; block it too so test order cannot matter.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "psycopg.conninfo", None)
    with pytest.raises(ConfigurationError, match=r"rastro-engine\[postgres\]"):
        PostgresDatasetStore("secret")


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:secret@remote.example/db",
        "host=localhost hostaddr=8.8.8.8 password=secret",
        "service=remote password=secret",
        "invalid-secret",
    ],
)
def test_remote_or_invalid_dsn_redacted(dsn):
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    with pytest.raises(ConfigurationError) as caught:
        PostgresDatasetStore(dsn)
    assert "secret" not in str(caught.value)
    assert "secret" not in repr(caught.value)


def test_repr_redacted():
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    store = PostgresDatasetStore("host=127.0.0.1 password=secret")
    assert "secret" not in repr(store)
    store.close()


def pg_job(env="RASTRO_PG_DSN"):
    from rastro.domain.jobs import Job
    from tests.unit.test_job_dataset import BASE

    return Job.model_validate(
        {
            **BASE,
            "dataset": {
                "name": "events",
                "identity": ["title"],
                "backend": "postgres",
                "dsn_env": env,
            },
        }
    )


def test_missing_env_and_untrusted_name(monkeypatch, tmp_path):
    from rastro.application.job_dataset import open_dataset

    monkeypatch.delenv("RASTRO_PG_DSN", raising=False)
    with (
        pytest.raises(ConfigurationError, match="environment variable") as caught,
        open_dataset(pg_job(), tmp_path / "state"),
    ):
        pytest.fail("missing DSN accepted")
    assert "RASTRO_PG_DSN" not in str(caught.value)
    with (
        pytest.raises(ConfigurationError, match="RASTRO_"),
        open_dataset(pg_job("PRIVATE_TOKEN"), tmp_path / "state"),
    ):
        pytest.fail("untrusted name accepted")


def test_remote_operator_opt_in(monkeypatch, tmp_path):
    from rastro.application.job_dataset import open_dataset
    from rastro.application.operator_trust import operator_trust

    monkeypatch.setenv("RASTRO_PG_DSN", "host=remote.example password=secret")
    with (
        pytest.raises(ConfigurationError, match="allow-remote"),
        open_dataset(pg_job(), tmp_path / "state"),
    ):
        pytest.fail("remote accepted")
    with operator_trust(allow_remote=True), open_dataset(pg_job(), tmp_path / "state") as store:
        assert "secret" not in repr(store)


def test_driver_failure_redacted(monkeypatch):
    import psycopg

    from rastro.adapters.postgres_dataset import PostgresDatasetStore
    from rastro.domain.dataset import DatasetStorageError

    def fail(**kwargs):
        raise psycopg.OperationalError("secret password in driver error")

    monkeypatch.setattr(psycopg, "connect", fail)
    store = PostgresDatasetStore("host=127.0.0.1 password=secret")
    with pytest.raises(DatasetStorageError) as caught:
        store.records("events")
    assert "secret" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize("name", ["PGSERVICE", "PGHOSTADDR"])
def test_libpq_environment_cannot_override_destination(monkeypatch, name):
    from rastro.adapters.postgres_dataset import PostgresDatasetStore

    monkeypatch.setenv(name, "remote-secret")
    store = PostgresDatasetStore("host=127.0.0.1 password=secret")
    with pytest.raises(ConfigurationError) as caught:
        store.records("events")
    assert "remote-secret" not in str(caught.value)


def test_config_round_trip():
    job = pg_job()
    assert type(job).model_validate_json(job.model_dump_json()) == job


def test_postgres_failure_reports_exit_five(monkeypatch, tmp_path):
    import json

    import psycopg

    from rastro.application.job_dataset import load_job_file
    from rastro.domain.job_results import JobExitCode

    monkeypatch.setenv("RASTRO_PG_DSN", "host=127.0.0.1 password=secret")

    def fail(**kwargs):
        raise psycopg.OperationalError("secret")

    monkeypatch.setattr(psycopg, "connect", fail)
    job = pg_job()
    (tmp_path / "page.html").write_text("<li><b>Show</b></li>")
    path = tmp_path / "job.json"
    data = job.model_dump(mode="json", exclude_none=True)
    data["fields"] = [{"name": "title", "type": "string"}]
    path.write_text(json.dumps(data))
    report = load_job_file(path)
    assert report.exit_code == JobExitCode.STORAGE_ERROR
    assert "secret" not in report.model_dump_json()


def test_invalid_adapter_options_have_typed_error():
    from rastro.adapters.postgres_dataset import PostgresDatasetStore
    from rastro.domain.dataset import DatasetStorageError

    with pytest.raises(DatasetStorageError):
        PostgresDatasetStore("host=127.0.0.1", near_match_tolerance="invalid")


def test_driver_errors_do_not_leak_through_context():
    """Neither __cause__ nor __context__ carries the driver error, DSN or server text."""
    pytest.importorskip("psycopg")
    import traceback

    from rastro.adapters.postgres_dataset import PostgresDatasetStore
    from rastro.domain.dataset import DatasetStorageError

    dsn = "postgresql://leakuser:leakpass@127.0.0.1:1/leakdb?connect_timeout=1"
    with pytest.raises(DatasetStorageError) as storage:
        PostgresDatasetStore(dsn).records("events")
    garbage = "invalid-leakpass"  # a variable keeps the secret out of the shown source line
    with pytest.raises(ConfigurationError) as invalid:
        PostgresDatasetStore(garbage)
    for error in (storage.value, invalid.value):
        assert error.__cause__ is None and error.__context__ is None
        shown = "".join(traceback.format_exception(error)) + str(error) + repr(error)
        for part in ("leakuser", "leakpass", "leakdb", "127.0.0.1", "invalid-"):
            assert part not in shown
