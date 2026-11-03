"""JSON operators that edit one field per record (MOCK-002)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field

from rastro.domain.json import MISSING, escape_token
from rastro.mocklab.mutations.contracts import MutationCategory, MutationChange
from rastro.mocklab.mutations.json_base import JsonFieldOperator
from rastro.mocklab.mutations.json_doc import Record, sibling
from rastro.mocklab.seeds import DeterministicStream


class JsonChangeType(JsonFieldOperator):
    """Map type or class discriminator strings, such as ``@type`` or ``kind``."""

    operator: Literal["json_change_type"] = "json_change_type"
    category: ClassVar[MutationCategory] = "class_changes"

    mapping: dict[str, str] = Field(min_length=1, max_length=32)

    def eligible(self, parent: Record, key: str) -> bool:
        value = parent[key]
        return isinstance(value, str) and self.mapping.get(value, value) != value

    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        before = parent[key]
        parent[key] = self.mapping[str(before)]
        return self.change(path, path, before, parent[key])


class JsonWrapValue(JsonFieldOperator):
    """Nest each field value one level deeper, under ``wrapper_key``."""

    operator: Literal["json_wrap_value"] = "json_wrap_value"
    category: ClassVar[MutationCategory] = "element_wrapping"

    wrapper_key: str = Field(default="value", min_length=1, max_length=128)

    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        before = parent[key]
        parent[key] = {self.wrapper_key: before}
        return self.change(path, f"{path}/{escape_token(self.wrapper_key)}", before, before)


class JsonRemoveField(JsonFieldOperator):
    """Delete the field, or set it to ``null``."""

    operator: Literal["json_remove_field"] = "json_remove_field"
    category: ClassVar[MutationCategory] = "missing_fields"

    mode: Literal["delete", "null"] = "delete"

    def eligible(self, parent: Record, key: str) -> bool:
        return self.mode == "delete" or parent[key] is not None

    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        before = parent.pop(key) if self.mode == "delete" else parent[key]
        if self.mode == "delete":
            return self.change(path, None, before, MISSING)
        parent[key] = None
        return self.change(path, path, before, None)


class JsonRenameKey(JsonFieldOperator):
    """Rename the field's key in place, keeping key order."""

    operator: Literal["json_rename_key"] = "json_rename_key"
    category: ClassVar[MutationCategory] = "changed_labels"

    new_key: str = Field(min_length=1, max_length=128)

    def eligible(self, parent: Record, key: str) -> bool:
        return self.new_key not in parent

    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        renamed = {(self.new_key if name == key else name): v for name, v in parent.items()}
        parent.clear()
        parent.update(renamed)
        return self.change(path, sibling(path, self.new_key), key, self.new_key)


class JsonModifyPagination(JsonFieldOperator):
    """Change a pagination field: rewrite a cursor, end the pages, or shift a page number.

    ``rewrite_cursor`` replaces ``{token}`` in ``cursor_template`` with seeded letters;
    ``end`` sets the field to ``null``; ``shift_page`` adds ``offset`` to an integer.
    """

    operator: Literal["json_modify_pagination"] = "json_modify_pagination"
    category: ClassVar[MutationCategory] = "modified_pagination"

    mode: Literal["rewrite_cursor", "end", "shift_page"] = "rewrite_cursor"
    cursor_template: str = Field(default="{token}", min_length=1, max_length=512)
    offset: int = Field(default=1, ge=-1_000, le=1_000)

    def eligible(self, parent: Record, key: str) -> bool:
        value = parent[key]
        if self.mode == "shift_page":
            return type(value) is int and self.offset != 0
        if self.mode == "end":
            return value is not None
        fixed = "{token}" not in self.cursor_template and value == self.cursor_template
        return not (isinstance(value, dict | list) or fixed)

    def edit(
        self, path: str, parent: Record, key: str, stream: DeterministicStream
    ) -> MutationChange:
        before = parent[key]
        parent[key] = self._next(before, stream)
        return self.change(path, path, before, parent[key])

    def _next(self, value: object, stream: DeterministicStream) -> object:
        if self.mode == "end":
            return None
        if isinstance(value, int) and self.mode == "shift_page":
            return value + self.offset
        return self.cursor_template.replace("{token}", stream.letters(8))
