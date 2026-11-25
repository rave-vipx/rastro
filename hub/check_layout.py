"""Refuse anything in the hub that is not the registry layout: executables, links, strays.

python hub/check_layout.py            # stdlib only
"""

import re
import stat
import sys
from pathlib import Path

HUB = Path(__file__).parent
TOP = {"index.json", "health.json", "build.py", "check_layout.py", "README.md", "LICENSE"}
PACKAGE = re.compile(
    r"recipes/[a-z0-9][a-z0-9-]{0,63}/\d+\.\d+\.\d+/"
    r"(bundle\.json|recipe\.json|schema\.json|expected\.json|card\.json|sample\.html|"
    r"sample\.json|\.gitattributes)"
)


def problems(hub: Path) -> list[str]:
    found = []
    for path in sorted(hub.rglob("*")):
        rel = path.relative_to(hub).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            found.append(f"{rel}: symbolic links are not allowed")
        elif stat.S_ISDIR(mode):
            continue
        elif mode & 0o111 and rel not in {"build.py", "check_layout.py"}:
            found.append(f"{rel}: executable files are not allowed")
        elif rel not in TOP and not PACKAGE.fullmatch(rel):
            found.append(f"{rel}: outside the allowed hub layout")
    return found


if __name__ == "__main__":
    issues = problems(HUB)
    print("\n".join(issues), file=sys.stderr)
    sys.exit(1 if issues else 0)
