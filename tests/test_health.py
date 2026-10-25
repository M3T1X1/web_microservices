def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    # Zmień oczekiwaną nazwę razem z nazwą swojej usługi.
    assert response.json() == {"status": "ok", "service": "replace-me"}


# Ten test startowy nie zastępuje trzech własnych testów domenowych.
# Dodaj testy zapisu i odczytu, błędnych danych i konfliktu bez zmiany stanu.
# Co najmniej jeden ma sprawdzać twoją regułę biznesową.
