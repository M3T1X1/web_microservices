from uuid import UUID

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
