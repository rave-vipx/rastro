"""Load and verify recovery benchmark corpora against the committed manifest.

This module is the only supported reader. It checks bytes against ``manifest.json``
before parsing, and refuses the sealed split unless the caller confirms it is running
the one-shot evaluation described in ``docs/recovery-benchmark.md``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parent
Split = Literal["development", "sealed"]


class CorpusIntegrityError(RuntimeError):
    """The committed corpus bytes differ from the recorded hash."""


class SealedSplitError(RuntimeError):
    """The sealed split was requested without explicit confirmation."""


@dataclass(frozen=True)
class Corpus:
    split: str
    sha256: str
    document: dict[str, Any]

    @property
    def scenarios(self) -> list[dict[str, Any]]:
        return list(self.document["scenarios"])


def scenario_hash(scenario: dict[str, Any]) -> str:
    """SHA-256 of the scenario's canonical JSON (sorted keys, compact separators)."""
    canonical = json.dumps(scenario, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_split(split: Split, *, confirm: bool = False, path: Path | None = None) -> Corpus:
    if split == "sealed" and not confirm:
        raise SealedSplitError("the sealed split is for the one-shot release evaluation only")
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["splits"][split]
    data = (path or ROOT.parent.parent / entry["path"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise CorpusIntegrityError(f"{split} corpus hash {digest} != recorded {entry['sha256']}")
    return Corpus(split, digest, json.loads(data))
