"""N17: every issue code emitted in src/rastro is documented in docs/issues.md."""

import ast
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
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            found = [_text(k.value) for k in node.keywords if k.arg in {"code", "issue_code"}]
            if name in {"Issue", "AcquisitionError"} and node.args:
                found.append(_text(node.args[0]))
            codes.update(code for code in found if code)
    return codes


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
