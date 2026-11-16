from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.models.stock import NonEmptyText


class ReceiptItem(BaseModel):
    sku: NonEmptyText
    quantity_received: int = Field(gt=0, strict=True)
    target_location: NonEmptyText


class ReceiptCreate(BaseModel):
    supplier: NonEmptyText
    items: list[ReceiptItem] = Field(min_length=1)


class Receipt(ReceiptCreate):
    id: UUID = Field(default_factory=uuid4)
    status: Literal["NEW", "ACCEPTED", "POSTED", "CANCELLED"] = "NEW"
