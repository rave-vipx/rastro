"""Instant Mock Lab: labeled, sanitized, reproducible test artifacts (MOCK-001 to MOCK-006).

Operators for structural mutations live in ``rastro.mocklab.mutations``.
"""

from rastro.mocklab.bundle import (
    ExpectedEvidence,
    ExpectedExtraction,
    ExpectedRecord,
    FixtureBundle,
    FixtureManifest,
    load_fixture,
)
from rastro.mocklab.errors import FixtureMismatchError, MockLabError, SanitizationError
from rastro.mocklab.fixtures import build_fixture, replay_fixture, verify_fixture
from rastro.mocklab.labels import (
    SYNTHETIC_KEY,
    ArtifactKind,
    SyntheticLabel,
    label_html,
    label_json,
    read_html_label,
    read_json_label,
)
from rastro.mocklab.mutations import (
    MutationChange,
    MutationPlan,
    MutationPlanResult,
    MutationResult,
    MutationStatus,
)
from rastro.mocklab.records import SyntheticRecords
from rastro.mocklab.sanitize import ALLOWED_HEADERS
from rastro.mocklab.seeds import DeterministicStream

__all__ = [
    "ALLOWED_HEADERS",
    "SYNTHETIC_KEY",
    "ArtifactKind",
    "DeterministicStream",
    "ExpectedEvidence",
    "ExpectedExtraction",
    "ExpectedRecord",
    "FixtureBundle",
    "FixtureManifest",
    "FixtureMismatchError",
    "MockLabError",
    "MutationChange",
    "MutationPlan",
    "MutationPlanResult",
    "MutationResult",
    "MutationStatus",
    "SanitizationError",
    "SyntheticLabel",
    "SyntheticRecords",
    "build_fixture",
    "label_html",
    "label_json",
    "load_fixture",
    "read_html_label",
    "read_json_label",
    "replay_fixture",
    "verify_fixture",
]
