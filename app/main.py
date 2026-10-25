from fastapi import FastAPI

from app.models.stock import Stock
from app.routers import health, orders, stocks
from app.services.orders import OrderService
from app.services.stocks import StockService


def create_app() -> FastAPI:
    app = FastAPI(
        title="My Service",
        description="Student microservice for LAB1",
        version="0.1.0",
    )
    # Każda instancja aplikacji ma własny magazyn w pamięci.
    # Zastąp "items" nazwą wynikającą z twojej domeny.
    app.state.items = {}
    stock = Stock(sku="SKU-001", location="A-01", quantity=10)
    app.state.items[stock.id] = stock
    app.state.stock_service = StockService(app.state.items)
    app.state.orders = {}
    app.state.order_service = OrderService(app.state.orders)

    # TODO: dodaj tutaj modele/endpointy własnej domeny lub dołącz routery.
    # Magazyn twórz tylko raz na aplikację; endpointy korzystają z app.state.
    app.include_router(health.router)
    app.include_router(stocks.router)
    app.include_router(orders.router)

    return app


app = create_app()


# TODO:
# 1. Choose your domain and name the service.
# 2. Add your domain models.
# 3. Add one list endpoint and one detail endpoint.
# 4. Add one create or update endpoint.
# 5. Add a business rule beyond types, required fields and unique identifiers.
# 6. Reject invalid operations before changing state; handle 404, 409 and 422.
# 7. Add at least three domain tests with fresh application state for each test.
# 8. Verify write/read and restart via HTTP; complete the README.
