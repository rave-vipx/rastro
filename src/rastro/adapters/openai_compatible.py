"""Optional OpenAI-compatible Chat Completions adapter (AI-001-005, SEC-004/005/011).

Install rastro-engine[openai]. One strategy serves OpenAI, OpenRouter, vLLM, LM Studio,
llama.cpp server and Ollama's ``/v1`` endpoint; loopback is the default, remote is opt-in.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import weakref
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, proposal_schema
from rastro.domain.json import strict_json
from rastro.domain.models import ConfigurationError

_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")
_ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
# Plain path segments only; dot segments could be normalised elsewhere by a proxy.
_PATH = re.compile(r"(?:/(?!\.\.?(?:/|$))[A-Za-z0-9._~-]+)*")
# Printable ASCII without spaces: a header value that cannot inject further headers.
_KEY = re.compile(r"[\x21-\x7e]{1,4096}")


class OpenAICompatibleModel:
    """Chat Completions with a structured response format; no tools, redirects or retries."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://127.0.0.1:11434/v1",
        api_key_env: str | None = None,
        allow_remote: bool = False,
        response_format: Literal["json_schema", "json_object"] = "json_schema",
    ) -> None:
        if not _MODEL.fullmatch(model):
            raise ConfigurationError("Select a bounded model name")
        if response_format not in {"json_schema", "json_object"}:
            raise ConfigurationError("response_format must be json_schema or json_object")
        try:
            parts = urlsplit(base_url)
            hostname = parts.hostname or ""
            if (
                parts.scheme not in {"http", "https"}
                or not hostname
                or parts.username is not None
                or parts.password is not None
                or parts.query
                or parts.fragment
                or parts.port == 0
                or not _PATH.fullmatch(parts.path.rstrip("/"))
            ):
                raise ValueError
        except ValueError:
            raise ConfigurationError("Use an HTTP(S) origin with an optional plain path") from None
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        loopback = address is not None and (
            address == ipaddress.ip_address("::1") if address.version == 6 else address.is_loopback
        )
        if not loopback:
            if not allow_remote:
                raise ConfigurationError(
                    "Non-loopback model endpoints receive page content; pass allow_remote=True"
                )
            if parts.scheme != "https":
                raise ConfigurationError("Remote model endpoints require HTTPS")
        elif not allow_remote and "cloud" in model.lower():
            # Local servers such as Ollama can proxy "cloud" models off the machine.
            raise ConfigurationError("Cloud-routed model names require allow_remote=True")
        if api_key_env is not None:
            if not _ENV.fullmatch(api_key_env):
                raise ConfigurationError("api_key_env must name an environment variable")
            if not _KEY.fullmatch(os.environ.get(api_key_env, "")):
                raise ConfigurationError(f"Set {api_key_env} to a valid API key")
        host = f"[{address}]" if address is not None and address.version == 6 else hostname
        port = f":{parts.port}" if parts.port is not None else ""
        self._url = f"{parts.scheme}://{host}{port}{parts.path.rstrip('/')}/chat/completions"
        self._model = model
        # Only the variable name is kept; the key is read per request and never stored.
        self._api_key_env = api_key_env
        self._response_format = response_format
        self._slots: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]
        ] = weakref.WeakKeyDictionary()

    def __repr__(self) -> str:
        return (
            f"OpenAICompatibleModel(model={self._model!r}, endpoint={self._url!r}, "
            f"api_key_env={self._api_key_env!r}, response_format={self._response_format!r})"
        )

    def _semaphore(self, limit: int) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        current = self._slots.get(loop)
        if current is None or current[0] != limit:
            current = (limit, asyncio.Semaphore(limit))
            self._slots[loop] = current
        return current[1]

    def _payload(self, request: ModelRequest, policy: InferencePolicy) -> dict[str, Any]:
        system = request.system
        response_format: dict[str, Any]
        if self._response_format == "json_schema":
            # OpenAI strict mode cannot express the field map or root anyOf; Rastro
            # validates every proposal independently, so best-effort decoding suffices.
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": "rastro_recipe_proposal",
                    "strict": False,
                    "schema": proposal_schema(),
                },
            }
        else:
            # JSON mode constrains syntax only, so the trusted prompt carries the schema.
            system += "\nRespond with JSON matching this schema: " + json.dumps(proposal_schema())
            response_format = {"type": "json_object"}
        return {
            "model": self._model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": request.content},
            ],
            "response_format": response_format,
            "temperature": 0,
            "max_tokens": policy.max_output_tokens,
            "n": 1,
        }

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
        if self._api_key_env is not None:
            key = os.environ.get(self._api_key_env, "")
            if not _KEY.fullmatch(key):
                raise ModelError("model_unavailable")
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        if request.context_bytes > policy.max_context_bytes:
            raise ModelError("model_context_limit")
        payload = self._payload(request, policy)
        headers = self._headers()
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                async with (
                    self._semaphore(policy.max_concurrency),
                    httpx.AsyncClient(
                        trust_env=False, follow_redirects=False, timeout=policy.timeout_seconds
                    ) as client,
                    client.stream("POST", self._url, json=payload, headers=headers) as response,
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
        return _content(value, policy)


def _content(value: object, policy: InferencePolicy) -> str:
    if not isinstance(value, dict) or "error" in value:
        raise ModelError("invalid_model_response")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ModelError("invalid_model_response")
    usage = value.get("usage")
    if isinstance(usage, dict):
        tokens = usage.get("completion_tokens")
        if isinstance(tokens, int) and tokens > policy.max_output_tokens:
            raise ModelError("invalid_model_response")
    # "length" truncates; "content_filter", "tool_calls" and refusals are not proposals.
    if choices[0].get("finish_reason") != "stop":
        raise ModelError("invalid_model_response")
    message = choices[0].get("message")
    if (
        not isinstance(message, dict)
        or message.get("role") != "assistant"
        or not isinstance(message.get("content"), str)
        or message.get("refusal")
        or message.get("tool_calls")
        or message.get("function_call")
    ):
        raise ModelError("invalid_model_response")
    return str(message["content"])
