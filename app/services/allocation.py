"""Atomowe, ponawialne rezerwowanie wszystkich pozycji jednego zamówienia."""
from fastapi import HTTPException
from sqlalchemy import select
from app.db_models import ReservationBatchRow, StockRow
from app.models.order import normalized_items
from app.repositories import write_transaction


class AllocationService:
    def __init__(self, sessions, cache):
        self.sessions, self.cache = sessions, cache

    def reserve(self, body):
        with write_transaction(self.sessions) as db:
            changed = self.reserve_in_transaction(db, body)
        for stock_id in changed:
            self.cache.delete(stock_id)
        return {"order_id": str(body.order_id), "status": "RESERVED"}

    def reserve_in_transaction(self, db, body):
        """Ta sama alokacja dla HTTP i konsumenta; transakcja nalezy do wywolujacego."""
        normalized = normalized_items(body.items)
        changed = []
        previous = db.get(ReservationBatchRow, body.order_id)
        if previous:
            if previous.items != normalized:
                raise HTTPException(409, "Order reservation payload differs from the original")
            # Przy ponowieniu nadal usuwamy cache rekordow dotknietych rezerwacja.
            return [row.id for row in db.scalars(select(StockRow))
                    if any(r["order_id"] == str(body.order_id) for r in row.reservations)]
        for item in normalized:
            remaining = item["quantity"]
            rows = db.scalars(select(StockRow).where(StockRow.sku == item["sku"]).order_by(StockRow.id).with_for_update()).all()
            for row in rows:
                available = row.quantity - sum(r["quantity"] for r in row.reservations)
                take = min(remaining, available)
                if take > 0:
                    row.reservations = [*row.reservations, {"order_id": str(body.order_id), "quantity": take}]
                    changed.append(row.id)
                    remaining -= take
                if not remaining:
                    break
            if remaining:
                raise HTTPException(409, "Insufficient available stock")
        db.add(ReservationBatchRow(order_id=body.order_id, items=normalized))
        return changed
