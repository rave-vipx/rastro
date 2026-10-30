import asyncio
import socket
from decimal import Decimal
from pathlib import Path

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
