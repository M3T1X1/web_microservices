from uuid import UUID
from urllib.error import HTTPError, URLError
from fastapi import HTTPException

from app.cache import JsonCache
from app.models.order import Order, OrderCreate
from app.repositories import OrderRepository
from app.services.cached_read import cached_read


class OrderService:
    def __init__(self, repository: OrderRepository, cache: JsonCache):
        self.repository, self.cache = repository, cache

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
