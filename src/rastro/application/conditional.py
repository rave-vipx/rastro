"""Per-load conditional request state; validators commit only after successful loads."""

import asyncio
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256

from rastro.domain.acquisition import HttpResponse
from rastro.domain.dataset import DatasetStorageError
from rastro.domain.models import Source
from rastro.ports.dataset import DatasetValidators


class ConditionalRequests:
    def __init__(self, store: DatasetValidators, dataset: str, inputs: Mapping[str, str]) -> None:
        """``inputs`` maps each source URL to its extraction-inputs digest (SYNC-004)."""
        self._store, self._dataset, self._inputs = store, dataset, dict(inputs)
        self.failed = False
        self._pending: dict[str, dict[str, str]] = {}

    async def headers(self, source_url: str, url: str) -> dict[str, str]:
        if source_url not in self._inputs:
            return {}  # revalidation is off for this source (for example, it paginates)
        try:
            values = await asyncio.to_thread(self._store.get_validators, self._dataset, source_url)
        except DatasetStorageError:
            self.failed = True
            return {}
        if self._inputs.get(source_url) != values.get("_inputs"):
            return {}  # the recipe, schema or job changed: a 304 would hide the change
        target = values.get("_target", sha256(source_url.encode()).hexdigest())
        if sha256(url.encode()).hexdigest() != target:
            return {}
        return {
            header: values[key]
            for key, header in (("etag", "If-None-Match"), ("last-modified", "If-Modified-Since"))
            if key in values
        }

    def remember(self, source_url: str, url: str, response: HttpResponse) -> None:
        if source_url not in self._inputs:
            return
        self._pending[source_url] = {
            key: value
            for key in ("etag", "last-modified")
            if (value := response.headers.get(key))
            and len(value.encode("utf-8", errors="replace")) <= 4096
            and all(32 <= ord(c) < 127 for c in value)
        }
        self._pending[source_url]["_inputs"] = self._inputs[source_url]
        if source_url != url:
            self._pending[source_url]["_target"] = sha256(url.encode()).hexdigest()
            self._pending[source_url]["_source"] = Source(url=url).evidence_url

    def save(self, source_url: str, observed_at: datetime) -> None:
        if source_url in self._pending:
            self._store.save_validators(
                self._dataset, source_url, self._pending[source_url], observed_at=observed_at
            )
