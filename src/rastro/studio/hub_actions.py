"""Hub actions behind Studio's registry routes: the per-site decision and one-step publish.

A decision to allow a capability for one site is held in memory for this Studio run only and is
never written to a job or a file; the grant-only capabilities cannot be allowed from the page.
``site_scope`` applies it: registry input for a job whose every URL is on an allowed site gets
those capabilities while Studio installs or runs that job; other sites and origins get nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from weakref import WeakKeyDictionary

from rastro.application.hub_publish import PackageInfo, publish_job_to_hub
from rastro.application.hub_search import url_domain
from rastro.application.operator_trust import operator_trust
from rastro.cli_hub import _parser_for as parser_for
from rastro.domain.capability_grants import CAPABILITIES, GRANT_ONLY, Capability
from rastro.domain.jobs import Job
from rastro.domain.models import ConfigurationError
from rastro.studio.web import Refused, Studio, integer, optional_text, plain, text

_HOST = re.compile(r"[A-Za-z0-9.-]+(:\d{1,5})?")
_SITE_GRANTS: WeakKeyDictionary[Studio, dict[str, frozenset[str]]] = WeakKeyDictionary()


def site_grants(studio: Studio) -> dict[str, frozenset[str]]:
    """Capabilities the operator allowed for hub recipes, by site, during this run."""
    return dict(_SITE_GRANTS.get(studio, {}))


def record_decision(studio: Studio, decision: tuple[str, frozenset[str]] | None) -> None:
    if decision is not None:
        site, names = decision
        held = _SITE_GRANTS.setdefault(studio, {})
        held[site] = held.get(site, frozenset()) | names


def _job_sites(job: Job) -> set[str]:
    urls = [getattr(source, "url", None) for source in job.sources]
    try:
        return {url_domain(url) for url in urls if url}
    except ConfigurationError:
        return set()  # a URL with no site cannot match a decision


@contextmanager
def site_scope(
    studio: Studio, job: Job, decision: tuple[str, frozenset[str]] | None = None
) -> Iterator[None]:
    """Grant registry input the capabilities allowed for every site ``job`` reads."""
    held = site_grants(studio)
    if decision is not None:
        held[decision[0]] = held.get(decision[0], frozenset()) | decision[1]
    sites = _job_sites(job)
    allowed = frozenset.intersection(*(held.get(s, frozenset()) for s in sites)) if sites else None
    granted = cast(frozenset[Capability], allowed or frozenset())  # names checked on record
    with operator_trust(untrusted={"registry": granted} if granted else None):
        yield


def decision_from(data: dict[str, Any]) -> tuple[str, frozenset[str]] | None:
    """The page's "allow only for this site" choice: known, non-grant-only names and a host."""
    allow = data.get("allow", [])
    if not isinstance(allow, list) or not all(isinstance(name, str) for name in allow):
        raise Refused(400, "invalid_allow")
    if not allow:
        return None
    if not set(allow) <= set(CAPABILITIES) or set(allow) & GRANT_ONLY:
        raise Refused(400, "invalid_allow")
    site = text(data, "site").strip()
    if "://" not in site and not _HOST.fullmatch(site):
        raise Refused(400, "invalid_site")
    return url_domain(site if "://" in site else f"https://{site}"), frozenset(allow)


def publish_job(
    studio: Studio, registry: Path, resolve: Callable[[Path, str], Path], data: dict[str, Any]
) -> dict[str, Any]:
    """One-step publish into a local hub folder: a proposal, never a push."""
    job_path = studio.job_path(text(data, "file"))
    sample = resolve(studio.root, text(data, "sample"))
    if not sample.is_file():
        raise Refused(400, "sample_required")
    notes = data.get("descriptions")
    if not isinstance(notes, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in notes.items()
    ):
        raise Refused(400, "invalid_descriptions")
    package = PackageInfo(
        id=text(data, "id"),
        version=text(data, "version"),
        author=text(data, "author"),
        license=text(data, "license"),
        author_url=optional_text(data, "author_url"),
    )
    done = publish_job_to_hub(
        job_path,
        registry,
        parser_for,
        source_index=integer(data, "source", 1, 999, 1),
        sample=sample,
        package=package,
        descriptions=notes,
        today=datetime.now(UTC).date(),
    )
    return {
        "ok": True,
        "entry": plain(done.published.entry),
        "card": plain(done.card),
        "pushed": False,
        "next_steps": list(done.published.git_commands),
    }
