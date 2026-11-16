"""The job's `[scroll]` table: a bounded scroll for browser sources (N9, ADR 0033)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt

from rastro.domain.job_contract import RedactedContract


class ScrollSpec(RedactedContract):
    """Scroll a rendered page until a step finds no new records, or a cap is reached.

    Scroll applies to ``render = "browser"``. It never enables stealth or challenge solving.
    HTTP sources ignore it and say so (``scroll_ignored_http``); so do stealth sources
    (``scroll_unsupported_stealth``): Scrapling keeps the page pooled, but Rastro does not
    drive scroll in it. Both stay successful runs.
    """

    max_steps: StrictInt = Field(default=10, ge=1, le=30)
    settle_ms: StrictInt = Field(default=500, ge=100, le=3_000)
    stop_when: Literal["no_new_records"] = "no_new_records"
