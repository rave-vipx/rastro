"""What one transfer asks for: the job's HTTP options and the media types it accepts."""

from __future__ import annotations

from email.message import Message
from typing import TypedDict

from rastro.domain.acquisition import AcquisitionError, AcquisitionPolicy, HttpResponse
from rastro.domain.models import Outcome
from rastro.domain.source_notes import BrowserFamily, HttpVersion

__all__ = ["XML_TYPES", "HttpTransferOptions", "check_media", "http_options"]

# A sitemap is XML, plain or gzipped (`.xml.gz`); the caller inflates it under a cap.
XML_TYPES = ("application/xml", "text/xml", "application/gzip", "application/x-gzip")


class HttpTransferOptions(TypedDict, total=False):
    impersonate: BrowserFamily
    http_version: HttpVersion


def http_options(policy: AcquisitionPolicy) -> HttpTransferOptions:
    """The transport keywords for the job's HTTP options; only the ones the job sets, so a
    transport written before them keeps working, and an impersonated browser keeps its own
    protocol unless the job names a version."""
    http = policy.http
    options: HttpTransferOptions = {}
    if http.impersonate != "off":
        options["impersonate"] = http.impersonate
    if "http_version" in http.model_fields_set:
        options["http_version"] = http.http_version
    return options


def check_media(response: HttpResponse, media: tuple[str, ...]) -> None:
    """Refuse a response whose content type is not one of ``media`` (when any is named)."""
    if not media:
        return
    parsed = Message()
    parsed["content-type"] = response.headers.get("content-type", "")
    if parsed.get_content_type() not in media:
        raise AcquisitionError("unsupported_content_type", Outcome.PERMANENT_FAILURE)
