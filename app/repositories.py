"""Wzorzec repozytorium z demo.py LAB2 zastosowany do Inventory i Outbound."""
from __future__ import annotations

import builtins
import sqlite3
from contextlib import contextmanager
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db_models import OrderRow, ReceiptRow, StockRow
from app.models.order import Order, OrderCreate
from app.models.receipt import Receipt, ReceiptCreate
from app.models.stock import Reservation, Stock, StockUpdate


@contextmanager
def write_transaction(sessions):
    # Jak w ZIP: nowa sesja, commit po sukcesie, rollback po wyjątku, close.
    with sessions.begin() as db:
        if db.get_bind().dialect.name == "sqlite":
            # Własne rozszerzenie: blokada PRZED odczytem reguły także między procesami.
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield db


def stock_payload(row: StockRow) -> Stock:
    return Stock(id=row.id, sku=row.sku, location=row.location,
                 quantity=row.quantity, reservations=row.reservations)


def order_payload(row: OrderRow) -> Order:
    return Order(id=row.id, recipient_name=row.recipient_name,
                 recipient_phone=row.recipient_phone, recipient_address=row.recipient_address,
                 status=row.status, items=row.items)


def receipt_payload(row: ReceiptRow) -> Receipt:
    return Receipt(id=row.id, supplier=row.supplier, status=row.status, items=row.items)


class StockRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def seed(self):
        with write_transaction(self.sessions) as db:
            if db.scalar(select(StockRow.id).limit(1)) is None:
                stock = Stock(sku="SKU-001", location="A-01", quantity=10)
                db.add(StockRow(**{**stock.model_dump(mode="json"), "id": stock.id}))

    def list(self) -> list[Stock]:
        with self.sessions() as db:
            return [stock_payload(row) for row in db.scalars(select(StockRow).order_by(StockRow.id))]

    def get(self, stock_id: UUID) -> Stock:
        with self.sessions() as db:
            return stock_payload(self._find(db, stock_id))

    def reserve(self, stock_id: UUID, reservation: Reservation) -> Reservation:
        with write_transaction(self.sessions) as db:
            row = self._find(db, stock_id, lock=True)
            reserved = sum(item["quantity"] for item in row.reservations)
            if reservation.quantity > row.quantity - reserved:
                raise HTTPException(409, "Insufficient available stock")
            row.reservations = [*row.reservations, reservation.model_dump(mode="json")]
        return reservation.model_copy(deep=True)

    def update(self, stock_id: UUID, body: StockUpdate) -> Stock:
        with write_transaction(self.sessions) as db:
            row = self._find(db, stock_id, lock=True)
            if body.quantity < sum(item["quantity"] for item in row.reservations):
                raise HTTPException(409, "Quantity cannot be lower than reserved stock")
            row.quantity = body.quantity
            result = stock_payload(row)
        return result

    @staticmethod
    def _find(db, stock_id, lock=False):
        query = select(StockRow).where(StockRow.id == stock_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "Stock not found")
        return row


class ReceiptRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def create(self, body: ReceiptCreate) -> Receipt:
        receipt = Receipt(**body.model_dump())
        with write_transaction(self.sessions) as db:
            db.add(ReceiptRow(**{**receipt.model_dump(mode="json"), "id": receipt.id}))
        return receipt

    def list(self) -> list[Receipt]:
        with self.sessions() as db:
            return [receipt_payload(row) for row in db.scalars(select(ReceiptRow).order_by(ReceiptRow.id))]

    def get(self, receipt_id: UUID) -> Receipt:
        with self.sessions() as db:
            return receipt_payload(self._find(db, receipt_id))

    def accept(self, receipt_id: UUID) -> Receipt:
        return self._change_status(receipt_id, ("NEW",), "ACCEPTED",
                                   "Only NEW receipts can be accepted")

    def cancel(self, receipt_id: UUID) -> Receipt:
        return self._change_status(receipt_id, ("NEW", "ACCEPTED"), "CANCELLED",
                                   "Only NEW or ACCEPTED receipts can be cancelled")

    def post(self, receipt_id: UUID) -> tuple[Receipt, builtins.list[UUID]]:
        with write_transaction(self.sessions) as db:
            row = self._find(db, receipt_id, lock=True)
            if row.status != "ACCEPTED":
                raise HTTPException(409, "Only ACCEPTED receipts can be posted")
            receipt = receipt_payload(row)
            totals = {}
            for item in receipt.items:
                key = (item.sku, item.target_location)
                totals[key] = totals.get(key, 0) + item.quantity_received
            changed_stock_ids = []
            for (sku, location), quantity in sorted(totals.items()):
                stock_row = db.scalar(select(StockRow).where(
                    StockRow.sku == sku, StockRow.location == location,
                ).with_for_update())
                if stock_row is None:
                    stock = Stock(sku=sku, location=location, quantity=quantity)
                    stock_row = StockRow(**{**stock.model_dump(mode="json"), "id": stock.id})
                    db.add(stock_row)
                else:
                    stock_row.quantity += quantity
                changed_stock_ids.append(stock_row.id)
            row.status = "POSTED"
            result = receipt_payload(row)
        return result, changed_stock_ids

    def _change_status(self, receipt_id, allowed, status, message):
        with write_transaction(self.sessions) as db:
            row = self._find(db, receipt_id, lock=True)
            if row.status not in allowed:
                raise HTTPException(409, message)
            row.status = status
            result = receipt_payload(row)
        return result

    @staticmethod
    def _find(db, receipt_id, lock=False):
        query = select(ReceiptRow).where(ReceiptRow.id == receipt_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "Receipt not found")
        return row


class OrderRepository:
    def __init__(self, sessions):
        self.sessions = sessions

    def create(self, body: OrderCreate) -> Order:
        order = Order(**body.model_dump())
        try:
            with write_transaction(self.sessions) as db:
                db.add(OrderRow(**{**order.model_dump(mode="json"), "id": order.id}))
        except IntegrityError as exc:
            # Demo mapuje wszystkie IntegrityError; tu tylko rozpoznaną kolizję PK.
            if (isinstance(exc.orig, sqlite3.IntegrityError)
                    and str(exc.orig) == "UNIQUE constraint failed: orders.id"):
                raise HTTPException(409, "Order already exists") from exc
            raise
        return order

    def list(self) -> list[Order]:
        with self.sessions() as db:
            return [order_payload(row) for row in db.scalars(select(OrderRow).order_by(OrderRow.id))]

    def get(self, order_id: UUID) -> Order:
        with self.sessions() as db:
            return order_payload(self._find(db, order_id))

    def pack(self, order_id: UUID) -> Order:
        with write_transaction(self.sessions) as db:
            row = self._find(db, order_id, lock=True)
            if row.status != "RESERVED":
                raise HTTPException(409, "Order must be reserved before packing")
            row.status = "PACKED"
            result = order_payload(row)
        return result

    @staticmethod
    def _find(db, order_id, lock=False):
        query = select(OrderRow).where(OrderRow.id == order_id)
        row = db.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise HTTPException(404, "Order not found")
        return row
