from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, StringConstraints


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Reservation(BaseModel):
    order_id: UUID
    quantity: int = Field(gt=0, strict=True)


class Stock(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    sku: NonEmptyText
    location: NonEmptyText
    quantity: int = Field(ge=0, strict=True)
    reservations: list[Reservation] = Field(default_factory=list)
