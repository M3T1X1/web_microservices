from typing import Annotated
from urllib.error import HTTPError, URLError
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from app.models.order import Order, OrderCreate
from app.models.stock import Stock
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


@router.get("/stock-check/{stock_id}")
async def check_stock(stock_id: UUID, request: Request):
    discovery = request.app.state.eureka_client
    if discovery is None:
        raise HTTPException(503, "Inventory Service is unavailable")
    try:
        payload = await discovery.do_service(
            "INVENTORY-SERVICE", f"/stocks/{stock_id}",
            return_type="json", prefer_ip=True, timeout=3,
        )
        stock = Stock.model_validate(payload)
        if stock.id != stock_id:
            raise ValueError("Inventory returned another stock")
    except HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(404, "Stock not found in Inventory") from exc
        raise HTTPException(502, "Inventory Service returned an HTTP error") from exc
    except (URLError, TimeoutError, ConnectionError) as exc:
        raise HTTPException(503, "Inventory Service is unavailable") from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise HTTPException(502, "Inventory Service returned invalid stock data") from exc
    reserved = sum(item.quantity for item in stock.reservations)
    return {"stock": stock, "available_quantity": stock.quantity - reserved}


@router.get("/{order_id}", response_model=Order)
def get_order(order_id: UUID, response: Response, service: OrderServiceDep) -> Order:
    result, source = service.get(order_id)
    response.headers["X-Cache"] = source
    return result


@router.post("/{order_id}/pack", response_model=Order)
def pack_order(order_id: UUID, service: OrderServiceDep) -> Order:
    return service.pack(order_id)
