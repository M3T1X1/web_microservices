from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.models.stock import Reservation, Stock
from app.services.stocks import StockService


router = APIRouter(prefix="/stocks")


def get_stock_service(request: Request) -> StockService:
    return request.app.state.stock_service


StockServiceDep = Annotated[StockService, Depends(get_stock_service)]


@router.get("", response_model=list[Stock])
def list_stocks(service: StockServiceDep) -> list[Stock]:
    return service.list()


@router.get("/{stock_id}", response_model=Stock)
def get_stock(stock_id: UUID, service: StockServiceDep) -> Stock:
    return service.get(stock_id)


@router.post("/{stock_id}/reservations", response_model=Reservation, status_code=201)
def create_reservation(
    stock_id: UUID, body: Reservation, service: StockServiceDep
) -> Reservation:
    return service.reserve(stock_id, body)
