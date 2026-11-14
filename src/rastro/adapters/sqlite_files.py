"""Owner-only SQLite files: datasets hold page samples, recipe memory holds selectors.

POSIX only; on Windows the ACLs of the parent folder apply and these calls do nothing.
SQLite gives its ``-journal``, ``-wal`` and ``-shm`` files the database file's mode.
"""

import os
import stat
from contextlib import suppress
from pathlib import Path

PRIVATE = 0o600
_SIDECARS = ("", "-journal", "-wal", "-shm")


def create_private(path: Path) -> None:
    """Create an empty owner-only file for SQLite to initialize; keep an existing one."""
    if os.name == "nt":
        return
    with suppress(FileExistsError):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, PRIVATE))


def restrict(path: Path) -> None:
    """Best effort: drop group and other access from the file and its SQLite sidecars."""
    if os.name == "nt":
        return
    for suffix in _SIDECARS:
        item = path.with_name(path.name + suffix)
        try:
            info = os.lstat(item)
            if stat.S_ISREG(info.st_mode) and info.st_mode & 0o077 and info.st_uid == os.getuid():
                os.chmod(item, PRIVATE)
        except OSError:
            continue
