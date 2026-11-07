"""Unvalidated repair proposals and saved relocation evidence (REPAIR-001, SCR-002).

A candidate is not a serving recipe or a validation verdict. Only recipe CI and
an explicit lifecycle transition may activate it. Fingerprints are private local
comparison data: hashes reduce disclosure, but are not anonymization.
"""

from hashlib import sha256
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field

from rastro.domain.lifecycle_history import Reason
from rastro.domain.models import Contract, Issue, Recipe


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def recipe_digest(recipe: Recipe) -> str:
    return digest(recipe.model_dump_json())


type FingerprintToken = Annotated[str, Field(pattern=r"^h[0-9a-f]{64}$")]


type FingerprintAttribute = Annotated[str, Field(pattern=r"^(class|id|href|src|h[0-9a-f]{64})$")]


class ElementFingerprint(Contract):
    """Scrapling 0.5 comparison properties, containing hashes instead of page text."""

    tag: FingerprintToken
    attributes: dict[FingerprintAttribute, FingerprintToken] = Field(max_length=100)
    text: FingerprintToken | None
    path: tuple[FingerprintToken, ...] = Field(max_length=128)


class RelocationSnapshot(Contract):
    """First record's field fingerprints, captured only after a successful run.

    The caller retains the latest successful snapshot per source/schema/recipe;
    failed runs must not replace it. There is no implicit Scrapling storage.
    """

    recipe_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    schema_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observed_at: AwareDatetime
    fields: dict[str, ElementFingerprint] = Field(min_length=1, max_length=100)


class RepairEvidence(Contract):
    field: str = Field(min_length=1, max_length=256)
    match_count: int = Field(ge=0, le=4096)


class RepairCandidate(Contract):
    recipe: Recipe
    origin: Literal["scrapling_relocation", "fallback", "discovery", "model"]
    rationale: Reason
    evidence: tuple[RepairEvidence, ...] = Field(default=(), max_length=100)

    @property
    def ambiguous(self) -> bool:
        return any(item.match_count > 1 for item in self.evidence)


class RepairCandidates(Contract):
    """An empty candidate set is explicit; issues retain model failure categories."""

    candidates: tuple[RepairCandidate, ...] = Field(default=(), max_length=32)
    issues: tuple[Issue, ...] = ()
    inference_calls: int = Field(default=0, ge=0, le=4)

    @property
    def outcome(self) -> Literal["candidates", "no_candidates"]:
        return "candidates" if self.candidates else "no_candidates"
