from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, StringConstraints


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class OrderItem(BaseModel):
    sku: NonEmptyText
    quantity: int = Field(gt=0, strict=True)


class OrderCreate(BaseModel):
    recipient_name: NonEmptyText
    recipient_phone: NonEmptyText
    recipient_address: NonEmptyText
    items: list[OrderItem] = Field(min_length=1)


class Order(OrderCreate):
    id: UUID = Field(default_factory=uuid4)
    status: Literal["NEW", "RESERVED", "PACKED", "SHIPPED", "CANCELLED"] = "NEW"
