from uuid import UUID
from pydantic import BaseModel, Field
from app.models.order import OrderItem


class ReservationBatch(BaseModel):
    order_id: UUID
    items: list[OrderItem] = Field(min_length=1)
