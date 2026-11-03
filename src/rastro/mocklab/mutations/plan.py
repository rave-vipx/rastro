"""A versioned, serializable plan that applies operators reproducibly (MOCK-002, MOCK-006)."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from rastro.domain.json import strict_json
from rastro.domain.sources import Representation
from rastro.mocklab.errors import MockLabError
from rastro.mocklab.labels import (
    MAX_SEED,
    SYNTHETIC_KEY,
    SyntheticLabel,
    label_html,
    label_json,
    read_html_label,
)
from rastro.mocklab.mutations.contracts import MutationResult, MutationStatus, StrictContract
from rastro.mocklab.mutations.html_content import (
    HtmlChangeClasses,
    HtmlChangeLabel,
    HtmlModifyPagination,
    HtmlRemoveField,
)
from rastro.mocklab.mutations.html_structure import (
    HtmlDuplicateElement,
    HtmlEmptyContainer,
    HtmlReorderChildren,
    HtmlWrapElement,
)
from rastro.mocklab.mutations.json_containers import (
    JsonDuplicateItem,
    JsonEmptyContainer,
    JsonReorder,
)
from rastro.mocklab.mutations.json_doc import check_tree, encode
from rastro.mocklab.mutations.json_fields import (
    JsonChangeType,
    JsonModifyPagination,
    JsonRemoveField,
    JsonRenameKey,
    JsonWrapValue,
)
from rastro.mocklab.mutations.limits import check_bytes
from rastro.mocklab.mutations.rng import step_stream

type MutationOperator = Annotated[
    HtmlReorderChildren
    | HtmlChangeClasses
    | HtmlWrapElement
    | HtmlRemoveField
    | HtmlDuplicateElement
    | HtmlEmptyContainer
    | HtmlChangeLabel
    | HtmlModifyPagination
    | JsonReorder
    | JsonChangeType
    | JsonWrapValue
    | JsonRemoveField
    | JsonDuplicateItem
    | JsonEmptyContainer
    | JsonRenameKey
    | JsonModifyPagination,
    Field(discriminator="operator"),
]


class MutationPlanResult(StrictContract):
    """The outcome of a plan.

    When at least one step applied, ``document`` is a Mock Lab artifact that starts with
    (HTML) or contains (JSON) ``label``. When no step applied, no artifact exists:
    ``document`` is the input unchanged and ``label`` is ``None``.
    """

    status: MutationStatus
    representation: Representation
    plan_fingerprint: str
    label: SyntheticLabel | None
    document: str
    steps: tuple[MutationResult, ...]


class MutationPlan(StrictContract):
    """Operators applied in order to one representation, each with its own seeded stream.

    ``to_json`` is canonical, so equal plans have equal bytes and fingerprints, and
    ``apply`` returns byte-identical documents for equal plans and inputs.
    """

    format_version: Literal[1] = 1
    seed: int = Field(ge=0, le=MAX_SEED)
    operators: tuple[MutationOperator, ...] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _one_representation(self) -> Self:
        if len({operator.representation for operator in self.operators}) > 1:
            raise ValueError("A plan's operators must all target one representation")
        return self

    @property
    def representation(self) -> Representation:
        return self.operators[0].representation

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def to_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> MutationPlan:
        return cls.model_validate_json(text)

    def apply(self, document: str) -> MutationPlanResult:
        """Apply every operator.

        Raises ``MockLabError`` when the output cannot carry a label or is not valid UTF-8,
        and ``ResourceLimitError`` when it exceeds the default ``Limits``.
        """
        current = _without_label(document, self.representation)
        steps: list[MutationResult] = []
        for index, operator in enumerate(self.operators):
            step = operator.apply(current, step_stream(self.seed, index, operator.operator))
            steps.append(step)
            current = step.document
        applied = any(step.status is MutationStatus.APPLIED for step in steps)
        label = SyntheticLabel(kind="mutation", seed=self.seed) if applied else None
        output = document if label is None else _artifact(current, label, self.representation)
        return MutationPlanResult(
            status=MutationStatus.APPLIED if applied else MutationStatus.NO_APPLICABLE_TARGET,
            representation=self.representation,
            plan_fingerprint=self.fingerprint,
            label=label,
            document=output,
            steps=tuple(steps),
        )


def _without_label(document: str, representation: Representation) -> str:
    """Drop the label of a Mock Lab input (a fixture body, say) before mutating it."""
    if representation == "html":
        try:
            read_html_label(document)
        except MockLabError:
            return document
        return document.split("\n", 1)[1]
    try:
        payload = strict_json(document)
    except (ValueError, RecursionError):
        return document
    if not (isinstance(payload, dict) and SYNTHETIC_KEY in payload):
        return document
    del payload[SYNTHETIC_KEY]
    return encode(payload)


def _artifact(document: str, label: SyntheticLabel, representation: Representation) -> str:
    """Label the output and check it against the same limits inputs must meet."""
    if representation == "html":
        output = label_html(document, label)
    else:
        payload = label_json(json.loads(document), label)
        check_tree(payload)
        output = encode(payload)
    check_bytes(output, representation)
    return output
