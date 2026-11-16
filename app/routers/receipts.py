from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.models.receipt import Receipt, ReceiptCreate
from app.services.receipts import ReceiptService


router = APIRouter(prefix="/receipts", tags=["Inventory — przyjęcia"])


def get_receipt_service(request: Request) -> ReceiptService:
    return request.app.state.receipt_service


ReceiptServiceDep = Annotated[ReceiptService, Depends(get_receipt_service)]


@router.post("", response_model=Receipt, status_code=201)
def create_receipt(body: ReceiptCreate, service: ReceiptServiceDep) -> Receipt:
    return service.create(body)


@router.get("", response_model=list[Receipt])
def list_receipts(service: ReceiptServiceDep) -> list[Receipt]:
    return service.list()


@router.get("/{receipt_id}", response_model=Receipt)
def get_receipt(receipt_id: UUID, service: ReceiptServiceDep) -> Receipt:
    return service.get(receipt_id)


@router.post("/{receipt_id}/accept", response_model=Receipt)
def accept_receipt(receipt_id: UUID, service: ReceiptServiceDep) -> Receipt:
    return service.accept(receipt_id)


@router.post("/{receipt_id}/post", response_model=Receipt)
def post_receipt(receipt_id: UUID, service: ReceiptServiceDep) -> Receipt:
    return service.post(receipt_id)


@router.post("/{receipt_id}/cancel", response_model=Receipt)
def cancel_receipt(receipt_id: UUID, service: ReceiptServiceDep) -> Receipt:
    return service.cancel(receipt_id)
