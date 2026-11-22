from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.db_models import ShipmentRow
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.events import SHIPMENT_ROUTING_KEY, ShipmentHandedOver
from app.messaging import BrokerUnavailableError, RabbitEventBroker
from app.repositories import write_transaction


def shipment_payload(row):
    return Shipment(id=row.id, order_id=row.order_id, carrier=row.carrier, weight_kg=row.weight_kg,
                    status=row.status, handed_over_at=row.handed_over_at,
                    notification_pending=row.notification_pending)


class ShippingService:
    def __init__(self, sessions, discovery, event_broker=None):
        self.sessions, self.discovery = sessions, discovery
        self.event_broker = event_broker if event_broker is not None else RabbitEventBroker()

    async def call_outbound(self, path, headers, **kwargs):
        if self.discovery is None:
            raise HTTPException(503, "Outbound Service is unavailable")
        try:
            return await self.discovery.do_service("OUTBOUND-SERVICE", path, return_type="json",
                                                   prefer_ip=True, timeout=3, headers=headers, **kwargs)
        except HTTPError as exc:
            if exc.code in {401, 403, 404, 409}:
                detail = {401: "Authentication expired", 403: "Insufficient permissions",
                          404: "Order not found in Outbound", 409: "Order cannot be dispatched"}[exc.code]
                raise HTTPException(exc.code, detail) from exc
            raise HTTPException(502, "Outbound Service returned an HTTP error") from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise HTTPException(503, "Outbound Service is unavailable") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(502, "Outbound Service returned invalid data") from exc

    async def packed_order(self, order_id, headers):
        payload = await self.call_outbound(f"/orders/{order_id}/shipping-check", headers)
        try:
            order = Order.model_validate(payload)
            if order.id != order_id:
                raise ValueError("Wrong order")
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(502, "Outbound Service returned invalid order data") from exc
        if order.status != "PACKED":
            raise HTTPException(409, "Order must be packed before shipping")
        return order

    async def create(self, body, headers):
        await self.packed_order(body.order_id, headers)
        with write_transaction(self.sessions) as db:
            if db.scalar(select(ShipmentRow.id).where(ShipmentRow.order_id == body.order_id)):
                raise HTTPException(409, "Shipment already exists for this order")
            row = ShipmentRow(id=uuid4(), **body.model_dump(), status="NEW", notification_pending=False)
            db.add(row)
            result = shipment_payload(row)
        return result

    def list(self):
        with self.sessions() as db:
            return [shipment_payload(row) for row in db.scalars(select(ShipmentRow).order_by(ShipmentRow.id))]

    def get(self, shipment_id):
        with self.sessions() as db:
            return shipment_payload(self.find(db, shipment_id))

    async def ready(self, shipment_id, headers):
        shipment = self.get(shipment_id)
        if shipment.status != "NEW":
            raise HTTPException(409, "Only NEW shipments can become READY")
        await self.packed_order(shipment.order_id, headers)
        return self.change_status(shipment_id, {"NEW"}, "READY")

    def change_status(self, shipment_id, allowed, status):
        with write_transaction(self.sessions) as db:
            row = self.find(db, shipment_id)
            if row.status not in allowed:
                raise HTTPException(409, "Invalid shipment status transition")
            row.status = status
            result = shipment_payload(row)
        return result

    async def hand_over(self, shipment_id, headers):
        shipment = self.get(shipment_id)
        if shipment.status != "READY":
            raise HTTPException(409, "Only READY shipments can be handed over")
        await self.packed_order(shipment.order_id, headers)
        # Lokalny fakt wydania i oczekujące powiadomienie są jedną transakcją.
        with write_transaction(self.sessions) as db:
            row = self.find(db, shipment_id)
            if row.status != "READY":
                raise HTTPException(409, "Only READY shipments can be handed over")
            row.status = "HANDED_OVER"
            row.handed_over_at = datetime.now(timezone.utc).isoformat()
            row.notification_pending = True
        return await self.sync(shipment_id, headers)

    async def sync(self, shipment_id, headers):
        shipment = self.get(shipment_id)
        if shipment.status != "HANDED_OVER":
            raise HTTPException(409, "Only HANDED_OVER shipments can be synchronized")
        if not shipment.notification_pending:
            return shipment
        event = ShipmentHandedOver.from_shipment(shipment)
        try:
            await run_in_threadpool(self.event_broker.publish, SHIPMENT_ROUTING_KEY, event.model_dump(mode="json"))
        except BrokerUnavailableError as exc:
            # Fakt wydania zostal zapisany; /sync ponowi publikacje tego samego event_id.
            raise HTTPException(503, "RabbitMQ broker is unavailable") from exc
        with write_transaction(self.sessions) as db:
            row = self.find(db, shipment_id)
            row.notification_pending = False
            result = shipment_payload(row)
        return result

    @staticmethod
    def find(db, shipment_id):
        row = db.scalar(select(ShipmentRow).where(ShipmentRow.id == shipment_id).with_for_update())
        if row is None:
            raise HTTPException(404, "Shipment not found")
        return row
