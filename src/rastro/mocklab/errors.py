"""Explicit Mock Lab failures. Messages never contain page content or secret values."""

from rastro.domain.models import RastroError


class MockLabError(RastroError):
    """A fixture cannot be generated, loaded or replayed as requested."""


class SanitizationError(MockLabError):
    """Sanitization could not prove an artifact free of sensitive data (fail-closed)."""


class FixtureMismatchError(MockLabError):
    """A fixture does not reproduce its expected extraction offline."""
