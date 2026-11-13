"""Issue messages never echo source values (P-005, SEC-005)."""

import re
from decimal import Decimal
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator
from pydantic_core import PydanticKnownError

from rastro.domain.safe_messages import SAFE_ERROR_TYPES, SCHEMA_CONTEXT, safe_message

SECRET = "SECRET-7f3a"


def test_allow_listed_templates_interpolate_only_schema_constraints():
    for kind in SAFE_ERROR_TYPES:
        try:
            template = PydanticKnownError(kind).message_template
        except TypeError:
            text = {"class_name", "pattern", "expected", "expected_schemes", "expected_plural"}
            context = {name: "x" if name in text else 1 for name in SCHEMA_CONTEXT}
            template = PydanticKnownError(kind, context).message_template
        assert set(re.findall(r"\{(\w+)\}", template)) <= SCHEMA_CONTEXT, kind


def test_input_echoing_and_custom_types_are_excluded():
    for kind in ("union_tag_invalid", "value_error", "assertion_error", "json_invalid"):
        assert kind not in SAFE_ERROR_TYPES
        assert safe_message(kind, f"Input tag '{SECRET}'") is None


class Cat(BaseModel):
    kind: Literal["cat"]


class Dog(BaseModel):
    kind: Literal["dog"]


class Everything(BaseModel):
    count: int
    price: Decimal = Field(max_digits=4)
    code: Annotated[str, Field(pattern=r"^[A-Z]{2}$", max_length=3)]
    mode: Literal["a", "b"]
    url: HttpUrl
    pet: Annotated[Cat | Dog, Field(discriminator="kind")]
    note: str

    @field_validator("note")
    @classmethod
    def refuse(cls, value: str) -> str:
        raise ValueError(f"bad {value}")


def test_real_errors_with_a_secret_input_never_carry_it():
    with pytest.raises(ValidationError) as error:
        Everything.model_validate(
            {
                "count": SECRET,
                "price": SECRET,
                "code": SECRET,
                "mode": SECRET,
                "url": SECRET,
                "pet": {"kind": SECRET},
                "note": SECRET,
            }
        )
    details = error.value.errors(include_url=False, include_context=False, include_input=False)
    kinds = {d["type"] for d in details}
    assert "union_tag_invalid" in kinds and "value_error" in kinds
    messages = [safe_message(d["type"], d["msg"]) for d in details]
    assert any(messages)
    assert all(SECRET not in (m or "") for m in messages)
