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


def safe[**P, T](operation: Callable[P, T]) -> Callable[P, T]:
    @wraps(operation)
    def call(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return operation(*args, **kwargs)
        except (DatasetStorageError, ConfigurationError):
            raise
        except Exception:
            # Driver errors can include credentials, SQL parameters and server content.
            raise DatasetStorageError("PostgreSQL dataset operation failed") from None

    return call


def parameters(dsn: str, schema: str, allow_remote: bool) -> dict[str, Any]:
    try:
        from psycopg.conninfo import conninfo_to_dict
    except ImportError:
        raise ConfigurationError(
            "Install PostgreSQL support: pip install 'rastro-engine[postgres]'"
        ) from None
    if not re.fullmatch(SCHEMA_NAME, schema):
        raise ConfigurationError("Invalid PostgreSQL schema identifier")
    try:
        params = conninfo_to_dict(dsn)
    except Exception:
        raise ConfigurationError("Invalid PostgreSQL connection configuration") from None
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


def migrate(db: Connection[Any], schema: str, *, write: bool) -> None:
    from psycopg import sql

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
    if (versions and versions != [(1,)]) or (not write and not versions):
        raise DatasetStorageError("Foreign or newer PostgreSQL dataset schema; migration refused")
    if not versions:
        for table, key in (("dataset_records", "key"), ("dataset_sources", "source")):
            db.execute(
                sql.SQL(
                    'CREATE TABLE {} (dataset TEXT COLLATE "C", {} TEXT COLLATE "C", '
                    "payload TEXT NOT NULL, identity_hash TEXT NOT NULL, "
                    "PRIMARY KEY(dataset, identity_hash))"
                ).format(sql.Identifier(table), sql.Identifier(key))
            )
        db.execute("INSERT INTO rastro_schema VALUES (1)")
