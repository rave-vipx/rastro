"""Find AI models already running on this machine (Ollama, LM Studio, llama.cpp servers).

Only the loopback address is probed, with a short timeout, using a plain listing request:
no prompt and no page data leave the process. Nothing is configured or started; the result
is a spec string the person can pass to ``--model`` or ``RASTRO_MODEL``.
"""

from __future__ import annotations

import http.client
import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

HOST = "127.0.0.1"
OLLAMA_PORT = 11434
DEFAULT_PROBES: tuple[tuple[str, int], ...] = (
    ("ollama", OLLAMA_PORT),
    ("openai_compatible", 1234),
    ("openai_compatible", 8000),
    ("openai_compatible", 8080),
)
_MAX_BYTES = 262_144
_MAX_MODELS = 50


class DetectedModel(BaseModel):
    """One local server and the models it lists, with a ready-to-use model spec."""

    model_config = ConfigDict(frozen=True)

    provider: str
    base_url: str
    models: tuple[str, ...]
    spec: str


def detect_local_models(
    probes: Sequence[tuple[str, int]] = DEFAULT_PROBES, timeout: float = 0.5
) -> tuple[DetectedModel, ...]:
    """Servers answering on the loopback ports; an absent or odd answer is skipped."""
    found: list[DetectedModel] = []
    for kind, port in probes:
        path = "/api/tags" if kind == "ollama" else "/v1/models"
        names = _names(kind, _get(port, path, timeout))
        if names:
            found.append(_detected(kind, port, names))
    return tuple(found)


def _detected(kind: str, port: int, names: tuple[str, ...]) -> DetectedModel:
    base = f"http://{HOST}:{port}"
    if kind == "ollama" and port == OLLAMA_PORT:
        spec = f"ollama:{names[0]}"
    else:
        spec = f"http:{names[0]}@{base}/v1"
    return DetectedModel(provider=kind, base_url=base, models=names, spec=spec)


def _names(kind: str, body: Any) -> tuple[str, ...]:
    if not isinstance(body, dict):
        return ()
    key, field = ("models", "name") if kind == "ollama" else ("data", "id")
    items = body.get(key)
    if not isinstance(items, list):
        return ()
    names = (item.get(field) for item in items if isinstance(item, dict))
    valid = (n for n in names if isinstance(n, str) and n.isprintable() and 0 < len(n) <= 200)
    return tuple(valid)[:_MAX_MODELS]


def _get(port: int, path: str, timeout: float) -> Any:
    """The JSON a loopback server returns for ``path``, or ``None``; redirects are not followed."""
    connection = http.client.HTTPConnection(HOST, port, timeout=timeout)
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            return None
        raw = response.read(_MAX_BYTES + 1)
        return None if len(raw) > _MAX_BYTES else json.loads(raw)
    except (OSError, http.client.HTTPException, ValueError):
        return None
    finally:
        connection.close()
