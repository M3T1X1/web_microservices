from fastapi import APIRouter, Depends, Query, Request
from app.models.allocation import ReservationBatch
from app.security import require_roles

router = APIRouter(prefix="/reservations", dependencies=[Depends(require_roles("WAREHOUSE"))])


@router.post("", include_in_schema=False)
def reserve(body: ReservationBatch, request: Request):
    return request.app.state.allocation_service.reserve(body)


@router.post("/consume-events", summary="Odbierz zlecenia rezerwacji i wyslij wyniki")
def consume_events(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    return request.app.state.reservation_event_service.consume(limit)


@router.post("/sync-events", summary="Ponow publikacje oczekujacych wynikow rezerwacji")
def sync_events(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    return request.app.state.reservation_event_service.sync(limit)


@router.post("/consume-shipment-events", summary="Odbierz wydania przesylek i rozlicz stan magazynowy")
def consume_shipment_events(request: Request, limit: int = Query(default=50, ge=1, le=500)):
    return request.app.state.inventory_shipping_service.consume(limit)
