from uuid import UUID

from app.cache import JsonCache
from app.models.stock import Reservation, Stock, StockUpdate
from app.repositories import StockRepository


class StockService:
    def __init__(self, repository: StockRepository, cache: JsonCache):
        self.repository = repository
        self.cache = cache

    def list(self) -> list[Stock]:
        return self.repository.list()

    def get(self, stock_id: UUID) -> tuple[Stock, str]:
        result, source = self.cache.get(stock_id, Stock)
        if result is None:
            result = self.repository.get(stock_id)
            if source != "BYPASS" and not self.cache.put(stock_id, result):
                source = "BYPASS"
        return result, source

    def reserve(self, stock_id: UUID, reservation: Reservation) -> Reservation:
        result = self.repository.reserve(stock_id, reservation)
        self.cache.delete(stock_id)  # Repozytorium zakończyło już commit.
        return result

    def update(self, stock_id: UUID, body: StockUpdate) -> Stock:
        result = self.repository.update(stock_id, body)
        self.cache.delete(stock_id)
        return result
