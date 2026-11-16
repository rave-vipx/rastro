"""The running rastro-engine version, as ``MAJOR.MINOR.PATCH`` (HUB-002)."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version

from rastro.domain.models import ConfigurationError
from rastro.domain.recipe_bundle import SEMVER_PATTERN

DISTRIBUTION = "rastro-engine"


def running_version() -> str:
    """Installed release without pre-release or local suffixes (``0.8.0.dev1`` is 0.8.0)."""
    try:
        raw = pkg_version(DISTRIBUTION)
    except PackageNotFoundError as error:
        raise ConfigurationError(
            f"Cannot determine the running version: {DISTRIBUTION} is not installed as a package"
        ) from error
    match = re.match(r"^\d+\.\d+\.\d+", raw)
    if match is None or not re.fullmatch(SEMVER_PATTERN, match.group(0)):
        raise ConfigurationError(f"Cannot determine the running {DISTRIBUTION} version")
    return match.group(0)
