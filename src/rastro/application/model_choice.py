"""Resolve the ``model=`` argument of the everyday flows into a ``ModelPort`` (AI-001, P-001).

Any object with an async ``generate(request, policy)`` method is accepted as is: Rastro
never special-cases a provider. A string is a provider spec resolved by ``rastro.model``;
``"env"`` reads the ``RASTRO_MODEL`` environment variable through the same factory.
Remote endpoints still need ``allow_remote=True``, enforced by the factory itself.
"""

from __future__ import annotations

from typing import Any

from rastro.domain.models import ConfigurationError
from rastro.ports.model import ModelPort

type ModelChoice = ModelPort | str | None


def resolve_model(choice: ModelChoice, *, allow_remote: bool = False) -> ModelPort | None:
    """``None`` stays ``None``; a port is returned unchanged; a spec goes to ``rastro.model``."""
    if choice is None:
        return None
    if not isinstance(choice, str):
        if not callable(getattr(choice, "generate", None)):
            raise ConfigurationError("model must be a ModelPort, a model spec string or 'env'")
        return choice
    import rastro

    factory: Any = getattr(rastro, "model", None)
    if factory is None:
        raise ConfigurationError("Model specs need rastro.model; pass a ModelPort object instead")
    options: dict[str, Any] = {"allow_remote": True} if allow_remote else {}
    spec = None if choice == "env" else choice
    port: ModelPort = factory(spec, **options)
    return port
