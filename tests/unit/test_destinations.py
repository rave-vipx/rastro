import pytest
from pydantic import ValidationError

from rastro import AcquisitionPolicy
from rastro.application.destinations import permitted_url, pin_destination, public_address
from rastro.domain.acquisition import AcquisitionError


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "0.0.0.0",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "224.0.0.1",
        "192.0.2.1",
        "::1",
        "fc00::1",
        "fe80::1%en0",
        "::ffff:8.8.8.8",
        "64:ff9b::808:808",
        "64:ff9b:1::1",
        "2002:808:808::1",
        "2001::1",
        "ff02::1",
        "not-an-ip",
    ],
)
def test_private_reserved_and_transition_addresses_are_denied(address):
    with pytest.raises(AcquisitionError, match="destination_denied"):
        public_address(address)


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses(address):
    assert public_address(address) == address


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("https://other.test/", "host_not_allowed"),
        ("http://example.com/", "https_required"),
        ("https://example.com:8443/", "port_not_allowed"),
        ("file:///etc/passwd", "invalid_url"),
        ("https://user:secret@example.com/", "invalid_url"),
    ],
)
def test_url_policy(url, code):
    with pytest.raises(AcquisitionError, match=code):
        permitted_url(url, AcquisitionPolicy(allowed_hosts=("example.com",)))


def test_policy_validation_and_canonicalization():
    policy = AcquisitionPolicy(allowed_hosts=("EXAMPLE.COM.", "example.com"), allow_http=True)
    assert policy.allowed_hosts == ("example.com",)
    assert permitted_url("http://example.com:80/a?q=1#x", policy) == (
        "http://example.com/a?q=1",
        "example.com",
        80,
    )
    assert AcquisitionPolicy(allowed_hosts=("8.8.8.8",)).allowed_hosts == ("8.8.8.8",)
    for host in ("*.example.com", "https://example.com", "a:443", "bad..test", "x" * 254):
        with pytest.raises(ValidationError):
            AcquisitionPolicy(allowed_hosts=(host,))
    with pytest.raises(ValidationError):
        AcquisitionPolicy(allowed_hosts=("example.com",), operation_timeout_seconds=1)


@pytest.mark.asyncio
async def test_mixed_and_empty_dns_answers():
    class Resolver:
        addresses = ("8.8.8.8", "127.0.0.1")

        async def resolve(self, host, port):
            return self.addresses

    resolver = Resolver()
    policy = AcquisitionPolicy(allowed_hosts=("example.com",))
    with pytest.raises(AcquisitionError, match="destination_denied"):
        await pin_destination("https://example.com", policy, resolver)
    resolver.addresses = ()
    with pytest.raises(AcquisitionError, match="dns_failure"):
        await pin_destination("https://example.com", policy, resolver)
    for encoded in ("127.1", "2130706433", "0x7f000001"):
        with pytest.raises(AcquisitionError, match="destination_denied"):
            await pin_destination(
                "https://" + encoded, AcquisitionPolicy(allowed_hosts=("127.0.0.1",)), resolver
            )
    target = await pin_destination(
        "https://[2606:4700:4700::1111]/",
        AcquisitionPolicy(allowed_hosts=("2606:4700:4700::1111",)),
        resolver,
    )
    assert target.address == "2606:4700:4700::1111"
