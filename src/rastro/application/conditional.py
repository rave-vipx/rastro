"""Per-load conditional request state; validators commit only after successful loads."""

import asyncio
from datetime import datetime
from hashlib import sha256

from rastro.domain.acquisition import HttpResponse
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.models import Source
from rastro.ports.dataset import DatasetValidators


class ConditionalRequests:
    def __init__(self, store: DatasetValidators, dataset: str) -> None:
        self._store, self._dataset = store, dataset
        self.failed = False
        self._pending: dict[str, dict[str, str]] = {}

    async def headers(self, source_url: str, url: str) -> dict[str, str]:
        try:
            values = await asyncio.to_thread(self._store.get_validators, self._dataset, source_url)
        except DatasetStorageError:
            self.failed = True
            return {}
        target = values.get("_target", sha256(source_url.encode()).hexdigest())
        if sha256(url.encode()).hexdigest() != target:
            return {}
        return {
            header: values[key]
            for key, header in (("etag", "If-None-Match"), ("last-modified", "If-Modified-Since"))
            if key in values
        }

    def remember(self, source_url: str, url: str, response: HttpResponse) -> None:
        self._pending[source_url] = {
            key: value
            for key in ("etag", "last-modified")
            if (value := response.headers.get(key))
            and len(value.encode("utf-8", errors="replace")) <= 4096
            and all(32 <= ord(c) < 127 for c in value)
        }
        if source_url != url:
            self._pending[source_url]["_target"] = sha256(url.encode()).hexdigest()
            self._pending[source_url]["_source"] = Source(url=url).evidence_url

    def save(self, source_url: str, observed_at: datetime) -> None:
        if source_url in self._pending:
            self._store.save_validators(
                self._dataset, source_url, self._pending[source_url], observed_at=observed_at
            )
