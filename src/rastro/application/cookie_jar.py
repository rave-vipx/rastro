"""Run-local, bounded host-only cookies (SCHED-005, SEC-005)."""

from email.utils import parsedate_to_datetime
from http.cookies import CookieError, SimpleCookie
from time import time
from urllib.parse import urlsplit


class CookieJar:
    def __init__(self) -> None:
        self._hosts: dict[str, dict[tuple[str, str], tuple[str, bool, float | None]]] = {}
        self.dropped = 0

    def receive(self, url: str, values: str) -> None:
        if not values:
            return
        parsed = urlsplit(url)
        cookies = self._hosts.setdefault(parsed.hostname or "", {})
        for value in values.split("\n"):
            if len(value.encode("utf-8", errors="replace")) > 4096 or any(
                ord(c) < 32 or ord(c) == 127 for c in value
            ):
                self.dropped += 1
                continue
            try:
                incoming = SimpleCookie()
                incoming.load(value)
                if len(incoming) != 1:
                    raise CookieError()
                name, cookie = next(iter(incoming.items()))
                path = cookie["path"] or parsed.path.rpartition("/")[0] or "/"
                if not path.startswith("/"):
                    path = "/"
                key = (name, path)
                expires = None
                if cookie["max-age"]:
                    expires = time() + int(cookie["max-age"])
                elif cookie["expires"]:
                    expires = parsedate_to_datetime(cookie["expires"]).timestamp()
                self._prune(cookies)
                if expires is not None and expires <= time():
                    cookies.pop(key, None)
                elif key not in cookies and len(cookies) >= 50:
                    self.dropped += 1
                else:
                    # Domain is deliberately ignored: only the response host owns the cookie.
                    cookies[key] = (cookie.coded_value, bool(cookie["secure"]), expires)
            except (CookieError, ValueError, OverflowError, TypeError):
                self.dropped += 1

    @staticmethod
    def _prune(cookies: dict[tuple[str, str], tuple[str, bool, float | None]]) -> None:
        for key, (_, _, expiry) in tuple(cookies.items()):
            if expiry is not None and expiry <= time():
                del cookies[key]

    def header(self, url: str) -> str:
        parsed = urlsplit(url)
        cookies = self._hosts.get(parsed.hostname or "", {})
        self._prune(cookies)
        path = parsed.path or "/"
        return "; ".join(
            f"{name}={value}"
            for (name, scope), (value, secure, _) in cookies.items()
            if (not secure or parsed.scheme == "https")
            and (path == scope or path.startswith(scope.rstrip("/") + "/"))
        )
