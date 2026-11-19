from uuid import UUID
from fastapi import APIRouter, Depends, Request

from app.models.shipment import Shipment, ShipmentCreate
from app.security import forwarded_headers, require_roles

router = APIRouter(prefix="/shipments", tags=["Shipping"], dependencies=[Depends(require_roles("SHIPPING"))])


@router.post("", response_model=Shipment, status_code=201)
async def create(body: ShipmentCreate, request: Request):
    return await request.app.state.shipping_service.create(body, forwarded_headers(request))


@router.get("", response_model=list[Shipment])
def list_shipments(request: Request):
    return request.app.state.shipping_service.list()


@router.get("/{shipment_id}", response_model=Shipment)
def get(shipment_id: UUID, request: Request):
    return request.app.state.shipping_service.get(shipment_id)


@router.post("/{shipment_id}/ready", response_model=Shipment)
async def ready(shipment_id: UUID, request: Request):
    return await request.app.state.shipping_service.ready(shipment_id, forwarded_headers(request))


@router.post("/{shipment_id}/hand-over", response_model=Shipment)
async def hand_over(shipment_id: UUID, request: Request):
    return await request.app.state.shipping_service.hand_over(shipment_id, forwarded_headers(request))


@router.post("/{shipment_id}/cancel", response_model=Shipment)
def cancel(shipment_id: UUID, request: Request):
    return request.app.state.shipping_service.change_status(shipment_id, {"NEW", "READY"}, "CANCELLED")


@router.post("/{shipment_id}/sync", response_model=Shipment)
async def sync(shipment_id: UUID, request: Request):
    return await request.app.state.shipping_service.sync(shipment_id, forwarded_headers(request))
