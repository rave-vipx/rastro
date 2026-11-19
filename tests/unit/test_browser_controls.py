"""Browser controls, HTTP options and host limits: job-file acquisition settings."""

import pytest
from pydantic import ValidationError

from rastro.domain.browser_controls import BrowserControls, HostLimits, HttpOptions


def test_defaults_request_no_capability():
    assert BrowserControls().requested_capabilities() == frozenset()
    assert HttpOptions().requested_capabilities() == frozenset()
    assert BrowserControls().disable_resources is True


def test_each_control_maps_to_its_capability():
    controls = BrowserControls(
        screenshot=True,
        capture_network=(r"^/api/",),
        session_profile="shop",
        real_chrome=True,
        cdp_url_env="RASTRO_CDP_URL",
    )
    assert controls.requested_capabilities() == {
        "screenshots",
        "network_capture",
        "persistent_session",
        "real_chrome",
        "cdp",
    }
    http = HttpOptions(impersonate="chrome", http_version="3")
    assert http.requested_capabilities() == {"impersonation", "http3"}


def test_booleans_and_numbers_are_strict():
    with pytest.raises(ValidationError):
        BrowserControls(screenshot="yes")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HostLimits(max_concurrency="4")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HostLimits(min_interval_seconds=61)
    with pytest.raises(ValidationError):
        HostLimits(max_concurrency=33)
    assert HostLimits(min_interval_seconds=1).min_interval_seconds == 1


def test_cdp_url_is_an_environment_name_never_a_url():
    with pytest.raises(ValidationError) as raised:
        BrowserControls(cdp_url_env="ws://user:secret@127.0.0.1:9222")
    assert "secret" not in str(raised.value)


def test_session_profile_is_a_name_not_a_path():
    for profile in ("../../.ssh", "/home/user/profile", "Shop Profile"):
        with pytest.raises(ValidationError):
            BrowserControls(session_profile=profile)


def test_blocked_domains_are_exact_hosts_and_bounded():
    controls = BrowserControls(blocked_domains=("Ads.Example.",))
    assert controls.blocked_domains == ("ads.example",)
    with pytest.raises(ValidationError):
        BrowserControls(blocked_domains=("https://ads.example/",))
    with pytest.raises(ValidationError):
        BrowserControls(blocked_domains=tuple(f"a{i}.example" for i in range(65)))


def test_capture_patterns_must_compile_and_stay_bounded():
    with pytest.raises(ValidationError):
        BrowserControls(capture_network=("(",))
    with pytest.raises(ValidationError):
        BrowserControls(capture_network=("a" * 257,))
    with pytest.raises(ValidationError):
        BrowserControls(capture_network=tuple(f"^/p{i}" for i in range(9)))


def test_wait_selector_and_vocabularies_are_bounded():
    with pytest.raises(ValidationError):
        BrowserControls(wait_selector="")
    with pytest.raises(ValidationError):
        BrowserControls(wait_state="loaded")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HttpOptions(impersonate="netscape")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HttpOptions(http_version="0.9")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BrowserControls(unknown=True)  # type: ignore[call-arg]
