"""Atomowe, ponawialne rezerwowanie wszystkich pozycji jednego zamówienia."""
from fastapi import HTTPException
from sqlalchemy import select
from app.db_models import ReservationBatchRow, StockRow
from app.repositories import write_transaction


class AllocationService:
    def __init__(self, sessions, cache):
        self.sessions, self.cache = sessions, cache

    def reserve(self, body):
        totals = {}
        for item in body.items:
            totals[item.sku] = totals.get(item.sku, 0) + item.quantity
        normalized = [{"sku": sku, "quantity": qty} for sku, qty in sorted(totals.items())]
        changed = []
        with write_transaction(self.sessions) as db:
            previous = db.get(ReservationBatchRow, body.order_id)
            if previous:
                if previous.items != normalized:
                    raise HTTPException(409, "Order reservation payload differs from the original")
                return {"order_id": str(body.order_id), "status": "RESERVED"}
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
        for stock_id in changed:
            self.cache.delete(stock_id)
        return {"order_id": str(body.order_id), "status": "RESERVED"}
