"""Seeded choices built on Mock Lab's SHA-256 stream (MOCK-006).

``random.Random`` is avoided: its sampling algorithms are an implementation detail of
CPython. ``DeterministicStream`` depends only on the seed, a namespace and call order.
"""

from __future__ import annotations

from rastro.mocklab.seeds import DeterministicStream


def step_stream(seed: int, step: int, operator: str) -> DeterministicStream:
    """An independent stream per plan step, so editing one step never reseeds another."""
    return DeterministicStream(seed, f"mutation/{step}/{operator}")


def shuffled(stream: DeterministicStream, count: int) -> list[int]:
    """A Fisher-Yates permutation of ``range(count)``."""
    order = list(range(count))
    for index in range(count - 1, 0, -1):
        other = stream.integer(0, index)
        order[index], order[other] = order[other], order[index]
    return order


def non_identity_permutation(stream: DeterministicStream, count: int) -> list[int]:
    """A permutation of ``range(count)`` (``count >= 2``) that moves at least one item."""
    if count < 2:
        raise ValueError("A reordering needs at least two items")
    order = shuffled(stream, count)
    if order == sorted(order):
        order = order[1:] + order[:1]
    return order


def sample_indices(stream: DeterministicStream, total: int, limit: int | None) -> list[int]:
    """All indices, or ``limit`` of them chosen by the stream, in document order."""
    if limit is None or limit >= total:
        return list(range(total))
    return sorted(shuffled(stream, total)[:limit])
