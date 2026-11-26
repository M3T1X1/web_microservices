"""Atomowe, ponawialne rezerwowanie wszystkich pozycji jednego zamówienia."""
from uuid import UUID

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
            if previous.status == "CONSUMED":
                raise HTTPException(409, "Order reservation has already been consumed")
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

    def consume_in_transaction(self, db, event):
        """Rozchod tylko z zapisanych rezerwacji; wywolujacy zapisuje tez deduplikacje."""
        batch = db.get(ReservationBatchRow, event.order_id)
        if batch is None:
            raise HTTPException(409, "Order has no complete reservation to consume")
        if batch.status == "CONSUMED":
            if batch.shipment_id != event.shipment_id:
                raise HTTPException(409, "Order reservation was consumed by another shipment")
            return [UUID(value) for value in batch.consumed_stock_ids]
        if db.scalar(select(ReservationBatchRow.order_id).where(
                ReservationBatchRow.shipment_id == event.shipment_id)) is not None:
            raise HTTPException(409, "Shipment already consumed another order reservation")

        plan, totals = [], {}
        for row in db.scalars(select(StockRow).order_by(StockRow.id).with_for_update()):
            own = [item for item in row.reservations if item["order_id"] == str(event.order_id)]
            if not own:
                continue
            quantity = sum(item["quantity"] for item in own)
            if quantity <= 0 or row.quantity < sum(item["quantity"] for item in row.reservations):
                raise HTTPException(409, "Stock does not cover its reservations")
            totals[row.sku] = totals.get(row.sku, 0) + quantity
            plan.append((row, quantity))
        actual = [{"sku": sku, "quantity": quantity} for sku, quantity in sorted(totals.items())]
        if actual != normalized_items(batch.items):
            raise HTTPException(409, "Active reservations do not match the complete order reservation")

        # Najpierw sprawdzamy calosc; potem usuwamy tylko rezerwacje tego zamowienia.
        for row, quantity in plan:
            row.quantity -= quantity
            row.reservations = [item for item in row.reservations if item["order_id"] != str(event.order_id)]
        batch.status = "CONSUMED"
        batch.shipment_id = event.shipment_id
        batch.consumed_stock_ids = [str(row.id) for row, _ in plan]
        return [row.id for row, _ in plan]
