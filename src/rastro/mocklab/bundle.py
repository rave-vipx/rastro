"""The versioned fixture bundle: manifest, sanitized body, expected result, synthetic records.

Bundles render to fixed file names, so a manifest never chooses filesystem paths.
Rendering is canonical (no timestamps, stable key order), so equal bundles are
byte-identical on disk (MOCK-006). Loading checks digests and every file's label.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, JsonValue, ValidationError

from rastro.domain.models import ExtractionResult, Issue, Outcome, Recipe, Representation, Source
from rastro.domain.sources import Contract
from rastro.mocklab.errors import MockLabError
from rastro.mocklab.labels import (
    SYNTHETIC_KEY,
    SyntheticLabel,
    label_html,
    label_json,
    read_html_label,
    read_json_label,
)
from rastro.mocklab.records import SyntheticRecords

MANIFEST = "manifest.json"
EXPECTED = "expected.json"
RECORDS = "records.json"
BODIES: dict[Representation, str] = {"html": "body.html", "json": "body.json"}
MAX_FILE_BYTES = 25_000_000


class ExpectedEvidence(Contract):
    source_url: str
    method: Literal["jsonld", "css", "json"]
    locator: str
    recipe_version: int


class ExpectedRecord(Contract):
    data: dict[str, JsonValue]
    evidence: dict[str, ExpectedEvidence]


class ExpectedExtraction(Contract):
    """The time-independent part of an extraction result that a replay must reproduce."""

    status: Outcome
    records: tuple[ExpectedRecord, ...]
    issues: tuple[Issue, ...]
    candidates: int
    accepted: int
    rejected: int

    @classmethod
    def from_result(cls, result: ExtractionResult[Any]) -> ExpectedExtraction:
        return cls(
            status=result.status,
            records=tuple(
                ExpectedRecord(
                    data=record.data.model_dump(mode="json"),
                    evidence={
                        name: ExpectedEvidence.model_validate(
                            evidence.model_dump(exclude={"observed_at"})
                        )
                        for name, evidence in record.evidence.items()
                    },
                )
                for record in result.records
            ),
            issues=result.issues,
            candidates=result.metrics.candidates,
            accepted=result.metrics.accepted,
            rejected=result.metrics.rejected,
        )


class FixtureManifest(Contract):
    format_version: Literal[1] = 1
    label: SyntheticLabel
    representation: Representation
    source: Source
    recipe: Recipe
    schema_fingerprint: str
    response_headers: dict[str, str] = Field(default_factory=dict)
    digests: dict[str, str] = Field(default_factory=dict)


class FixtureBundle(Contract):
    """A self-describing fixture; ``body`` is sanitized and unlabeled."""

    manifest: FixtureManifest
    body: str
    expected: ExpectedExtraction
    records: SyntheticRecords

    @property
    def labeled_body(self) -> str:
        """The body exactly as written to disk and replayed, label included."""
        if self.manifest.representation == "html":
            return label_html(self.body, self.manifest.label)
        return _dumps(label_json(json.loads(self.body), self.manifest.label))

    def render(self) -> dict[str, bytes]:
        """Every file of the bundle, keyed by its fixed name, with digests in the manifest."""
        label = self.manifest.label
        files = {
            BODIES[self.manifest.representation]: self.labeled_body,
            EXPECTED: _dumps(label_json(self.expected.model_dump(mode="json"), label)),
            RECORDS: _dumps(label_json(self.records.model_dump(mode="json"), label)),
        }
        encoded = {name: text.encode() for name, text in files.items()}
        digests = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(encoded.items())}
        manifest = self.manifest.model_copy(update={"digests": digests})
        encoded[MANIFEST] = _dumps(manifest.model_dump(mode="json")).encode()
        return encoded

    def scan_view(self) -> dict[str, bytes]:
        """Rendered files with hashes removed from the manifest, for sensitive-data scans.

        Digests and the schema fingerprint are generated hex strings, not source data,
        and would otherwise match the opaque-token detectors.
        """
        files = self.render()
        manifest = self.manifest.model_dump(mode="json", exclude={"digests", "schema_fingerprint"})
        files[MANIFEST] = _dumps(manifest).encode()
        return files

    def write(self, directory: str | Path) -> Path:
        """Write every file atomically; existing symlinks at bundle paths are refused."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        files = self.render()
        if any((target / name).is_symlink() for name in files):
            raise MockLabError("Refusing to write a fixture file through a symbolic link")
        for name, data in files.items():
            _write_atomic(target / name, data)
        return target


def load_fixture(directory: str | Path) -> FixtureBundle:
    """Load a written bundle, rejecting tampered digests and missing or foreign labels."""
    root = Path(directory)
    try:
        manifest = FixtureManifest.model_validate_json(_read(root / MANIFEST))
    except ValidationError:
        raise MockLabError("Fixture manifest is invalid") from None
    body_name = BODIES[manifest.representation]
    names = {body_name, EXPECTED, RECORDS}
    if set(manifest.digests) != names:
        raise MockLabError("Fixture manifest does not list the expected files")
    texts = {name: _read(root / name) for name in sorted(names)}
    for name, text in texts.items():
        if hashlib.sha256(text.encode()).hexdigest() != manifest.digests[name]:
            raise MockLabError(f"Fixture file {name} does not match its digest")
    try:
        expected, records = (_payload(texts[name], manifest.label) for name in (EXPECTED, RECORDS))
        return FixtureBundle(
            manifest=manifest.model_copy(update={"digests": {}}),
            body=_unlabeled_body(texts[body_name], manifest),
            expected=ExpectedExtraction.model_validate(expected),
            records=SyntheticRecords.model_validate(records),
        )
    except (ValidationError, RecursionError):
        raise MockLabError("Fixture file contents are invalid") from None


def _unlabeled_body(text: str, manifest: FixtureManifest) -> str:
    if manifest.representation == "html":
        _require_label(read_html_label(text), manifest.label)
        return text.split("\n", 1)[1]
    payload = _payload(text, manifest.label)
    return _dumps(payload)


def _payload(text: str, label: SyntheticLabel) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except ValueError:
        raise MockLabError("Fixture file is not valid JSON") from None
    _require_label(read_json_label(payload), label)
    return {key: value for key, value in payload.items() if key != SYNTHETIC_KEY}


def _require_label(found: SyntheticLabel, expected: SyntheticLabel) -> None:
    if found != expected:
        raise MockLabError("Fixture file label does not match its manifest")


def _read(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise MockLabError(f"Fixture file {path.name} cannot be read") from None
    if len(data) > MAX_FILE_BYTES:
        raise MockLabError(f"Fixture file {path.name} exceeds {MAX_FILE_BYTES} bytes")
    try:
        return data.decode()
    except UnicodeDecodeError:
        raise MockLabError(f"Fixture file {path.name} is not UTF-8") from None


def _write_atomic(path: Path, data: bytes) -> None:
    # A fresh temporary file (O_EXCL, O_NOFOLLOW) is renamed over the destination, so a
    # symlink planted at either path is never followed.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary, flags, 0o644)
    except OSError:
        raise MockLabError(f"Fixture file {path.name} cannot be written") from None
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise MockLabError(f"Fixture file {path.name} cannot be written") from None


def _dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
