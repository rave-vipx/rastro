import asyncio
import socket
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel, Field

from rastro import Recipe, Source


class Product(BaseModel):
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = "USD"


@pytest.fixture
def schema() -> type[Product]:
    return Product


@pytest.fixture
def source() -> Source:
    return Source(url="https://example.com/products?token=private#secret", locale="es-UY")


@pytest.fixture
def recipe() -> Recipe:
    return Recipe(
        strategy="jsonld", item_type="Product", fields={"name": "/name", "price": "/offers/price"}
    )


# Exact payload shapes written by earlier releases for the `recipe` fixture.
# M3 predates require_evidence and record_path; 0.1.0 serialized every selector.
LEGACY_RECIPE_PAYLOADS = {
    "m3": (
        '{"fallbacks":[],"fields":{"name":"/name","price":"/offers/price"},'
        '"item_type":"Product","record_selector":null,"strategy":"jsonld","version":1}'
    ),
    "v0.1": (
        '{"fallbacks":[],"fields":{"name":"/name","price":"/offers/price"},'
        '"item_type":"Product","record_path":null,"record_selector":null,'
        '"require_evidence":false,"strategy":"jsonld","version":1}'
    ),
}


@pytest.fixture(params=sorted(LEGACY_RECIPE_PAYLOADS))
def legacy_payload(request) -> str:
    return LEGACY_RECIPE_PAYLOADS[request.param]


@pytest.fixture
def html() -> str:
    return (Path(__file__).parent / "fixtures" / "products.html").read_text()


# Exact DDL and markers of recipe schema 1 (M3 and 0.1.0), for migration tests.
SCHEMA_V1 = """CREATE TABLE recipes (
    key TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    payload TEXT NOT NULL,
    selected_index INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    last_success_at TEXT NOT NULL,
    successful_runs INTEGER NOT NULL CHECK (successful_runs > 0),
    PRIMARY KEY (key, version)
) WITHOUT ROWID;
CREATE INDEX recipe_retention ON recipes(last_success_at);
PRAGMA application_id = 1381192786;
PRAGMA user_version = 1;
"""


@pytest.fixture
def schema_v1_database():
    """Create a synthetic schema 1 file; rows are (key, version, payload, index, at, runs)."""

    def create(path: Path, rows=()) -> Path:
        with closing(sqlite3.connect(path)) as db:
            db.executescript(SCHEMA_V1)
            db.executemany(
                "INSERT INTO recipes VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(key, ver, body, index, at, at, runs) for key, ver, body, index, at, runs in rows],
            )
            db.commit()
        return path

    return create


# Build asyncio's private wake-up socket before running any task, then restore
# pytest-socket's guard. Windows uses a loopback pair; Unix uses AF_UNIX.
_ORIGINAL_SOCKET = socket.socket


def pytest_asyncio_loop_factories(config, item):
    def factory():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket, "socket", _ORIGINAL_SOCKET)
            # curl_cffi requires add_reader; use the same portable loop as the HTTP example.
            return asyncio.SelectorEventLoop()

    return {"offline": factory}


@pytest.fixture(autouse=True)
def forbid_native_network(monkeypatch):
    # curl's C extension bypasses Python socket guards. Fail before it can connect.
    from curl_cffi import AsyncCurl, Curl

    def forbidden(*args, **kwargs):
        pytest.fail("A test attempted native HTTP access; inject a fake transport")

    monkeypatch.setattr(Curl, "perform", forbidden)
    monkeypatch.setattr(AsyncCurl, "add_handle", forbidden)


@pytest.fixture(autouse=True)
def pin_observation_clock():
    """QUAL-005, SYNC-008: November samples retain semantics and ordered observations."""
    from contextlib import ExitStack

    observed = datetime(2026, 11, 7, 12, tzinfo=UTC)
    ticks = count()
    with ExitStack() as stack:
        for module in (
            "rastro.application.extraction",
            "rastro.application.service",
            "rastro.application.memory",
            "rastro.application.outcomes",
            "rastro.application.intelligence",
            "rastro.cli_recipe",
        ):
            clock = stack.enter_context(patch(f"{module}.datetime", wraps=datetime))
            clock.now.side_effect = lambda tz: observed + timedelta(microseconds=next(ticks))
        yield


@pytest.fixture
def offline_cli_loop(monkeypatch):
    original_loop = asyncio.SelectorEventLoop

    def factory():
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(socket, "socket", _ORIGINAL_SOCKET)
            return original_loop()

    monkeypatch.setattr(asyncio, "SelectorEventLoop", factory)
