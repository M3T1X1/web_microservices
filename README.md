# System magazynowy — Inventory, Outbound, Shipping i Auth

Docelowo system obejmuje cztery mikrousługi: Inventory, Outbound, Shipping i Auth. **Zaimplementowane są Inventory i Outbound**; Shipping i Auth pozostają planowane. Inventory odpowiada za stany, rezerwacje i przyjęcia towaru. **Inbound jest modułem Inventory**, korzysta z tej samej bazy i transakcji. Outbound odpowiada za zamówienia, Shipping będzie obsługiwać wysyłkę, a Auth — konta, logowanie i role. Inventory i Outbound działają obecnie w jednym procesie FastAPI, z osobnymi bazami SQLite i opcjonalnym cache Redis.

## Uruchomienie i testy

Wymagania: Python 3.12 lub nowszy i internet do pobrania zależności. Skopiuj projekt na nowy komputer bez katalogu `.venv`. Otwórz terminal w katalogu z tym README.

### Windows 11

Zainstaluj Python 3.12 lub nowszy z [python.org](https://www.python.org/downloads/windows/), wraz z pip i launcherem `py`, i otwórz nowy PowerShell.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
```

`Bypass` dotyczy tylko uruchamianego procesu i nie zmienia trwale polityki PowerShell.

### Debian 13

```bash
sudo apt update
sudo apt install python3 python3-venv
bash scripts/setup.sh
```

Skrypty tworzą lokalne `.venv`, instalują `requirements.txt` i uruchamiają aplikację bez `--reload`. Ponowne uruchomienie korzysta z istniejącego środowiska. Nie trzeba go aktywować. Przeniesione lub uszkodzone `.venv` należy usunąć i odtworzyć skryptem.

| Opcja | Windows | Debian |
|---|---|---|
| Samo przygotowanie środowiska | `-SetupOnly` | `--setup-only` |
| Testy przed uruchomieniem | `-Test` | `--test` |
| Inny port | `-Port 8200` | `--port 8200` |

Opcje można łączyć, np. `bash scripts/setup.sh --setup-only --test`. Na Windows dopisz je po ścieżce do skryptu, np. `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SetupOnly -Test`. Błąd instalacji lub testów przerywa uruchamianie.

Domyślnie API działa pod `http://127.0.0.1:8101`, status pod `/health`, a dokumentacja pod `/docs`. Serwer jest dostępny lokalnie; zatrzymanie: `Ctrl+C`.

Uruchomienie obejmuje tylko Inventory i Outbound. Shipping i Auth nie mają jeszcze kodu, baz ani osobnych procesów; obecne endpointy nie wymagają logowania.

### Uruchomienie ręczne (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8101
```

W drugim terminalu: `Invoke-RestMethod http://localhost:8101/health`. Dokumentacja: [Swagger UI](http://localhost:8101/docs). Zatrzymanie serwera: `Ctrl+C`. Nie trzeba aktywować środowiska. Zmień `replace-me` w aplikacji i teście na nazwę usługi.

## Struktura kodu

Poniższe pliki dotyczą zaimplementowanych domen Inventory i Outbound. Pliki Shipping i Auth powstaną w kolejnym etapie.

- `app/main.py` — inicjalizacja baz, serwisów i rejestracja routerów.
- `app/models/order.py` — modele zamówień i walidacja danych.
- `app/models/stock.py` — model stanu i walidacja rezerwacji.
- `app/models/receipt.py` — modele przyjęcia i pozycji dostawy.
- `app/routers/` — endpointy: `health.py`, `stocks.py`, `receipts.py`, `orders.py`.
- `app/services/orders.py` — operacje na zamówieniach i reguła pakowania.
- `app/services/stocks.py` — odczyt stanów i kontrola dostępności przy rezerwacji.
- `app/services/receipts.py` — operacje przyjęć i unieważnianie cache zmienionych stanów po księgowaniu.
- `app/repositories.py` — transakcje, reguły biznesowe i zapis obu domen; przyjęcia należą do Inventory.
- `app/db_models.py` — tabele `stocks`, `receipts` i `orders`.
- `tests/conftest.py` — wspólny klient ze świeżą aplikacją dla każdego testu.
- `tests/test_*.py` — scenariusze health, Inventory i Outbound.

## 1. Problem i granice

Inventory pomaga pracownikowi magazynu kontrolować ilość i dostępność towaru. Odpowiada za stany, lokalizacje, rezerwacje i przyjęcia dostaw wraz z danymi dostawcy; nie obsługuje kompletacji ani wysyłki.

Planowany Auth będzie uwierzytelniać pracowników i zarządzać ich rolami. Auth wydaje token, a każda usługa sprawdza uprawnienia do swoich operacji. Konta i hasła należą wyłącznie do Auth.

Pojęcia: **SKU** — kod produktu; **lokalizacja** — miejsce składowania; **rezerwacja** — towar przydzielony zamówieniu; **dostępność** — ilość pomniejszona o rezerwacje; **przyjęcie** — dostawa księgowana na stan Inventory.

| Usługa i etap | Odpowiedzialność | Własne dane | Operacje | Docelowo potrzebne informacje od innej usługi |
|---|---|---|---|---|
| Inventory (z przyjęciami), zaimplementowane | Stany, rezerwacje i dostawy | `Stock`, `Receipt` | Odczyt, korekta, rezerwacja, rejestracja i księgowanie przyjęć | Identyfikator zamówienia z Outbound; tożsamość i rola z tokenu Auth |
| Outbound, zaimplementowane | Realizacja zamówień | `Order` | Przyjęcie zamówienia, kompletacja, pakowanie | Dostępność i potwierdzenie rezerwacji z Inventory; tożsamość i rola z tokenu Auth |
| Shipping, planowane | Wysyłka paczek | `Shipment` | Rejestracja przesyłki, wydanie kurierowi | Potwierdzenie spakowania z Outbound; tożsamość i rola z tokenu Auth |
| Auth, planowana czwarta usługa | Konta, logowanie i role | `User` | Tworzenie kont przez administratora, logowanie, wydawanie JWT, odczyt własnego profilu | Nie wymaga danych magazynowych ani zamówień |

Każda usługa zapisuje własne dane: Inventory — stany, rezerwacje i przyjęcia, Outbound — zamówienia, docelowo Shipping — przesyłki, a Auth — konta i role. Księgowanie przyjęcia zmienia stany wewnętrznie, w tej samej transakcji Inventory; zmiany danych innej usługi wymagają API. Inventory, Outbound i Shipping nie zapisują haseł ani nie modyfikują bazy Auth.

Przyszłe HTTP: **Outbound → Inventory**, `POST /stocks/{id}/reservations`, żądanie `{"order_id":"22222222-2222-4222-8222-222222222222","quantity":3}`, odpowiedź `201` z utworzoną rezerwacją. Przy niedostępności Inventory zamówienie pozostaje `NEW`, bez potwierdzenia rezerwacji. Integracja HTTP nie jest jeszcze zaimplementowana.

## 2. Model i reguła biznesowa

### Inventory — `Stock`

`Stock`: `id` — UUID nadawany przez serwer; `sku` i `location` — wymagane, niepuste teksty; `quantity` — wymagana liczba całkowita ≥ 0; `reservations` — lista, początkowo pusta. Każda rezerwacja ma wymagane `order_id` (UUID) i `quantity` (liczba całkowita > 0).

**Reguła:** suma rezerwacji nie może przekraczać stanu. Przy 10 sztukach i 7 zarezerwowanych można zarezerwować kolejne 3, ale nie 4 (`409 Conflict`). Wszystkie warunki odrzucenia sprawdzamy przed zmianą danych.

Para `(sku, location)` jest unikalna w bazie Inventory.

### Inventory — `Receipt` (moduł przyjęć)

`Receipt`: `id` — UUID serwera; `supplier` — niepusty tekst; `status` — początkowo `NEW`; `items` — niepusta lista pozycji: `sku`, `target_location` (niepuste teksty), `quantity_received` (liczba całkowita > 0).

**Reguła:** `NEW → ACCEPTED → POSTED`. Księgowanie dodaje ilości do stanów według SKU i lokalizacji albo tworzy brakujące stany. Cała dostawa i status `POSTED` są zapisywane w jednej transakcji; istniejące rezerwacje pozostają bez zmian. Powtórne księgowanie daje `409`. Anulowanie do `CANCELLED` jest dozwolone tylko z `NEW` lub `ACCEPTED`.

### Outbound — `Order`

`Order`: `id` — UUID serwera; `recipient_name`, `recipient_phone`, `recipient_address` — niepuste teksty; `status` — początkowo `NEW`; `items` — niepusta lista pozycji: `sku` (niepusty tekst), `quantity` (liczba całkowita > 0).

**Reguła:** tylko zamówienie z potwierdzoną rezerwacją (`RESERVED`) można oznaczyć jako spakowane (`PACKED`). Niedozwolona operacja zwraca `409` bez zmiany danych.

### Shipping — `Shipment` (planowana usługa)

`Shipment`: `id` — UUID serwera; `order_id` — UUID zamówienia; `carrier` — niepusty tekst; `weight_kg` — liczba > 0; `status` — początkowo `NEW`; `handed_over_at` — data i godzina wydania, początkowo brak.

**Reguła:** tylko przesyłkę `READY` można wydać kurierowi; otrzymuje wtedy status `HANDED_OVER` i czas wydania. Niedozwolona operacja zwraca `409` bez zmiany danych.

### Auth — `User` (planowana czwarta usługa)

`User`: `id` — UUID serwera; `email` — wymagany poprawny i unikalny adres; `password_hash` — hash hasła, np. Argon2id; `role` — `ADMIN`, `WAREHOUSE` lub `SHIPPING`; `is_active` — wartość logiczna, początkowo `true`. Utworzenie konta przyjmuje hasło, a odpowiedź z profilem nie zawiera hasła ani jego hasha.

**Reguła:** tylko `ADMIN` może tworzyć konta (`201`); próba wykonania tej operacji przez `WAREHOUSE` lub `SHIPPING` daje `403`, bez zapisu użytkownika. Logowanie wymaga poprawnego hasła i aktywnego konta; w przeciwnym razie zwraca `401` bez wydania tokenu. Są to wymagania planowane, jeszcze niezaimplementowane.

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

### Przyjęcia w Inventory

| Metoda | Ścieżka | Znaczenie | Sukces | Błędy |
|---|---|---|---|---|
| POST | `/receipts` | Utworzenie przyjęcia `NEW` | 201 | 422, 503 |
| GET | `/receipts` | Lista przyjęć | 200 | 503 |
| GET | `/receipts/{id}` | Szczegóły przyjęcia | 200 | 404, 422, 503 |
| POST | `/receipts/{id}/accept` | Zatwierdzenie `NEW → ACCEPTED` | 200 | 404, 409, 422, 503 |
| POST | `/receipts/{id}/post` | Księgowanie `ACCEPTED → POSTED` i aktualizacja stanów | 200 | 404, 409, 422, 503 |
| POST | `/receipts/{id}/cancel` | Anulowanie `NEW` lub `ACCEPTED` | 200 | 404, 409, 422, 503 |

Przykład `POST /receipts`:

```json
{"supplier":"Dostawca ABC","items":[{"sku":"SKU-001","quantity_received":5,"target_location":"A-01"}]}
```

Odpowiedź zawiera te same dane oraz UUID `id` i `status: "NEW"`. Zwrócony UUID podstaw pod `{id}`, następnie wywołaj `/accept` i `/post` (bez treści żądania). Stan 10 wzrośnie do 15 dopiero po `/post`. Powtarzające się SKU i lokalizacja w pozycjach są sumowane. Odczyty przyjęć korzystają bezpośrednio z bazy.

Brak przyjęcia: `404`, `{"detail":"Receipt not found"}`. Księgowanie przed zatwierdzeniem, ponowne księgowanie albo księgowanie po anulowaniu: `409`, `{"detail":"Only ACCEPTED receipts can be posted"}`. Anulowanie zaksięgowanego przyjęcia daje `409`; nie odejmuje towaru od stanu.

### Auth — planowany kontrakt (jeszcze niedostępny)

| Metoda | Ścieżka | Znaczenie | Sukces | Błędy |
|---|---|---|---|---|
| POST | `/auth/login` | Logowanie aktywnego użytkownika i wydanie JWT | 200 | 401, 422 |
| GET | `/auth/me` | Własny profil po uwierzytelnieniu | 200 | 401 |
| POST | `/auth/users` | Utworzenie konta przez `ADMIN` | 201 | 401, 403, 409, 422 |

Przykładowe przyszłe logowanie:

```json
{"email":"pracownik@example.com","password":"przykladowe-haslo"}
```

Odpowiedź: `200`, `{"access_token":"<JWT>","token_type":"bearer"}`. Klient dołącza `Authorization: Bearer <JWT>` do żądań. Usługi lokalnie sprawdzają podpis, wystawcę, odbiorcę i czas ważności tokenu oraz rolę wymaganą przez endpoint; nie wywołują Auth przy każdym żądaniu. Hasło nie jest umieszczane w tokenie.

Administrator utworzy konto przez `/auth/users`, przekazując `email`, `password` i `role`; odpowiedź zawiera UUID, email, rolę i `is_active`. `409` oznacza zajęty email. `401` oznacza błędne logowanie albo brak poprawnego tokenu, a `403` — poprawny token bez wymaganych uprawnień. Planowane przykłady: `{"detail":"Invalid credentials"}` i `{"detail":"Insufficient permissions"}`. Obecne Inventory i Outbound nie weryfikują jeszcze tokenów; te endpointy Auth nie pojawiają się obecnie w Swaggerze.

## 4. Testy i stan danych

Poniższa tabela i wynik 22 testów dokumentują LAB1. Aktualny zestaw obejmuje też SQLite, Redis i przyjęcia; jego wynik znajduje się w punkcie 10.

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

Planowane testy Auth: poprawne logowanie i odczyt `/auth/me`, odrzucenie błędnego hasła lub nieaktywnego konta (`401`), odrzucenie nieważnego tokenu (`401`), utworzenie konta przez `ADMIN` oraz odmowa dla innej roli (`403`) bez zmiany danych. Testy i ich wyniki powstaną razem z implementacją Auth; nie są wliczone do obecnego zestawu. Shipping również nie jest jeszcze testowany.

## 5. Porównanie architektur

```text
Monolit:
[1 proces: Inventory (stany + przyjęcia) + Outbound + Shipping + Auth] -> [wspólna baza]

Modularny monolit (strzałki wewnątrz = wywołania funkcji):
[1 proces: Inventory (stany + przyjęcia) <- Outbound <- Shipping; Auth: konta i role]
    -> [baza: tabele modułów]

Mikrousługi (każda usługa = osobny proces i własna baza):
[Inventory/Stock + Receipt] <--HTTP-- [Outbound/Order] <--HTTP-- [Shipping/Shipment]
[Klient] --HTTP: logowanie--> [Auth/User]
[Klient] --HTTP + JWT--> Inventory / Outbound / Shipping (lokalna weryfikacja tokenu)
```

1. Inventory ma własne dane i API oraz może działać w osobnym procesie; moduł jest częścią aplikacji.
2. W monolicie przyjęcia, stany, zamówienia, wysyłka oraz konta i logowanie działałyby w jednym procesie ze wspólną bazą.
3. Podział daje niezależne wdrażanie, ale dodaje opóźnienia sieci i awarie zależności; brak Inventory blokuje rezerwacje, a brak Auth nowe logowania.
4. Modularny monolit — prostsze wdrażanie i utrzymanie przy małej skali.

Schematy pokazują docelowy system czterech usług. Obecnie działają tylko Inventory i Outbound w jednym procesie; Shipping i Auth nie są zaimplementowane.

## 6. Dokumentacja wykorzystana podczas pracy

| Odnośnik do fragmentu | Rozwiązywany problem | Co zastosowano w kodzie | Jak sprawdzono działanie |
|---|---|---|---|
| [FastAPI — Path operation](https://fastapi.tiangolo.com/tutorial/first-steps/#step-3-create-a-path-operation) | Obsługa żądania GET | `@app.get("/health")` | Test sprawdza `200` i JSON |
| [FastAPI — TestClient](https://fastapi.tiangolo.com/tutorial/testing/#using-testclient) | Testowanie API | `TestClient(create_app())` | `pytest tests/test_health.py`: **1 passed** |
| [OWASP — Access Control i JWT](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html#access-control), [Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) | Projektowanie logowania i uprawnień Auth | Plan: hash hasła, JWT i kontrola ról w usługach; bez implementacji | Przegląd projektu; testy Auth są dopiero planowane |

## 7. Ograniczenia, autorzy i AI

Dane są trwałe w SQLite. Przyjęcia należą do Inventory; brakuje jeszcze automatycznej integracji Inventory–Outbound, zwalniania rezerwacji i wysyłki. Shipping i Auth są planowane. Obecnie nie ma kont, logowania, wydawania JWT ani kontroli ról w endpointach. Redis może zwracać starą kopię do wygaśnięcia TTL.

- AI: ChatGPT pomógł w testach i endpointach; weryfikacja przez przegląd kodu i test `/health`.

## 8. Podział systemu na usługi

Docelowo: cztery usługi. Inventory i Outbound są zaimplementowane; Shipping i Auth powstaną później.

### Serwis Magazynu i Przyjęć (Inventory)

**Usługa odpowiada za:**
- Przechowywanie ilości i lokalizacji produktów.
- Rezerwowanie towaru dla zamówień.
- Rejestrowanie dostaw, dostawcy, otrzymanych ilości i lokalizacji docelowych.
- Zatwierdzanie, księgowanie i anulowanie przyjęć przed zaksięgowaniem.
- Aktualizowanie stanów przy księgowaniu dostawy i korekcie ilości.

**Usługa nie odpowiada za:**
- Kompletowanie i pakowanie zamówień.
- Obsługę wysyłki paczek.
- Wystawianie dokumentów PZ.

Inbound jest modułem tej usługi, bez osobnego procesu, bazy i integracji HTTP z Inventory.

### Serwis Realizacji Zamówień (Outbound)

**Usługa odpowiada za:**
- Przyjmowanie zamówień i danych odbiorcy.
- Zlecanie rezerwacji towaru w Inventory.
- Tworzenie list kompletacyjnych i potwierdzanie spakowania.

**Usługa nie odpowiada za:**
- Samodzielne zmienianie stanów magazynowych.
- Rejestrowanie dostaw od dostawców.
- Obsługę przekazania paczek kurierowi.

### Serwis Wysyłki i Kurierów (Shipping — planowany)

**Usługa odpowiada za:**
- Rejestrowanie przesyłek dla spakowanych zamówień.
- Zapisywanie przewoźnika i wagi paczki.
- Potwierdzanie przekazania paczki kurierowi i powiadamianie pozostałych usług.

**Usługa nie odpowiada za:**
- Kompletowanie i pakowanie zamówień.
- Zarządzanie stanami magazynowymi.
- Generowanie etykiet i obsługę numerów śledzenia.

### Serwis Kont i Logowania (Auth — planowana czwarta usługa)

**Usługa będzie odpowiadać za:**
- Przechowywanie kont pracowników, hashy haseł, ról i aktywności kont.
- Sprawdzanie danych logowania oraz wydawanie podpisanych JWT z czasem ważności.
- Tworzenie kont przez administratora i udostępnianie własnego profilu.

**Usługa nie będzie odpowiadać za:**
- Zarządzanie towarem, przyjęciami, zamówieniami ani przesyłkami.
- Podejmowanie decyzji biznesowych, np. czy wystarczy towaru na rezerwację.

Planowane role: `ADMIN` — konta i wszystkie operacje; `WAREHOUSE` — Inventory i Outbound; `SHIPPING` — wysyłka. Każda usługa będzie weryfikować token i uprawnienia do własnych endpointów. Auth będzie mieć osobną bazę i docelowo osobny proces; obecnie nie jest uruchamiany.

## 9. Modele danych

Inventory ma dwie tabele we własnej bazie: `stocks` i `receipts`. Outbound ma tabelę `orders` w osobnej bazie. Planowane Shipping i Auth będą mieć własne bazy: odpowiednio z przesyłkami i tabelą `users`. Identyfikatory UUID nadaje serwer. Pola oznaczone `↳` są elementami JSON, a nie osobnymi kolumnami.

### Inventory — tabela `stocks` (model `Stock`)

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

### Inventory — tabela `receipts` (model `Receipt`, moduł przyjęć)

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
- `POSTED` — ilości dodane do stanów w tej samej transakcji Inventory
- `CANCELLED` — przyjęcie anulowane

### Outbound — tabela `orders` (model `Order`)

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

### Shipping — tabela `Shipment` (planowana)

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


### Auth — tabela `users`, model `User` (planowane)

Jeden rekord będzie opisywać konto pracownika we własnej bazie `auth.db`.

| Pole | Opis |
|---|---|
| `id` | UUID użytkownika nadawany przez serwer |
| `email` | Wymagany, poprawny i unikalny adres używany jako login |
| `password_hash` | Wymagany hash hasła, np. Argon2id; niewracany w API |
| `role` | Wymagana rola: `ADMIN`, `WAREHOUSE` lub `SHIPPING` |
| `is_active` | Czy konto może się logować; domyślnie `true` |

Planowane modele API: `UserCreate` przyjmuje email, hasło i rolę; `UserRead` zwraca UUID, email, rolę i aktywność, bez hasła i hasha. JWT jest wydawanym po logowaniu tokenem z identyfikatorem użytkownika, rolą i czasem ważności. Tabela i modele Auth jeszcze nie istnieją w kodzie.

## 10. LAB2 — baza danych i cache

Poniższe sekcje opisują SQLite i Redis z LAB2 oraz przyjęcia włączone do Inventory. Historyczna tabela restartu i wynik 22 testów w punkcie 4 dotyczą LAB1; aktualne dane są trwałe, a wynik testów podano poniżej.

### Zmiany i wykorzystany kod pomocniczy

Rozwiązanie oparto na rzeczywiście pobranym [kodzie pomocniczym LAB2 — ZIP](https://pawoom.lazysolutions.pl/2026_2027/lab2/lab2-support.zip), zgodnie z [instrukcją laboratorium](https://pawoom.lazysolutions.pl/2026_2027/lab2/). Oryginalna paczka jest zachowana w [materials/lab2-support.zip](materials/lab2-support.zip). Pobrano 03.11.2026; SHA-256 archiwum:

```text
575fbe27f7bafb582311c35ccc7eb4478921c72c798a72117ce2df4b0361e902
```

| Plik z ZIP-a | Wykorzystanie we własnym projekcie |
|---|---|
| `database.py` | Przeniesiony do `app/database.py` **bez zmian**: `Base`, `make_database()`, fabryka sesji, `PRAGMA foreign_keys=ON` |
| `cache.py` | Przeniesiony do `app/cache.py` **bez zmian**: `JsonCache`, `make_redis()`, TTL, GET/SET/DELETE, timeouty i brak ponowień |
| `demo.py` | Wzorzec sesji i repozytorium dostosowany do `StockRepository` i `OrderRepository`; odczyt cache-aside z `CachedLockers.get` przeniesiony do `cached_read()`; commit przed DEL w serwisach |
| `tests/test_storage.py` | Klasa `MemoryRedis` skopiowana do `tests/fakes.py`; własna podklasa `ControlledRedis` dodaje awarie poszczególnych poleceń, kontrolowany zegar TTL i obserwację zamknięcia klienta |
| `tests/test_storage.py` | Scenariusze trwałości, rollbacku, MISS/HIT z licznikiem odczytów, awarii SET i nieudanego unieważnienia dostosowane do własnego API HTTP |
| `README-LAB2-template.md` | Podstawa struktury niniejszego uzupełnienia: decyzje, uruchomienie, dowody, porównanie, źródła i autorzy |

Paczkomat z `demo.py` służył wyłącznie do zrozumienia mechanizmu repozytorium. Aplikacja obsługuje własną domenę magazynu; nie zawiera tabel ani endpointów paczkomatów. Oryginalny przykład pozostaje jedynie wewnątrz archiwum źródłowego.

Własne rozszerzenia względem paczki: modele ORM magazynu, repozytoria obu domen, walidacja schematu i UUID kopii w `cached_read()`, HTTP i `X-Cache`, `lifespan`, dwie osobne bazy, blokada zapisu SQLite przed sprawdzeniem reguły, korekta ilości, gotowość `/ready` i testy API. W przeciwieństwie do demonstracyjnego `create()` nie mapujemy wszystkich `IntegrityError` na konflikt — tylko rozpoznaną kolizję UUID zamówienia. Pozostałe awarie bazy dają 503.

Zachowano fabrykę `create_app()`, dotychczasowe metody i ścieżki HTTP, format UUID, JSON, walidację, błędy 404/409/422 oraz reguły rezerwacji i pakowania. Zmieniono magazyn w pamięci na SQLite dla **Inventory i Outbound**. Dodano `PATCH /stocks/{id}` do korekty ilości, `X-Cache` na odczytach szczegółów oraz opcjonalny `/ready`. Nazwa `replace-me` w `/health` pozostaje zgodna z kontraktem LAB1.

### Dane, właściciel i transakcje

| Baza i właściciel zapisu | Tabela | Pola i ograniczenia |
|---|---|---|
| Inventory — `inventory.db` | `stocks` | `id` UUID PK; unikalna para `(sku, location)`; `quantity` całkowite ≥ 0; `reservations` JSON z `order_id` UUID i dodatnią ilością |
| Inventory — ta sama baza | `receipts` | `id` UUID PK; wymagany dostawca; dozwolony status; pozycje dostawy w JSON |
| Outbound — `outbound.db` | `orders` | `id` UUID PK; wymagane dane odbiorcy; `status` ograniczony do statusów LAB1; `items` JSON z SKU i dodatnią ilością |

Tabela opisuje obecne bazy. Planowany Auth będzie właścicielem `auth.db` i tabeli `users`; pozostałe usługi nie będą odczytywać jej bezpośrednio. Bazy Shipping i Auth nie są obecnie tworzone, nie dodano ich konfiguracji ani zależności do uruchamianej aplikacji.

`StockRow`, `ReceiptRow` i `OrderRow` korzystają z `Base` pobranego z ZIP-a. Inicjalizacja tworzy wyłącznie tabele właściciela danej bazy: `stocks` i `receipts` w Inventory, `orders` w Outbound. Nowa lista rezerwacji jest przypisywana jako całość, aby ORM wykrył zmianę JSON.

Baza chroni klucz główny, `NOT NULL`, nieujemną ilość, dozwolone statusy oraz unikalną parę SKU–lokalizacja. Pydantic pilnuje tekstów, UUID i ścisłych typów ilości. `order_id` w rezerwacji jest odwołaniem do obcej domeny bez FK między bazami; Inventory nie kopiuje danych odbiorcy ani statusu zamówienia. Outbound nie zapisuje cudzych stanów magazynowych.

`app/main.py` tworzy brakujące tabele przez `Base.metadata.create_all()`. Przy tworzeniu nowej tabeli `stocks` powstaje też indeks `uq_stocks_sku_location`, zadeklarowany w `app/db_models.py`. Nie ma automatycznej migracji istniejących tabel ani dodawania do nich nowych indeksów. Podczas developmentu po zmianie struktury odtwarzamy lokalne bazy od zera; zwykły restart bez usuwania plików zachowuje dane.

Przyjęcie i wszystkie jego pozycje są księgowane w jednej transakcji `BEGIN IMMEDIATE`: istniejące stany są zwiększane, brakujące tworzone, a przyjęcie otrzymuje `POSTED`. Nie zmieniamy rezerwacji. Powtórzone pozycje tej samej pary SKU–lokalizacja są sumowane. Błąd cofa również wcześniejsze zmiany pozycji tej dostawy. Równoczesne próby zaksięgowania tego samego przyjęcia dają jeden sukces i jeden konflikt; nie podwajają dostawy.

Wszystkie listy, szczegóły i operacje zapisu przeniesionych danych korzystają z repozytoriów SQLite; stare słowniki usunięto. Silnik i fabryka sesji powstają raz na aplikację, ale każda operacja otwiera osobną sesję. `with sessions.begin()` z wzorca ZIP zatwierdza **commit** po poprawnym wyjściu, a po wyjątku wykonuje **rollback** i zamyka sesję. Odczyty używają `with sessions()` i również zamykają sesję. `lifespan` przygotowuje zasoby, a `ExitStack` zamyka oba silniki i klienta Redis, także przy błędzie startu. Import modułu nie tworzy plików baz.

W SQLite zapis zaczyna się od `BEGIN IMMEDIATE` przed odczytem dostępności. Dzięki temu konkurencyjny zapis z innego procesu czeka na zwolnienie blokady, a potem sprawdza już zatwierdzony stan. Zachowano domyślny timeout SQLite wynoszący 5 s. Dla innych baz kod używa `SELECT ... FOR UPDATE` przy modyfikacjach; wariant PostgreSQL wymaga osobnej weryfikacji.

Seed dodaje `SKU-001`, `A-01`, ilość 10 i pustą listę rezerwacji tylko do pustej tabeli stanów. UUID nadal nadaje serwer; po pierwszym zapisie jest trwały. Restart nie odtwarza początkowej ilości, nie zmienia identyfikatora i nie usuwa rezerwacji. Zamówienia także zachowują dane po restarcie.

Reguła Inventory: przy 10 sztukach rezerwacja 7 jest dozwolona, a kolejna 4 daje `409`, bez zmiany stanu. Nowa korekta nie może obniżyć `quantity` poniżej sumy rezerwacji: przy rezerwacji 3 można ustawić 3, lecz nie 2. Reguła Outbound: tylko `RESERVED` można zmienić na `PACKED`; `NEW` i ponowne pakowanie `PACKED` są odrzucane bez zmian.

Plan integracji pozostaje **Outbound → Inventory**, `POST /stocks/{id}/reservations`. Po potwierdzeniu rezerwacji Outbound będzie mógł nadać `RESERVED`. Integracji HTTP jeszcze nie dodano; `RESERVED` w testach jest przygotowywany w bazie. Przyjęcia (dawny Inbound) działają już wewnątrz Inventory, bez wywołania HTTP między domenami. Shipping i Auth pozostają planowane; tokeny i kontrola ról zostaną dodane razem z Auth.

### Uruchomienie LAB2

Wymagany Python 3.12+. Zależności z LAB1 zachowano; dopisano `SQLAlchemy>=2.0,<2.1` i `redis>=5,<7`, zgodnie z ZIP-em. Redis jest osobnym, opcjonalnym procesem. Testy automatyczne używają atrapy i nie wymagają Dockera ani serwera Redis.

Z katalogu projektu, Debian:

```bash
export DATABASE_URL='sqlite:///./inventory.db'
export OUTBOUND_DATABASE_URL='sqlite:///./outbound.db'
export REDIS_URL='redis://127.0.0.1:6379/0'
export CACHE_PREFIX='lab2:131427:warehouse'
export CACHE_TTL_SECONDS='60'
docker compose up -d redis
docker compose exec redis redis-cli PING
bash scripts/setup.sh --test
```

Debian wymaga `python3` i `python3-venv`. Dla wariantu Compose wymagany jest Docker z Compose. Oczekiwany wynik PING: `PONG`. Jeśli masz własny serwer Redis, ustaw jego URL i pomiń uruchamianie Compose. Nie zatrzymuj serwera współdzielonego w celu symulowania awarii.

Windows 11, PowerShell, Python 3.12+ i Docker Desktop:

```powershell
$env:DATABASE_URL = 'sqlite:///./inventory.db'
$env:OUTBOUND_DATABASE_URL = 'sqlite:///./outbound.db'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:CACHE_PREFIX = 'lab2:131427:warehouse'
$env:CACHE_TTL_SECONDS = '60'
docker compose up -d redis
docker compose exec redis redis-cli PING
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Test
```

Skrypty setup zachowują opcje opisane na początku README. API działa pod `http://127.0.0.1:8101`, Swagger pod `/docs`. Zatrzymanie: `Ctrl+C`. Do demonstracji restartów uruchamiaj bez `--reload`.

| Zmienna | Domyślnie | Znaczenie |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./inventory.db` | Baza Inventory |
| `OUTBOUND_DATABASE_URL` | `sqlite:///./outbound.db` | Osobna baza Outbound |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Połączenie z cache |
| `CACHE_PREFIX` | `lab2:local:warehouse` | Własna przestrzeń kluczy; ustaw numer albumu |
| `CACHE_TTL_SECONDS` | `60` | Dodatni czas życia kopii w sekundach |

Ścieżki SQLite są względne do katalogu uruchomienia. Skrypty setup przechodzą do katalogu projektu. Używaj dwóch różnych plików baz. Nowy zbiór baz powinien mieć nowy prefiks Redis, aby nie odczytać kopii poprzedniego zbioru. `.env.example` jest wzorem konfiguracji — aplikacja nie wczytuje go automatycznie. Po zmianie zmiennych środowiska zatrzymaj i uruchom Uvicorn ponownie; samo `--reload` nie przejmuje środowiska terminala.

Brakujące pliki baz i tabele tworzy `main.py` przy starcie, razem z indeksami nowych tabel. `create_all()` nie aktualizuje struktury istniejących tabel; po zmianie modeli bazy odtwarzamy w czasie developmentu. `.gitignore` wyklucza lokalne bazy, pliki pomocnicze SQLite i `.env`. Compose udostępnia Redis 7 tylko na `127.0.0.1:6379`; cache jest odtwarzalny, bez zapisu na dysku.

### Uzupełnienie kontraktu API

| Metoda | Ścieżka | Sukces | Błędy |
|---|---|---|---|
| GET | `/health` | `200`, dokładny JSON z LAB1 | Sprawdza tylko życie procesu |
| GET | `/ready` | `200`, status `ok` albo `degraded` bez Redis | `503` przy niedostępnej bazie |
| GET | `/stocks` | `200`, lista stanów z bazy | `503` przy awarii bazy |
| GET | `/stocks/{id}` | `200`, `Stock` i `X-Cache` | `404`, `422`, `503` |
| POST | `/stocks/{id}/reservations` | `201`, rezerwacja | `404`, `409`, `422`, `503` |
| PATCH | `/stocks/{id}` | `200`, zaktualizowany `Stock` | `404`, `409`, `422`, `503` |
| POST | `/orders` | `201`, zamówienie `NEW` z UUID | `422`, `409` przy kolizji serwerowego UUID, `503` |
| GET | `/orders` | `200`, lista zamówień z bazy | `503` |
| GET | `/orders/{id}` | `200`, `Order` i `X-Cache` | `404`, `422`, `503` |
| POST | `/orders/{id}/pack` | `200`, zamówienie `PACKED` | `404`, `409`, `422`, `503` |

Endpointy przyjęć Inventory opisano w punkcie 3. Wszystkie korzystają z tej samej bazy `inventory.db`; nie dochodzi nowy adres bazy ani osobny serwer Inbound.

Korekta przyjmuje `{"quantity":14}`; typ musi być całkowity i nieujemny. Korekta poniżej sumy rezerwacji: `409`, `{"detail":"Quantity cannot be lower than reserved stock"}`. Brak zamówienia: `404`, `{"detail":"Order not found"}`. Pakowanie przed rezerwacją: `409`, `{"detail":"Order must be reserved before packing"}`. Rozpoznana kolizja UUID zamówienia: `409`, `{"detail":"Order already exists"}` bez nadpisania danych. Pozostałe błędy bazy: `503`, `{"detail":"Database unavailable"}`. Istniejące odpowiedzi Inventory i błędy walidacji z sekcji 3 zachowano.

Przykład `POST /orders`:

```json
{"recipient_name":"Jan Kowalski","recipient_phone":"123456789","recipient_address":"ul. Polna 1, Warszawa","items":[{"sku":"SKU-001","quantity":3}]}
```

Odpowiedź zawiera te same pola oraz serwerowy UUID `id` i `status: "NEW"`. `/ready` zwraca osobny stan baz `inventory` i `outbound` oraz cache. Sam `SELECT 1` potwierdza połączenie, ale nie poprawność całego schematu ani możliwość każdego zapisu. Awaria inicjalizacji bazy uniemożliwia start aplikacji.

### Cache, TTL i awarie

Cache-aside obejmuje wyłącznie szczegóły stanu i zamówienia. Listy i odczyty przyjęć czytają bazę. Do Redis trafia serializowana odpowiedź Pydantic, a nie obiekt ORM lub sesja. Adapter pobrany z ZIP sprawdza poprawność JSON, natomiast `cached_read()` dodatkowo sprawdza schemat Pydantic i UUID; uszkodzona kopia jest odtwarzana. Nie cache’ujemy 404.

| Odczyt | Klucz dla prefiksu `lab2:131427:warehouse` | TTL | Unieważnienie |
|---|---|---|---|
| `GET /stocks/{id}` | `lab2:131427:warehouse:inventory:stock:<UUID>` | 60 s | Udana rezerwacja, korekta ilości i księgowanie przyjęcia |
| `GET /orders/{id}` | `lab2:131427:warehouse:outbound:order:<UUID>` | 60 s | Udane pakowanie |

Nagłówek `X-Cache`: **MISS** — brak kopii i odczyt z bazy; **HIT** — odpowiedź z cache bez odczytu repozytorium; **BYPASS** — awaria GET lub SET Redis, odpowiedź z bazy. Przy 404 nagłówek nie jest wymagany. Po udanym zapisie repozytorium najpierw zatwierdza **commit**, a serwis dopiero później wykonuje **DEL**. Przy księgowaniu przyjęcia usuwa kopie wszystkich zmienionych stanów. Odrzucona operacja nie zmienia danych ani poprawnej kopii.

Użyto parametrów `make_redis()` z ZIP: `socket_connect_timeout=0.3`, `socket_timeout=0.3` oraz `Retry(NoBackoff(), 0)`. Awaria GET, SET lub DELETE nie blokuje sprawnej bazy. Błąd DELETE jest logowany i nie cofa już zatwierdzonej operacji.

Magazynier może tolerować do 60 s opóźnienia w informacyjnym podglądzie szczegółów. Rezerwacja i pakowanie zawsze podejmują decyzję na danych z transakcji bazy. Nawet stary podgląd dostępności nie pozwala przekroczyć stanu magazynowego. Gdy odbiorca wymaga ściśle aktualnego widoku, trzeba ominąć cache lub zastosować mocniejszy mechanizm spójności.

Nieudane unieważnienie może pozostawić stary wpis do końca TTL, także po przywróceniu połączenia. Zmiana konfiguracji TTL nie skraca życia istniejącej kopii. Commit SQLite i DEL Redis nie są jedną transakcją. Przy współbieżnym GET i zapisie możliwe jest też ponowne wstawienie starej kopii. Prosty cache-aside nie gwarantuje ścisłej spójności; ta wada z materiału pomocniczego jest zachowana, opisana i przetestowana. Podczas awarii bazy możliwy jest jeszcze HIT szczegółów, ale listy i zapisy nie działają; `/ready` daje 503.

### Testy i dowody LAB2

LAB1: **22 passed**; LAB2 przed włączeniem przyjęć: **50 passed**. Aktualnie: **73 passed**, w tym 23 przypadki dla przyjęć i tworzenia nowej bazy. Testy automatyczne uruchomiono poleceniem:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Na Windows użyj `.\.venv\Scripts\python.exe`. Każdy test ma własne `tmp_path`, dwie bazy, prefiks i atrapę Redis zbudowaną na klasie z ZIP-a. Nowa instancja aplikacji z tą samą bazą zachowuje stan; sama fabryka nie resetuje już danych. `TestClient` w bloku `with` uruchamia i zamyka `lifespan`.

Cztery wymagane grupy własnych testów:

- `test_both_domains_survive_restart_with_empty_cache` — zapis obu domen, zamknięcie aplikacji, nowa instancja z tymi samymi bazami i nowym cache, zgodny odczyt MISS.
- `test_miss_hit_skips_repository` — MISS → HIT, zgodny JSON i TTL, licznik wykazujący dokładnie jeden odczyt repozytorium dla dwóch GET.
- `test_reservation_and_update_invalidate_only_after_commit`, `test_pack_invalidates_order_cache` — po zapełnieniu kopii modyfikacja usuwa ją dopiero po commit, następny GET zwraca aktualne dane i MISS.
- `test_cache_outage_does_not_block_read_or_writes` — osobne awarie GET, SET, DELETE oraz całego klienta, odczyt obu domen i poprawnie zatwierdzone zapisy.

Zachowano scenariusze LAB1. Dodatkowo sprawdzono rollback po rzeczywistym `flush()` i błędzie commit, znaną kolizję PK, korektę ilości, dwie konkurencyjne instancje rezerwujące ten sam stan, osobne tabele właścicieli, `PRAGMA foreign_keys`, uszkodzony cache, stare dane po nieudanym DEL, kontrolowane wygaśnięcie TTL oraz `/ready` przy awarii cache i bazy. Stan `RESERVED` w scenariuszu pakowania jest przygotowywany w bazie, bo integracja między usługami nadal jest planowana.

`tests/test_receipts.py` sprawdza cykl przyjęcia, walidację, anulowanie, zachowanie rezerwacji, tworzenie stanów w nowych lokalizacjach, sumowanie powtórzonych pozycji, rollback całej dostawy po błędzie zapisu, DEL wszystkich kopii dopiero po commit, awarię Redis, trwałość po restarcie, równoczesne księgowanie oraz utworzenie plików baz, tabel i indeksu unikalności od zera.

Rzeczywiste procesy Redis i Uvicorna oraz żądania HTTP zweryfikowano niezależnie od atrap. Powtarzalny skrypt dla Debiana/WSL z `redis-server` w PATH:

```bash
.venv/bin/python scripts/verify_lab2.py --output reports/lab2-verification.json
```

Skrypt wybiera wolne porty, własny prefiks i tymczasowe bazy, a na końcu zamyka procesy i usuwa dane testowe. Nie dotyka aplikacji użytkownika ani współdzielonego Redis i nie wykonuje globalnego czyszczenia cache. Opcjonalne `--baseline-dir <katalog>` sprawdza dodatkowo restart wskazanej kopii początkowego LAB1; do raportu użyto kopii wykonanej przed edycją plików, bez odczytu historii repozytorium.

[Raport z rzeczywistego wykonania](reports/lab2-verification.json) zawiera historyczne wyniki LAB2 z 03.11.2026, sprzed dodania przyjęć. Nowe przyjęcia zweryfikowano testami API z SQLite i atrapą Redis; nie przypisujemy ich temu raportowi. Do demonstracji TTL skrypt używa 2 s, a do odtworzenia starej kopii 5 s. Normalny TTL aplikacji wynosi 60 s. Testy z atrapą nie zastępują pomiaru wygaśnięcia na serwerze Redis.

| Scenariusz | Stan początkowy i żądanie | Oczekiwany wynik | Wynik rzeczywisty | Reset |
|---|---|---|---|---|
| Początkowy LAB1 | Rezerwacja 3, zamówienie, restart procesu | Nowy UUID, utrata rezerwacji i zamówień, stary UUID 404 | Zgodny; zapis w raporcie | Oddzielny katalog początkowej aplikacji |
| Zapis po restarcie bez cache | Rezerwacja 3, korekta do 14, zamówienie; restart Uvicorna i DEL obu kluczy | Dane i UUID zachowane; odczyty MISS | Zgodny | Nowe procesy, te same tymczasowe bazy |
| Reguła Inventory | Ilość 14, rezerwacja 3, dodatkowe 12 | 409, stan bez zmian | Zgodny | Nowe bazy |
| Reguła Outbound | Zamówienie NEW, POST pakowania | 409, zamówienie bez zmian | Zgodny | Nowa baza |
| MISS → HIT | Dwa GET przy pustym kluczu | MISS, HIT, ten sam JSON | Zgodny; pominięcie repozytorium dodatkowo potwierdzone testem z licznikiem | DEL tylko własnego klucza |
| Wygaśnięcie TTL | Wpis TTL 2 s; oczekiwanie, GET | TTL -2, potem MISS | Zgodny na rzeczywistym Redis | Upływ TTL |
| Zmiana i unieważnienie | HIT, POST rezerwacji i PATCH ilości | DEL po commit, aktualna odpowiedź MISS | Zgodny | Nowa baza i prefiks |
| Awaria GET/SET/DELETE | Osobne awarie atrapy; zatrzymanie własnego serwera w scenariuszu HTTP | Odczyt z bazy, zapis działa; BYPASS dla niedostępnego odczytu | Zgodny dla obu domen | Przywrócenie klienta albo start własnego serwera |
| Przywrócenie Redis | Start własnego procesu z pustym cache | MISS, następnie HIT, aktualne dane | Zgodny | Restart własnego Redis |
| Nieudane unieważnienie | Cache ilości 20; klient z niedostępnym URL; PATCH 25; powrót właściwego URL | Stary HIT 20 do TTL, potem MISS 25 | Zgodny | Wygaśnięcie TTL |

Do samodzielnej prezentacji użyj `/docs`, aby odczytać UUID, wykonać rezerwację i korektę. Nagłówki można obejrzeć przez `curl -i http://127.0.0.1:8101/stocks/<UUID>` (Windows: `curl.exe -i ...`). TTL własnego klucza: `docker compose exec redis redis-cli TTL 'lab2:131427:warehouse:inventory:stock:<UUID>'`. Używaj rzeczywistego UUID i prefiksu. Nie wykonuj `FLUSHALL` ani `FLUSHDB`.

Reset podczas developmentu: zatrzymaj aplikację, usuń lokalne pliki `inventory.db` i `outbound.db`, a następnie uruchom ją ponownie. Powstaną puste bazy z aktualnymi tabelami i indeksami oraz początkowym produktem. Reset usuwa zapisane dane. Po resecie ustaw nowy `CACHE_PREFIX`, aby nie korzystać z kopii poprzednich danych w Redis. Przy normalnym restarcie pozostaw pliki baz.

### Porównanie i ograniczenia LAB2

| Cecha | Pamięć procesu LAB1 | SQLite | Redis jako cache |
|---|---|---|---|
| Rola | Stan wewnątrz aplikacji | Źródło zatwierdzonych danych | Odtwarzalna kopia odpowiedzi |
| Restart Uvicorna | Utrata zmian | Plik i dane pozostają | Osobny proces może zachować kopię |
| Utrata zawartości | Trzeba utworzyć dane ponownie | Wymaga odtworzenia bazy z kopii | Kopia odbuduje się z bazy |
| Awaria | Proces nie obsługuje żądań | Listy i zapisy nie działają | BYPASS, sprawna baza działa dalej |
| Główny problem | Utrata stanu | Transakcje, blokady, schemat i kopie bezpieczeństwa | Aktualność, TTL, nieudane DEL |

Dla klienta dotychczasowe API i błędy są zachowane, a zapis przestał znikać po restarcie; doszły nagłówki cache, korekta ilości, gotowość oraz moduł przyjęć Inventory. Wewnątrz zmieniono słowniki na repozytoria, sesje i transakcje oraz dodano opcjonalny cache.

Inventory i Outbound nadal działają w jednym procesie FastAPI. Dodanie bazy i Redis samo nie tworzy systemu niezależnych mikrousług. Osobne bazy i odpowiedzialności przygotowują przyszłe wydzielenie procesów i integrację HTTP. Druga usługa powinna zmieniać cudze dane przez API, bez bezpośredniego zapisu do jej tabel.

Docelowo dołączą Shipping i Auth jako trzecia i czwarta usługa. Obecne testy, raporty i konfiguracja uruchomienia dotyczą wyłącznie zaimplementowanych Inventory i Outbound; nie potwierdzają działania planowanego logowania ani wysyłki.

Awaria procesu przerywa obsługę żądań, ale nie usuwa zatwierdzonych danych. Awaria Redis pozwala działać bazie; po odzyskaniu połączenia możliwe są stare wpisy do TTL. Awaria bazy blokuje zapis i listy; gotowość to 503. Dopuszczalne opóźnienie informacyjnego podglądu wynosi 60 s, ale decyzje domenowe zawsze wymagają bazy.

Zmiana SQLite na PostgreSQL wymaga sterownika, konfiguracji, schematu/migracji, sprawdzenia UUID i JSON, ograniczeń, izolacji i blokad oraz testów współbieżności. `BEGIN IMMEDIATE` jest mechanizmem SQLite. Wariant PostgreSQL nie był testowany, ORM nie zapewnia automatycznej zgodności. `create_all()` nie migruje kolumn; Alembic pozostaje rozszerzeniem.

Brakuje jeszcze integracji HTTP Inventory–Outbound, zwalniania rezerwacji, implementacji Shipping i Auth oraz mechanizmu naprawy unieważnień poza TTL. Do wdrożenia Auth pozostają konta, logowanie i sprawdzanie JWT oraz ról w pozostałych usługach. Przyjęcia są częścią Inventory; anulowanie po `POSTED` wymagałoby osobnego mechanizmu odwrócenia dostawy. Cache odpowiedzi z danymi odbiorcy wymaga ograniczenia dostępu do Redis. Na tym etapie jest on wystawiony wyłącznie lokalnie. Nie deklarujemy gotowości produkcyjnej.

### Dokumentacja, autorzy i AI — LAB2

| Źródło | Problem | Zastosowanie | Weryfikacja |
|---|---|---|---|
| [Oficjalny ZIP LAB2](https://pawoom.lazysolutions.pl/2026_2027/lab2/lab2-support.zip) | Włączenie persystencji i opcjonalnego cache do własnego LAB1 | Oryginalne adaptery, wzorzec repozytorium i cache-aside, atrapa Redis, układ dokumentacji | Porównanie plików bajt po bajcie z archiwum, hashe w raporcie, własne testy API i HTTP |
| [SQLAlchemy 2.0 — sesje i transakcje](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block) | Zatwierdzenie albo wycofanie całej operacji | Osobna sesja na operację, `sessions.begin()`, zamykanie sesji | Testy błędu po flush: brak częściowego UPDATE lub INSERT, kolejny zapis działa |
| [redis-py 6.4 — połączenia](https://redis.readthedocs.io/en/v6.4.0/connections.html#redis.Redis.from_url) | URL, timeouty, ponowienia i opcjonalność cache | `make_redis()` z ZIP: `Redis.from_url`, tekst, timeouty 0,3 s i brak ponowień | Awaria GET/SET/DELETE w testach oraz zatrzymanie i przywrócenie rzeczywistego serwera |

Dokumentację oficjalnych bibliotek przeczytano i porównano z kodem. Nie instalowano zewnętrznych skilli SQLAlchemy. Testy wstrzykują własnego klienta cache i adresy baz przez parametry `create_app()`.

Numer albumu: **131427**. 

Zajmowałem się kwestiami Outboundu oraz redisa. Przeglądałem testy napisane przez duży model językowy.

Numer albumu: **131413**. 

Zajmowałem się kwestiami Inventory oraz sqlalchemy. Przeglądałem testy napisane przez duży model językowy.

AI: użyto codexa to napisania testów. Pomógł też w kwestii ustalenia najlepszych wersji redisa i sqlalchemy


