"""Shared corpus contracts, validation and value normalization; no extraction logic."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Listing(BaseModel):
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Literal["USD", "UYU"]


class RegionalListing(Listing):
    @field_validator("price", mode="before")
    @classmethod
    def decimal_comma(cls, value: object) -> object:
        # Explicit caller normalization, shared by both executors; not an engine feature.
        return value.replace(".", "").replace(",", ".") if isinstance(value, str) else value


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["css", "jsonld"]
    fields: dict[str, str]
    record_selector: str | None = None
    item_type: str | None = None


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")
    html: str
    mappings: tuple[Mapping, ...] = Field(min_length=1, max_length=4)
    regional: bool = False

    @property
    def record_schema(self) -> type[Listing]:
        return RegionalListing if self.regional else Listing


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    category: str
    document: Document
    expected: tuple[Listing, ...]
    requires_warning: bool = False
    repeat: int = Field(default=1, ge=1, le=1000)

    def expanded(self) -> "Case":
        """Expand the large-page template and its separately declared oracle before timing."""
        if self.repeat == 1:
            return self
        return self.model_copy(
            update={
                "document": self.document.model_copy(
                    update={
                        "html": "".join(
                            self.document.html.replace("{index}", str(i))
                            for i in range(self.repeat)
                        ),
                    }
                ),
                "expected": tuple(
                    record.model_copy(
                        update={
                            "sku": record.sku.replace("{index}", str(i)),
                            "name": record.name.replace("{index}", str(i)),
                        }
                    )
                    for i in range(self.repeat)
                    for record in self.expected
                ),
            }
        )


@dataclass(frozen=True)
class Observation:
    status: str
    records: tuple[Listing, ...]
    inference_calls: int = 0


def values(record: Listing) -> dict[str, str]:
    return {
        "sku": record.sku,
        "name": record.name,
        "price": format(record.price.normalize(), "f"),
        "currency": record.currency,
    }
