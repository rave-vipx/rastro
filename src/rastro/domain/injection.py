"""Flag instruction-like text in untrusted source values (N19, SEC-008, AI-001, REPAIR-002).

Page content may address the model or agent that later reads it ("ignore previous
instructions", a fake ``<|im_start|>system`` turn, a call to ``recipe_promote``). Rastro
never drops or rewrites such values: they may be real data. It flags them so model
contexts and MCP payloads can label them, and so automatic promotion can refuse them.

Rules are conservative (an ordinary event, product or news text in Spanish or English
does not match) and bounded: at most ``MAX_SCAN_CHARS`` per value are scanned, with
linear patterns only. A flag names the field and the rule ids, never the matched text.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence

CODE = "instruction_like_content"
MAX_SCAN_CHARS = 64 * 1024
# Every Rastro MCP tool name, plus the gate-override flag; kept in sync by a test.
TOOL_NAMES = (
    "browser_extract",
    "browser_network",
    "browser_screenshot",
    "browser_snapshot",
    "dataset_records",
    "drift_status",
    "job_extract",
    "job_load",
    "job_new",
    "job_preview",
    "job_run",
    "job_validate",
    "override_gates",
    "recipe_apply",
    "recipe_history",
    "recipe_list",
    "recipe_promote",
    "recipe_propose",
    "recipe_repair",
    "recipe_try",
    "repair_context",
    "scrape_file",
    "semantic_review",
    "suggest_context",
    "suggest_recipe",
)
_QUALIFIER = r"(?:previous|prior|above|earlier|preceding|system|original|all)"
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "role_tag",  # chat-template turns and role headers a model may obey
        re.compile(
            r"<\|(?:im_start|im_end|system|user|assistant|endoftext)\|>|\[/?INST\]|<</?SYS>>"
            r"|</?(?:system|assistant)>"
            # possessive whitespace: a near-miss header cannot backtrack quadratically
            r"|^[ \t]*+#{1,6}+[ \t]*+(?:system|assistant)(?:[ \t]++(?:prompt|message))?+"
            r"[ \t]*+(?::[ \t]*+)?$",
            re.I | re.M,
        ),
    ),
    (
        "ignore_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget)\s+(?:(?:all|any)\s+(?:of\s+)?)?(?:(?:the|your|my)\s+)?"
            rf"{_QUALIFIER}\s+(?:instructions|prompts?|directives|rules)\b"
            r"|\b(?:ignore|disregard|forget)\s+(?:all|any|your)\s+(?:instructions|prompts?)\b"
            r"|\b(?:ignora|olvida|descarta)\s+(?:todas\s+(?:las\s+)?|tus\s+)"
            r"(?:instrucciones|indicaciones)\b"
            r"|\b(?:instrucciones|indicaciones)\s+(?:anteriores|previas)\b",
            re.I,
        ),
    ),
    (
        "role_assignment",
        re.compile(
            r"\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|assistant|chatbot|language\s+model|llm)\b"
            r"|\b(?:ahora\s+)?eres\s+(?:un\s+|una\s+)?(?:ia|asistente|chatbot|modelo\s+de\s+lenguaje)\b",
            re.I,
        ),
    ),
    ("system_prompt", re.compile(r"\bsystem\s+prompt\b|\bprompt\s+del?\s+sistema\b", re.I)),
    ("tool_reference", re.compile(r"\b(?:" + "|".join(TOOL_NAMES) + r")\b", re.I)),
    (
        "exfiltration_link",  # markdown image/link whose URL carries query data
        re.compile(r"!?\[[^\]\n]{0,200}\]\(\s*<?https?://[^\s)?]{1,300}\?[^\s)]{0,1000}="),
    ),
)
_BLOB = re.compile(r"[A-Za-z0-9+/_-]{100,}={0,2}")
_TAG_RUN = re.compile("[\U000e0020-\U000e007f]{8,}")
_TAG_TO_ASCII = {code: code - 0xE0000 for code in range(0xE0020, 0xE007F)}
_TAG_CHARS = re.compile("[\U000e0020-\U000e007e]+")
_SPACES = {ord("\n"): " ", ord("\r"): " ", ord("\t"): " "}


def instruction_like(text: str) -> tuple[str, ...]:
    """The ids of the rules ``text`` matches, in rule order; ``()`` for ordinary text."""
    if not isinstance(text, str) or not text:
        return ()
    head = text[:MAX_SCAN_CHARS]
    scanned = _prepare(head)
    found = [rule for rule, pattern in _RULES if pattern.search(scanned)]
    if any(_looks_encoded(match.group()) for match in _BLOB.finditer(scanned)):
        found.append("encoded_blob")
    if _TAG_RUN.search(head):  # longer than any subdivision-flag tag sequence
        found.append("unicode_tag_text")
    return tuple(found)


def value_flags(record: Mapping[str, object]) -> list[dict[str, object]]:
    """One flag per field whose value looks like instructions; values are not altered."""
    flags: list[dict[str, object]] = []
    for name, value in record.items():
        if value is None or isinstance(value, bool | int | float):
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if rules := instruction_like(text):
            flags.append({"field": str(name), "code": CODE, "rules": list(rules)})
    return flags


def record_flags(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """``value_flags`` for each row, with its zero-based ``record`` index."""
    return [
        {"record": index, **flag}
        for index, row in enumerate(rows)
        if isinstance(row, Mapping)
        for flag in value_flags(row)
    ]


def _prepare(text: str) -> str:
    """Reveal tag-smuggled ASCII, fold compatibility forms, drop invisible characters."""
    text = _TAG_CHARS.sub(lambda run: f" {run.group().translate(_TAG_TO_ASCII)} ", text)
    text = unicodedata.normalize("NFKC", text.translate(_SPACES))
    return "".join(c for c in text if unicodedata.category(c) not in {"Cc", "Cf"})


def _looks_encoded(run: str) -> bool:
    """Mixed case and digits with few slashes: an encoded payload, not a path or hex id."""
    return (
        any(c.isupper() for c in run)
        and any(c.islower() for c in run)
        and any(c.isdigit() for c in run)
        and run.count("/") * 16 < len(run)
    )
