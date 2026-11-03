"""Declarative model provider specifications referencing credentials only via env names."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, field_validator, model_validator

from rastro.domain.job_contract import ENV_REFERENCE, RedactedContract

_OLLAMA_MODEL = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$"
_OPENAI_MODEL = r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$"
_API_PATH = re.compile(r"(?:/(?!\.\.?(?:/|$))[A-Za-z0-9._~-]+)*")


def _validate_endpoint(url: str, *, allow_path: bool, allow_remote: bool, model: str) -> str:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        raise ValueError("Invalid model endpoint port") from None
    path_ok = (
        bool(_API_PATH.fullmatch(parts.path.rstrip("/"))) if allow_path else parts.path in {"", "/"}
    )
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or port == 0
        or not path_ok
    ):
        raise ValueError("Model endpoint must be a credential-free HTTP(S) origin")
    try:
        addr = ipaddress.ip_address(parts.hostname)
    except ValueError:
        addr = None
    loopback = addr is not None and (
        addr == ipaddress.ip_address("::1") if addr.version == 6 else addr.is_loopback
    )
    if not loopback:
        if not allow_remote or parts.scheme != "https":
            raise ValueError("Non-loopback model endpoints require allow_remote = true and HTTPS")
    elif not allow_remote and "cloud" in model.lower():
        raise ValueError("Cloud-routed model names require allow_remote = true")
    return url


class OllamaProvider(RedactedContract):
    """Local Ollama model configuration; never accepts remote hosts or inline secrets."""

    provider: Literal["ollama"] = "ollama"
    model: str = Field(pattern=_OLLAMA_MODEL)
    base_url: str = Field(default="http://127.0.0.1:11434", min_length=1, max_length=2048)
    base_url_env: str | None = Field(default=None, pattern=ENV_REFERENCE)

    @model_validator(mode="after")
    def check_endpoint(self) -> Self:
        if self.base_url_env is not None and "base_url" in self.model_fields_set:
            raise ValueError("Specify either base_url or base_url_env, not both")
        _validate_endpoint(self.base_url, allow_path=False, allow_remote=False, model=self.model)
        return self


class OpenAICompatibleProvider(RedactedContract):
    """Chat Completions model configuration; credentials are env variable names only."""

    provider: Literal["openai_compatible"] = "openai_compatible"
    model: str = Field(pattern=_OPENAI_MODEL)
    base_url: str = Field(default="http://127.0.0.1:11434/v1", min_length=1, max_length=2048)
    base_url_env: str | None = Field(default=None, pattern=ENV_REFERENCE)
    api_key_env: str | None = Field(default=None, pattern=ENV_REFERENCE)
    allow_remote: StrictBool = False
    response_format: Literal["json_schema", "json_object"] = "json_schema"
    token_limit: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    temperature: float | None = Field(default=0, ge=0, le=2, allow_inf_nan=False)

    @field_validator("temperature", mode="before")
    @classmethod
    def reject_bool_temperature(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("temperature must be a number or null")
        return value

    @model_validator(mode="after")
    def check_endpoint(self) -> Self:
        if self.base_url_env is not None and "base_url" in self.model_fields_set:
            raise ValueError("Specify either base_url or base_url_env, not both")
        if self.base_url_env is None:
            _validate_endpoint(
                self.base_url,
                allow_path=True,
                allow_remote=self.allow_remote,
                model=self.model,
            )
        elif not self.allow_remote:
            # The endpoint is only known at run time, so it cannot be proven loopback here.
            raise ValueError("base_url_env requires allow_remote = true")
        return self


type JobModelProvider = Annotated[
    OllamaProvider | OpenAICompatibleProvider, Field(discriminator="provider")
]
