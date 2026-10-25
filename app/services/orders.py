from threading import Lock
from uuid import UUID

from fastapi import HTTPException

from app.models.order import Order, OrderCreate


class OrderService:
    def __init__(self, orders: dict[UUID, Order]) -> None:
        self._orders = orders
        self._lock = Lock()

    def create(self, body: OrderCreate) -> Order:
        order = Order(**body.model_dump())
        with self._lock:
            self._orders[order.id] = order
            return order.model_copy(deep=True)

    def list(self) -> list[Order]:
        with self._lock:
            return [order.model_copy(deep=True) for order in self._orders.values()]

    def get(self, order_id: UUID) -> Order:
        with self._lock:
            return self._find(order_id).model_copy(deep=True)

    def pack(self, order_id: UUID) -> Order:
        with self._lock:
            order = self._find(order_id)
            if order.status != "RESERVED":
                raise HTTPException(
                    status_code=409, detail="Order must be reserved before packing"
                )
            order.status = "PACKED"
            return order.model_copy(deep=True)

    def _find(self, order_id: UUID) -> Order:
        order = self._orders.get(order_id)
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        return order
