from threading import Lock
from uuid import UUID

from fastapi import HTTPException

from app.models.stock import Reservation, Stock


class StockService:
    def __init__(self, stocks: dict[UUID, Stock]) -> None:
        self._stocks = stocks
        self._lock = Lock()

    def list(self) -> list[Stock]:
        with self._lock:
            return [stock.model_copy(deep=True) for stock in self._stocks.values()]

    def get(self, stock_id: UUID) -> Stock:
        with self._lock:
            return self._find(stock_id).model_copy(deep=True)

    def reserve(self, stock_id: UUID, reservation: Reservation) -> Reservation:
        # Sprawdzenie dostępności i zapis są jedną operacją.
        with self._lock:
            stock = self._find(stock_id)
            reserved = sum(item.quantity for item in stock.reservations)
            if reservation.quantity > stock.quantity - reserved:
                raise HTTPException(
                    status_code=409, detail="Insufficient available stock"
                )
            stock.reservations.append(reservation.model_copy(deep=True))
            return reservation.model_copy(deep=True)

    def _find(self, stock_id: UUID) -> Stock:
        stock = self._stocks.get(stock_id)
        if stock is None:
            raise HTTPException(status_code=404, detail="Stock not found")
        return stock
