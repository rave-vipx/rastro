"""WARNING log line per failed job source: index, host and outcome codes only (OBS, QUAL-005).

Python's last-resort handler prints WARNING records to stderr even without ``--verbose``.
Paths, queries, page content and records are never logged.
"""

import logging
from typing import Any
from urllib.parse import urlsplit

import structlog

from rastro.domain.failure_reason import failure_detail
from rastro.domain.job_results import JobRunResult
from rastro.domain.models import Outcome

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


def host(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def log_failures(report: JobRunResult[Any]) -> None:
    for index, result in enumerate(report.results, start=1):
        if result.status == Outcome.SUCCESS:
            continue
        _LOG.warning(
            "source_failed",
            source_index=index,
            host=host(result.source_url),
            status=result.status.value,
            reason=failure_detail(result),
        )
