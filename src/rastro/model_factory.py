"""``rastro.model()``: plug in whatever AI you use, with no provider special-cased (AI-001-005).

Three generic transports: an OpenAI-compatible HTTP endpoint, any local command, or a
Python function. A ``ModelPort`` object passes through unchanged. Concrete products
(local servers, hosted APIs, subscription CLIs) are documented presets, not code paths.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from pydantic import TypeAdapter, ValidationError

from rastro.domain.job_providers import (
    CommandProvider,
    JobModelProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from rastro.domain.model_spec import SPEC_FORMS, invalid_spec, parse_model_spec
from rastro.domain.models import ConfigurationError
from rastro.ports.model import ModelPort

ENV_SPEC = "RASTRO_MODEL"
ENV_ALLOW_REMOTE = "RASTRO_MODEL_ALLOW_REMOTE"
ENV_LOCAL = "RASTRO_MODEL_LOCAL"
_TRUE = frozenset({"1", "true", "yes"})
_PROVIDER: TypeAdapter[JobModelProvider] = TypeAdapter(JobModelProvider)


def model(spec: object = None, **options: Any) -> ModelPort:
    """Build a ``ModelPort`` from a spec string, a provider name with options, or a function.

    With no spec, ``RASTRO_MODEL`` is read; ``RASTRO_MODEL_ALLOW_REMOTE=1`` (or
    ``RASTRO_MODEL_LOCAL=1`` for a command) then records the privacy opt-in.
    Raises ``ConfigurationError`` listing the valid forms for anything else.
    """
    if spec is not None and not isinstance(spec, str):
        return _from_object(spec, options)
    if spec is None:
        spec = os.environ.get(ENV_SPEC, "").strip()
        if not spec:
            raise ConfigurationError(
                f"No model configured: pass a spec or set {ENV_SPEC}",
                hint="; ".join(SPEC_FORMS),
            )
        data = parse_model_spec(spec)
        data.update(_env_opt_in(data))
    else:
        data = parse_model_spec(spec)
    data.update(options)
    return build_model(provider_from(data))


def provider_from(data: dict[str, Any]) -> JobModelProvider:
    try:
        return _PROVIDER.validate_python(data)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first["loc"][1:]) or "provider"
        raise ConfigurationError(f"Invalid model option {where}: {first['msg']}") from None


def build_model(spec: JobModelProvider) -> ModelPort:
    """Construct the adapter for a validated provider; HTTP needs an optional extra."""
    if isinstance(spec, CommandProvider):
        from rastro.adapters.command_model import CommandModel

        return CommandModel(
            spec.argv,
            allow_remote=spec.allow_remote,
            local=spec.local,
            timeout=spec.timeout,
            output=spec.output,
            json_path=spec.json_path,
            env=spec.env,
        )
    import rastro

    base_url = spec.base_url
    if spec.base_url_env is not None:
        base_url = os.environ.get(spec.base_url_env, "").strip()
        if not base_url:
            raise ConfigurationError(f"Set {spec.base_url_env} to a valid model endpoint URL")
    try:
        if isinstance(spec, OllamaProvider):
            ollama_cls: Any = rastro.OllamaModel
            return ollama_cls(spec.model, base_url=base_url)  # type: ignore[no-any-return]
        assert isinstance(spec, OpenAICompatibleProvider)
        http_cls: Any = rastro.OpenAICompatibleModel
        return http_cls(  # type: ignore[no-any-return]
            spec.model,
            base_url=base_url,
            api_key_env=spec.api_key_env,
            allow_remote=spec.allow_remote,
            response_format=spec.response_format,
            token_limit=spec.token_limit,
            temperature=spec.temperature,
        )
    except ImportError as error:
        raise ConfigurationError(str(error)) from None


def _from_object(spec: object, options: dict[str, Any]) -> ModelPort:
    if options:
        raise ConfigurationError("Options apply only to spec strings and provider names")
    if callable(getattr(spec, "generate", None)):
        return spec  # type: ignore[return-value]
    if callable(spec):
        from rastro.adapters.callable_model import CallableModel

        function: Callable[..., Any] = spec
        return CallableModel(function)
    raise invalid_spec()


def _env_opt_in(data: dict[str, Any]) -> dict[str, Any]:
    opt_in: dict[str, Any] = {}
    remote = os.environ.get(ENV_ALLOW_REMOTE, "").strip().lower() in _TRUE
    if remote and data.get("provider") != "ollama":
        opt_in["allow_remote"] = True
    if data.get("provider") == "command" and os.environ.get(ENV_LOCAL, "").strip().lower() in _TRUE:
        opt_in["local"] = True
    return opt_in
