# System Magazynowy — System mikrousługowy

Projekt semestralny z przedmiotu *Projektowanie aplikacji webowych opartych o mikrousługi*. System obsługuje przyjęcie towaru do magazynu, rezerwację stanów dla zamówień, pakowanie, wydanie przesyłki kurierowi oraz konta pracowników z rolami. Składa się z czterech mikrousług domenowych w FastAPI, z których każda ma własną bazę SQLite. Infrastrukturę tworzą Spring Cloud Gateway, Eureka, Redis i RabbitMQ. Całość uruchamia się jednym poleceniem Docker Compose.

## Spis treści

1. [Autorzy i podział odpowiedzialności](#1-autorzy-i-podział-odpowiedzialności)
2. [Opis domeny biznesowej i granice kontekstów (DDD)](#2-opis-domeny-biznesowej-i-granice-kontekstów-ddd)
3. [Architektura systemu i konfiguracja kontenerowa (LAB1–LAB6)](#3-architektura-systemu-i-konfiguracja-kontenerowa-lab1lab6)
4. [Kontrakty komunikacji synchronicznej (API Gateway + REST + Eureka)](#4-kontrakty-komunikacji-synchronicznej-api-gateway--rest--eureka)
5. [Kontrakty komunikacji asynchronicznej (RabbitMQ + Saga)](#5-kontrakty-komunikacji-asynchronicznej-rabbitmq--saga)
6. [Instrukcja uruchomienia, testowania i zatrzymania systemu](#6-instrukcja-uruchomienia-testowania-i-zatrzymania-systemu)
7. [Wyniki weryfikacji i eksperymentów awarii (LAB1–LAB6)](#7-wyniki-weryfikacji-i-eksperymentów-awarii-lab1lab6)
8. [Wykorzystana dokumentacja techniczna (EK_03)](#8-wykorzystana-dokumentacja-techniczna-ek_03)

---

## 1. Autorzy i podział odpowiedzialności

Projekt realizowany w parze.

### Kacper Dusza — nr albumu 131427

| Mikrousługa | Odpowiedzialność biznesowa | Własne dane (obiekty domenowe) | Endpointy (przez Gateway) |
|---|---|---|---|
| **Outbound** (`outbound-service`, :8102) | Realizacja zamówień: przyjęcie zamówienia, zlecenie rezerwacji towaru, pakowanie, anulowanie, odnotowanie wysyłki | `Order` (pozycje `OrderItem`), `processed_events`, `event_outbox` w `outbound.db` | `/api/orders`, `/api/orders/{id}`, `/api/orders/stock-check/{stock_id}`, `/api/orders/{id}/reserve`, `/pack`, `/cancel`, `/sync-reservation`, `/sync-cancellation`, `/shipping-check`, `/dispatch`, `/api/orders/consume-events`, `/api/orders/consume-reservation-events` |
| **Shipping** (`shipping-service`, :8103) | Obsługa przesyłek: rejestracja przesyłki dla spakowanego zamówienia, przygotowanie, wydanie kurierowi i publikacja zdarzenia | `Shipment` w `shipping.db` | `/api/shipments`, `/api/shipments/{id}`, `/api/shipments/{id}/ready`, `/hand-over`, `/cancel`, `/sync` |

**Wkład w infrastrukturę i testy:** serwer Eureka (`eureka-server/`), obraz Python (`Dockerfile`), adapter cache Redis (`app/cache.py`), klient discovery (`app/discovery.py`). Współtworzenie `compose.yaml`, tras Gateway i testów integracyjnych. Testy jednostkowe: `tests/test_outbound.py`, `test_order_cancellations.py`, `test_auth_shipping.py` (część Shipping), `test_discovery.py`.

### Radosław Cebula — nr albumu 131413

| Mikrousługa | Odpowiedzialność biznesowa | Własne dane (obiekty domenowe) | Endpointy (przez Gateway) |
|---|---|---|---|
| **Inventory** (`inventory-service`, :8101) | Stany magazynowe w lokalizacjach, przyjęcia dostaw, rezerwacje dla zamówień, rozchód towaru po wysyłce i zwalnianie rezerwacji po anulowaniu | `Stock` (z `Reservation`), `Receipt` (z pozycjami), `reservation_batches`, `processed_events`, `event_outbox` w `inventory.db` | `/api/stocks`, `/api/stocks/{id}`, `/api/stocks/{id}/reservations`, `/api/receipts`, `/api/receipts/{id}`, `/accept`, `/post`, `/cancel`, `/api/reservations/consume-events`, `/sync-events`, `/consume-shipment-events`, `/consume-cancellation-events` |
| **Auth** (`auth-service`, :8104) | Konta pracowników, logowanie, wydawanie tokenów JWT RS256, role i aktywność kont | `User` w `auth.db` | `/api/auth/login`, `/api/auth/me`, `/api/auth/users`, `/api/auth/users/{id}` |

**Wkład w infrastrukturę i testy:** adapter RabbitMQ (`app/messaging.py`) z publisher confirms i ręcznym ACK, wzorzec outbox oraz deduplikacja zdarzeń w Inventory. Współtworzenie `compose.yaml`, tras Gateway i testów integracyjnych. Testy jednostkowe: `tests/test_inventory.py`, `test_receipts.py`, `test_reservation_events.py`, `test_inventory_shipments.py`, `test_messaging.py`, `test_auth_shipping.py` (część Auth).

---

## 2. Opis domeny biznesowej i granice kontekstów (DDD)

### Problem biznesowy

Magazyn przyjmuje dostawy od dostawców, przechowuje towar w lokalizacjach i realizuje zamówienia klientów. Zamówienie można spakować tylko wtedy, gdy towar został dla niego zarezerwowany. Nie wolno przy tym przekroczyć fizycznego stanu. Spakowaną paczkę wydaje się kurierowi, a po wydaniu zamówienie zmienia stan na wysłane i towar schodzi ze stanu magazynowego. Każdą operację wykonuje uwierzytelniony pracownik o odpowiedniej roli.

### Słownik pojęć (Ubiquitous Language)

| Pojęcie | Znaczenie |
|---|---|
| **SKU** | Kod produktu (Stock Keeping Unit) |
| **Lokalizacja** | Miejsce składowania w magazynie, np. `A-01`; para (SKU, lokalizacja) jest unikalna |
| **Stan (`Stock`)** | Fizyczna ilość danego SKU w lokalizacji |
| **Rezerwacja** | Ilość towaru przydzielona konkretnemu zamówieniu |
| **Dostępność** | Ilość fizyczna pomniejszona o sumę rezerwacji |
| **Przyjęcie (`Receipt`)** | Dostawa od dostawcy: `NEW → ACCEPTED → POSTED`; zaksięgowanie zwiększa stany |
| **Alokacja** | Rozdział pozycji zamówienia na konkretne stany (lokalizacje) w jednej transakcji |
| **Zamówienie (`Order`)** | Zlecenie klienta: `NEW → RESERVATION_PENDING → RESERVED → PACKED → SHIPPED`; alternatywnie `RESERVATION_REJECTED` lub `CANCELLED` |
| **Przesyłka (`Shipment`)** | Paczka dla spakowanego zamówienia: `NEW → READY → HANDED_OVER` lub `CANCELLED` |
| **Wydanie kurierowi** | Fakt przekazania przesyłki; generuje zdarzenie `ShipmentHandedOver` |
| **Rozchód** | Odjęcie wydanych sztuk ze stanu i rozliczenie rezerwacji (`CONSUMED`) |
| **Zwolnienie** | Usunięcie rezerwacji anulowanego zamówienia (`RELEASED`) bez zmiany ilości fizycznej |
| **Rola** | `ADMIN`, `WAREHOUSE` (magazynier) lub `SHIPPING` (spedytor) |

### Mikrousługi, właściciele danych i reguły biznesowe

| Mikrousługa | Właściciel (autor) | Baza danych (wolumen) | Tabele | Kluczowe reguły biznesowe |
|---|---|---|---|---|
| Inventory | Radosław Cebula | `inventory.db` (`inventory-data`) | `stocks`, `receipts`, `reservation_batches`, `processed_events`, `event_outbox` | Suma rezerwacji ≤ ilość fizyczna (409). Korekta nie może zejść poniżej rezerwacji (409). Przyjęcie można zaksięgować tylko z `ACCEPTED` (409). Alokacja całego zamówienia jest atomowa: brak jednej pozycji odrzuca całość |
| Outbound | Kacper Dusza | `outbound.db` (`outbound-data`) | `orders`, `order_dispatches`, `processed_events`, `event_outbox` | Pakowanie wymaga stanu `RESERVED` (409). Anulowanie jest dozwolone tylko z `RESERVED`. `SHIPPED` wyłącznie po zdarzeniu wydania przesyłki |
| Shipping | Kacper Dusza | `shipping.db` (`shipping-data`) | `shipments` | Przesyłkę można utworzyć tylko dla zamówienia `PACKED` (409). Jedna przesyłka na zamówienie. Wydanie kurierowi wyłącznie z `READY` (409) |
| Auth | Radosław Cebula | `auth.db` (`auth-data`) | `users` | Konta tworzy tylko `ADMIN` (403). Email unikalny (409). Logowanie wymaga aktywnego konta (401). Nie można dezaktywować ostatniego administratora (409) |

Żadna usługa nie otwiera bazy innej usługi. Dane z cudzego kontekstu są pobierane przez REST (przez Eurekę) albo docierają jako zdarzenia RabbitMQ.

### Diagram kontekstów ograniczonych

```mermaid
flowchart LR
    subgraph AUTH[Auth Service]
        direction TB
        AU["User<br/>id, email, password_hash, role, is_active"]
        AR["Reguła: konta tworzy tylko ADMIN"]
        ADB[(auth.db)]
    end
    subgraph INV[Inventory Service]
        direction TB
        IS["Stock + Reservation<br/>sku, location, quantity"]
        IR["Receipt<br/>supplier, items, status"]
        IRL["Reguła: rezerwacje ≤ ilość fizyczna"]
        IDB[(inventory.db<br/>+ prefiks Redis :inventory)]
    end
    subgraph OUT[Outbound Service]
        direction TB
        OO["Order<br/>recipient, items, status"]
        ORL["Reguła: PACKED tylko z RESERVED"]
        ODB[(outbound.db<br/>+ prefiks Redis :outbound)]
    end
    subgraph SHP[Shipping Service]
        direction TB
        SS["Shipment<br/>order_id, courier_id, weight_kg, status"]
        SRL["Reguła: wydanie tylko z READY"]
        SDB[(shipping.db)]
    end

    OUT -->|"REST GET /stocks/{id} (stock-check)"| INV
    SHP -->|"REST GET /orders/{id}/shipping-check"| OUT
    OUT -.->|"AMQP OrderReservationRequested / OrderCancelled"| INV
    INV -.->|"AMQP OrderStockReserved / OrderStockReservationRejected"| OUT
    SHP -.->|"AMQP ShipmentHandedOver"| OUT
    SHP -.->|"AMQP ShipmentHandedOver"| INV
    AUTH -.->|"JWT RS256 (klucz publiczny)"| INV
    AUTH -.->|"JWT RS256"| OUT
    AUTH -.->|"JWT RS256"| SHP
```

Linie ciągłe oznaczają wywołania synchroniczne HTTP, a przerywane zdarzenia asynchroniczne lub zaufanie do podpisu JWT. Auth nie jest wywoływany przy każdym żądaniu: usługi weryfikują token lokalnie kluczem publicznym.

---

## 3. Architektura systemu i konfiguracja kontenerowa (LAB1–LAB6)

### Diagram architektury kontenerowej

```mermaid
flowchart TB
    C["Klient (przeglądarka, curl, pytest)"] -->|"127.0.0.1:8080"| G

    subgraph NET["Sieć Docker Compose: warehouse (bridge)"]
        G["api-gateway :8080<br/>ports 127.0.0.1:8080<br/>StripPrefix=1, X-Gateway: lab4"]
        E["eureka :8761<br/>expose"]
        I["inventory :8101<br/>expose"]
        O["outbound :8102<br/>expose"]
        S["shipping :8103<br/>expose"]
        A["auth :8104<br/>expose"]
        R[("redis :6379<br/>expose, cache-aside")]
        Q["rabbitmq :5672 / :15672<br/>expose, exchange warehouse.events"]
        K["init-keys<br/>zadanie jednorazowe"]

        G -->|"lb://INVENTORY-SERVICE"| I
        G -->|"lb://OUTBOUND-SERVICE"| O
        G -->|"lb://SHIPPING-SERVICE"| S
        G -->|"lb://AUTH-SERVICE"| A
        O -->|"HTTP + JWT (stock-check)"| I
        S -->|"HTTP + JWT (shipping-check)"| O
        I <-->|"publish / basic_get + ACK"| Q
        O <-->|"publish / basic_get + ACK"| Q
        S -->|"publish ShipmentHandedOver"| Q
        I --> R
        O --> R
        G -. "pobieranie rejestru" .-> E
        I -. rejestracja .-> E
        O -. rejestracja .-> E
        S -. rejestracja .-> E
        A -. rejestracja .-> E
    end

    I --- VI[("inventory-data<br/>/data/inventory.db")]
    O --- VO[("outbound-data<br/>/data/outbound.db")]
    S --- VS[("shipping-data<br/>/data/shipping.db")]
    A --- VA[("auth-data<br/>/data/auth.db")]
    Q --- VQ[("rabbitmq-data")]
    K --> VPR[("jwt-private")]
    K --> VPU[("jwt-public")]
    VPR -->|ro| A
    VPU -->|ro| I & O & S & A
```

### Kontenery w `compose.yaml`

| Kontener | Obraz / budowanie | Port wewnętrzny (`expose`) | Port opublikowany (`ports`) | Kluczowe zmienne środowiskowe | Wolumeny |
|---|---|---|---|---|---|
| `init-keys` | `warehouse-python:lab6` (główny `Dockerfile`) | — | — | — | `jwt-private`, `jwt-public` (zapis) |
| `eureka` | `eureka-server/Dockerfile` (Maven → JRE 21) | 8761 | — | `SERVER_ADDRESS=0.0.0.0`, `JAVA_TOOL_OPTIONS` | — |
| `redis` | `redis:7-alpine` | 6379 | — | `--save "" --appendonly no` (cache ulotny) | — |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 5672, 15672 | — | `RABBITMQ_DEFAULT_USER/PASS` | `rabbitmq-data:/var/lib/rabbitmq` |
| `inventory` | `warehouse-python:lab6` | 8101 | — | `DATABASE_URL=sqlite:////data/inventory.db`, wspólne¹ | `inventory-data:/data`, `jwt-public:ro` |
| `outbound` | `warehouse-python:lab6` | 8102 | — | `OUTBOUND_DATABASE_URL=sqlite:////data/outbound.db`, wspólne¹ | `outbound-data:/data`, `jwt-public:ro` |
| `shipping` | `warehouse-python:lab6` | 8103 | — | `SHIPPING_DATABASE_URL=sqlite:////data/shipping.db`, wspólne¹ | `shipping-data:/data`, `jwt-public:ro` |
| `auth` | `warehouse-python:lab6` | 8104 | — | `AUTH_DATABASE_URL=sqlite:////data/auth.db`, `JWT_PRIVATE_KEY_FILE`, `JWT_TTL_SECONDS=900` | `auth-data:/data`, `jwt-private:ro`, `jwt-public:ro` |
| `api-gateway` | `gateway/Dockerfile` (Maven → JRE 21) | 8080 | **`127.0.0.1:8080:8080`** | `GATEWAY_EUREKA_URL=http://eureka:8761/eureka/` | — |

¹ Wspólne zmienne usług Python: `EUREKA_SERVER_URL=http://eureka:8761`, `REDIS_URL=redis://redis:6379/0`, `RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/%2F`, `RABBITMQ_EXCHANGE=warehouse.events`, `CACHE_PREFIX=lab6:warehouse`, `CACHE_TTL_SECONDS=60`, `JWT_PUBLIC_KEY_FILE`, `JWT_ISSUER=warehouse-auth`, `JWT_AUDIENCE=warehouse-api`, `INSTANCE_HOST`/`INSTANCE_IP` (nazwa kontenera), `SERVICE_PORT`.

**Kolejność startu i healthchecki.** Usługi domenowe czekają na `service_healthy` Eureki, Redis i RabbitMQ oraz na `service_completed_successfully` zadania `init-keys`. Gateway czeka na zdrowie wszystkich czterech usług. Healthcheck usług Python wywołuje `/ready`, który sprawdza własną bazę i Redis. RabbitMQ jest sprawdzany przez `rabbitmq-diagnostics check_port_listener 5672`, a Eureka i Gateway przez `wget`. Stan `Exited (0)` kontenera `init-keys` jest prawidłowy.

**Obrazy.** Gateway i Eureka budują się wieloetapowo: etap `maven:3.9.9-eclipse-temurin-21` kompiluje JAR, a obraz końcowy `eclipse-temurin:21-jre-alpine` zawiera tylko JRE i aplikację. Obraz Python instaluje `requirements.txt` przed skopiowaniem kodu, więc zmiana kodu nie unieważnia warstwy zależności. Wszystkie procesy działają jako nieuprzywilejowany użytkownik `warehouse` (UID 10001). Pliki `.dockerignore` wykluczają `.venv`, bazy, klucze i katalogi `target/`.

### Uzasadnienie izolacji, wysokiej spójności i luźnego powiązania

- **Izolacja sieciowa.** Jedynym portem opublikowanym na hoście jest `127.0.0.1:8080` (Gateway). Mikrousługi, Eureka, Redis i RabbitMQ mają tylko `expose`, więc klient z hosta nie ominie bramki. Gateway nie zawiera logiki biznesowej ani dostępu do baz: tylko routuje, usuwa prefiks `/api` i dodaje nagłówek diagnostyczny. Uwierzytelnianie JWT działa w każdej usłudze, także przy dostępie z wnętrza sieci.
- **Wysoka spójność.** Każda usługa obejmuje jeden kontekst biznesowy z własnymi regułami i własną bazą (Database per Service). Przyjęcia dostaw są modułem Inventory, ponieważ ich zaksięgowanie musi atomowo zmienić stany w tej samej transakcji.
- **Luźne powiązanie.** Usługi adresują się po nazwach logicznych z Eureki, a nie po adresach. Procesy rozciągnięte w czasie (rezerwacja, wysyłka, anulowanie) przebiegają przez trwałe kolejki RabbitMQ. Awaria lub restart konsumenta nie blokuje producenta, a wiadomości czekają w kolejce. Synchroniczne zostały tylko odczyty potrzebne do natychmiastowej decyzji (dostępność towaru, stan `PACKED` przed wydaniem).
- **Kompromis.** Usługi Python są budowane z jednego obrazu i wspólnego pakietu `app/`, co upraszcza laboratorium, ale wiąże cykl wydań. Każdy proces uruchamia jednak tylko swoją aplikację (`app.inventory:app`, `app.outbound:app` itd.) i inicjalizuje wyłącznie własną bazę.

---

## 4. Kontrakty komunikacji synchronicznej (API Gateway + REST + Eureka)

### Trasy API Gateway (`gateway/src/main/resources/application.yml`)

| Ścieżka zewnętrzna (`127.0.0.1:8080`) | Cel (`lb://`) | Ścieżka wewnętrzna po `StripPrefix=1` |
|---|---|---|
| `/api/stocks`, `/api/stocks/**` | `lb://INVENTORY-SERVICE` | `/stocks/**` |
| `/api/receipts`, `/api/receipts/**` | `lb://INVENTORY-SERVICE` | `/receipts/**` |
| `/api/reservations/consume-events`, `/sync-events`, `/consume-shipment-events`, `/consume-cancellation-events` | `lb://INVENTORY-SERVICE` | `/reservations/...` |
| `/api/orders`, `/api/orders/**` | `lb://OUTBOUND-SERVICE` | `/orders/**` |
| `/api/shipments`, `/api/shipments/**` | `lb://SHIPPING-SERVICE` (timeout 10 s) | `/shipments/**` |
| `/api/auth`, `/api/auth/**` | `lb://AUTH-SERVICE` | `/auth/**` |
| `/docs/inventory`, `/docs/outbound`, `/docs/shipping`, `/docs/auth` | odpowiednia usługa (bez StripPrefix) | Swagger UI usługi |

Wszystkie odpowiedzi z tras otrzymują nagłówek `X-Gateway: lab4` (`default-filters`). Timeout połączenia wynosi 1 s, a odpowiedzi 5 s (dla Shipping 10 s). `discovery.locator` jest wyłączony, więc dostępne są tylko jawnie zdefiniowane trasy. Endpoint `POST /reservations` (bezpośrednia rezerwacja HTTP z wcześniejszych etapów) celowo nie ma trasy. Pod `http://127.0.0.1:8080/` Gateway serwuje panel testowy wszystkich usług.

### Uwierzytelnianie i role

Każde żądanie domenowe wymaga nagłówka `Authorization: Bearer <access_token>`. Token wydaje Auth (`POST /api/auth/login`). Jest podpisany RS256 kluczem prywatnym dostępnym tylko w kontenerze `auth` i ważny 900 s. Pozostałe usługi sprawdzają podpis, `iss`, `aud`, `exp`, `nbf` i rolę lokalnie. Brak lub błędny token daje **401**, a niewłaściwa rola **403**.

| Rola | Uprawnienia |
|---|---|
| `ADMIN` | Wszystkie operacje, zarządzanie kontami |
| `WAREHOUSE` | Inventory oraz zamówienia Outbound (tworzenie, rezerwacja, pakowanie, anulowanie, odbiór wyników rezerwacji) |
| `SHIPPING` | Shipping oraz `/orders/{id}/shipping-check`, `/orders/{id}/dispatch`, `/orders/consume-events` |

### Specyfikacja endpointów

Wszystkie ścieżki podano od strony klienta Gateway. Wspólne kody błędów dla każdej usługi: **401/403** (JWT/rola), **422** (walidacja Pydantic, np. błędny UUID lub `quantity: 0`) oraz **503** `{"detail":"Database unavailable"}` (awaria bazy).

#### Auth (autor: Radosław Cebula)

| Metoda i ścieżka | Opis | Sukces | Błędy |
|---|---|---|---|
| `POST /api/auth/login` | Logowanie, wydanie JWT (publiczne) | 200 | 401, 422 |
| `GET /api/auth/me` | Profil zalogowanego użytkownika | 200 | 401 |
| `POST /api/auth/users` | Utworzenie konta (ADMIN) | 201 | 401, 403, 409, 422 |
| `PATCH /api/auth/users/{id}` | Aktywacja/dezaktywacja konta (ADMIN) | 200 | 401, 403, 404, 409, 422 |

```http
POST /api/auth/login
{"email":"admin@example.com","password":"<hasło>"}

200 OK
{"access_token":"eyJhbGciOiJSUzI1NiIs...","token_type":"bearer"}

401 Unauthorized
{"detail":"Invalid credentials"}
```

```http
POST /api/auth/users
{"email":"magazynier@example.com","password":"haslo-co-najmniej-12-znakow","role":"WAREHOUSE"}

201 Created
{"id":"6f0c...","email":"magazynier@example.com","role":"WAREHOUSE","is_active":true}

409 Conflict
{"detail":"Email already exists"}
```

#### Inventory (autor: Radosław Cebula)

| Metoda i ścieżka | Opis | Sukces | Błędy |
|---|---|---|---|
| `GET /api/stocks` | Lista stanów | 200 | 503 |
| `GET /api/stocks/{id}` | Szczegóły stanu (cache-aside, nagłówek `X-Cache`) | 200 | 404, 422 |
| `PATCH /api/stocks/{id}` | Korekta ilości `{"quantity":14}` | 200 | 404, 409, 422 |
| `POST /api/stocks/{id}/reservations` | Rezerwacja pojedynczego stanu | 201 | 404, 409, 422 |
| `POST /api/receipts` | Rejestracja przyjęcia (`NEW`) | 201 | 422 |
| `GET /api/receipts`, `GET /api/receipts/{id}` | Lista i szczegóły przyjęć | 200 | 404, 422 |
| `POST /api/receipts/{id}/accept` | `NEW → ACCEPTED` | 200 | 404, 409 |
| `POST /api/receipts/{id}/post` | `ACCEPTED → POSTED`, zwiększenie lub utworzenie stanów | 200 | 404, 409 |
| `POST /api/receipts/{id}/cancel` | `NEW/ACCEPTED → CANCELLED` | 200 | 404, 409 |
| `POST /api/reservations/consume-events?limit=50` | Odbiór zleceń rezerwacji, alokacja, publikacja wyniku | 200 | 503 |
| `POST /api/reservations/sync-events` | Ponowienie publikacji oczekujących wyników | 200 | 503 |
| `POST /api/reservations/consume-shipment-events` | Odbiór wydań przesyłek, rozchód towaru | 200 | 409, 503 |
| `POST /api/reservations/consume-cancellation-events` | Odbiór anulowań, zwolnienie rezerwacji | 200 | 503 |

```http
POST /api/receipts
{"supplier":"Dostawca ABC","items":[{"sku":"SKU-001","target_location":"A-01","quantity_received":10}]}

201 Created
{"id":"483ba94c-...","supplier":"Dostawca ABC","status":"NEW",
 "items":[{"sku":"SKU-001","target_location":"A-01","quantity_received":10}]}

POST /api/receipts/{id}/post   (ponownie, gdy już POSTED)
409 Conflict
{"detail":"Only ACCEPTED receipts can be posted"}
```

```http
GET /api/stocks/a599e3fb-ee66-4dd1-aea5-72adba04102a
200 OK   X-Cache: MISS   X-Gateway: lab4
{"id":"a599e3fb-...","sku":"SKU-001","location":"A-01","quantity":10,"reservations":[]}

POST /api/stocks/{id}/reservations
{"order_id":"22222222-2222-4222-8222-222222222222","quantity":11}
409 Conflict
{"detail":"Insufficient available stock"}

{"order_id":"22222222-2222-4222-8222-222222222222","quantity":0}
422 Unprocessable Entity
{"detail":[{"type":"greater_than","loc":["body","quantity"],"msg":"Input should be greater than 0","input":0,"ctx":{"gt":0}}]}

GET /api/stocks/51269b12-cb93-409d-85fb-ef090600b325
404 Not Found
{"detail":"Stock not found"}
```

#### Outbound (autor: Kacper Dusza)

| Metoda i ścieżka | Opis | Sukces | Błędy |
|---|---|---|---|
| `POST /api/orders` | Utworzenie zamówienia (`NEW`) | 201 | 409, 422 |
| `GET /api/orders`, `GET /api/orders/{id}` | Lista, szczegóły (cache-aside, `X-Cache`) | 200 | 404, 422 |
| `GET /api/orders/stock-check/{stock_id}` | Dostępność towaru z Inventory przez Eurekę | 200 | 404, 422, **502**, **503** |
| `POST /api/orders/{id}/reserve` | Zlecenie rezerwacji przez RabbitMQ → `RESERVATION_PENDING` | **202** | 404, 409, 503 |
| `POST /api/orders/{id}/sync-reservation` | Ponowienie publikacji zlecenia | 200 | 404, 409, 503 |
| `POST /api/orders/consume-reservation-events` | Odbiór wyników rezerwacji → `RESERVED` / `RESERVATION_REJECTED` | 200 | 422, 503 |
| `POST /api/orders/{id}/pack` | `RESERVED → PACKED` | 200 | 404, 409 |
| `POST /api/orders/{id}/cancel` | `RESERVED → CANCELLED`, publikacja `OrderCancelled` | 200 | 404, 409, 503 |
| `POST /api/orders/{id}/sync-cancellation` | Ponowienie publikacji anulowania | 200 | 404, 409, 503 |
| `GET /api/orders/{id}/shipping-check` | Odczyt z bazy (bez cache) na potrzeby Shipping | 200 | 404 |
| `POST /api/orders/consume-events` | Odbiór `ShipmentHandedOver` → `SHIPPED` | 200 | 422, 503 |
| `POST /api/orders/{id}/dispatch` | Zgodność wsteczna z HTTP (LAB4) | 200 | 404, 409 |

```http
POST /api/orders
{"recipient_name":"Jan Kowalski","recipient_phone":"123456789",
 "recipient_address":"ul. Polna 1, Warszawa","items":[{"sku":"SKU-001","quantity":3}]}

201 Created
{"id":"c295eade-...","status":"NEW","recipient_name":"Jan Kowalski", ...,
 "items":[{"sku":"SKU-001","quantity":3}]}

POST /api/orders/{id}/reserve
202 Accepted
{"id":"c295eade-...","status":"RESERVATION_PENDING","reservation_attempt_id":"...", ...}

POST /api/orders/{id}/pack   (przed odbiorem wyniku rezerwacji)
409 Conflict
{"detail":"Order must be reserved before packing"}

GET /api/orders/stock-check/{stock_id}
200 OK
{"stock":{"id":"a599e3fb-...","sku":"SKU-001","quantity":14,"reservations":[]},"available_quantity":14}

503 Service Unavailable   (Inventory zatrzymany)
{"detail":"Inventory Service is unavailable"}
```

#### Shipping (autor: Kacper Dusza)

| Metoda i ścieżka | Opis | Sukces | Błędy |
|---|---|---|---|
| `POST /api/shipments` | Rejestracja przesyłki dla zamówienia `PACKED` | 201 | 404, 409, 422, **502**, **503** |
| `GET /api/shipments`, `GET /api/shipments/{id}` | Lista i szczegóły | 200 | 404, 422 |
| `POST /api/shipments/{id}/ready` | `NEW → READY` (ponownie sprawdza `PACKED`) | 200 | 404, 409, 502, 503 |
| `POST /api/shipments/{id}/hand-over` | `READY → HANDED_OVER`, publikacja `ShipmentHandedOver` | 200 | 404, 409, 502, 503 |
| `POST /api/shipments/{id}/cancel` | `NEW/READY → CANCELLED` | 200 | 404, 409 |
| `POST /api/shipments/{id}/sync` | Ponowienie publikacji zdarzenia (ten sam `event_id`) | 200 | 404, 409, 503 |

```http
POST /api/shipments
{"order_id":"c295eade-...","destination_address":"Warszawa, Testowa 1",
 "courier_id":"22222222-2222-4222-8222-222222222222","weight_kg":2.5}

201 Created
{"id":"8531c72d-...","status":"NEW","handed_over_at":null,"notification_pending":false, ...}

POST /api/shipments/{id}/hand-over   (przesyłka w stanie NEW)
409 Conflict
{"detail":"Only READY shipments can be handed over"}
```

### Wywołania między usługami przez Eurekę

Usługi rejestrują się w Eurece (`py-eureka-client`) pod nazwami `INVENTORY-SERVICE`, `OUTBOUND-SERVICE`, `SHIPPING-SERVICE` i `AUTH-SERVICE`, z nazwą kontenera jako hostem. Gateway nie rejestruje się (`register-with-eureka: false`), tylko pobiera rejestr.

| Wywołanie | Funkcja | Timeout | Mapowanie błędów |
|---|---|---|---|
| Outbound → Inventory `GET /stocks/{id}` | `do_service("INVENTORY-SERVICE", ...)` | 3 s | 404 → 404; brak instancji / timeout / połączenie → **503**; inny błąd HTTP lub zła odpowiedź → **502** |
| Shipping → Outbound `GET /orders/{id}/shipping-check` | `do_service("OUTBOUND-SERVICE", ...)` | 3 s | 404 → 404; niedostępność → **503**; błąd HTTP / złe dane → **502** |

Token JWT użytkownika jest przekazywany dalej w nagłówku `Authorization`. Nieprawidłowy UUID jest odrzucany kodem 422 bez wywołania zależności.

### Cache Redis (cache-aside)

| Odczyt | Klucz (`CACHE_PREFIX=lab6:warehouse`) | TTL | Unieważnienie (`DEL` po `COMMIT`) |
|---|---|---|---|
| `GET /stocks/{id}` | `lab6:warehouse:inventory:stock:<UUID>` | 60 s | Rezerwacja, korekta, zaksięgowanie przyjęcia, alokacja z RabbitMQ, rozchód, zwolnienie |
| `GET /orders/{id}` | `lab6:warehouse:outbound:order:<UUID>` | 60 s | Każda zmiana statusu, także po odbiorze zdarzenia z RabbitMQ |

Nagłówek `X-Cache` przyjmuje wartości **MISS** (odczyt z bazy i zapis kopii), **HIT** (odpowiedź z Redis) oraz **BYPASS** (Redis niedostępny, odpowiedź z bazy). Klient Redis ma timeouty 0,3 s i brak ponowień, więc awaria cache nie blokuje usługi. Odpowiedzi 404 i listy nie są cache'owane. Decyzje biznesowe (rezerwacja, pakowanie, wydanie) zawsze opierają się na danych z transakcji bazy, a nie na kopii w Redis. Dlatego Shipping korzysta z osobnego endpointu `shipping-check` bez cache.

### Diagram sekwencji — przepływ synchroniczny

```mermaid
sequenceDiagram
    autonumber
    participant C as Klient
    participant G as api-gateway :8080
    participant E as eureka :8761
    participant O as outbound :8102
    participant I as inventory :8101
    participant R as redis :6379
    participant DB as inventory.db

    Note over O,I: przy starcie: rejestracja i heartbeat
    O->>E: register OUTBOUND-SERVICE
    I->>E: register INVENTORY-SERVICE
    G->>E: fetch registry

    C->>G: GET /api/orders/stock-check/{id}<br/>Authorization: Bearer JWT
    G->>O: lb://OUTBOUND-SERVICE, StripPrefix=1<br/>GET /orders/stock-check/{id}
    O->>O: weryfikacja JWT (RS256, rola WAREHOUSE)
    O->>I: do_service("INVENTORY-SERVICE", GET /stocks/{id}, timeout=3)
    I->>R: GET lab6:warehouse:inventory:stock:{id}
    alt HIT
        R-->>I: JSON stanu
        I-->>O: 200, X-Cache: HIT
    else MISS
        R-->>I: nil
        I->>DB: SELECT stocks + reservations
        DB-->>I: wiersz
        I->>R: SET key TTL 60 s
        I-->>O: 200, X-Cache: MISS
    else Redis niedostępny
        I->>DB: SELECT
        I-->>O: 200, X-Cache: BYPASS
    end
    alt Inventory niedostępny lub timeout
        O-->>G: 503 Inventory Service is unavailable
    else odpowiedź OK
        O->>O: available = quantity − Σ rezerwacji
        O-->>G: 200 {stock, available_quantity}
    end
    G-->>C: odpowiedź + X-Gateway: lab4
```

---

## 5. Kontrakty komunikacji asynchronicznej (RabbitMQ + Saga)

### Przepływy zdarzeń

Wszystkie zdarzenia są publikowane do jednego trwałego exchange'a `warehouse.events` (typ `topic`, `durable=True`) jako trwałe wiadomości JSON (`delivery_mode=2`). Publikacja używa `mandatory=True` i publisher confirms. Każdy konsument ma własną trwałą kolejkę, a odbiór uruchamia się ręcznie endpointem HTTP (bez procesu w tle).

| Zdarzenie | Producent (endpoint) | Routing key | Kolejka | Konsument (endpoint) | Skutek |
|---|---|---|---|---|---|
| `OrderReservationRequested` | Outbound `/orders/{id}/reserve` | `order.reservation.requested` | `inventory.reservation-requests` | Inventory `/reservations/consume-events` | Atomowa alokacja całego zamówienia, zapis rezerwacji, publikacja wyniku |
| `OrderStockReserved` / `OrderStockReservationRejected` | Inventory (w trakcie odbioru) | `order.stock.reserved` / `order.stock.reservation_rejected` | `outbound.reservation-results` | Outbound `/orders/consume-reservation-events` | `RESERVED` albo `RESERVATION_REJECTED` z powodem |
| `ShipmentHandedOver` | Shipping `/shipments/{id}/hand-over` | `shipment.handed_over` | `outbound.shipment-events` | Outbound `/orders/consume-events` | `PACKED → SHIPPED` |
| `ShipmentHandedOver` | jw. | `shipment.handed_over` | `inventory.shipment-events` | Inventory `/reservations/consume-shipment-events` | Rozchód towaru, rezerwacje → `CONSUMED` |
| `OrderCancelled` | Outbound `/orders/{id}/cancel` | `order.cancelled` | `inventory.order-cancellations` | Inventory `/reservations/consume-cancellation-events` | Zwolnienie rezerwacji (`RELEASED`) |

Proces realizacji zamówienia jest **Sagą w wariancie choreografii**: nie ma centralnego koordynatora, a każda usługa reaguje na zdarzenia innych. Akcją kompensującą jest `OrderCancelled`, które zwalnia rezerwacje w Inventory. Odmowa rezerwacji (`OrderStockReservationRejected`) nie zmienia stanów, a zamówienie można ponowić nową próbą (`reservation_attempt_id`).

### Przykładowe zdarzenia (JSON)

```json
{
  "event_id": "9a9ed832-ecd9-5a50-abc6-25c0dbcd858a",
  "event_type": "ShipmentHandedOver",
  "shipment_id": "8531c72d-7576-4f4e-b844-5f3bc7aabb3c",
  "order_id": "c295eade-2128-4441-b04a-16edfa67e53a",
  "occurred_at": "2026-11-27T10:39:52.139586Z"
}
```

```json
{
  "event_id": "1b7c1c0e-5c43-5d0e-9a51-2f0d3c6c8a11",
  "event_type": "OrderReservationRequested",
  "attempt_id": "0d4f6a6e-8b8e-4f0e-a0a5-4b7f3b1d2c10",
  "order_id": "c295eade-2128-4441-b04a-16edfa67e53a",
  "items": [{"sku": "SKU-001", "quantity": 3}],
  "occurred_at": "2026-11-27T10:39:40.000000Z"
}
```

`OrderStockReserved` dodaje pole `stock_ids`, a `OrderStockReservationRejected` pole `reason`. `OrderCancelled` zawiera `order_id` i `items`. Modele są w `app/models/events.py`. Konsument ignoruje nadmiarowe pola (Tolerant Reader), a wiadomości nie zawierają haseł ani tokenów.

### Gwarancje dostarczenia, idempotencja i cache

- **Deterministyczny `event_id`.** Identyfikator powstaje jako UUIDv5 z identyfikatora zasobu, np. `uuid5(NAMESPACE_URL, "warehouse/shipments/{id}/handed-over")`. Ponowna publikacja tego samego faktu (`/sync`, restart) ma więc ten sam `event_id`. Model zdarzenia odrzuca wiadomość, której `event_id` nie pasuje do zasobu.
- **Publikacja po zapisie (outbox).** Zmiana stanu i wiadomość do wysłania (`event_outbox` albo `notification_pending=true`) zapisują się w jednej transakcji SQLite. Publikacja następuje dopiero po `COMMIT`. Gdy RabbitMQ jest niedostępny, operacja zwraca 503, ale stan pozostaje zapisany, a publikację ponawia endpoint `/sync...`.
- **Odbiór z ręcznym ACK.** `basic_get(auto_ack=False)`. Konsument w jednej transakcji zmienia stan i zapisuje `event_id` w `processed_events`, potem usuwa klucz Redis (`DEL`), a na końcu wysyła `basic_ack`. Błąd przetwarzania lub niepoprawna wiadomość kończą się `basic_nack(requeue=True)`, a wiadomość zostaje w kolejce.
- **Idempotencja.** `event_id` jest kluczem głównym `processed_events`. Duplikat nie zmienia stanu ponownie (nie odejmuje drugi raz towaru, nie rezerwuje drugi raz), ale ponownie wykonuje `DEL` w Redis i potwierdza wiadomość. Naprawia to sytuację, w której poprzednia próba zakończyła się po `COMMIT`, a przed `DEL`/`ACK`.
- **Unieważnienie cache.** Po każdym przetworzonym zdarzeniu konsument usuwa kopie zmienionych zasobów: zamówienia w Outbound oraz wszystkich dotkniętych stanów w Inventory. Pierwszy odczyt po odbiorze zwraca więc `X-Cache: MISS`.

### Diagram sekwencji — przepływ asynchroniczny (wydanie przesyłki)

```mermaid
sequenceDiagram
    autonumber
    participant C as Klient
    participant G as api-gateway
    participant S as shipping
    participant O as outbound
    participant MQ as rabbitmq<br/>warehouse.events
    participant QO as outbound.shipment-events
    participant R as redis

    C->>G: POST /api/shipments/{id}/hand-over
    G->>S: POST /shipments/{id}/hand-over
    S->>S: walidacja 422, reguła READY (409)
    S->>O: GET /orders/{order_id}/shipping-check (HTTP, Eureka)
    O-->>S: 200 status PACKED
    S->>S: COMMIT: HANDED_OVER, handed_over_at,<br/>notification_pending = true
    S->>MQ: basic_publish shipment.handed_over<br/>{event_id, ...} (persistent, confirm)
    MQ->>QO: routing do trwałej kolejki
    S->>S: notification_pending = false
    S-->>C: 200 HANDED_OVER

    Note over QO: wiadomość czeka, dopóki Outbound jej nie odbierze

    C->>G: POST /api/orders/consume-events
    G->>O: POST /orders/consume-events
    O->>QO: basic_get(auto_ack=False)
    QO-->>O: wiadomość
    alt event_id już w processed_events (duplikat)
        O->>R: DEL lab6:warehouse:outbound:order:{id}
        O->>QO: basic_ack
    else nowe zdarzenie
        O->>O: COMMIT: PACKED → SHIPPED,<br/>INSERT processed_events(event_id)
        O->>R: DEL lab6:warehouse:outbound:order:{id}
        O->>QO: basic_ack
    else błąd przetwarzania
        O->>QO: basic_nack(requeue=True)
    end
    O-->>C: 200 {consumed_messages, unique_events_processed}
```

Rezerwacja przebiega analogicznie, ale w dwóch krokach. Outbound `/reserve` publikuje `OrderReservationRequested` i zwraca 202. Inventory `/reservations/consume-events` alokuje towar w transakcji `BEGIN IMMEDIATE` i publikuje wynik. Outbound `/orders/consume-reservation-events` ustawia `RESERVED`.

---

## 6. Instrukcja uruchomienia, testowania i zatrzymania systemu

### Wymagania wstępne

- **Windows:** Docker Desktop z Compose v2 (WSL 2), PowerShell 5.1 lub 7.
- **Debian/Linux:** Docker Engine z wtyczką `docker compose`.
- Do testów z hosta: Python 3.12+ (testy sprawdzono na 3.13).
- Java, Maven ani lokalny Redis/RabbitMQ nie są potrzebne: wszystko budują i uruchamiają kontenery. Pierwsze budowanie wymaga dostępu do Docker Hub, PyPI i Maven Central.

Wszystkie polecenia wykonuje się w głównym katalogu projektu.

### Uruchomienie

```sh
docker compose up --build -d
docker compose ps -a
```

Wszystkie kontenery powinny mieć stan `Up (healthy)`, a `init-keys` stan `Exited (0)`. Po starcie Gateway potrzebuje kilku sekund na pobranie rejestru Eureki, więc pierwsze żądania mogą zwrócić 503 i należy je ponowić.

### Pierwsze konto administratora

Baza Auth jest początkowo pusta i nie ma domyślnego hasła. Skrypt pyta dwukrotnie o hasło (12–128 znaków):

```sh
docker compose exec auth python scripts/init_auth.py --email admin@example.com --keys-dir /keys/private
```

### Weryfikacja przepływów przez Gateway

**Windows (PowerShell, `curl.exe`):**

```powershell
# Logowanie i token
$T = (Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/api/auth/login `
      -ContentType 'application/json' `
      -Body '{"email":"admin@example.com","password":"<haslo>"}').access_token
$H = "Authorization: Bearer $T"

# Przyjęcie towaru: NEW -> ACCEPTED -> POSTED
'{"supplier":"Dostawca ABC","items":[{"sku":"SKU-001","target_location":"A-01","quantity_received":10}]}' |
  curl.exe -s -H $H -H "Content-Type: application/json" --data-binary "@-" http://127.0.0.1:8080/api/receipts
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/receipts/<RECEIPT_ID>/accept
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/receipts/<RECEIPT_ID>/post

# Przepływ synchroniczny i cache (dwa odczyty: X-Cache MISS, potem HIT)
curl.exe -s -H $H http://127.0.0.1:8080/api/stocks
curl.exe -i -H $H http://127.0.0.1:8080/api/stocks/<STOCK_ID>
curl.exe -i -H $H http://127.0.0.1:8080/api/stocks/<STOCK_ID>
curl.exe -s -H $H http://127.0.0.1:8080/api/orders/stock-check/<STOCK_ID>

# Przepływ asynchroniczny: zamówienie -> rezerwacja przez RabbitMQ -> pakowanie
'{"recipient_name":"Jan Kowalski","recipient_phone":"123456789","recipient_address":"ul. Polna 1, Warszawa","items":[{"sku":"SKU-001","quantity":3}]}' |
  curl.exe -s -H $H -H "Content-Type: application/json" --data-binary "@-" http://127.0.0.1:8080/api/orders
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/orders/<ORDER_ID>/reserve
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/reservations/consume-events
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/orders/consume-reservation-events
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/orders/<ORDER_ID>/pack

# Przesyłka i zdarzenie ShipmentHandedOver
'{"order_id":"<ORDER_ID>","destination_address":"Warszawa, Testowa 1","courier_id":"22222222-2222-4222-8222-222222222222","weight_kg":2.5}' |
  curl.exe -s -H $H -H "Content-Type: application/json" --data-binary "@-" http://127.0.0.1:8080/api/shipments
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/shipments/<SHIPMENT_ID>/ready
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/shipments/<SHIPMENT_ID>/hand-over
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/orders/consume-events
curl.exe -s -X POST -H $H http://127.0.0.1:8080/api/reservations/consume-shipment-events
curl.exe -i -H $H http://127.0.0.1:8080/api/orders/<ORDER_ID>
```

**Debian/Linux (bash, `curl`):**

```bash
# Logowanie i token
T=$(curl -s -X POST http://127.0.0.1:8080/api/auth/login -H 'Content-Type: application/json' \
      -d '{"email":"admin@example.com","password":"<haslo>"}' \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')
H="Authorization: Bearer $T"
GW=http://127.0.0.1:8080/api

# Przyjęcie towaru: NEW -> ACCEPTED -> POSTED
curl -s -H "$H" -H 'Content-Type: application/json' $GW/receipts \
  -d '{"supplier":"Dostawca ABC","items":[{"sku":"SKU-001","target_location":"A-01","quantity_received":10}]}'
curl -s -X POST -H "$H" $GW/receipts/<RECEIPT_ID>/accept
curl -s -X POST -H "$H" $GW/receipts/<RECEIPT_ID>/post

# Przepływ synchroniczny i cache (dwa odczyty: X-Cache MISS, potem HIT)
curl -s -H "$H" $GW/stocks
curl -i -H "$H" $GW/stocks/<STOCK_ID>
curl -i -H "$H" $GW/stocks/<STOCK_ID>
curl -s -H "$H" $GW/orders/stock-check/<STOCK_ID>

# Przepływ asynchroniczny: zamówienie -> rezerwacja przez RabbitMQ -> pakowanie
curl -s -H "$H" -H 'Content-Type: application/json' $GW/orders \
  -d '{"recipient_name":"Jan Kowalski","recipient_phone":"123456789","recipient_address":"ul. Polna 1, Warszawa","items":[{"sku":"SKU-001","quantity":3}]}'
curl -s -X POST -H "$H" $GW/orders/<ORDER_ID>/reserve
curl -s -X POST -H "$H" $GW/reservations/consume-events
curl -s -X POST -H "$H" $GW/orders/consume-reservation-events
curl -s -X POST -H "$H" $GW/orders/<ORDER_ID>/pack

# Przesyłka i zdarzenie ShipmentHandedOver
curl -s -H "$H" -H 'Content-Type: application/json' $GW/shipments \
  -d '{"order_id":"<ORDER_ID>","destination_address":"Warszawa, Testowa 1","courier_id":"22222222-2222-4222-8222-222222222222","weight_kg":2.5}'
curl -s -X POST -H "$H" $GW/shipments/<SHIPMENT_ID>/ready
curl -s -X POST -H "$H" $GW/shipments/<SHIPMENT_ID>/hand-over
curl -s -X POST -H "$H" $GW/orders/consume-events
curl -s -X POST -H "$H" $GW/reservations/consume-shipment-events
curl -i -H "$H" $GW/orders/<ORDER_ID>
```

Te same operacje są dostępne w panelu testowym `http://127.0.0.1:8080/` oraz w Swagger UI: `/docs/auth`, `/docs/inventory`, `/docs/outbound`, `/docs/shipping`. W Swaggerze token wkleja się w **Authorize**. Stan kolejek RabbitMQ i rejestru Eureki można sprawdzić bez publikowania ich portów:

```sh
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged durable
docker compose exec eureka wget -qO- --header "Accept: application/json" http://127.0.0.1:8761/eureka/apps
```

### Testy automatyczne

**Testy jednostkowe i komponentowe** używają atrap (`tests/fakes.py`: pamięciowy Redis, broker zdarzeń w pamięci, sztuczny klient Eureki) oraz tymczasowych baz SQLite. Nie wymagają uruchomionego systemu. `pytest.ini` domyślnie pomija `tests/integration`.

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

```bash
# Debian
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

Alternatywnie, bez Pythona na hoście: `docker compose exec inventory python -m pytest -q`.

**Testy integracyjne** wysyłają żądania do działających kontenerów wyłącznie przez Gateway (`GATEWAY_URL`, domyślnie `http://127.0.0.1:8080`). Wymagają tokenu administratora. `LAB6_CONTAINERS=1` włącza inspekcję mapowań portów i wstrzykiwanie duplikatu zdarzenia przez `docker compose exec`. Testy tworzą w bazach rekordy z unikalnymi identyfikatorami.

```powershell
# Windows
$env:GATEWAY_TOKEN = $T
$env:LAB6_CONTAINERS = "1"
.\.venv\Scripts\python.exe -m pytest -o addopts= tests/integration -q
```

```bash
# Debian
LAB6_CONTAINERS=1 GATEWAY_TOKEN="$T" .venv/bin/python -m pytest -o addopts= tests/integration -q
```

### Diagnostyka

```sh
docker compose logs --tail=50 inventory outbound shipping auth
docker compose logs --tail=50 eureka api-gateway rabbitmq
docker compose restart inventory
```

### Zatrzymanie

```sh
docker compose down       # usuwa kontenery i sieć, zachowuje wolumeny z bazami, kolejkami i kluczami
docker compose down -v    # dodatkowo usuwa wszystkie wolumeny: bazy, wiadomości RabbitMQ i klucze JWT
```

Po `down -v` kolejny start wymaga ponownego utworzenia konta administratora.

---

## 7. Wyniki weryfikacji i eksperymentów awarii (LAB1–LAB6)

Weryfikację wykonano 27.11.2026 na Debianie 13 (Docker Engine 29.10.1, Docker Compose 5.8.0, Python 3.13.5). Uruchomiono czysty system z aktualnego kodu (`docker compose up --build -d --wait`, świeże wolumeny, nowe konto administratora). Zamiast zrzutów ekranu poniżej zamieszczono rzeczywiste logi z terminala.

### Podsumowanie

| # | Eksperyment | Oczekiwany wynik | Wynik rzeczywisty |
|---|---|---|---|
| 1 | Start systemu | Wszystkie kontenery healthy, `init-keys` Exited (0), 4 usługi UP w Eurece | Zgodny: 8 × healthy, Exited (0), 4 × UP z nazwami kontenerów |
| 2 | Testy jednostkowe (atrapy) | Wszystkie przechodzą | **182 passed, 2 skipped** (pominięte przypadki dotyczą wyłącznie Windows) |
| 3 | Testy integracyjne przez Gateway | Wszystkie przechodzą | **20 passed** |
| 4 | Izolacja portów `expose` | Z hosta dostępny tylko 8080 | 8101–8104, 8761, 5672, 15672 odrzucone. Port 6379 na hoście należy do systemowego `redis-server`, a nie do Compose (kontener Redis ma tylko `6379/tcp` bez mapowania) |
| 5 | Walidacja i reguły (LAB1) | 422 / 409 / 404 bez zmiany stanu | `quantity: 0` → 422; rezerwacja 11 z 10 → 409; ponowne księgowanie → 409; nieznany UUID → 404 |
| 6 | Brak trasy a brak zasobu | Oba 404, ale z różnych warstw | `/api/nie-ma-takiej-trasy` → 404 z Gateway bez `X-Gateway`; `/api/stocks/{losowy}` → 404 z Inventory z `X-Gateway: lab4` i `Stock not found` |
| 7 | Cache MISS → HIT i unieważnienie (LAB2) | MISS, HIT, po zapisie MISS | MISS → HIT; po `PATCH` → MISS → HIT |
| 8 | Awaria Redis | Odpowiedź z bazy, `BYPASS` | `docker compose stop redis` → 200 `X-Cache: BYPASS`, listy 200; po starcie Redis → MISS → HIT |
| 9 | Awaria zależności (LAB3) | Outbound zwraca 503, sam działa dalej | `stock-check` → 503 `Inventory Service is unavailable`; `GET /api/orders` → 200; po starcie Inventory 200 po ok. 1 s |
| 10 | Gateway bez instancji usługi (LAB4) | 503 | Przez pierwsze ok. 30 s **500** (Gateway ma jeszcze w pamięci rejestr z zatrzymaną instancją), potem stale **503** |
| 11 | Buforowanie w RabbitMQ (LAB5) | Wiadomość czeka przy wyłączonym konsumencie | Inventory zatrzymany → `/reserve` 202, `inventory.reservation-requests` = 1 wiadomość; po starcie odbiór 1/1 i `RESERVED` |
| 12 | Pełna Saga wysyłki | `SHIPPED` i rozchód towaru po odbiorach | Po `hand-over` po 1 wiadomości w `outbound.shipment-events` i `inventory.shipment-events`; zamówienie `PACKED` do chwili odbioru, potem `SHIPPED`; stan 14 → 11, rezerwacje rozliczone |
| 13 | Duplikat `event_id` | Brak ponownej zmiany stanu, ACK | 2 kopie → `consumed_messages: 2, unique_events_processed: 0` w obu konsumentach; stan nadal 11 |
| 14 | Trwałość po `down` / `up` (LAB6) | Dane zachowane, cache pusty | Nowe ID wszystkich kontenerów; stan, zamówienie `SHIPPED`, przesyłka `HANDED_OVER`, przyjęcie `POSTED` zachowane; pierwszy odczyt `MISS`; ten sam JWT nadal ważny |
| 15 | Błędny JWT | 401 | `GET /api/stocks` z `Bearer zly-token` → 401 |

**Ograniczenie wynikające z eksperymentu 10:** przez czas odświeżania rejestru (domyślnie ok. 30 s) Gateway próbuje łączyć się z nieistniejącą instancją i zwraca 500 zamiast 503. Klient powinien traktować oba kody jako błąd przejściowy. Poprawą byłoby skrócenie interwału odświeżania rejestru albo dodanie filtra `Retry`/`CircuitBreaker` w Gateway.

### Logi z weryfikacji

<details>
<summary><b>docker compose ps</b> i rejestr Eureki (odpowiednik <code>eureka-up.png</code>, <code>docker-ps-pytest.png</code>)</summary>

```text
$ docker compose ps -a --format '{{.Service}}\t{{.Status}}\t{{.Ports}}'
api-gateway   Up About a minute (healthy)    127.0.0.1:8080->8080/tcp
auth          Up About a minute (healthy)    8101-8104/tcp
eureka        Up About a minute (healthy)    8761/tcp
init-keys     Exited (0) About a minute ago
inventory     Up About a minute (healthy)    8101-8104/tcp
outbound      Up About a minute (healthy)    8101-8104/tcp
rabbitmq      Up About a minute (healthy)    4369/tcp, 5671-5672/tcp, 15671-15672/tcp, 15691-15692/tcp, 25672/tcp
redis         Up About a minute (healthy)    6379/tcp
shipping      Up About a minute (healthy)    8101-8104/tcp

$ docker compose exec eureka wget -qO- --header "Accept: application/json" http://127.0.0.1:8761/eureka/apps
AUTH-SERVICE       UP  host=auth
INVENTORY-SERVICE  UP  host=inventory
OUTBOUND-SERVICE   UP  host=outbound
SHIPPING-SERVICE   UP  host=shipping

$ .venv/bin/python -m pytest -q
182 passed, 2 skipped, 1 warning in 13.53s

$ LAB6_CONTAINERS=1 GATEWAY_TOKEN=... .venv/bin/python -m pytest -o addopts= tests/integration -q
20 passed, 1 warning in 3.88s
```

Ostrzeżenie dotyczy deprecjacji aliasu w Starlette/AnyIO i nie wpływa na wynik testów.
</details>

<details>
<summary><b>Izolacja portów, walidacja, reguły biznesowe i cache</b></summary>

```text
Połączenia z hosta (127.0.0.1):
8101: odrzucony   8102: odrzucony   8103: odrzucony   8104: odrzucony
8761: odrzucony   5672: odrzucony   15672: odrzucony
GET /health (Gateway) -> 200

POST /api/receipts -> 201
POST /api/receipts/483ba94c-.../accept -> 200
POST /api/receipts/483ba94c-.../post -> 200
POST /api/receipts/483ba94c-.../post -> 409 {"detail":"Only ACCEPTED receipts can be posted"}
GET  /api/stocks/a599e3fb-... -> 200 X-Cache: MISS X-Gateway: lab4
GET  /api/stocks/a599e3fb-... -> 200 X-Cache: HIT  X-Gateway: lab4
POST /api/stocks/a599e3fb-.../reservations {"quantity":0}  -> 422 {"detail":[{"type":"greater_than","loc":["body","quantity"],...}]}
POST /api/stocks/a599e3fb-.../reservations {"quantity":11} -> 409 {"detail":"Insufficient available stock"}
GET  /api/stocks/51269b12-... (nieistniejący) -> 404 X-Gateway: lab4 {"detail":"Stock not found"}
GET  /api/nie-ma-takiej-trasy -> 404 X-Gateway: None
PATCH /api/stocks/a599e3fb-... {"quantity":14} -> 200
GET  /api/stocks/a599e3fb-... -> 200 X-Cache: MISS   (unieważnienie po zapisie)
GET  /api/stocks/a599e3fb-... -> 200 X-Cache: HIT
GET  /api/stocks (Bearer zly-token) -> 401
```
</details>

<details>
<summary><b>Awaria Redis i awaria Inventory</b></summary>

```text
$ docker compose stop redis
GET /api/stocks/a599e3fb-... -> 200 X-Cache: BYPASS
GET /api/stocks -> 200
$ docker compose start redis
GET /api/stocks/a599e3fb-... -> 200 X-Cache: MISS
GET /api/stocks/a599e3fb-... -> 200 X-Cache: HIT

GET /api/orders/stock-check/a599e3fb-... -> 200 available=14
$ docker compose stop inventory
GET /api/orders/stock-check/a599e3fb-... -> 503 {"detail":"Inventory Service is unavailable"}
GET /api/orders -> 200   (Outbound działa dalej)
GET /api/stocks co 10 s:  t=0 s 500 | t=10 s 500 | t=20 s 500 | t=30 s ... t=120 s 503
$ docker compose start inventory
stock-check ponownie 200 po 1.0 s
```
</details>

<details>
<summary><b>RabbitMQ: buforowanie, Saga i duplikat (odpowiednik <code>rabbitmq-queues.png</code>)</b></summary>

```text
POST /api/orders -> 201
$ docker compose stop inventory
POST /api/orders/c295eade-.../reserve -> 202 status=RESERVATION_PENDING
POST /api/orders/c295eade-.../pack    -> 409 {"detail":"Order must be reserved before packing"}
$ docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged durable
inventory.order-cancellations   0  0  true
outbound.reservation-results    0  0  true
inventory.reservation-requests  1  0  true
inventory.shipment-events       0  0  true
outbound.shipment-events        0  0  true
$ docker compose start inventory
POST /api/reservations/consume-events          -> 200 {"consumed_messages":1,"unique_events_processed":1}
POST /api/orders/consume-reservation-events    -> 200 {"consumed_messages":1,"unique_events_processed":1}
GET  /api/orders/c295eade-... -> 200 X-Cache: MISS status=RESERVED
POST /api/orders/c295eade-.../pack -> 200
POST /api/shipments -> 201
POST /api/shipments/8531c72d-.../hand-over -> 409 {"detail":"Only READY shipments can be handed over"}
POST /api/shipments/8531c72d-.../ready -> 200
POST /api/shipments/8531c72d-.../hand-over -> 200 status=HANDED_OVER
$ docker compose exec rabbitmq rabbitmqctl list_queues ...
inventory.shipment-events       1  0  true
outbound.shipment-events        1  0  true
GET  /api/orders/c295eade-... -> 200 status=PACKED
POST /api/orders/consume-events                 -> 200 {"consumed_messages":1,"unique_events_processed":1}
POST /api/reservations/consume-shipment-events  -> 200 {"consumed_messages":1,"unique_events_processed":1}
GET  /api/orders/c295eade-... -> 200 X-Cache: MISS status=SHIPPED
GET  /api/stocks/a599e3fb-... -> 200 X-Cache: MISS quantity=11 reservations=0

Ponowna publikacja 2 kopii tego samego zdarzenia:
{"event_id":"9a9ed832-ecd9-5a50-abc6-25c0dbcd858a","event_type":"ShipmentHandedOver",
 "shipment_id":"8531c72d-...","order_id":"c295eade-...","occurred_at":"2026-11-27T10:39:52.139586Z"}
inventory.shipment-events       2  0  true
outbound.shipment-events        2  0  true
POST /api/orders/consume-events                 -> 200 {"consumed_messages":2,"unique_events_processed":0}
POST /api/reservations/consume-shipment-events  -> 200 {"consumed_messages":2,"unique_events_processed":0}
stan po duplikatach: quantity=11 (bez ponownego odjęcia)
```
</details>

<details>
<summary><b>Trwałość danych po <code>docker compose down</code> / <code>up</code></b></summary>

```text
$ docker compose down && docker compose up -d --wait
nowe ID wszystkich kontenerów: True
GET /api/stocks/a599e3fb-...    -> 200 X-Cache: MISS quantity=11
GET /api/orders/c295eade-...    -> 200 status=SHIPPED
GET /api/shipments/8531c72d-... -> 200 status=HANDED_OVER
GET /api/receipts/483ba94c-...  -> 200 status=POSTED
GET /api/auth/me                -> 200 (ten sam JWT po odtworzeniu kontenerów)
```
</details>

### Ograniczenia systemu

- Nie ma transakcji obejmującej dwie bazy. Spójność między usługami jest ostateczna i opiera się na outboxie, ponowieniach (`/sync...`) oraz idempotentnych konsumentach.
- Odbiór zdarzeń jest ręczny (endpointy `consume-*`), zgodnie z formą laboratorium. W produkcji działałby ciągły konsument `basic_consume`.
- Cache-aside nie gwarantuje ścisłej spójności. Nieudane `DEL` może pozostawić starą kopię do końca TTL (60 s), a współbieżny odczyt może ponownie zapisać starą wartość. Decyzje biznesowe nie korzystają z cache.
- Dezaktywacja konta nie unieważnia już wydanego JWT przed upływem `exp` (maks. 900 s). Nie ma refresh tokenów ani listy unieważnień.
- SQLite na wolumenie nie pozwala skalować usług poziomo. Do tego potrzebna byłaby baza serwerowa (np. PostgreSQL).
- Anulowanie zamówienia jest możliwe tylko w stanie `RESERVED`. Obowiązuje jedna przesyłka na zamówienie, bez rejestru kurierów (sprawdzany jest tylko format `courier_id`).

---

## 8. Wykorzystana dokumentacja techniczna (EK_03)

| Technologia | Dokumentacja | Zastosowanie w projekcie |
|---|---|---|
| FastAPI | [fastapi.tiangolo.com — Tutorial, Lifespan Events, Testing](https://fastapi.tiangolo.com/tutorial/) | Routery, modele odpowiedzi, `lifespan`, zależności (`Depends`) do JWT i ról, `TestClient` |
| Pydantic | [docs.pydantic.dev — Models, Validators](https://docs.pydantic.dev/latest/concepts/models/) | Walidacja 422, `model_validator` dla deterministycznego `event_id`, Tolerant Reader (`extra="ignore"`) |
| SQLAlchemy 2.0 | [docs.sqlalchemy.org — ORM Session, Transactions](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html) | Sesje, transakcje, `BEGIN IMMEDIATE` w SQLite, klucz główny `processed_events` |
| Redis / redis-py | [redis.io — Cache-aside](https://redis.io/docs/latest/develop/use/client-side-caching/), [redis-py — Retry](https://redis.readthedocs.io/en/stable/retry.html) | `SET` z TTL, `DEL` po `COMMIT`, krótkie timeouty i brak ponowień |
| Eureka | [Spring Cloud Netflix — Eureka Server](https://docs.spring.io/spring-cloud-netflix/reference/spring-cloud-netflix.html), [py-eureka-client](https://github.com/keijack/python-eureka-client) | Serwer rejestru, rejestracja usług Python, `do_service()` po nazwie logicznej |
| Spring Cloud Gateway | [docs.spring.io — Spring Cloud Gateway Server WebFlux](https://docs.spring.io/spring-cloud-gateway/reference/spring-cloud-gateway-server-webflux.html) | Trasy `lb://`, predykat `Path`, filtr `StripPrefix`, `AddResponseHeader`, timeouty per trasa |
| RabbitMQ / Pika | [rabbitmq.com — Reliability Guide, Publisher Confirms](https://www.rabbitmq.com/docs/reliability), [Pika — BlockingConnection](https://pika.readthedocs.io/en/stable/modules/adapters/blocking.html) | Trwały exchange `topic` i kolejki, `delivery_mode=2`, `confirm_delivery`, `basic_get(auto_ack=False)`, `basic_ack`/`basic_nack` |
| Docker / Compose | [Multi-stage builds](https://docs.docker.com/build/building/multi-stage/), [Startup order](https://docs.docker.com/compose/how-tos/startup-order/), [Networking](https://docs.docker.com/compose/how-tos/networking/), [Volumes](https://docs.docker.com/engine/storage/volumes/) | Obrazy JVM Maven → JRE, `healthcheck` i `depends_on: condition`, `expose` vs `ports`, nazwane wolumeny `/data` |
| PyJWT | [pyjwt.readthedocs.io — Usage](https://pyjwt.readthedocs.io/en/stable/usage.html) | Podpis RS256, weryfikacja `iss`, `aud`, `exp`, `nbf` z ustalonym algorytmem |
| argon2-cffi | [argon2-cffi.readthedocs.io — PasswordHasher](https://argon2-cffi.readthedocs.io/en/stable/howto.html) | Hashowanie haseł Argon2id |
| pytest / HTTPX | [docs.pytest.org — Fixtures](https://docs.pytest.org/en/stable/how-to/fixtures.html), [python-httpx.org](https://www.python-httpx.org/) | Atrapy w fixture'ach, testy integracyjne przez Gateway |
| Mermaid | [mermaid.js.org — Flowchart, Sequence diagram](https://mermaid.js.org/syntax/sequenceDiagram.html) | Diagramy architektury i przepływów w README |
