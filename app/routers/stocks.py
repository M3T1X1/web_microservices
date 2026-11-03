from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.models.stock import Reservation, Stock, StockUpdate
from app.services.stocks import StockService


router = APIRouter(prefix="/stocks")


def get_stock_service(request: Request) -> StockService:
    return request.app.state.stock_service


StockServiceDep = Annotated[StockService, Depends(get_stock_service)]


@router.get("", response_model=list[Stock])
def list_stocks(service: StockServiceDep) -> list[Stock]:
    return service.list()


@router.get("/{stock_id}", response_model=Stock)
def get_stock(stock_id: UUID, response: Response, service: StockServiceDep) -> Stock:
    result, source = service.get(stock_id)
    response.headers["X-Cache"] = source
    return result


@router.patch("/{stock_id}", response_model=Stock)
def update_stock(stock_id: UUID, body: StockUpdate, service: StockServiceDep) -> Stock:
    return service.update(stock_id, body)


@router.post("/{stock_id}/reservations", response_model=Reservation, status_code=201)
def create_reservation(
    stock_id: UUID, body: Reservation, service: StockServiceDep
) -> Reservation:
    return service.reserve(stock_id, body)
