from uuid import UUID

from fastapi import FastAPI, HTTPException


def create_app() -> FastAPI:
    app = FastAPI(
        title="My Service",
        description="Student microservice for LAB1",
        version="0.1.0",
    )
    # Każda instancja aplikacji ma własny magazyn w pamięci.
    # Zastąp "items" nazwą wynikającą z twojej domeny.
    app.state.items = {}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "replace-me"}

    # TODO: dodaj tutaj modele/endpointy własnej domeny lub dołącz routery.
    # Magazyn twórz tylko raz na aplikację; endpointy korzystają z app.state.
    @app.get("/stocks")
    def list_stocks() -> list[dict[str, object]]:
        return list(app.state.items.values())

    @app.get("/stocks/{stock_id}")
    def get_stock(stock_id: UUID) -> dict[str, object]:
        stock = app.state.items.get(stock_id)
        if stock is None:
            raise HTTPException(status_code=404, detail="Stock not found")
        return stock

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
