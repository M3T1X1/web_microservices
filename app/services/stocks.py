from uuid import UUID

from app.cache import JsonCache
from app.models.stock import Reservation, Stock, StockUpdate
from app.repositories import StockRepository
from app.services.cached_read import cached_read


class StockService:
    def __init__(self, repository: StockRepository, cache: JsonCache):
        self.repository, self.cache = repository, cache

    def list(self) -> list[Stock]:
        return self.repository.list()

    def get(self, stock_id: UUID) -> tuple[Stock, str]:
        return cached_read(stock_id, self.repository, self.cache, Stock)

    def reserve(self, stock_id: UUID, reservation: Reservation) -> Reservation:
        result = self.repository.reserve(stock_id, reservation)
        self.cache.delete(stock_id)  # Wzorzec z ZIP: commit first, potem invalidation.
        return result

    def update(self, stock_id: UUID, body: StockUpdate) -> Stock:
        result = self.repository.update(stock_id, body)
        self.cache.delete(stock_id)
        return result
