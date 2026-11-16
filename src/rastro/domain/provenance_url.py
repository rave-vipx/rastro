"""The URL form that provenance may store (N3, SEC-008).

Provenance keeps scheme, host, port and path. Userinfo, ``;params``, query and
fragment can carry credentials, session ids or tokens, so they are never stored;
``page_index`` already identifies the page.
"""

from urllib.parse import urlsplit, urlunsplit


def provenance_url(url: str) -> str:
    """``url`` reduced to scheme, host, port and path; never raises."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            port = None
        netloc = f"{host}:{port}" if port else host
    except ValueError:
        return ""
    path = "/".join(segment.partition(";")[0] for segment in parts.path.split("/"))
    return urlunsplit((parts.scheme, netloc, path, "", ""))
