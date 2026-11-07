"""Site/task-specific schema; no oracle or Rastro runtime imports."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl

Text = Annotated[str, Field(min_length=1)]


class Performance(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    performance_id: Text
    title: Text
    performers: Text
    venue: Text
    city: Text
    starts_at: AwareDatetime
    ticket_url: HttpUrl
    price: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Literal["UYU"]
