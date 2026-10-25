from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.models.order import Order, OrderCreate
from app.services.orders import OrderService


router = APIRouter(prefix="/orders")


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]


@router.post("", response_model=Order, status_code=201)
def create_order(body: OrderCreate, service: OrderServiceDep) -> Order:
    return service.create(body)


@router.get("", response_model=list[Order])
def list_orders(service: OrderServiceDep) -> list[Order]:
    return service.list()


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: UUID, service: OrderServiceDep) -> Order:
    return service.get(order_id)


@router.post("/{order_id}/pack", response_model=Order)
def pack_order(order_id: UUID, service: OrderServiceDep) -> Order:
    return service.pack(order_id)
