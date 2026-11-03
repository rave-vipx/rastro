"""Deterministic pseudo-random streams for reproducible artifacts (MOCK-006).

Streams use SHA-256 in counter mode, so output depends only on the seed, the
namespace and the call order: never on the Python version, ``random`` internals or
the content being replaced. Placeholders therefore leak nothing about the original
text except its length and whitespace layout.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


class DeterministicStream:
    """A reproducible byte stream derived from ``seed`` and a ``namespace``."""

    def __init__(self, seed: int, namespace: str) -> None:
        self._prefix = f"rastro.mocklab:{seed}:{namespace}:".encode()
        self._counter = 0
        self._buffer = b""

    def _bytes(self, count: int) -> bytes:
        while len(self._buffer) < count:
            block = hashlib.sha256(self._prefix + str(self._counter).encode()).digest()
            self._buffer += block
            self._counter += 1
        result, self._buffer = self._buffer[:count], self._buffer[count:]
        return result

    def integer(self, low: int, high: int) -> int:
        """An integer in ``[low, high]``; the tiny modulo bias is irrelevant here."""
        if high < low:
            raise ValueError("high must not be lower than low")
        span = high - low + 1
        return low + int.from_bytes(self._bytes(16)) % span

    def choice[T](self, items: Sequence[T]) -> T:
        if not items:
            raise ValueError("Cannot choose from an empty sequence")
        return items[self.integer(0, len(items) - 1)]

    def letters(self, count: int) -> str:
        return "".join(_LETTERS[byte % len(_LETTERS)] for byte in self._bytes(count))

    def placeholder(self, original: str) -> str:
        """Replace every non-whitespace character, preserving length and whitespace."""
        replacement = iter(self.letters(len(original)))
        return "".join(char if char.isspace() else next(replacement) for char in original)
