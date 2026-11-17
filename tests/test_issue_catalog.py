"""N17: every issue code emitted in src/rastro is documented in docs/issues.md."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "issues.md"
# Codes built at run time (f-strings, stop reasons); each is listed here and in the doc.
DERIVED = {
    "scroll_max_steps",
    "scroll_budget",
    "scroll_step_failed",
    "scroll_records_rejected",
    "scroll_record_limit",
    "scroll_ignored_http",
    "scroll_unsupported_stealth",
    "pagination_max_pages",
    "pagination_budget",
    "pagination_invalid_next",
    "pagination_off_host",
    "pagination_https_downgrade",
    "pagination_page_failure",
    "pagination_record_limit",
    "spider_budget",
    "spider_page_limit",
    "spider_depth_limit",
    "spider_page_failed",
}


def _text(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def emitted_codes() -> set[str]:
    codes: set[str] = set()
    for path in (ROOT / "src" / "rastro").rglob("*.py"):
        if "studio" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):  # CODE = "..." / *_CODE = "..." constants
                targets = [getattr(t, "id", "") for t in node.targets]
                if any(t == "CODE" or t.endswith("_CODE") for t in targets):
                    codes.update(c for c in [_text(node.value)] if c and _CODE_SHAPE.match(c))
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            found = [_text(k.value) for k in node.keywords if k.arg in {"code", "issue_code"}]
            if name in {"Issue", "AcquisitionError"} and node.args:
                found.append(_text(node.args[0]))
            if name in _CODE_CALLS:  # positional code arguments, e.g. fail(outcome, "code")
                found.extend(c for c in map(_text, node.args) if c and _CODE_SHAPE.match(c))
            codes.update(code for code in found if code)
    return codes


# Calls whose positional string arguments are issue codes (N16 sitemap, N19 flags).
_CODE_CALLS = {"AcquisitionResult", "SitemapError", "fail"}
_CODE_SHAPE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")


def documented(text: str) -> set[str]:
    rows = (line.split("|") for line in text.splitlines() if line.startswith("| `"))
    return {cells[1].strip().strip("`") for cells in rows}


def missing(text: str) -> list[str]:
    return sorted((emitted_codes() | DERIVED) - documented(text))


def test_every_emitted_issue_code_is_documented():
    assert missing(DOC.read_text(encoding="utf-8")) == []


def test_the_check_fails_when_a_row_is_removed():
    text = DOC.read_text(encoding="utf-8")
    kept = "\n".join(line for line in text.splitlines() if not line.startswith("| `robots_denied`"))
    assert missing(kept) == ["robots_denied"]


def test_the_runtime_allowlist_matches_the_documented_codes():
    """Studio's code allowlist (review of #147) is exactly the documented vocabulary."""
    from rastro.domain.issue_codes import ISSUE_CODES

    assert documented(DOC.read_text(encoding="utf-8")) == ISSUE_CODES
