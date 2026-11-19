"""PostgreSQL connection boundary and forward-only schema (DATA-001, SEC-011)."""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import wraps
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any

from rastro.domain.dataset import DatasetNotFoundError, DatasetStorageError
from rastro.domain.models import ConfigurationError

if TYPE_CHECKING:
    from psycopg import Connection

SCHEMA_NAME = r"^[a-z_][a-z0-9_]{0,62}$"


def _v1(db: Connection[Any]) -> None:
    from psycopg import sql

    for table, key in (("dataset_records", "key"), ("dataset_sources", "source")):
        db.execute(
            sql.SQL(
                'CREATE TABLE {} (dataset TEXT COLLATE "C", {} TEXT COLLATE "C", '
                "payload TEXT NOT NULL, identity_hash TEXT NOT NULL, "
                "PRIMARY KEY(dataset, identity_hash))"
            ).format(sql.Identifier(table), sql.Identifier(key))
        )


MIGRATIONS: tuple[Callable[[Connection[Any]], None], ...] = (_v1,)
SCHEMA_VERSION = len(MIGRATIONS)
KNOWN_VERSIONS = tuple(range(1, SCHEMA_VERSION + 1))


def safe[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return operation(*args, **kwargs)
        except (DatasetStorageError, ConfigurationError):
            raise
        except Exception:
            pass  # Driver errors can include credentials, SQL parameters and server content.
        # Raised outside the except block so the driver error is not left in __context__.
        raise DatasetStorageError("PostgreSQL dataset operation failed")

    return call


def parameters(dsn: str, schema: str, allow_remote: bool) -> dict[str, Any]:
    try:
        from psycopg.conninfo import conninfo_to_dict

        installed = True
    except ImportError:
        installed = False
    if not installed:
        raise ConfigurationError(
            "Install PostgreSQL support: pip install 'rastro-engine[postgres]'"
        )
    if not re.fullmatch(SCHEMA_NAME, schema):
        raise ConfigurationError("Invalid PostgreSQL schema identifier")
    try:
        params: dict[str, Any] | None = dict(conninfo_to_dict(dsn))
    except Exception:
        params = None  # The parser error can quote the DSN.
    if params is None:
        raise ConfigurationError("Invalid PostgreSQL connection configuration")
    if "service" in params or not params.get("host"):
        raise ConfigurationError("PostgreSQL requires an explicit host and no service reference")
    hosts = str(params["host"]).split(",")
    addresses = str(params.get("hostaddr", "")).split(",")
    if not allow_remote and not all(_local(h) for h in hosts + [a for a in addresses if a]):
        raise ConfigurationError("Remote PostgreSQL requires --allow-remote")
    # Explicit overrides prevent libpq environment defaults bypassing the checked target.
    params.update(hostaddr=params.get("hostaddr", ""), connect_timeout=5)
    if hosts == ["localhost"] and not params["hostaddr"]:
        params["hostaddr"] = "127.0.0.1"
    return params


def _local(host: str) -> bool:
    if host == "localhost" or host.startswith("/"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def migrate(
    db: Connection[Any], schema: str, *, write: bool, to: int = SCHEMA_VERSION
) -> tuple[int, int]:
    from psycopg import sql

    if isinstance(to, bool) or not isinstance(to, int) or to not in KNOWN_VERSIONS:
        raise DatasetStorageError("Unknown PostgreSQL dataset schema version; migration refused")
    # Serialize schema creation and snapshot writers, including the initial empty schema.
    db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("rastro:" + schema,))
    if write:
        db.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
    db.execute(sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(sql.Identifier(schema)))
    if write:
        db.execute("CREATE TABLE IF NOT EXISTS rastro_schema (version INTEGER PRIMARY KEY)")
    elif db.execute("SELECT to_regclass(%s)", (schema + ".rastro_schema",)).fetchone() == (None,):
        raise DatasetNotFoundError("PostgreSQL dataset schema does not exist")
    versions = db.execute("SELECT version FROM rastro_schema").fetchall()
    if (
        len(versions) > 1
        or (versions and versions[0][0] not in KNOWN_VERSIONS)
        or (not write and not versions)
    ):
        raise DatasetStorageError("Foreign or newer PostgreSQL dataset schema; migration refused")
    current = int(versions[0][0]) if versions else 0
    if not write:
        return current, current
    if to < current:
        raise DatasetStorageError(
            "PostgreSQL dataset schema migrations are forward-only; downgrade refused"
        )
    if current < to:
        for step in MIGRATIONS[current:to]:
            step(db)
        db.execute("DELETE FROM rastro_schema")
        db.execute("INSERT INTO rastro_schema VALUES (%s)", (to,))
    return current, to
