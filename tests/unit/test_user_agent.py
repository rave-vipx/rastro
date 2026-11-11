"""ACQ-*: the User-Agent carries the installed package version."""

from importlib import metadata

from rastro.application import http_policy


def test_user_agent_uses_the_installed_version() -> None:
    assert http_policy.USER_AGENT.startswith(f"Rastro/{metadata.version('rastro-engine')} ")


def test_user_agent_falls_back_when_metadata_is_missing(monkeypatch) -> None:
    def missing(name: str) -> str:
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    assert http_policy._version() == "0"
