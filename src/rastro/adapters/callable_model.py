"""Wrap an in-process Python function as a ModelPort (AI-001-005).

The function receives the ``ModelRequest`` (trusted ``system``, untrusted ``content`` and
``output_schema``) and returns JSON text. It may be ``async`` or blocking; a blocking
function runs in a worker thread. Its answer is untrusted like any model output.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest
from rastro.domain.models import ConfigurationError

type ModelFunction = Callable[[ModelRequest], str | Awaitable[str]]


class CallableModel:
    """Apply the policy's context, time and size budgets around a user function."""

    def __init__(self, function: ModelFunction) -> None:
        if not callable(function):
            raise ConfigurationError("Pass a function that takes a ModelRequest")
        self._function = function

    def __repr__(self) -> str:
        return f"CallableModel({getattr(self._function, '__qualname__', 'function')!r})"

    async def generate(self, request: ModelRequest, policy: InferencePolicy) -> str:
        if request.context_bytes > policy.max_context_bytes:
            raise ModelError("model_context_limit")
        try:
            async with asyncio.timeout(policy.timeout_seconds):
                if inspect.iscoroutinefunction(self._function):
                    answer = await self._function(request)
                else:
                    answer = await asyncio.to_thread(self._function, request)
                    if inspect.isawaitable(answer):
                        answer = await answer
        except TimeoutError:
            raise ModelError("model_timeout") from None
        if not isinstance(answer, str):
            raise ModelError("invalid_model_response")
        if len(answer.encode("utf-8", "surrogatepass")) > policy.max_response_bytes:
            raise ModelError("model_response_limit")
        return answer
