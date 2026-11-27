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
    status: Literal["NEW", "RESERVATION_PENDING", "RESERVATION_REJECTED", "RESERVED", "PACKED", "SHIPPED", "CANCELLED"] = "NEW"
    reservation_attempt_id: UUID | None = None
    reservation_error: str | None = None
    reservation_notification_pending: bool = False


def normalized_items(items):
    totals = {}
    for item in items:
        sku, quantity = (item.sku, item.quantity) if isinstance(item, OrderItem) else (item["sku"], item["quantity"])
        totals[sku] = totals.get(sku, 0) + quantity
    return [{"sku": sku, "quantity": quantity} for sku, quantity in sorted(totals.items())]
