from uuid import UUID
from urllib.error import HTTPError, URLError
from fastapi import HTTPException
from pydantic import ValidationError

from app.cache import JsonCache
from app.models.order import Order, OrderCreate
from app.models.events import OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY, ShipmentHandedOver
from app.messaging import BrokerUnavailableError, RabbitEventBroker
from app.repositories import OrderRepository
from app.services.cached_read import cached_read


class OrderService:
    def __init__(self, repository: OrderRepository, cache: JsonCache, event_broker=None):
        self.repository, self.cache = repository, cache
        self.event_broker = event_broker if event_broker is not None else RabbitEventBroker()

    def consume_events(self, limit=50):
        unique = 0

        def handle_event(payload):
            nonlocal unique
            event = ShipmentHandedOver.model_validate(payload)
            applied = self.repository.apply_shipment_event(event)
            # Takze duplikat naprawia cache, jesli poprzednia proba padla po COMMIT.
            if not self.cache.delete(event.order_id):
                raise HTTPException(503, "Cache invalidation failed; event remains queued")
            unique += int(applied)

        try:
            self.event_broker.bind_queue(OUTBOUND_SHIPMENT_QUEUE, SHIPMENT_ROUTING_KEY)
            consumed = self.event_broker.consume_available(OUTBOUND_SHIPMENT_QUEUE, handle_event, limit=limit)
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid shipment event; message remains queued") from exc
        return {"consumed_messages": consumed, "unique_events_processed": unique}

    def create(self, body: OrderCreate) -> Order:
        return self.repository.create(body)

    def list(self) -> list[Order]:
        return self.repository.list()

    def get(self, order_id: UUID) -> tuple[Order, str]:
        return cached_read(order_id, self.repository, self.cache, Order)

    def pack(self, order_id: UUID) -> Order:
        result = self.repository.pack(order_id)
        self.cache.delete(order_id)
        return result

    async def reserve(self, order_id, discovery, headers):
        order = self.repository.get(order_id)
        if order.status == "RESERVED":
            return order
        if order.status != "NEW":
            raise HTTPException(409, "Only NEW orders can be reserved")
        if discovery is None:
            raise HTTPException(503, "Inventory Service is unavailable")
        try:
            confirmation = await discovery.do_service(
                "INVENTORY-SERVICE", "/reservations", return_type="json", prefer_ip=True,
                timeout=3, method="POST", headers=headers,
                data={"order_id": str(order.id), "items": [item.model_dump() for item in order.items]},
            )
        except HTTPError as exc:
            if exc.code in {401, 403, 409}:
                raise HTTPException(exc.code, "Inventory rejected the reservation") from exc
            raise HTTPException(502, "Inventory Service returned an HTTP error") from exc
        except (URLError, TimeoutError, ConnectionError) as exc:
            raise HTTPException(503, "Inventory Service is unavailable") from exc
        if not isinstance(confirmation, dict) or confirmation.get("order_id") != str(order.id) or confirmation.get("status") != "RESERVED":
            raise HTTPException(502, "Inventory Service returned invalid reservation confirmation")
        result = self.repository.mark_reserved(order_id)
        self.cache.delete(order_id)
        return result

    def dispatch(self, order_id, shipment_id):
        result = self.repository.dispatch(order_id, shipment_id)
        self.cache.delete(order_id)
        return result
