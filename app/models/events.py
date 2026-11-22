from datetime import timezone
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

SHIPMENT_ROUTING_KEY = "shipment.handed_over"
OUTBOUND_SHIPMENT_QUEUE = "outbound.shipment-events"


class ShipmentHandedOver(BaseModel):
    model_config = ConfigDict(extra="ignore")

    event_id: UUID
    event_type: Literal["ShipmentHandedOver"]
    shipment_id: UUID
    order_id: UUID
    occurred_at: AwareDatetime

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)

    @classmethod
    def from_shipment(cls, shipment):
        # Jedno fizyczne wydanie = jeden identyfikator, takze po restarcie i /sync.
        return cls(event_id=uuid5(NAMESPACE_URL, f"warehouse/shipments/{shipment.id}/handed-over"),
                   event_type="ShipmentHandedOver", shipment_id=shipment.id,
                   order_id=shipment.order_id, occurred_at=shipment.handed_over_at)
