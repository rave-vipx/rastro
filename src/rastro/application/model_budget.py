"""Enforced model cost limit and the remote-model cost notice (COST-002).

``BudgetedModel`` wraps any ``ModelPort``: it states the operation, model, call and cost
ceiling once before the first call to a remote model, and stops calling the model once the
next call would exceed the limit (``model_budget_exhausted``, never a silent success).
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import ValidationError

from rastro.domain.intelligence import InferencePolicy, ModelBudget, ModelError, ModelRequest
from rastro.domain.job_providers import JobModelProvider
from rastro.domain.models import ConfigurationError
from rastro.ports.model import ModelPort

_OVERRIDE: ContextVar[float | None] = ContextVar("rastro_max_model_cost", default=None)


@contextmanager
def model_cost_override(max_cost: float | None) -> Iterator[None]:
    """The operator's ``--max-model-cost``; it beats the job file's ``max_cost``."""
    token = _OVERRIDE.set(max_cost)
    try:
        yield
    finally:
        _OVERRIDE.reset(token)


def budget_for(spec: JobModelProvider, override: float | None = None) -> ModelBudget:
    limit = override if override is not None else _OVERRIDE.get()
    if limit is None:
        return spec.budget
    try:
        return ModelBudget(cost_per_call=spec.cost_per_call, max_cost=limit)
    except ValidationError:
        raise ConfigurationError(
            "--max-model-cost must be a positive number and needs [model] cost_per_call",
            location="model.cost_per_call",
        ) from None


def _usd(value: float) -> str:
    return f"${value:.4f}"


class BudgetedModel:
    """Counts calls before they happen, so the call that would overspend never starts."""

    def __init__(
        self,
        model: ModelPort,
        budget: ModelBudget,
        *,
        name: str,
        operation: str,
        remote: bool,
        calls_per_source: int,
    ) -> None:
        self._model, self._budget = model, budget
        self._name, self._operation, self._per_source = name, operation, calls_per_source
        self.remote = remote
        self._used = 0
        self._noticed = False

    def notice(self) -> str:
        allowed, price = self._budget.allowed_calls, self._budget.cost_per_call
        calls = f"{allowed}" if allowed is not None else f"{self._per_source} per source"
        count = allowed if allowed is not None else self._per_source
        cost = (
            "unknown"
            if price is None
            else _usd(count * price) + ("" if allowed is not None else " per source")
        )
        limit = "none" if self._budget.max_cost is None else _usd(self._budget.max_cost)
        return (
            f"rastro: {self._operation} will call remote model {self._name}: "
            f"max calls {calls}, estimated max cost {cost}, limit {limit}"
        )

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        allowed = self._budget.allowed_calls
        if allowed is not None and self._used >= allowed:
            raise ModelError("model_budget_exhausted")
        self._used += 1
        if self.remote and not self._noticed:
            self._noticed = True
            print(self.notice(), file=sys.stderr, flush=True)
        return await self._model.generate(request, policy)


def with_budget(
    model: ModelPort, spec: JobModelProvider, policy: InferencePolicy | None, *, remote: bool
) -> ModelPort:
    """Wrap ``model`` when it is remote (notice) or the job or operator set a limit."""
    budget = budget_for(spec)
    if not remote and budget.max_cost is None:
        return model
    return BudgetedModel(
        model,
        budget,
        name=getattr(spec, "model", "command"),
        operation="recipe proposal and repair",
        remote=remote,
        calls_per_source=(policy or InferencePolicy()).max_calls,
    )
