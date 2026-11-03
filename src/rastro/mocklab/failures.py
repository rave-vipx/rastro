"""Deterministic synthetic HTTP failures for MOCK-003 and MOCK-004."""

from dataclasses import dataclass
from urllib.parse import urlsplit

from rastro.domain.acquisition import HttpResponse, PinnedTarget, ProxyRoute


@dataclass(frozen=True)
class FailureScenario:
    """A labeled, reusable synthetic response or timeout."""

    name: str
    status: int = 200
    body: bytes = b""
    headers: tuple[tuple[str, str], ...] = ()
    timeout: bool = False
    synthetic: bool = True

    def build_transport(self) -> "FailureTransport":
        return FailureTransport(self)


@dataclass(frozen=True)
class FailureTransport:
    scenario: FailureScenario

    async def get(
        self,
        target: PinnedTarget,
        *,
        headers: dict[str, str],
        timeout: float,
        max_bytes: int,
        proxy: ProxyRoute | None,
    ) -> HttpResponse:
        if urlsplit(target.url).path == "/robots.txt":
            return HttpResponse(404, {}, b"", 0)
        if self.scenario.timeout:
            raise TimeoutError
        body = self.scenario.body
        return HttpResponse(
            self.scenario.status,
            {"content-type": "text/html", **dict(self.scenario.headers)},
            body,
            len(body),
        )


SCENARIOS = (
    FailureScenario("http_403", status=403),
    FailureScenario("http_429", status=429, headers=(("retry-after", "60"),)),
    FailureScenario("timeout", timeout=True),
    FailureScenario("empty_page"),
    FailureScenario(
        "invalid_structured_response", body=b'<script type="application/ld+json">{</script>'
    ),
    FailureScenario(
        "missing_required_fields",
        body=b'<script type="application/ld+json">{"name":"Synthetic"}</script>',
    ),
)
