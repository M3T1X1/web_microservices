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
        "status IN ('NEW', 'RESERVED', 'PACKED', 'SHIPPED', 'CANCELLED')",
        name="order_status_valid",
    ),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    recipient_name: Mapped[str] = mapped_column(String, nullable=False)
    recipient_phone: Mapped[str] = mapped_column(String, nullable=False)
    recipient_address: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    items: Mapped[list] = mapped_column(JSON, nullable=False)
