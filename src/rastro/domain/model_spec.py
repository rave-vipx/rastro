"""Parse one-line model specs (``RASTRO_MODEL``, ``--model``, job files) into provider data.

Forms: ``http:<model>[@<base_url>]``, ``ollama:<model>`` (loopback Chat Completions) and
``command:<argv>``. A command is split with :mod:`shlex` into an argument list that is
never given to a shell, so ``|``, ``;`` or ``$(...)`` are plain arguments.
"""

from __future__ import annotations

import shlex
from typing import Any

from rastro.domain.models import ConfigurationError

SPEC_FORMS = (
    "http:<model>@<base_url>",
    "ollama:<model>",
    "command:<program and arguments>",
    'a provider name ("http", "command", "ollama") with options',
)
PROVIDER_NAMES = frozenset({"http", "openai_compatible", "command", "ollama"})
MAX_SPEC_CHARS = 8192


def invalid_spec() -> ConfigurationError:
    return ConfigurationError(
        "Unrecognised model spec; use one of: " + "; ".join(SPEC_FORMS),
        hint="Python callers can also pass a ModelPort object or a function",
    )


def parse_model_spec(spec: str) -> dict[str, Any]:
    """Provider data for ``spec``; options given separately are merged by the caller."""
    if not isinstance(spec, str) or not spec.strip() or len(spec) > MAX_SPEC_CHARS:
        raise invalid_spec()
    spec = spec.strip()
    if spec in PROVIDER_NAMES:
        return {"provider": spec}
    kind, separator, rest = spec.partition(":")
    rest = rest.strip()
    if not separator or not rest:
        raise invalid_spec()
    if kind == "http":
        model, _, base_url = rest.rpartition("@")
        if base_url.startswith(("http://", "https://")):
            return {"provider": "http", "model": model, "base_url": base_url}
        return {"provider": "http", "model": rest}
    if kind == "ollama":
        return {"provider": "http", "model": rest}
    if kind == "command":
        try:
            argv = shlex.split(rest)
        except ValueError:
            raise ConfigurationError("The model command has unbalanced quotes") from None
        return {"provider": "command", "argv": argv}
    raise invalid_spec()
