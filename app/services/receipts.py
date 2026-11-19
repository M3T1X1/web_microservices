from uuid import UUID

from app.cache import JsonCache
from app.models.receipt import Receipt, ReceiptCreate
from app.repositories import ReceiptRepository


class ReceiptService:
    def __init__(self, repository: ReceiptRepository, stock_cache: JsonCache):
        self.repository, self.stock_cache = repository, stock_cache

    def create(self, body: ReceiptCreate) -> Receipt:
        return self.repository.create(body)

    def list(self) -> list[Receipt]:
        return self.repository.list()

    def get(self, receipt_id: UUID) -> Receipt:
        return self.repository.get(receipt_id)

    def accept(self, receipt_id: UUID) -> Receipt:
        return self.repository.accept(receipt_id)

    def cancel(self, receipt_id: UUID) -> Receipt:
        return self.repository.cancel(receipt_id)

    def post(self, receipt_id: UUID) -> Receipt:
        receipt, stock_ids = self.repository.post(receipt_id)
        for stock_id in stock_ids:
            self.stock_cache.delete(stock_id)
        return receipt
