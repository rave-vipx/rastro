"""README renders on PyPI, so every link and image must be absolute (release docs)."""

import re
from pathlib import Path

_MARKDOWN = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)")
_HTML = re.compile(r"""(?:src|href)=["']([^"']+)""")


def test_readme_has_no_relative_links_or_images() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    targets = _MARKDOWN.findall(text) + _HTML.findall(text)
    assert targets
    relative = [t for t in targets if not t.startswith(("https://", "mailto:"))]
    assert relative == []
