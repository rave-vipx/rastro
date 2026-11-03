"""Deterministic, seeded structural mutations of HTML and JSON documents (MOCK-002, MOCK-006).

Build a ``MutationPlan`` from operator configs and a seed, then ``apply`` it. Every
operator reports what it changed or why no target applied.
"""

from rastro.mocklab.mutations.contracts import (
    MutationCategory,
    MutationChange,
    MutationResult,
    MutationStatus,
    SkipReason,
)
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
from rastro.mocklab.mutations.json_fields import (
    JsonChangeType,
    JsonModifyPagination,
    JsonRemoveField,
    JsonRenameKey,
    JsonWrapValue,
)
from rastro.mocklab.mutations.plan import MutationOperator, MutationPlan, MutationPlanResult

__all__ = [
    "HtmlChangeClasses",
    "HtmlChangeLabel",
    "HtmlDuplicateElement",
    "HtmlEmptyContainer",
    "HtmlModifyPagination",
    "HtmlRemoveField",
    "HtmlReorderChildren",
    "HtmlWrapElement",
    "JsonChangeType",
    "JsonDuplicateItem",
    "JsonEmptyContainer",
    "JsonModifyPagination",
    "JsonRemoveField",
    "JsonRenameKey",
    "JsonReorder",
    "JsonWrapValue",
    "MutationCategory",
    "MutationChange",
    "MutationOperator",
    "MutationPlan",
    "MutationPlanResult",
    "MutationResult",
    "MutationStatus",
    "SkipReason",
]
