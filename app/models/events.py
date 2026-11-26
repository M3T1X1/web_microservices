from datetime import timezone
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.order import OrderItem
from app.models.stock import NonEmptyText

SHIPMENT_ROUTING_KEY = "shipment.handed_over"
OUTBOUND_SHIPMENT_QUEUE = "outbound.shipment-events"
INVENTORY_SHIPMENT_QUEUE = "inventory.shipment-events"
RESERVATION_REQUEST_KEY = "order.reservation.requested"
RESERVATION_RESERVED_KEY = "order.stock.reserved"
RESERVATION_REJECTED_KEY = "order.stock.reservation_rejected"
INVENTORY_RESERVATION_QUEUE = "inventory.reservation-requests"
OUTBOUND_RESERVATION_QUEUE = "outbound.reservation-results"
CANCELLATION_ROUTING_KEY = "order.cancelled"
INVENTORY_CANCELLATION_QUEUE = "inventory.order-cancellations"


def cancellation_event_id(order_id):
    return uuid5(NAMESPACE_URL, f"warehouse/orders/{order_id}/cancelled")


class OrderCancelled(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: UUID
    event_type: Literal["OrderCancelled"]
    order_id: UUID
    items: list[OrderItem] = Field(min_length=1)
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def stable_identifier(self):
        if self.event_id != cancellation_event_id(self.order_id):
            raise ValueError("Event ID does not match order cancellation")
        self.occurred_at = self.occurred_at.astimezone(timezone.utc)
        return self


def reservation_request_id(attempt_id):
    return uuid5(NAMESPACE_URL, f"warehouse/reservation-requests/{attempt_id}")


def reservation_result_id(attempt_id):
    return uuid5(NAMESPACE_URL, f"warehouse/reservation-results/{attempt_id}")


class ReservationMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: UUID
    event_type: str
    attempt_id: UUID
    order_id: UUID
    items: list[OrderItem] = Field(min_length=1)
    occurred_at: AwareDatetime

    @field_validator("occurred_at")
    @classmethod
    def utc_time(cls, value):
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def stable_identifier(self):
        make_id = reservation_request_id if self.event_type == "OrderReservationRequested" else reservation_result_id
        if self.event_id != make_id(self.attempt_id):
            raise ValueError("Event ID does not match reservation attempt")
        return self


class OrderReservationRequested(ReservationMessage):
    event_type: Literal["OrderReservationRequested"]


class OrderStockReserved(ReservationMessage):
    event_type: Literal["OrderStockReserved"]
    stock_ids: list[UUID] = Field(default_factory=list)


class OrderStockReservationRejected(ReservationMessage):
    event_type: Literal["OrderStockReservationRejected"]
    reason: NonEmptyText


def reservation_result(payload):
    event_type = payload.get("event_type") if isinstance(payload, dict) else None
    model = {"OrderStockReserved": OrderStockReserved,
             "OrderStockReservationRejected": OrderStockReservationRejected}.get(event_type)
    if model is None:
        raise ValueError("Unknown reservation result event")
    return model.model_validate(payload)


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
