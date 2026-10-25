from uuid import UUID

from fastapi import APIRouter, HTTPException, Request


router = APIRouter(prefix="/stocks")


@router.get("")
def list_stocks(request: Request) -> list[dict[str, object]]:
    return list(request.app.state.items.values())


@router.get("/{stock_id}")
def get_stock(stock_id: UUID, request: Request) -> dict[str, object]:
    stock = request.app.state.items.get(stock_id)
    if stock is None:
        raise HTTPException(status_code=404, detail="Stock not found")
    return stock
