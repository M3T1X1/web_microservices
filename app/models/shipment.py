from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from app.models.stock import NonEmptyText


class ShipmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: UUID
    carrier: NonEmptyText
    weight_kg: float = Field(gt=0, allow_inf_nan=False, strict=True)


class Shipment(ShipmentCreate):
    id: UUID
    status: Literal["NEW", "READY", "HANDED_OVER", "CANCELLED"]
    handed_over_at: datetime | None = None
    notification_pending: bool = Field(default=False, description="Oczekuje publikacji do RabbitMQ; false nie oznacza jeszcze odbioru przez Outbound.")


class DispatchRequest(BaseModel):
    shipment_id: UUID
