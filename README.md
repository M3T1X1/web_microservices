# Własna usługa — LAB1

To szablon do uzupełnienia. Zastąp wskazówki własnymi decyzjami i wynikami. Obowiązuje zakres z instrukcji `lab1.md` otrzymanej wraz z materiałami. Starter zawiera wyłącznie `/health`, pusty magazyn w pamięci i test startowy.

## Uruchomienie i testy

Wymagania: Python 3.12 lub nowszy, PowerShell. Otwórz terminal w katalogu z tym README i `requirements.txt`.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8101
```

W drugim terminalu: `Invoke-RestMethod http://localhost:8101/health`. Dokumentacja: [Swagger UI](http://localhost:8101/docs). Zatrzymanie serwera: `Ctrl+C`. Nie trzeba aktywować środowiska. Zmień `replace-me` w aplikacji i teście na nazwę usługi.

## Struktura kodu

- `app/main.py` — tworzenie aplikacji, magazynów i rejestracja routerów.
- `app/models/order.py` — modele zamówień i walidacja danych.
- `app/models/stock.py` — model stanu i walidacja rezerwacji.
- `app/routers/` — endpointy: `health.py`, `stocks.py`, `orders.py`.
- `app/services/orders.py` — operacje na zamówieniach i reguła pakowania.
- `app/services/stocks.py` — odczyt stanów i kontrola dostępności przy rezerwacji.
- `tests/conftest.py` — wspólny klient ze świeżą aplikacją dla każdego testu.
- `tests/test_*.py` — scenariusze health, Inventory i Outbound.

## 1. Problem i granice

Inventory pomaga pracownikowi magazynu kontrolować ilość i dostępność towaru. Odpowiada za stany, lokalizacje i rezerwacje; nie obsługuje dostawców, kompletacji ani wysyłki.

Pojęcia: **SKU** — kod produktu; **lokalizacja** — miejsce składowania; **rezerwacja** — towar przydzielony zamówieniu; **dostępność** — ilość pomniejszona o rezerwacje.

| Planowana usługa | Odpowiedzialność | Własne dane | Operacje | Potrzebne informacje od innej usługi |
|---|---|---|---|---|
| Inventory| Stany i rezerwacje | `Stock` | Odczyt, aktualizacja stanu, rezerwacja | Identyfikator zamówienia z Outbound |
| Inbound| Przyjęcia towaru | `Receipt` | Rejestracja i zatwierdzenie dostawy | Potwierdzenie aktualizacji stanu z Inventory |
| Outbound| Realizacja zamówień | `Order` | Przyjęcie zamówienia, kompletacja, pakowanie | Dostępność i potwierdzenie rezerwacji z Inventory |
| Shipping| Wysyłka paczek | `Shipment` | Rejestracja przesyłki, wydanie kurierowi | Potwierdzenie spakowania z Outbound |

Każda usługa zapisuje własne dane: Inventory — stany i rezerwacje, Inbound — przyjęcia, Outbound — zamówienia, Shipping — przesyłki. Zmiany cudzych danych wymagają wywołania API.

Przyszłe HTTP: **Outbound → Inventory**, `POST /stocks/{id}/reservations`, żądanie `{"order_id":"22222222-2222-4222-8222-222222222222","quantity":3}`, odpowiedź `201` z utworzoną rezerwacją. Przy niedostępności Inventory zamówienie pozostaje `PENDING`, bez potwierdzenia rezerwacji. Połączenie nie jest implementowane na LAB1.

## 2. Model i reguła biznesowa

### Inventory — `Stock`

`Stock`: `id` — UUID nadawany przez serwer; `sku` i `location` — wymagane, niepuste teksty; `quantity` — wymagana liczba całkowita ≥ 0; `reservations` — lista, początkowo pusta. Każda rezerwacja ma wymagane `order_id` (UUID) i `quantity` (liczba całkowita > 0).

**Reguła:** suma rezerwacji nie może przekraczać stanu. Przy 10 sztukach i 7 zarezerwowanych można zarezerwować kolejne 3, ale nie 4 (`409 Conflict`). Wszystkie warunki odrzucenia sprawdzamy przed zmianą danych.

### Inbound — `Receipt` (planowana usługa)

`Receipt`: `id` — UUID serwera; `supplier` — niepusty tekst; `status` — początkowo `NEW`; `items` — niepusta lista pozycji: `sku`, `target_location` (niepuste teksty), `quantity_received` (liczba całkowita > 0).

**Reguła:** tylko przyjęcie `ACCEPTED` można przekazać do Inventory; po potwierdzeniu otrzymuje status `POSTED`. Niedozwolona operacja zwraca `409` bez zmiany danych.

### Outbound — `Order` (planowana usługa)

`Order`: `id` — UUID serwera; `recipient_name`, `recipient_phone`, `recipient_address` — niepuste teksty; `status` — początkowo `NEW`; `items` — niepusta lista pozycji: `sku` (niepusty tekst), `quantity` (liczba całkowita > 0).

**Reguła:** tylko zamówienie z potwierdzoną rezerwacją (`RESERVED`) można oznaczyć jako spakowane (`PACKED`). Niedozwolona operacja zwraca `409` bez zmiany danych.

### Shipping — `Shipment` (planowana usługa)

`Shipment`: `id` — UUID serwera; `order_id` — UUID zamówienia; `carrier` — niepusty tekst; `weight_kg` — liczba > 0; `status` — początkowo `NEW`; `handed_over_at` — data i godzina wydania, początkowo brak.

**Reguła:** tylko przesyłkę `READY` można wydać kurierowi; otrzymuje wtedy status `HANDED_OVER` i czas wydania. Niedozwolona operacja zwraca `409` bez zmiany danych.

## 3. Kontrakt API

Kontrakt API:

| Metoda | Ścieżka | Znaczenie | Sukces | Błędy |
|---|---|---|---|---|
| GET | `/health` | Status i nazwa usługi | 200 | — |
| GET | `/stocks` | Lista stanów | 200 | — |
| GET | `/stocks/{id}` | Szczegóły stanu | 200 | 404, 422 |
| POST | `/stocks/{id}/reservations` | Rezerwacja towaru | 201 | 404, 409, 422 |

Przykłady (`{id}` oznacza UUID):

- `GET /health` → `200`, `{"status":"ok","service":"replace-me"}`.
- `GET /stocks` przy pustym magazynie → `200`, `[]`.
- `GET /stocks/{id}` dla istniejącego stanu → `200`, `{"id":"11111111-1111-4111-8111-111111111111","sku":"SKU-001","location":"A-01","quantity":10,"reservations":[]}`.
- `POST /stocks/{id}/reservations`, JSON `{"order_id":"22222222-2222-4222-8222-222222222222","quantity":3}` → `201` z takim samym JSON-em utworzonej rezerwacji.
- Odczyt nieistniejącego UUID → `404`, `{"detail":"Stock not found"}`.
- Rezerwacja 11 z 10 dostępnych sztuk → `409`, `{"detail":"Insufficient available stock"}`.
- Rezerwacja z `quantity: 0` → `422`, np.:

```json
{"detail":[{"type":"greater_than","loc":["body","quantity"],"msg":"Input should be greater than 0","input":0,"ctx":{"gt":0}}]}
```

`200` oznacza odczyt, `201` utworzenie rezerwacji, `404` brak zasobu, `409` konflikt z dostępnością, `422` błędne dane. Reguła: rezerwacja nie przekracza dostępnego stanu; odrzucenie nie zmienia danych. `detail` w `HTTPException` jest tutaj tekstem, a przy walidacji FastAPI — listą błędów.

## 4. Testy i stan danych

Testy Inventory i Outbound sprawdzają zapis i odczyt oraz odrzucanie operacji bez zmiany danych: `404` dla brakującego zasobu, `409` dla braku dostępności lub pakowania bez rezerwacji, `422` dla błędnych UUID i ilości. Każdy test używa nowego `create_app()`. Początkowo jest produkt `SKU-001`, lokalizacja `A-01`, ilość 10, bez rezerwacji. Zamówienia są początkowo puste; integracja potwierdzająca rezerwację z Inventory pozostaje do implementacji.

`S` — UUID tego stanu; `O1` — `22222222-2222-4222-8222-222222222222`, `O2` — `33333333-3333-4333-8333-333333333333`. W żądaniach podstawiamy te UUID.

| Scenariusz | Stan początkowy i dokładne żądanie | Oczekiwany status, treść i stan | Wynik rzeczywisty | Reset danych |
|---|---|---|---|---|
| Health | `GET /health` | `200`, `{"status":"ok","service":"replace-me"}` | Zgodny | `create_app()` |
| Lista i szczegóły | Stan początkowy; `GET /stocks`, `GET /stocks/S` | `200`, jeden produkt i jego szczegóły, ilość 10 | Zgodny | `create_app()` |
| Zapis → odczyt | Stan początkowy; `POST /stocks/S/reservations`, `{"order_id":"O1","quantity":3}`, potem `GET /stocks/S` | `201`, JSON rezerwacji; odczyt `200`, rezerwacja 3, ilość 10 | Zgodny | `create_app()` |
| Brak zasobu | `GET /stocks/99999999-9999-4999-8999-999999999999` | `404`, `{"detail":"Stock not found"}` | Zgodny | `create_app()` |
| Błędne dane | Stan początkowy; POST jak wyżej, `{"order_id":"O1","quantity":0}`, potem odczyt | `422`, błąd pola `quantity`; stan bez zmian | Zgodny | `create_app()` |
| Konflikt | Zarezerwowane 7; POST jak wyżej, `{"order_id":"O2","quantity":4}`, potem odczyt | `409`, `Insufficient available stock`; nadal tylko rezerwacja 7 | Zgodny | `create_app()` |
| Reguła biznesowa | Stan początkowy; POST rezerwacji O1 na 7, następnie O2 na 4 | `201`, potem `409`; ilość 10, zarezerwowane 7 | Zgodny | `create_app()` |
| Restart | Rezerwacja 3 i odczyt przez HTTP; restart Uvicorna; odczyt listy i starego S | Przed restartem rezerwacja 3; po restarcie produkt z nowym UUID, ilość 10, brak rezerwacji; stary S daje `404` | Zgodny | Restart procesu |

Wynik `.\.venv\Scripts\python.exe -m pytest -q`: **22 passed** (Inventory, Outbound i health).

Eksperyment HTTP wykonano bez `--reload` i zmian kodu. Rezerwacja przetrwała kolejne żądania, ale zniknęła po restarcie: dane są w pamięci procesu, a `create_app()` odtwarza produkt początkowy.

## 5. Porównanie architektur

```text
Monolit:
[1 proces: Inventory + Inbound + Outbound + Shipping] -> [wspólna baza]

Modularny monolit (strzałki wewnątrz = wywołania funkcji):
[1 proces: Inbound -> Inventory <- Outbound <- Shipping] -> [baza: tabele modułów]

Mikrousługi (każdy nawias = osobny proces i własna baza):
[Inbound/Receipt] --HTTP--> [Inventory/Stock] <--HTTP-- [Outbound/Order] <--HTTP-- [Shipping/Shipment]
```

1. Inventory ma własne dane i API oraz może działać w osobnym procesie; moduł jest częścią aplikacji.
2. W monolicie wszystkie cztery funkcje działałyby w jednym procesie ze wspólną bazą.
3. Podział daje niezależne wdrażanie, ale dodaje opóźnienia sieci i awarie zależności; brak Inventory blokuje rezerwacje.
4. Modularny monolit — prostsze wdrażanie i utrzymanie przy małej skali.

## 6. Dokumentacja wykorzystana podczas pracy

| Odnośnik do fragmentu | Rozwiązywany problem | Co zastosowano w kodzie | Jak sprawdzono działanie |
|---|---|---|---|
| [FastAPI — Path operation](https://fastapi.tiangolo.com/tutorial/first-steps/#step-3-create-a-path-operation) | Obsługa żądania GET | `@app.get("/health")` | Test sprawdza `200` i JSON |
| [FastAPI — TestClient](https://fastapi.tiangolo.com/tutorial/testing/#using-testclient) | Testowanie API | `TestClient(create_app())` | `pytest tests/test_health.py`: **1 passed** |

## 7. Ograniczenia, autorzy i AI

Dane są w pamięci, znikają po restarcie i nie są współdzielone między instancjami. Brakuje trwałej bazy, endpointów domenowych i integracji usług.

- AI: ChatGPT pomógł w testach i endpointach; weryfikacja przez przegląd kodu i test `/health`.

## 8. Podział systemu na usługi

### Serwis Stanów Magazynowych (Inventory)

**Usługa odpowiada za:**
- Przechowywanie ilości i lokalizacji produktów.
- Rezerwowanie towaru dla zamówień i zwalnianie rezerwacji.
- Aktualizowanie stanów przy przyjęciu, przesunięciu i wydaniu towaru.

**Usługa nie odpowiada za:**
- Rejestrowanie dostaw od dostawców.
- Kompletowanie i pakowanie zamówień.
- Obsługę wysyłki paczek.

### Serwis Przyjęć Towaru (Inbound)

**Usługa odpowiada za:**
- Rejestrowanie dostaw i danych dostawcy.
- Zapisywanie otrzymanych ilości i lokalizacji docelowych.
- Zgłaszanie przyjęcia towaru do Inventory.

**Usługa nie odpowiada za:**
- Prowadzenie globalnych stanów magazynowych.
- Wystawianie dokumentów PZ.
- Realizację zamówień i wysyłkę paczek.

### Serwis Realizacji Zamówień (Outbound)

**Usługa odpowiada za:**
- Przyjmowanie zamówień i danych odbiorcy.
- Zlecanie rezerwacji towaru w Inventory.
- Tworzenie list kompletacyjnych i potwierdzanie spakowania.

**Usługa nie odpowiada za:**
- Samodzielne zmienianie stanów magazynowych.
- Rejestrowanie dostaw od dostawców.
- Obsługę przekazania paczek kurierowi.

### Serwis Wysyłki i Kurierów (Shipping)

**Usługa odpowiada za:**
- Rejestrowanie przesyłek dla spakowanych zamówień.
- Zapisywanie przewoźnika i wagi paczki.
- Potwierdzanie przekazania paczki kurierowi i powiadamianie pozostałych usług.

**Usługa nie odpowiada za:**
- Kompletowanie i pakowanie zamówień.
- Zarządzanie stanami magazynowymi.
- Generowanie etykiet i obsługę numerów śledzenia.

## 9. Modele danych

Każdy mikroserwis ma jedną tabelę we własnej bazie danych. Identyfikatory UUID nadaje serwer. Pola oznaczone `↳` są elementami JSON, a nie osobnymi kolumnami.

### Inventory — tabela `Stock`

Jeden rekord opisuje stan produktu w konkretnej lokalizacji.

| Pole | Opis |
|---|---|
| `id` | Identyfikator rekordu |
| `sku` | Kod produktu |
| `location` | Lokalizacja produktu |
| `quantity` | Liczba sztuk, razem z zarezerwowanymi |
| `reservations` | Lista aktywnych rezerwacji — JSON |
| ↳ `order_id` | Identyfikator zamówienia |
| ↳ `quantity` | Liczba zarezerwowanych sztuk |

Lista rezerwacji może być pusta. Dostępny stan to `quantity` pomniejszone o sumę rezerwacji.

### Inbound — tabela `Receipt`

Jeden rekord opisuje dostawę wraz z produktami.

| Pole | Opis |
|---|---|
| `id` | Identyfikator przyjęcia |
| `supplier` | Nazwa dostawcy |
| `status` | Etap obsługi dostawy |
| `items` | Lista produktów w dostawie — JSON |
| ↳ `sku` | Kod produktu |
| ↳ `quantity_received` | Otrzymana liczba sztuk |
| ↳ `target_location` | Lokalizacja docelowa |

Statusy:
- `NEW` — nowe przyjęcie
- `ACCEPTED` — przyjęcie zatwierdzone
- `POSTED` — towar dodany do Inventory
- `CANCELLED` — przyjęcie anulowane

### Outbound — tabela `Order`

Jeden rekord opisuje zamówienie wraz z odbiorcą i produktami.

| Pole | Opis |
|---|---|
| `id` | Identyfikator zamówienia |
| `recipient_name` | Imię i nazwisko lub nazwa odbiorcy |
| `recipient_phone` | Numer telefonu odbiorcy |
| `recipient_address` | Adres dostawy |
| `status` | Etap realizacji zamówienia |
| `items` | Lista zamówionych produktów — JSON |
| ↳ `sku` | Kod produktu |
| ↳ `quantity` | Zamówiona liczba sztuk |

Statusy:
- `NEW` — nowe zamówienie
- `RESERVED` — towar zarezerwowany
- `PACKED` — zamówienie spakowane
- `SHIPPED` — paczka przekazana kurierowi
- `CANCELLED` — zamówienie anulowane

### Shipping — tabela `Shipment`

Jeden rekord opisuje jedną paczkę.

| Pole | Opis |
|---|---|
| `id` | Identyfikator przesyłki |
| `order_id` | Identyfikator zamówienia |
| `carrier` | Firma kurierska |
| `weight_kg` | Waga paczki w kilogramach |
| `status` | Etap wysyłki |
| `handed_over_at` | Data i godzina wydania kurierowi |

Statusy:
- `NEW` — przesyłka zarejestrowana
- `READY` — gotowa do wydania
- `HANDED_OVER` — przekazana kurierowi
- `CANCELLED` — przesyłka anulowana
