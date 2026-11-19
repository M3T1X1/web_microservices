from fastapi import APIRouter, Depends, Request
from app.models.allocation import ReservationBatch
from app.security import require_roles

router = APIRouter(prefix="/reservations", dependencies=[Depends(require_roles("WAREHOUSE"))])


@router.post("")
def reserve(body: ReservationBatch, request: Request):
    return request.app.state.allocation_service.reserve(body)
