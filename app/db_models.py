"""Modele ORM, niezależne od kontraktu Pydantic i od siebie nawzajem."""
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, Integer, String, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class InventoryBase(DeclarativeBase):
    pass


class OutboundBase(DeclarativeBase):
    pass


class StockRow(InventoryBase):
    __tablename__ = "stocks"
    __table_args__ = (CheckConstraint("quantity >= 0", name="stock_quantity_nonnegative"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    sku: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reservations: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class OrderRow(OutboundBase):
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
