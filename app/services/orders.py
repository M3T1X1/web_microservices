from uuid import UUID

from app.cache import JsonCache
from app.models.order import Order, OrderCreate
from app.repositories import OrderRepository


class OrderService:
    def __init__(self, repository: OrderRepository, cache: JsonCache):
        self.repository = repository
        self.cache = cache

    def create(self, body: OrderCreate) -> Order:
        return self.repository.create(body)

    def list(self) -> list[Order]:
        return self.repository.list()

    def get(self, order_id: UUID) -> tuple[Order, str]:
        result, source = self.cache.get(order_id, Order)
        if result is None:
            result = self.repository.get(order_id)
            if source != "BYPASS" and not self.cache.put(order_id, result):
                source = "BYPASS"
        return result, source

    def pack(self, order_id: UUID) -> Order:
        result = self.repository.pack(order_id)
        self.cache.delete(order_id)
        return result
