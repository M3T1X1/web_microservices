from typing import Annotated
from urllib.error import HTTPError, URLError
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import ValidationError

from app.models.order import Order, OrderCreate
from app.models.stock import Stock
from app.models.shipment import DispatchRequest
from app.security import forwarded_headers, require_roles
from app.services.orders import OrderService


router = APIRouter(prefix="/orders")


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


OrderServiceDep = Annotated[OrderService, Depends(get_order_service)]


@router.post("/consume-events", dependencies=[Depends(require_roles("SHIPPING"))],
             summary="Odbierz oczekujące powiadomienia o wydaniu przesyłek")
def consume_events(service: OrderServiceDep, limit: int = Query(default=50, ge=1, le=500)):
    return service.consume_events(limit)


@router.post("/consume-reservation-events", dependencies=[Depends(require_roles("WAREHOUSE"))],
             summary="Odbierz wyniki rezerwacji z Inventory")
def consume_reservation_events(service: OrderServiceDep, limit: int = Query(default=50, ge=1, le=500)):
    return service.consume_reservation_events(limit)


@router.post("", response_model=Order, status_code=201, dependencies=[Depends(require_roles("WAREHOUSE"))])
def create_order(body: OrderCreate, service: OrderServiceDep) -> Order:
    return service.create(body)


@router.get("", response_model=list[Order], dependencies=[Depends(require_roles("WAREHOUSE"))])
def list_orders(service: OrderServiceDep) -> list[Order]:
    return service.list()


@router.get("/stock-check/{stock_id}", dependencies=[Depends(require_roles("WAREHOUSE"))])
async def check_stock(stock_id: UUID, request: Request):
    discovery = request.app.state.eureka_client
    if discovery is None:
        raise HTTPException(503, "Inventory Service is unavailable")
    try:
        payload = await discovery.do_service(
            "INVENTORY-SERVICE", f"/stocks/{stock_id}",
            return_type="json", prefer_ip=True, timeout=3, **({"headers": forwarded_headers(request)} if request.headers.get("authorization") else {}),
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


@router.get("/{order_id}", response_model=Order, dependencies=[Depends(require_roles("WAREHOUSE"))])
def get_order(order_id: UUID, response: Response, service: OrderServiceDep) -> Order:
    result, source = service.get(order_id)
    response.headers["X-Cache"] = source
    return result


@router.post("/{order_id}/pack", response_model=Order, dependencies=[Depends(require_roles("WAREHOUSE"))])
def pack_order(order_id: UUID, service: OrderServiceDep) -> Order:
    return service.pack(order_id)


@router.post("/{order_id}/reserve", response_model=Order, status_code=202,
             dependencies=[Depends(require_roles("WAREHOUSE"))])
def reserve_order(order_id: UUID, service: OrderServiceDep):
    return service.reserve(order_id)


@router.post("/{order_id}/sync-reservation", response_model=Order,
             dependencies=[Depends(require_roles("WAREHOUSE"))])
def sync_reservation(order_id: UUID, service: OrderServiceDep):
    return service.sync_reservation(order_id)


@router.post("/{order_id}/cancel", response_model=Order,
             dependencies=[Depends(require_roles("WAREHOUSE"))], summary="Anuluj RESERVED przed pakowaniem")
def cancel_order(order_id: UUID, service: OrderServiceDep):
    return service.cancel(order_id)


@router.post("/{order_id}/sync-cancellation", response_model=Order,
             dependencies=[Depends(require_roles("WAREHOUSE"))], summary="Ponow publikacje anulowania")
def sync_cancellation(order_id: UUID, service: OrderServiceDep):
    return service.sync_cancellation(order_id)


@router.get("/{order_id}/shipping-check", response_model=Order, dependencies=[Depends(require_roles("SHIPPING"))])
def shipping_check(order_id: UUID, service: OrderServiceDep):
    # Decyzja o wysyłce zawsze czyta bazę, nie potencjalnie nieaktualny cache.
    return service.repository.get(order_id)


@router.post("/{order_id}/dispatch", response_model=Order, dependencies=[Depends(require_roles("SHIPPING"))])
def dispatch(order_id: UUID, body: DispatchRequest, service: OrderServiceDep):
    return service.dispatch(order_id, body.shipment_id)
