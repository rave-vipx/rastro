"""Size checks shared by mutation inputs and outputs."""

from __future__ import annotations

from rastro.domain.sources import Limits, Representation, ResourceLimitError
from rastro.mocklab.errors import MockLabError


def utf8_size(text: str) -> int:
    """Encoded size; a lone surrogate cannot be written as UTF-8 and is refused."""
    try:
        return len(text.encode())
    except UnicodeEncodeError:
        raise MockLabError("Document contains a lone surrogate and is not valid UTF-8") from None


def check_bytes(text: str, representation: Representation) -> None:
    """Raise ``ResourceLimitError`` when ``text`` exceeds the default ``Limits``."""
    if utf8_size(text) > Limits().max_input_bytes(representation):
        raise ResourceLimitError(f"{representation.upper()} document exceeds the byte limit")
