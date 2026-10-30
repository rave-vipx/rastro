"""Optional, local-only Ollama chat adapter (AI-001-004). Install rastro-engine[ollama]."""

from __future__ import annotations

import asyncio
import ipaddress
import re
from urllib.parse import urlsplit

import httpx

from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, proposal_schema
from rastro.domain.json import strict_json
from rastro.domain.models import ConfigurationError


class OllamaModel:
    """Use an already-installed model on a trusted local Ollama server; never pull models."""

    def __init__(self, model: str, *, base_url: str = "http://127.0.0.1:11434") -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model) or (
            "cloud" in model.lower()
        ):
            raise ConfigurationError("Select an installed local model name")
        try:
            parts = urlsplit(base_url)
            address = ipaddress.ip_address(parts.hostname or "")
            if (
                not address.is_loopback
                or (address.version == 6 and address != ipaddress.ip_address("::1"))
                or parts.scheme not in {"http", "https"}
                or parts.username is not None
                or parts.password is not None
                or parts.path not in {"", "/"}
                or parts.query
                or parts.fragment
                or parts.port == 0
            ):
                raise ValueError
        except ValueError:
            raise ConfigurationError("Ollama requires a literal loopback HTTP(S) origin") from None
        host = f"[{address}]" if address.version == 6 else str(address)
        port = f":{parts.port}" if parts.port is not None else ""
        self._url = f"{parts.scheme}://{host}{port}/api/chat"
        self._model = model

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        if request.context_bytes > policy.max_context_bytes:
            raise ModelError("model_context_limit")
        payload = {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.content},
            ],
            "format": proposal_schema(),
            # Capable models otherwise spend the output budget on hidden reasoning.
            "think": False,
            "options": {
                "temperature": 0,
                "num_predict": policy.max_output_tokens,
                "num_ctx": policy.context_tokens,
            },
        }
        try:
            async with (
                asyncio.timeout(policy.timeout_seconds),
                httpx.AsyncClient(
                    trust_env=False, follow_redirects=False, timeout=policy.timeout_seconds
                ) as client,
                client.stream(
                    "POST", self._url, json=payload, headers={"Accept-Encoding": "identity"}
                ) as response,
            ):
                if response.status_code != 200:
                    raise ModelError("model_unavailable")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ModelError("invalid_model_response")
                body = bytearray()
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > policy.max_response_bytes:
                        raise ModelError("model_response_limit")
                    body.extend(chunk)
            value = strict_json(bytes(body))
        except (TimeoutError, httpx.TimeoutException):
            raise ModelError("model_timeout") from None
        except httpx.HTTPError:
            raise ModelError("model_unavailable") from None
        except (ValueError, RecursionError):
            raise ModelError("invalid_model_response") from None
        if (
            not isinstance(value, dict)
            or value.get("done") is not True
            or value.get("done_reason") == "length"
            or "error" in value
        ):
            raise ModelError("invalid_model_response")
        message = value.get("message")
        if (
            not isinstance(message, dict)
            or message.get("role") != "assistant"
            or not isinstance(message.get("content"), str)
            or message.get("tool_calls")
        ):
            raise ModelError("invalid_model_response")
        return str(message["content"])
