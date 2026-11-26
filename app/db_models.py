"""Własna domena na Base z database.py dostarczonego w ZIP LAB2."""
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class StockRow(Base):
    __tablename__ = "stocks"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="stock_quantity_nonnegative"),
        Index("uq_stocks_sku_location", "sku", "location", unique=True),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reservations: Mapped[list] = mapped_column(JSON, nullable=False)


class ReceiptRow(Base):
    __tablename__ = "receipts"
    __table_args__ = (CheckConstraint(
        "status IN ('NEW', 'ACCEPTED', 'POSTED', 'CANCELLED')",
        name="receipt_status_valid",
    ),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    supplier: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items: Mapped[list] = mapped_column(JSON, nullable=False)


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (CheckConstraint(
        "status IN ('NEW', 'RESERVATION_PENDING', 'RESERVATION_REJECTED', 'RESERVED', 'PACKED', 'SHIPPED', 'CANCELLED')",
        name="order_status_valid",
    ),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    recipient_name: Mapped[str] = mapped_column(String, nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String, nullable=False)
    recipient_address: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items: Mapped[list] = mapped_column(JSON, nullable=False)

    reservation_attempt_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    reservation_error: Mapped[str | None] = mapped_column(String, nullable=True)
    reservation_notification_pending: Mapped[bool] = mapped_column(nullable=False, default=False)


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)


class ShipmentRow(Base):
    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint("weight_kg > 0", name="shipment_positive_weight"),
        CheckConstraint("status IN ('NEW','READY','HANDED_OVER','CANCELLED')", name="shipment_valid_status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    order_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)
    destination_address: Mapped[str] = mapped_column(String, nullable=False)
    courier_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    weight_kg: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    handed_over_at: Mapped[str | None] = mapped_column(String, nullable=True)
    notification_pending: Mapped[bool] = mapped_column(nullable=False, default=False)


class OrderDispatchRow(Base):
    __tablename__ = "order_dispatches"
    order_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    shipment_id: Mapped[UUID] = mapped_column(Uuid, unique=True, nullable=False)


class ReservationBatchRow(Base):
    __tablename__ = "reservation_batches"
    __table_args__ = (CheckConstraint("status IN ('RESERVED','CONSUMED')", name="reservation_batch_status_valid"),)
    order_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="RESERVED")
    shipment_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True, nullable=True)
    consumed_stock_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ProcessedEventRow(Base):
    __tablename__ = "processed_events"
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)


class OutboxEventRow(Base):
    __tablename__ = "event_outbox"
    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    routing_key: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    published: Mapped[bool] = mapped_column(nullable=False, default=False)
