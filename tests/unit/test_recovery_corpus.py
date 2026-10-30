"""Verify the frozen recovery benchmark corpus (DIFF-001, REPAIR-001..006, DRIFT-001..005).

Offline. No executor or repair code exists; the reference extractor below is a deliberately
strict, selector-only oracle that checks the corpus labels are internally true.
"""

import hashlib
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pytest
from scrapling.parser import Selector

from benchmarks.recovery import corpus as recovery_corpus

ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / "benchmarks" / "recovery"
FIELDS = ("sku", "name", "price", "priceCurrency")
KINDS = {
    "class_rename",
    "wrapper_insertion",
    "reordering",
    "field_move",
    "duplicated_candidate",
    "removed_field",
    "semantic_trap",
    "empty_container",
}
STATES = {"correct", "partial_expected", "must_not_succeed"}
SPLITS = {"development": RECOVERY / "corpus.json", "sealed": RECOVERY / "sealed" / "corpus.json"}


def _bytes(split: str) -> bytes:
    return SPLITS[split].read_bytes()


def _key(record: dict[str, str]) -> tuple[str, str, Decimal, str]:
    return (record["sku"], record["name"], Decimal(record["price"]), record["priceCurrency"])


def _reference(html: str, integration: dict[str, Any]) -> Counter[tuple[str, str, Decimal, str]]:
    """Strict selector run: ambiguous, missing or invalid fields reject the record."""
    page = Selector(html, adaptive=False, huge_tree=False)
    found: Counter[tuple[str, str, Decimal, str]] = Counter()
    for node in page.css(integration["record_selector"]):
        row: dict[str, str] = {}
        for field, selector in integration["fields"].items():
            matches = node.css(selector).getall()
            if len(matches) == 1 and str(matches[0]).strip():
                row[field] = str(matches[0]).strip()
        if row.keys() != set(FIELDS) or row["priceCurrency"] not in {"USD", "EUR", "UYU"}:
            continue
        try:
            found[_key(row)] += 1
        except InvalidOperation:
            continue
    return found


def _expected(epoch: dict[str, Any]) -> Counter[tuple[str, str, Decimal, str]]:
    return Counter(_key(record) for record in epoch["expected"])


def _scenarios() -> list[tuple[str, dict[str, Any]]]:
    return [
        (split, scenario) for split in SPLITS for scenario in json.loads(_bytes(split))["scenarios"]
    ]


@pytest.mark.parametrize("split", SPLITS)
def test_recorded_hashes_match_committed_files(split: str) -> None:
    manifest = json.loads((RECOVERY / "manifest.json").read_text(encoding="utf-8"))
    entry = manifest["splits"][split]
    assert entry["path"] == SPLITS[split].relative_to(ROOT).as_posix()
    assert entry["sha256"] == hashlib.sha256(_bytes(split)).hexdigest()
    document = json.loads(_bytes(split))
    for scenario in document["scenarios"]:
        assert entry["scenarios"][scenario["id"]] == recovery_corpus.scenario_hash(scenario)
    assert set(entry["scenarios"]) == {s["id"] for s in document["scenarios"]}


@pytest.mark.parametrize("split", SPLITS)
def test_protocol_document_records_the_same_hashes(split: str) -> None:
    protocol = (ROOT / "docs" / "recovery-benchmark.md").read_text(encoding="utf-8")
    assert hashlib.sha256(_bytes(split)).hexdigest() in protocol


def test_hashes_are_stable_across_line_endings_policy() -> None:
    assert b"\r" not in _bytes("development") + _bytes("sealed")
    attributes = (RECOVERY / ".gitattributes").read_text(encoding="utf-8")
    assert "-text" in attributes


def test_loader_rejects_a_tampered_split(tmp_path: Path) -> None:
    tampered = tmp_path / "corpus.json"
    tampered.write_bytes(_bytes("development").replace(b"Kettle", b"Kettlf", 1))
    with pytest.raises(recovery_corpus.CorpusIntegrityError):
        recovery_corpus.load_split("development", path=tampered)
    assert recovery_corpus.load_split("development").split == "development"
    assert recovery_corpus.load_split("sealed", confirm=True).split == "sealed"


def test_sealed_split_requires_explicit_confirmation() -> None:
    with pytest.raises(recovery_corpus.SealedSplitError):
        recovery_corpus.load_split("sealed")


def test_corpus_shape_and_labels() -> None:
    ids: set[str] = set()
    for split, scenario in _scenarios():
        assert scenario["id"] not in ids
        ids.add(scenario["id"])
        assert scenario["id"].startswith("dev-" if split == "development" else "sealed-")
        assert scenario["url"].startswith("https://") and ".example/" in scenario["url"]
        epochs = scenario["epochs"]
        assert [e["epoch"] for e in epochs] == list(range(len(epochs)))
        assert len(epochs) >= 6
        starts = [e["epoch"] for e in epochs if e["episode_start"]]
        assert starts and starts[0] >= 2  # two healthy epochs of history precede any change
        assert not any(e["change_kinds"] or e["breaks_integration"] for e in epochs[:2])
        for e in epochs:
            assert set(e["change_kinds"]) <= KINDS
            assert e["state"] in STATES
            if e["episode_start"]:
                assert e["change_kinds"] and e["breaks_integration"]
            if e["state"] != "correct":
                assert e["requires_warning"] and not e["repair_possible"]
            if e["trap"]:
                assert e["requires_warning"] and e["state"] == "correct" and e["repair_possible"]
            assert (e["oracle_integration"] is not None) == e["repair_possible"]
            assert "<script" not in e["html"].lower()
            assert e["html"].count("<html>") == 1


def test_corpus_covers_every_mutation_kind_in_each_split() -> None:
    for split in SPLITS:
        kinds = {
            kind
            for s_split, s in _scenarios()
            if s_split == split
            for e in s["epochs"]
            for kind in e["change_kinds"]
        }
        assert kinds == KINDS, split


def test_labels_are_true_for_the_reference_extractor() -> None:
    for _, scenario in _scenarios():
        initial = scenario["initial_integration"]
        carried = initial
        for e in scenario["epochs"]:
            expected = _expected(e)
            if e["repair_possible"]:
                assert _reference(e["html"], e["oracle_integration"]) == expected, (
                    scenario["id"],
                    e["epoch"],
                )
            if e["epoch"] == 0:
                assert _reference(e["html"], initial) == expected
            breaks = _reference(e["html"], initial) != expected or e["state"] != "correct"
            assert breaks == e["breaks_integration"], (scenario["id"], e["epoch"])
            if e["episode_start"] and e["state"] == "correct":
                assert _reference(e["html"], carried) != expected, (scenario["id"], e["epoch"])
            if e["state"] == "partial_expected":
                assert _reference(e["html"], carried) == expected
            if e["state"] == "must_not_succeed":
                assert not _reference(e["html"], carried)
            if e["repair_possible"]:
                carried = e["oracle_integration"]


def test_harmless_epochs_exist_so_false_alarms_are_measurable() -> None:
    for split in SPLITS:
        harmless = [
            e
            for s_split, s in _scenarios()
            if s_split == split
            for e in s["epochs"]
            if e["change_kinds"] and not e["episode_start"] and e["state"] == "correct"
        ]
        assert len(harmless) >= 2, split


def test_split_sizes_are_the_frozen_protocol_sizes() -> None:
    for split, episodes, recoverable, unrecoverable, traps in (
        ("development", 10, 8, 2, 1),
        ("sealed", 12, 8, 4, 2),
    ):
        epochs = [
            e
            for s_split, s in _scenarios()
            if s_split == split
            for e in s["epochs"]
            if e["episode_start"]
        ]
        assert len(epochs) == episodes
        assert sum(e["repair_possible"] for e in epochs) == recoverable
        assert sum(not e["repair_possible"] for e in epochs) == unrecoverable
        assert sum(e["trap"] for e in epochs) == traps
        assert sum(len(s["epochs"]) for s_split, s in _scenarios() if s_split == split) == 36


def test_development_code_does_not_reference_the_sealed_split() -> None:
    for path in (ROOT / "src").rglob("*.py"):
        assert "recovery/sealed" not in path.read_text(encoding="utf-8"), path
