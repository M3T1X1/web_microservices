# System magazynowy — Inventory, Outbound, Shipping i Auth

## LAB6 — cały system w Docker Compose

Swagger UI przez Gateway: `http://127.0.0.1:8080/docs/auth`, `/docs/inventory`, `/docs/outbound` i `/docs/shipping`. Zaloguj się w Auth, a następnie wklej sam token JWT w **Authorize** na stronie wybranej usługi. Przycisk **Execute** wywołuje API przez `/api` na porcie 8080. Ustawienie `SWAGGER_GATEWAY_ENABLED=true` w Compose konfiguruje adresy dokumentacji; tryb lokalny zachowuje `/docs`.


Aktualny sposób uruchamiania to **Docker Compose**. Cztery mikrousługi, Eureka, Redis, RabbitMQ i Gateway działają w kontenerach. Dodatkowy kontener `init-keys` wykonuje jednorazową inicjalizację kluczy i kończy pracę kodem `0`; jego stan `Exited (0)` jest prawidłowy. Dokumentacja wcześniejszych etapów poniżej zachowuje historyczne przykłady uruchamiania na hoście.

### Uruchomienie i pierwsze konto

Wymagania do uruchomienia: działający Docker Engine / Docker Desktop z Docker Compose v2. Python, Java i Maven na hoście nie są potrzebne. Budowanie wymaga dostępu do rejestru obrazów, PyPI i Maven Central. Polecenia wykonuj w głównym katalogu projektu:

```sh
docker compose up --build -d
docker compose ps -a
docker compose logs --tail=50
docker compose logs -f inventory outbound shipping auth api-gateway
```

Procesy JVM kompilują się wieloetapowo w Maven/JDK 21. Obrazy uruchomieniowe zawierają JRE 21. Python 3.13 instaluje zależności przed skopiowaniem kodu, dzięki czemu edycja aplikacji zachowuje cache warstwy instalacji. `.dockerignore` w trzech kontekstach wyklucza lokalne środowiska, bazy, klucze i artefakty kompilacji. Usługi aplikacyjne oraz JVM działają jako użytkownik `warehouse` (UID 10001), z prawem zapisu do przygotowanego `/data`.

Przy pierwszym uruchomieniu utwórz administratora (email zastąp własnym):

```sh
docker compose exec auth python scripts/init_auth.py --email admin@example.com --keys-dir /keys/private
```

Skrypt zapyta o hasło dwukrotnie; nie umieszczaj hasła w poleceniu. Bootstrap wymaga pustej tabeli użytkowników. Klucze istnieją już w wolumenach i nie są nadpisywane. Kolejne konta tworzy administrator przez `POST /api/auth/users`. Zaloguj się przez `POST http://127.0.0.1:8080/api/auth/login` z JSON `{"email":"admin@example.com","password":"<własne hasło>"}` i używaj `Authorization: Bearer <access_token>`. Token ma ważność 900 s.

```sh
curl -i http://127.0.0.1:8080/health
curl -i -H 'Authorization: Bearer <access_token>' http://127.0.0.1:8080/api/stocks
```

Po starcie kontenerów klienci Eureki potrzebują jeszcze chwili na pobranie rejestru. Stan `healthy` Gateway potwierdza jego `/health`, a nie gotowość wszystkich tras `lb://`. Początkowe 503 należy ponowić; skrypt weryfikacyjny czeka na wszystkie cztery trasy.

### Sieć, porty, zależności i dane

| Usługa | Obraz / Dockerfile | Port w sieci Compose | Port hosta | Konfiguracja i wolumeny |
|---|---|---|---|---|
| `init-keys` | `warehouse-python:lab6`, główny `Dockerfile` | — | — | Zapis do `jwt-private` i `jwt-public`; zadanie jednorazowe |
| `eureka` | `eureka-server/Dockerfile`, Maven → JRE | 8761 | — | `SERVER_ADDRESS=0.0.0.0`; healthcheck strony głównej |
| `redis` | `redis:7-alpine` | 6379 | — | Cache ulotny; wyłączone RDB i AOF; `redis-cli ping` |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 5672, 15672 | — | `rabbitmq-data:/var/lib/rabbitmq`; `rabbitmq-diagnostics ping` |
| `inventory` | główny `Dockerfile` | 8101 | — | `DATABASE_URL=sqlite:////data/inventory.db`; `inventory-data:/data` |
| `outbound` | główny `Dockerfile` | 8102 | — | `OUTBOUND_DATABASE_URL=sqlite:////data/outbound.db`; `outbound-data:/data` |
| `shipping` | główny `Dockerfile` | 8103 | — | `SHIPPING_DATABASE_URL=sqlite:////data/shipping.db`; `shipping-data:/data` |
| `auth` | główny `Dockerfile` | 8104 | — | `AUTH_DATABASE_URL=sqlite:////data/auth.db`; `auth-data:/data`; klucz prywatny tylko w Auth |
| `api-gateway` | `gateway/Dockerfile`, Maven → JRE | 8080 | `127.0.0.1:8080` | `SERVER_ADDRESS=0.0.0.0`, `GATEWAY_EUREKA_URL=http://eureka:8761/eureka/` |

Wszystkie kontenery należą do sieci mostkowej `warehouse`, której rzeczywista nazwa otrzymuje prefiks projektu Compose. Brak `ports` ukrywa mikrousługi i infrastrukturę przed klientem łączącym się z portami hosta; `expose` opisuje porty wewnętrzne, nie jest zaporą między kontenerami. Sieć nie ma `internal: true`: izolację wejścia zapewnia brak publikacji portów, a kontenery mogą wykonywać ruch wychodzący. Na Linuksie administrator hosta może mieć dostęp do adresów IP kontenerów; nie jest to izolacja od administratora.

Uvicorn nasłuchuje na `0.0.0.0`, aby przyjmować ruch z sieci kontenerowej. `127.0.0.1` wewnątrz kontenera oznacza sam kontener. Host korzysta z `127.0.0.1:8080`, natomiast usługi używają DNS Dockera:

| Zmienna | Wartość w Compose |
|---|---|
| `EUREKA_SERVER_URL` | `http://eureka:8761` |
| `INSTANCE_HOST`, `INSTANCE_IP` | Odpowiednio `inventory`, `outbound`, `shipping`, `auth` |
| `SERVICE_PORT` | Odpowiednio 8101, 8102, 8103, 8104 |
| `REDIS_URL` | `redis://redis:6379/0` |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/%2F` |
| `RABBITMQ_EXCHANGE` | `warehouse.events` |
| `CACHE_PREFIX`, `CACHE_TTL_SECONDS` | `lab6:warehouse`, 60 |
| `JWT_PUBLIC_KEY_FILE` | `/keys/public/jwt-public.pem` we wszystkich czterech usługach |
| `JWT_PRIVATE_KEY_FILE` | `/keys/private/jwt-private.pem` wyłącznie w Auth |
| `JWT_ISSUER`, `JWT_AUDIENCE` | `warehouse-auth`, `warehouse-api` |
| `JWT_TTL_SECONDS` | 900 w Auth |
| `JAVA_TOOL_OPTIONS` | `-Xms64m -Xmx256m` w Gateway i Eurece |

Compose podaje te wartości jawnie i nie wymaga `.env`. `.env.example` dotyczy starszego trybu hosta. Klucze generowane są przy pierwszym starcie; `jwt-public` montowany jest tylko do odczytu w czterech usługach, `jwt-private` tylko w Auth (oraz do zapisu w zadaniu inicjalizującym). Do obrazów nie trafiają klucze ani hasła administratora. Nie ma domyślnego hasła konta aplikacji. Konto `guest` RabbitMQ dotyczy lokalnego środowiska laboratoryjnego, którego port AMQP nie jest publikowany.

`depends_on` czeka na `service_healthy` Eureki, Redis i RabbitMQ przed uruchomieniem Inventory, Outbound i Shipping. Auth wymaga Eureki; wszystkie cztery usługi wymagają udanego zakończenia `init-keys` (`service_completed_successfully`). Gateway czeka na gotowość czterech usług i Eureki. Healthcheck usług Python wywołuje `/ready` (własna baza; dla usług domenowych także informacja o Redis). Redis może oznaczać stan `degraded` przy nadal sprawnej bazie; kod 200 pozwala wtedy dalej obsługiwać ruch. Healthcheck nie zastępuje obsługi awarii w działającej aplikacji i sam nie restartuje procesu.

```mermaid
flowchart TB
    C[Klient na hoście] -->|127.0.0.1:8080| G
    subgraph N[Sieć Docker Compose: warehouse]
        G[api-gateway :8080]
        E[eureka :8761]
        R[(redis :6379)]
        Q[rabbitmq :5672]
        I[inventory :8101]
        O[outbound :8102]
        S[shipping :8103]
        A[auth :8104]
        K[init-keys: zadanie jednorazowe]
        G -->|lb://INVENTORY-SERVICE| I
        G -->|lb://OUTBOUND-SERVICE| O
        G -->|lb://SHIPPING-SERVICE| S
        G -->|lb://AUTH-SERVICE| A
        O -->|HTTP z JWT, Eureka| I
        S -->|HTTP z JWT, Eureka| O
        S -->|ShipmentHandedOver| Q
        Q -->|consume-events, ACK, deduplikacja| O
        I --> R
        O --> R
        G -. rejestr .-> E
        I -. rejestracja .-> E
        O -. rejestracja .-> E
        S -. rejestracja .-> E
        A -. rejestracja .-> E
    end
    I --> ID[(inventory-data)]
    O --> OD[(outbound-data)]
    S --> SD[(shipping-data)]
    A --> AD[(auth-data)]
    Q --> QD[(rabbitmq-data)]
    K --> PUB[(jwt-public)]
    K --> PRIV[(jwt-private)]
    PRIV --> A
    PUB --> I
    PUB --> O
    PUB --> S
    PUB --> A
```

### Testy i sześć scenariuszy weryfikacyjnych

Testy jednostkowe można wykonać wewnątrz działającego kontenera; nie korzystają z rzeczywistych baz i brokerów:

```sh
docker compose exec inventory python -m pytest -q
```

Testy integracyjne działają na hoście i wysyłają żądania domenowe wyłącznie przez Gateway. Wymagają lokalnego Pythona 3.12+ i zależności z `requirements.txt`. W aktywnym środowisku Pythona:

```sh
python -m pip install -r requirements.txt
python scripts/verify_lab6.py
python scripts/verify_lab6.py --lifecycle
```

Skrypt pyta o email i hasło administratora; można też przekazać `LAB6_EMAIL` i `LAB6_PASSWORD` w środowisku albo gotowy `GATEWAY_TOKEN`. Dla pierwszego testu na pustej bazie Auth dostępny jest wariant:

```sh
python scripts/verify_lab6.py --bootstrap-test-admin --lifecycle
```

Tworzy on konto `lab6-admin@example.com` z losowym hasłem i zapisuje dane w lokalnym `.local/lab6-test-admin.json` z uprawnieniami `0600`. Plik nie jest kopiowany do obrazu. Kolejne uruchomienia skryptu odczytują go automatycznie. Jest to alternatywa dla ręcznego utworzenia pierwszego konta, wymaga pustej tabeli użytkowników. Nie uruchamiaj obu sposobów bootstrapu na tej samej bazie.

Samodzielne uruchomienie pytest w Bash (token uzyskaj przez logowanie):

```sh
LAB6_CONTAINERS=1 GATEWAY_TOKEN='<access_token>' python -m pytest -o addopts= tests/integration -q
```

`LAB6_CONTAINERS=1` włącza inspekcję kontenerów i wstrzyknięcie duplikatu wiadomości przez `docker compose exec -T outbound`; nie wymaga wystawiania AMQP. Potrzebny jest dostęp do lokalnego silnika Docker. Testy integracyjne tworzą dane z unikalnymi identyfikatorami i pozostawiają je w bazach testowanego systemu. `--lifecycle` dodatkowo wykonuje `down`, `up -d --wait` i restart Inventory, więc powoduje krótką przerwę w tym projekcie. Nie usuwa wolumenów ani nie ingeruje w inne projekty.

Wyniki rzeczywistego uruchomienia zapisuje `reports/lab6-verification.json` (bez haseł i tokenów). Sześć scenariuszy obejmuje:

| Scenariusz | Sprawdzenie |
|---|---|
| 1. Budowanie i start | Budowanie Python/JVM, 8 usług healthy, inicjalizacja kluczy `Exited (0)`, cztery wpisy UP z nazwami kontenerów w Eurece |
| 2. Izolacja portów | Inspekcja wszystkich mapowań: tylko `127.0.0.1:8080`; połączenia hosta z 8101–8104 odrzucane; przez Gateway 200 |
| 3. Przepływ synchroniczny | JWT, walidacja 422, konflikt 409, zapis, cache MISS → HIT, unieważnienie, Outbound → Inventory przez Eurekę, `X-Gateway: lab4` |
| 4. RabbitMQ | Wydanie przesyłki, stan PACKED przed odbiorem, dwie kopie zdarzenia → jedno przetworzenie, SHIPPED i cache MISS po odbiorze |
| 5. Odtworzenie kontenerów | Po down/up nowe ID kontenerów; zachowane stany Inventory, Outbound, Shipping i Auth oraz przyjęcie POSTED; stary JWT nadal poprawny; pierwszy odczyt cache MISS |
| 6. Restart Inventory | Ponowny odczyt przez Gateway i przez Outbound/Eurekę; pozostałe kontenery zachowują ID |

Wyniki liczbowe i obserwacje po wykonaniu testów znajdują się poniżej w podsekcji „Wynik wykonania LAB6”. Działający na hoście niezależny Redis nie jest składnikiem tego środowiska; jego port nie wpływa na dowód braku mapowań Redis w Compose.

### Wynik wykonania LAB6

Weryfikacja 24.11.2026 na Debianie: Docker Engine **29.7.0**, Compose **5.3.1**, Python **3.13** oraz JRE **21**. Oba obrazy JVM zbudowano z powodzeniem, a w obrazie końcowym Gateway potwierdzono UID 10001 i brak Mavena.

- Testy jednostkowe na hoście i wewnątrz kontenera: **129 passed, 2 skipped**. Pominięte przypadki dotyczą wyłącznie Windows.
- Testy integracyjne przez skonteneryzowany Gateway: **15 passed**.
- Osiem usług osiągnęło stan **healthy**, inicjalizacja kluczy zakończyła się **Exited (0)**.
- Inspekcja mapowań potwierdziła wyłącznie **127.0.0.1:8080**; porty domenowe hosta **8101–8104** nie przyjmowały połączeń.
- Cache, walidacja, konflikty, rezerwacja przez Eurekę i przekazywanie JWT zachowały kontrakty. Duplikat zdarzenia dał **2 odebrane wiadomości i 1 unikalne przetworzenie**.
- Po **down/up** wszystkie kontenery otrzymały nowe ID; dane czterech domen i przyjęcie POSTED pozostały dostępne. Dotychczasowy JWT nadal działał. Pierwszy odczyt stanów i zamówienia zwrócił **MISS**.
- Po restarcie **Inventory** powrócił odczyt przez Gateway oraz Outbound/Eurekę; pozostałe usługi zachowały ID kontenerów.

Szczegółowy dowód sześciu scenariuszy i identyfikatory rekordów: [reports/lab6-verification.json](reports/lab6-verification.json). Ostrzeżenie deprecjacyjne Starlette/AnyIO nie spowodowało niepowodzenia testów.

### Obsługa i diagnostyka

```sh
docker compose ps -a
docker compose logs --tail=50 inventory outbound shipping auth
docker compose logs --tail=50 eureka api-gateway rabbitmq
docker compose exec inventory python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8101/ready').read().decode())"
docker compose exec eureka wget -qO- http://127.0.0.1:8761/eureka/apps
docker compose exec rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
docker compose restart inventory
docker compose down
docker compose up -d
```

`down` usuwa kontenery i sieć, zachowując nazwane wolumeny. Restart zachowuje również warstwę zapisu kontenera, ale trwałość baz nie zależy od niej: wszystkie pliki SQLite leżą w `/data` na osobnych wolumenach. Redis celowo traci cache przy odtworzeniu. RabbitMQ zachowuje dane i kolejki na `rabbitmq-data`.

**Pełne wyczyszczenie tego projektu**, jeśli dane są już niepotrzebne:

```sh
docker compose down -v
```

Wariant `-v` usuwa wszystkie jego wolumeny: cztery bazy, wiadomości RabbitMQ i klucze JWT. Kolejny start wymaga ponownego bootstrapu konta; przy użyciu testowego konta usuń także nieaktualny `.local/lab6-test-admin.json`. Zwykłe zatrzymanie nie wymaga `-v`.

Starszy tryb lokalnych procesów nadal obsługują `scripts/setup.sh`, `scripts/setup.ps1` i `scripts/run_services.py`. Używa osobnego projektu Compose `warehouse-host` i dodatku `compose.host.yaml`, który publikuje Redis i RabbitMQ dla procesów hosta. Nie uruchamiaj jednocześnie obu wariantów z Gateway na 8080. Dawne ręczne komendy `docker compose up -d redis rabbitmq` wymagają w tym trybie pełnego prefiksu `docker compose -p warehouse-host -f compose.yaml -f compose.host.yaml`. Podstawowe `compose.yaml` zachowuje izolację LAB6.

### Źródła techniczne (EK_03)

| Oficjalna dokumentacja | Problem i zastosowanie | Weryfikacja |
|---|---|---|
| [Docker — multi-stage builds](https://docs.docker.com/build/building/multi-stage/) | Oddzielenie Maven/JDK od JRE; `FROM ... AS build`, `COPY --from=build` w obu komponentach JVM | Poprawna kompilacja i uruchomienie; w obrazie końcowym brak Mavena |
| [Docker — startup order](https://docs.docker.com/compose/how-tos/startup-order/) | Wyścig przy starcie; `healthcheck`, `service_healthy`, `service_completed_successfully` | Pierwszy start, odtworzenie całego środowiska i kontrola zdrowia |
| [Docker — networking in Compose](https://docs.docker.com/compose/how-tos/networking/) | Adresacja między kontenerami; DNS usług, `ports` tylko w Gateway | Wpisy Eureki, wywołania HTTP między usługami i inspekcja mapowań |
| [Docker — volumes](https://docs.docker.com/engine/storage/volumes/) | Stan poza cyklem życia kontenera; osobne nazwane wolumeny i `/data` | Odtworzenie wszystkich kontenerów bez utraty danych i kluczy |

Zachowano logikę domenową i kontrakty LAB1–LAB5. Inventory zarządza stanami i przyjęciami, Outbound zamówieniami i deduplikacją zdarzeń, Shipping przesyłkami, a Auth kontami. Każda usługa otwiera wyłącznie własną bazę. Wspólny kod Pythona upraszcza laboratorium, ale wiąże cykl wydania usług; wspólna baza ani bezpośrednie odczyty cudzych tabel nie są używane. Synchroniczne sprawdzanie i rezerwacja tworzą zależności dostępności, natomiast zdarzenie RabbitMQ oddziela wydanie przesyłki od późniejszej zmiany zamówienia. Nie jest to pełny mechanizm kompensacji wszystkich etapów procesu.

Wersjonowanie API można wprowadzić przez nowe prefiksy i równoległe trasy Gateway; wzorzec Strangler Fig pozwoliłby stopniowo przenosić wybrane ścieżki ze starego systemu do nowych usług. W Kubernetes obrazy odpowiadałyby Deploymentom/Podom, komunikacja wewnętrzna Services, wejście Gateway API/Ingress, konfiguracja ConfigMap/Secret, dane PVC, a kontrole zdrowia readiness/liveness probes. SQLite i wspólne wolumeny nie zapewniają tu gotowego skalowania poziomego. Proponowany potok wdrożenia: testy jednostkowe → budowanie obrazów → testy integracyjne Compose → publikacja obrazów → wdrożenie. Orkiestracji Kubernetes i takiego potoku nie wdrażano w LAB6.


## Aktualny stan — cztery mikrousługi

Zaimplementowane są **Inventory, Outbound, Shipping i Auth**. Każda usługa działa w osobnym kontenerze i posiada własną bazę SQLite. Inbound (przyjęcia dostaw) pozostaje modułem Inventory. Wszystkie usługi rejestrują się w Eurece, a Gateway udostępnia ich API pod `/api/...`.

| Usługa | Plik startowy | Port | Baza / tabele | Odpowiedzialność |
|---|---|---|---|---|
| Inventory | `app/inventory.py` | 8101 | `inventory.db`: stocks, receipts, reservation_batches | Stany, przyjęcia, rezerwacje |
| Outbound | `app/outbound.py` | 8102 | `outbound.db`: orders, order_dispatches, processed_events | Zamówienia, rezerwowanie przez Inventory, pakowanie i potwierdzanie wysyłki |
| Shipping | `app/shipping.py` | 8103 | `shipping.db`: shipments | Przesyłki, przygotowanie, wydanie kurierowi i zdarzenie dla Outbound przez RabbitMQ |
| Auth | `app/auth.py` | 8104 | `auth.db`: users | Konta, Argon2id, logowanie, JWT RS256 i role |

Gateway jest publikowany na 127.0.0.1:8080. Eureka :8761, Redis :6379 i RabbitMQ :5672 (panel :15672) są dostępne w sieci Compose. Auth nie korzysta z Redis. Shipping zapisuje i czyta przesyłki bez cache; wspólna fabryka inicjalizuje adapter Redis i uwzględnia go w `/ready`, lecz jego awaria nie blokuje operacji na przesyłkach. Wszystkie usługi mają dostęp do wspólnego adaptera RabbitMQ; obecnie używa go tylko zdarzenie Shipping → Outbound. Szczegóły w [LAB5](#lab5).

```mermaid
flowchart TB
    C[Klient + JWT] --> G[Gateway :8080]
    G --> I[Inventory :8101]
    G --> O[Outbound :8102]
    G --> S[Shipping :8103]
    G --> A[Auth :8104]
    O -->|HTTP z JWT: rezerwacja i odczyt| I
    S -->|HTTP z JWT: sprawdzenie PACKED| O
    S -->|ShipmentHandedOver| B[RabbitMQ :5672]
    B -->|ręczny odbiór w Outbound| O
    I --> ID[(inventory.db)]
    O --> OD[(outbound.db)]
    S --> SD[(shipping.db)]
    A --> AD[(auth.db)]
    I --> R[(Redis)]
    O --> R
    G -. pobieranie rejestru .-> E[Eureka :8761]
    I -. rejestracja .-> E
    O -. rejestracja i rejestr .-> E
    S -. rejestracja i rejestr .-> E
    A -. rejestracja .-> E
```

### Konta, JWT i role

Auth przechowuje wyłącznie hashe Argon2id. JWT zawiera identyfikator użytkownika, rolę, wystawcę, odbiorcę, czasy ważności i identyfikator tokenu; nie zawiera hasła. Tylko Auth podpisuje token kluczem prywatnym RSA. Inventory, Outbound i Shipping znają klucz publiczny i sprawdzają podpis RS256, issuer, audience oraz exp/iat/nbf. Algorytm jest ustalony w kodzie, nie pobierany z niezaufanego nagłówka tokenu. Domyślny czas ważności to 900 s, konfigurowalny od 1 do 3600 s.

| Rola | Uprawnienia |
|---|---|
| ADMIN | Wszystkie operacje, tworzenie kont i aktywowanie/dezaktywowanie użytkowników |
| WAREHOUSE | Inventory i zwykłe operacje Outbound: zamówienia, rezerwowanie, pakowanie |
| SHIPPING | Shipping oraz endpointy Outbound `/orders/{id}/shipping-check`, `/orders/{id}/dispatch` i `/orders/consume-events` |

Gateway zachowuje domyślny timeout odpowiedzi 5 s, a trasa Shipping ma `metadata.response-timeout: 10000` (10 s). Wydanie kurierowi sprawdza PACKED przez HTTP z timeoutem 3 s, zapisuje wydanie i publikuje do RabbitMQ; nie czeka na przetworzenie zdarzenia przez Outbound. Testy HTTP mają limit 15 s.

Brak lub błędny token daje 401; niewłaściwa rola 403. Kontrola działa również na bezpośrednich portach usług. `/health`, `/ready` i dokumentacja pozostają technicznie dostępne lokalnie. Auth sprawdza aktywność konta przy logowaniu i `/auth/me`, a także aktualną rolę administratora przy zarządzaniu kontami. Pozostałe usługi weryfikują JWT lokalnie: dezaktywowanie konta nie unieważnia natychmiast jego już wydanego tokenu, który może działać do `exp`. Zatrzymanie Auth nie blokuje operacji z ważnym tokenem. Nie dodano refresh tokenów, centralnej listy unieważnień ani limitowania prób logowania.

Kontrola JWT jest domyślnie włączona we wspólnej fabryce, również w trybie `both`. `app.main` udostępnia teraz wyłącznie funkcję `create_app()`, bez obiektu `app` do uruchamiania przez Uvicorn. Jawne `auth_enabled=False` w testach izoluje historyczne scenariusze biznesowe od uwierzytelniania; oddzielne testy sprawdzają zabezpieczenia z prawdziwymi podpisami. Nie ma zmiennej środowiskowej wyłączającej ochronę. Auth zawsze wymaga uwierzytelniania swoich chronionych operacji. Brak klucza publicznego przy włączonej ochronie przerywa start usługi; nie ma automatycznego przejścia w tryb bez ochrony.

### Przygotowanie i uruchomienie wszystkich usług — starszy tryb hosta

Wymagania: Python 3.12+, JDK 21, Maven 3.9.x oraz działający Docker z Compose (na Windows Docker Desktop). Redis i RabbitMQ działają w kontenerach; Eureka i Gateway lokalnie przez Maven. `mvn -version` powinno wskazywać Javę 21 lub nowszą. Po instalacji narzędzi uruchom ponownie terminal i IDE.

Jeśli nie masz `.env`, skopiuj `.env.example` do `.env`. Nie kopiuj `.venv` z innego komputera.

#### Start skryptem

Windows, z głównego katalogu projektu:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1
# Jedna usługa; infrastruktura uruchomiona osobno:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Service inventory
# Inny port wybranej usługi:
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Service outbound -Port 8120
```

Debian/Linux:

```bash
bash scripts/setup.sh
bash scripts/setup.sh --service inventory
bash scripts/setup.sh --service outbound --port 8120
```

Bez wyboru usługi skrypt przygotowuje `.venv`, instaluje zależności i uruchamia Redis, RabbitMQ, Eurekę, Auth, Inventory, Outbound, Shipping oraz Gateway. Przy pierwszym starcie pyta o email i hasło administratora. `-Service` / `--service` wybiera `inventory`, `outbound`, `shipping` lub `auth`; port jest ustawiany również w konfiguracji Eureki. `-Port` / `--port` wymaga wybrania jednej usługi.

`-SetupOnly` / `--setup-only` tylko przygotowuje środowisko Pythona; `-Test` / `--test` dodatkowo uruchamia testy. Wspólna logika jest w `scripts/run_services.py`. Skrypt wczytuje `.env` (zmienne terminala mają pierwszeństwo), czeka na gotowość procesów i zapisuje logi do `.local/logs/`. `Ctrl+C` zatrzymuje uruchomione procesy oraz te kontenery Redis/RabbitMQ, które nie działały przed startem skryptu. Bazy, klucze i wolumen RabbitMQ pozostają zachowane.

#### Start ręczny

Na Windows jednorazowo przygotuj środowisko; w Linuxie użyj `python3` i `.venv/bin/python`:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
# Tylko przy pierwszym przygotowaniu pustej bazy Auth; podaj własny email:
.\.venv\Scripts\python.exe scripts/init_auth.py --email admin@example.com
```

`init_auth.py` pyta dwukrotnie o hasło (12–128 znaków), tworzy klucze RSA w `.local/` i administratora w `auth.db`. Nie ma domyślnego hasła; istniejące klucze nie są nadpisywane, a kolejne konta tworzy administrator przez API. Przy zmianie `AUTH_DATABASE_URL` przekaż ten sam adres do `init_auth.py` przez `--database-url`. Nie usuwaj baz ani kluczy przy zwykłym restarcie.

Uruchom kolejno z głównego katalogu projektu; każdy serwer pierwszoplanowy w osobnym terminalu:

```powershell
# 1. Redis i RabbitMQ w Dockerze
docker compose up -d redis rabbitmq

# 2. Eureka
mvn -f eureka-server/pom.xml spring-boot:run

# 3. Auth
.\.venv\Scripts\python.exe -m uvicorn app.auth:app --port 8104 --env-file .env

# 4. Inventory
.\.venv\Scripts\python.exe -m uvicorn app.inventory:app --port 8101 --env-file .env

# 5. Outbound
.\.venv\Scripts\python.exe -m uvicorn app.outbound:app --port 8102 --env-file .env

# 6. Shipping
.\.venv\Scripts\python.exe -m uvicorn app.shipping:app --port 8103 --env-file .env

# 7. Gateway
mvn -f gateway/pom.xml spring-boot:run
```

W Linuxie zamień `.\.venv\Scripts\python.exe` na `.venv/bin/python`. Maven buduje aplikacje przy `spring-boot:run`; nie trzeba obrazów Docker dla Eureki ani Gateway.

Przy ręcznym starcie na domyślnych portach `SERVICE_PORT` może pozostać nieustawione. Usuń jego starą wartość z terminala, jeżeli została po wcześniejszym uruchomieniu. Przy innym `--port` ustaw zgodny `SERVICE_PORT` w terminalu tej konkretnej usługi. Skrypty robią to automatycznie.

| Zmienna | Domyślnie |
|---|---|
| `DATABASE_URL` / `OUTBOUND_DATABASE_URL` | `sqlite:///./inventory.db` / `sqlite:///./outbound.db` |
| `SHIPPING_DATABASE_URL` / `AUTH_DATABASE_URL` | `sqlite:///./shipping.db` / `sqlite:///./auth.db` |
| `JWT_PRIVATE_KEY_FILE` / `JWT_PUBLIC_KEY_FILE` | `.local/jwt-private.pem` (tylko Auth) / `.local/jwt-public.pem` |
| `JWT_ISSUER` / `JWT_AUDIENCE` / `JWT_TTL_SECONDS` | `warehouse-auth` / `warehouse-api` / 900 |
| `EUREKA_SERVER_URL` | `http://127.0.0.1:8761` |
| `INSTANCE_IP` / `INSTANCE_HOST` | `127.0.0.1` / `localhost`, adres instancji w rejestrze |
| `SERVICE_PORT` | 8101 / 8102 / 8103 / 8104, zależnie od usługi |
| `EUREKA_PORT` / `GATEWAY_PORT` | 8761 / 8080 |
| `GATEWAY_EUREKA_URL` | `http://127.0.0.1:8761/eureka/` |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` |
| `CACHE_PREFIX` / `CACHE_TTL_SECONDS` | `lab2:local:warehouse` / 60 |
| `RABBITMQ_URL` | `amqp://guest:guest@127.0.0.1:5672/%2F` |
| `RABBITMQ_EXCHANGE` | `warehouse.events` |

`.env.example` jest wzorem. `.env` wczytują skrypty oraz ręczny Uvicorn z `--env-file .env`. Przy ręcznym starcie aplikacji Java ustaw zmienione wartości Eureki/Gateway w ich terminalach — Maven nie wczytuje `.env` automatycznie. Zmiana `INSTANCE_IP` / `INSTANCE_HOST` nie zmienia adresu nasłuchu Uvicorna. Pliki SQLite pozostają w katalogu roboczym; każdy proces tworzy tylko własne brakujące tabele.

Poczekaj na cztery wpisy UP w Eurece i odświeżenie rejestrów klientów. Pierwsze wywołania mogą zwracać 503. Dokumentacje są pod `/docs` na portach 8101–8104. W Auth wykonaj `/auth/login`, a otrzymany `access_token` wklej w **Authorize** w dokumentacji wybranej usługi.

Przy starcie ręcznym zatrzymuj serwery przez `Ctrl+C` w ich terminalach, a Redis i RabbitMQ przez `docker compose stop redis rabbitmq`. Zachowaj bazy, klucze i wolumen brokera.

### Endpointy nowych usług i przepływ realizacji zamówienia

Poniżej ścieżki widziane przez klienta Gateway (:8080); `StripPrefix=1` usuwa `/api`. Bezpośrednie porty usług używają ścieżek bez `/api`.

| Metoda i ścieżka | Działanie | Rola |
|---|---|---|
| POST `/api/auth/login` | Email i hasło → access_token, token_type | Publiczne logowanie |
| GET `/api/auth/me` | Profil aktywnego użytkownika, bez hasha | Każda zalogowana rola |
| POST `/api/auth/users` | Konto: email, password, role; sukces 201 | ADMIN |
| PATCH `/api/auth/users/{id}` | `{"is_active":false}` lub true; ostatni aktywny administrator nie może zostać wyłączony | ADMIN |
| POST `/api/orders/{id}/reserve` | Rezerwacja wszystkich pozycji przez Inventory i status RESERVED | WAREHOUSE, ADMIN |
| POST `/api/orders/{id}/pack` | RESERVED → PACKED | WAREHOUSE, ADMIN |
| POST `/api/orders/consume-events` | Ręczny odbiór zdarzeń z RabbitMQ; domyślnie maksymalnie 50 | SHIPPING, ADMIN |
| POST `/api/shipments` | order_id, carrier, weight_kg; nowe NEW, sukces 201 | SHIPPING, ADMIN |
| GET `/api/shipments`, GET `/api/shipments/{id}` | Lista i szczegóły przesyłek | SHIPPING, ADMIN |
| POST `/api/shipments/{id}/ready` | NEW → READY; sprawdza PACKED w Outbound | SHIPPING, ADMIN |
| POST `/api/shipments/{id}/hand-over` | READY → HANDED_OVER, czas UTC i publikacja zdarzenia do RabbitMQ | SHIPPING, ADMIN |
| POST `/api/shipments/{id}/cancel` | NEW/READY → CANCELLED | SHIPPING, ADMIN |
| POST `/api/shipments/{id}/sync` | Ponowienie oczekującej publikacji zdarzenia o wysyłce | SHIPPING, ADMIN |

Shipping rejestruje przesyłkę wyłącznie dla istniejącego zamówienia PACKED i ponownie sprawdza stan przed przygotowaniem oraz wydaniem. Outbound udostępnia Shipping bezpośredni odczyt bazy (`/orders/{id}/shipping-check`), aby decyzja nie opierała się na potencjalnie starej kopii Redis. Po wydaniu Shipping publikuje `ShipmentHandedOver`. Dopiero ręczny odbiór w Outbound zmienia PACKED → SHIPPED, zapisuje identyfikator przesyłki i zdarzenia oraz usuwa cache zamówienia. Duplikaty są rozpoznawane po event_id. Dotychczasowe `POST /orders/{id}/dispatch` pozostaje zgodne z wcześniejszym API: ten sam shipment_id jest idempotentny, inny daje 409. Odczyt Shipping, dispatch i odbiór zdarzeń wymagają SHIPPING/ADMIN.

Przykłady JSON:

```json
{"email":"pracownik@example.com","password":"haslo-co-najmniej-12-znakow","role":"WAREHOUSE"}
```

```json
{"order_id":"UUID-spakowanego-zamowienia","carrier":"Firma kurierska","weight_kg":2.5}
```

Każde żądanie domenowe wymaga `Authorization: Bearer <access_token>`. Gateway przekazuje ten nagłówek, a wywołania HTTP Outbound → Inventory i Shipping → Outbound zachowują token użytkownika oraz `Content-Type: application/json` przy wysyłaniu JSON. Zdarzenie RabbitMQ nie zawiera tokenu; operacja uruchamiająca odbiór wymaga JWT. Bramka nie sprawdza reguł biznesowych i nie łączy się z bazami.

Przykładowa kolejność przez Gateway: zaloguj administratora, utwórz pracownika WAREHOUSE i SHIPPING, utwórz zamówienie, wykonaj `/reserve`, `/pack`, utwórz przesyłkę, wykonaj `/ready` i `/hand-over`, następnie `/orders/consume-events`. Dopiero po odbiorze zamówienie ma SHIPPED; przesyłka ma HANDED_OVER już po wydaniu kurierowi.

Rezerwacja zbiorcza Inventory (`POST /reservations`, tylko port Inventory) obejmuje wszystkie SKU i lokalizacje w jednej transakcji. Brak towaru daje 409 i rollback całej operacji. Zapisuje żądanie według order_id, więc utracona odpowiedź lub ponowienie po błędzie zapisu Outbound nie rezerwuje towaru drugi raz. Zmienione pozycje pod tym samym order_id dają 409. Outbound nadaje RESERVED dopiero po poprawnym potwierdzeniu. Nie dodano zewnętrznej trasy `/api/reservations`; klient korzysta z operacji na zamówieniu.

Wydanie kurierowi i wpis `notification_pending=true` są jedną transakcją Shipping. Potem usługa publikuje zdarzenie. Awaria RabbitMQ daje 503, ale przesyłka pozostaje HANDED_OVER z oczekującym powiadomieniem. Po przywróceniu brokera wywołaj `/sync` z ważnym tokenem: ponowi publikację z tym samym event_id. Potwierdzenie RabbitMQ ustawia false; oznacza przyjęcie wiadomości przez broker, a nie jej przetworzenie przez Outbound. Publikacja i odbiór nie mają automatycznego procesu w tle; stan oczekujący przetrwa restart.

Ograniczenia: jedna przesyłka na zamówienie, także po jej anulowaniu; brak etykiet i numerów śledzenia. Rezerwacje po wysyłce pozostają zapisane w Inventory — nie dodano fizycznego rozchodu towaru ani zwalniania rezerwacji. Nie ma transakcji obejmującej dwie bazy; dlatego jawnie obsługujemy ponowienia i stan oczekującego powiadomienia. Nie dodano paczkomatów ani integracji z przewoźnikiem.

### Testy nowych usług

Weryfikacja z 22.11.2026: **131 testów jednostkowych i komponentowych oraz 12 integracyjnych przeszło**. Pełny zestaw działał z Redis i RabbitMQ w Dockerze oraz Eureką i Gateway na JDK 21/Maven. Sprawdzono publikację, odbiór, cache, duplikaty, awarię brokera i powrót wyłączonego Outbound. Testy wykonano na tymczasowych bazach, kluczach, prefiksie Redis i vhoście RabbitMQ, po czym zatrzymano własne procesy. Szczegóły i [raport](reports/lab5-verification.json) w [LAB5](#lab5).

```bash
# Szybkie testy: SQLite, podpisy RSA, atrapy Redis, RabbitMQ i discovery
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check

# Prawdziwy system już działa; GATEWAY_TOKEN to token administratora
export GATEWAY_TOKEN='<access_token>'
.venv/bin/python -m pytest -o addopts= tests/integration -q

```

W PowerShell token ustaw przez `$env:GATEWAY_TOKEN = '<access_token>'` i użyj `.\.venv\Scripts\python.exe`. Integracje wymagają czterech usług, Gateway, Eureki, Redis i RabbitMQ oraz tych samych zmiennych brokera co aplikacje. Uruchamiaj je na odrębnych bazach, prefiksie Redis i vhoście RabbitMQ: tworzą dane, a odbiór przetwarza całą powiązaną kolejkę. `pytest.ini` pomija `tests/integration` podczas zwykłego uruchomienia; `-o addopts=` umożliwia ich osobne wykonanie.

Testy obejmują hashe, role i JWT, atomowość rezerwacji, przejścia statusów przesyłki oraz awarie zależności. LAB5 dodaje publikację po zapisie, ręczny odbiór, duplikaty, ACK/NACK, utratę potwierdzenia publikacji i naprawę cache po ponowieniu. Integracje obejmują wcześniejsze scenariusze bramki/Auth/wysyłki oraz rzeczywisty duplikat RabbitMQ i unieważnienie Redis.

Oficjalne źródła: [PyJWT — podpisy RSA i walidacja claims](https://pyjwt.readthedocs.io/en/stable/usage.html), użyte PyJWT 2.15.1; [argon2-cffi — PasswordHasher](https://argon2-cffi.readthedocs.io/en/stable/howto.html), użyte 25.1.0. Ich zastosowanie sprawdzono testami błędnych tokenów i logowania oraz rzeczywistym wywołaniem HTTP między usługami.

## Dokumentacja historycznych etapów

Sekcje LAB1/LAB2 i LAB4 zachowują opis wcześniejszych etapów. Określenia „planowane” i przykłady bez JWT dotyczą tamtego stanu projektu. Sekcja LAB3 opisuje discovery, a [LAB5](#lab5) obecne zdarzenie RabbitMQ. Do uruchamiania aktualnego kodu stosuj instrukcję na początku README.

## Uruchomienie i testy — historyczny tryb LAB1/LAB2

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

## 7. Ograniczenia

Dane są trwałe w SQLite. Przyjęcia należą do Inventory; brakuje jeszcze automatycznej integracji Inventory–Outbound, zwalniania rezerwacji i wysyłki. Shipping i Auth są planowane. Obecnie nie ma kont, logowania, wydawania JWT ani kontroli ról w endpointach. Redis może zwracać starą kopię do wygaśnięcia TTL.


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
| `README-LAB2-template.md` | Podstawa struktury niniejszego uzupełnienia: decyzje, uruchomienie, dowody, porównanie, źródła |

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
export CACHE_PREFIX='lab2:local:warehouse'
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
$env:CACHE_PREFIX = 'lab2:local:warehouse'
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

| Odczyt | Klucz dla prefiksu `lab2:local:warehouse` | TTL | Unieważnienie |
|---|---|---|---|
| `GET /stocks/{id}` | `lab2:local:warehouse:inventory:stock:<UUID>` | 60 s | Udana rezerwacja, korekta ilości i księgowanie przyjęcia |
| `GET /orders/{id}` | `lab2:local:warehouse:outbound:order:<UUID>` | 60 s | Udane pakowanie |

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

Do samodzielnej prezentacji użyj `/docs`, aby odczytać UUID, wykonać rezerwację i korektę. Nagłówki można obejrzeć przez `curl -i http://127.0.0.1:8101/stocks/<UUID>` (Windows: `curl.exe -i ...`). TTL własnego klucza: `docker compose exec redis redis-cli TTL 'lab2:local:warehouse:inventory:stock:<UUID>'`. Używaj rzeczywistego UUID i prefiksu. Nie wykonuj `FLUSHALL` ani `FLUSHDB`.

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

### Dokumentacja — LAB2

| Źródło | Problem | Zastosowanie | Weryfikacja |
|---|---|---|---|
| [Oficjalny ZIP LAB2](https://pawoom.lazysolutions.pl/2026_2027/lab2/lab2-support.zip) | Włączenie persystencji i opcjonalnego cache do własnego LAB1 | Oryginalne adaptery, wzorzec repozytorium i cache-aside, atrapa Redis, układ dokumentacji | Porównanie plików bajt po bajcie z archiwum, hashe w raporcie, własne testy API i HTTP |
| [SQLAlchemy 2.0 — sesje i transakcje](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block) | Zatwierdzenie albo wycofanie całej operacji | Osobna sesja na operację, `sessions.begin()`, zamykanie sesji | Testy błędu po flush: brak częściowego UPDATE lub INSERT, kolejny zapis działa |
| [redis-py 6.4 — połączenia](https://redis.readthedocs.io/en/v6.4.0/connections.html#redis.Redis.from_url) | URL, timeouty, ponowienia i opcjonalność cache | `make_redis()` z ZIP: `Redis.from_url`, tekst, timeouty 0,3 s i brak ponowień | Awaria GET/SET/DELETE w testach oraz zatrzymanie i przywrócenie rzeczywistego serwera |

Dokumentację oficjalnych bibliotek przeczytano i porównano z kodem. Nie instalowano zewnętrznych skilli SQLAlchemy. Testy wstrzykują własnego klienta cache i adresy baz przez parametry `create_app()`.

## 11. LAB3 — rejestracja i odnajdywanie usług

Zakres według [instrukcji LAB3](https://pawoom.lazysolutions.pl/2026_2027/lab3/) i [szablonu dokumentacji](https://pawoom.lazysolutions.pl/2026_2027/lab3/lab3-support/demo/README-LAB3-template.html). Minimum etapu stanowią Inventory i Outbound. Opis odpowiada obecnemu kodowi; Shipping, Auth i Gateway są rozszerzeniami późniejszych etapów.

### Usługi i granice odpowiedzialności

| Usługa | Odpowiedzialność | Własne dane | Nazwa w Eurece | Port |
|---|---|---|---|---|
| Inventory | Stany, przyjęcia i rezerwacje | `inventory.db`: `stocks`, `receipts`, `reservation_batches` | `INVENTORY-SERVICE` | 8101 |
| Outbound | Zamówienia, rezerwowanie przez Inventory i pakowanie | `outbound.db`: `orders`, `order_dispatches`, `processed_events` (LAB5) | `OUTBOUND-SERVICE` | 8102 |

Każda usługa działa w osobnym procesie i otwiera wyłącznie swoją bazę. Outbound korzysta z API Inventory. Zachowano transakcje SQLite, cache-aside Redis, TTL i unieważnianie cache po zapisie z LAB2. Inbound pozostaje modułem Inventory. Aktualnie rejestrują się również `SHIPPING-SERVICE` (8103) i `AUTH-SERVICE` (8104).

### Konfiguracja i uruchomienie

Sprawdzone wersje: Python **3.13.9**, FastAPI **0.162.0**, `py-eureka-client` **0.13.3**, JDK **21.0.13.1**, Maven **3.9.16**, Spring Boot **4.1.1** i Spring Cloud **2025.1.3**. Komendy Windows/Linux są w [aktualnej instrukcji startu](#przygotowanie-i-uruchomienie-wszystkich-usług).

| Ustawienie | Wartość / znaczenie |
|---|---|
| `EUREKA_SERVER_URL` | `http://127.0.0.1:8761`, adres bazowy bez `/eureka` |
| `eureka_context` w `app/discovery.py` | `/eureka`, endpointy serwera Spring Cloud |
| `INSTANCE_IP`, `INSTANCE_HOST` | Domyślnie `127.0.0.1`, `localhost`; adresy instancji w rejestrze |
| `SERVICE_PORT` | Port danego procesu, zgodny z `uvicorn --port` |
| `home_page_url` / `status_page_url` / `health_check_url` | `/docs` / `/ready` / `/health` |

Eureka działa lokalnie przez `mvn -f eureka-server/pom.xml spring-boot:run`; Docker Compose uruchamia wyłącznie Redis. Serwer Eureki sam nie rejestruje się ani nie pobiera rejestru. Adapter Python jest w `app/discovery.py`. Wszystkie usługi rejestrują instancje; Outbound i Shipping dodatkowo pobierają lokalną kopię rejestru.

`lifespan` najpierw przygotowuje własną bazę i Redis, potem wykonuje `await client.start()`. Przy zamknięciu `finally` wykonuje `await client.stop()`, po czym zamykane są zasoby bazy i cache. Klient odpowiada za rejestrację, heartbeat i wyrejestrowanie. W testach jest zastępowany atrapą. `app.main` zawiera wspólną fabrykę; `both` pozostaje dla wcześniejszych testów, a zwykły start używa osobnych modułów usług.

### Kontrakt komunikacji między usługami

Outbound udostępnia `GET /orders/stock-check/{stock_id}`. Przyjmuje UUID i przez `do_service("INVENTORY-SERVICE", "/stocks/{id}", return_type="json", prefer_ip=True, timeout=3)` wywołuje Inventory. Zwraca obiekt `stock` i `available_quantity`, czyli ilość pomniejszoną o rezerwacje. Jest to odczyt dostępności; rezerwację wykonuje osobna operacja zamówienia.

Aktualne API wymaga JWT z rolą WAREHOUSE lub ADMIN. Outbound przekazuje token do Inventory. Adres wybiera po nazwie logicznej z lokalnej kopii rejestru; żądanie biznesowe biegnie bezpośrednio między usługami.

```mermaid
flowchart LR
    O[Outbound :8102] -->|HTTP GET /stocks/id| I[Inventory :8101]
    I -. rejestracja i heartbeat .-> E[Eureka :8761]
    O -. rejestracja, heartbeat i pobieranie rejestru .-> E
```

| Kod | Znaczenie |
|---|---|
| 200 | Odczyt Inventory i obliczona dostępność |
| 404 | Inventory nie znalazł stanu o podanym UUID |
| 422 | Niepoprawny UUID; brak wywołania Inventory |
| 502 | Nieoczekiwany błąd HTTP zależności lub niepoprawne dane odpowiedzi |
| 503 | Brak instancji, błąd połączenia lub timeout (3 s) |
| 401 / 403 | Brak poprawnego tokenu / roli przy wejściu do endpointu Outbound |

### Wyniki weryfikacji

Weryfikacja warstwy LAB3 z 22.11.2026, przed dodaniem LAB5: **120 testów automatycznych**, również wcześniejsze scenariusze LAB1/LAB2, oraz **11 integracyjnych** na rzeczywistym systemie. Osobno wykonano scenariusze LAB3 na tymczasowych bazach, kluczach i prefiksie cache:

| Scenariusz | Stan początkowy i żądanie | Oczekiwany wynik | Wynik rzeczywisty | Przywrócenie stanu |
|---|---|---|---|---|
| Rejestracja | Eureka, Inventory i Outbound uruchomione osobno | Obie instancje UP | Dwa wpisy UP pod właściwymi nazwami i portami | Zatrzymanie własnych procesów |
| Odnajdywanie | Znany UUID, GET Outbound `stock-check` | 200 i dane Inventory | 200, właściwy UUID, `available_quantity=10` | Brak zmiany danych |
| Brak zasobu | GET `stock-check` z losowym UUID | 404 | 404, `Stock not found in Inventory` | Brak zmiany danych |
| Awaria zależności | Zatrzymanie Inventory, ponowny GET | 503, health Outbound 200 | 503, `Inventory Service is unavailable`; health 200 | Ponowny start Inventory |
| Restart zależności | Start Inventory na tej samej bazie i porcie | Ponownie 200 | 200 po 2,05 s, ten sam UUID, bez restartu Outbound | Zatrzymanie procesów po sprawdzeniu |
| Awaria rejestru | Zatrzymanie Eureki po pobraniu rejestru | Odczyt może użyć zapisanych adresów | GET 200; health obu usług 200 | Kolejny start zaczyna się od Eureki |
| Testy z atrapą | `pytest` bez działającej Eureki | Kontrolowane błędy i poprawny cykl klienta | 120 passed; m.in. 404, 422, 502, 503 i start/stop | Automatyczne zamknięcie zasobów |

Własne testy LAB3 w `tests/test_discovery.py`: `test_stock_check_uses_logical_name_and_three_second_timeout`, `test_dependency_errors_are_controlled_and_health_stays_up`, `test_malformed_dependency_response_is_bad_gateway`, `test_invalid_uuid_does_not_call_inventory` i `test_separate_process_apps_only_initialize_owned_data`. Dodatkowe testy sprawdzają konfigurację adresów instancji i stron.

Powtórzenie ręczne: uruchom system, zaloguj się w Auth, pobierz UUID z `GET :8101/stocks` i wykonaj `GET :8102/orders/stock-check/{UUID}` z `Authorization: Bearer <token>`. Do niezależnego zatrzymywania usług użyj osobnych terminali; wspólny skrypt przy awarii procesu zatrzymuje cały zestaw. Po teście zatrzymaj własne procesy i `docker compose stop redis`. Restart zachowuje bazy. Czyste dane uzyskuj przez osobne adresy baz i nowy `CACHE_PREFIX`.

### Ograniczenia i wykorzystana dokumentacja

UP potwierdza rejestrację/heartbeat, `/health` dostępność procesu, a odpowiedź domenowa działanie konkretnego przepływu. Jawne adresy health/ready są informacją w rejestrze; nie włączają ich odpytywania przez Eurekę. Lokalne kopie rejestru i dzierżawy aktualizują się asynchronicznie. Po nagłej awarii wpis może pozostać widoczny, również przez self-preservation. Wynik po zatrzymaniu Eureki dotyczy klienta z już pobranym adresem. Pomiar restartu nie gwarantuje czasu odzyskania ani odnalezienia instancji po zmianie portu. Brak klastra rejestru.

| Źródło | Problem i zastosowanie | Sprawdzenie |
|---|---|---|
| [py-eureka-client 0.13.3](https://pypi.org/project/py-eureka-client/0.13.3/), [API biblioteki](https://github.com/keijack/python-eureka-client) | Klient asynchroniczny, konfiguracja instancji i `do_service` | Atrapy oraz rzeczywisty odczyt Outbound → Inventory |
| [Netflix — Eureka REST operations](https://github.com/Netflix/eureka/wiki/Eureka-REST-operations) | Rejestracja, heartbeat, pobieranie i usunięcie wpisu; Spring używa `/eureka/*` zamiast pokazanego w specyfikacji `/eureka/v2` | Wpisy UP i zatrzymanie usług/rejestru |
| [FastAPI — lifespan](https://fastapi.tiangolo.com/advanced/events/) | Start i zamknięcie klienta w cyklu aplikacji | Test start/stop i zamknięcie rzeczywistych procesów |


## Etap LAB4 — bramka API i dwie niezależne usługi (opis historyczny)

### Ciągłość projektu i zakres

Etap LAB4 dodał Gateway do Inventory i Outbound oraz wykorzystywał rejestrację i komunikację przez Eurekę. LAB3 był wcześniej rozwijany osobno na `lab3_r`; jego aktualny opis znajduje się w sekcji 11. Poniżej zachowano historyczny zakres LAB4, sprzed Shipping i Auth. Aktualne uruchamianie czterech usług opisano na początku README.

Inventory (`app.inventory:app`, port 8101, `INVENTORY-SERVICE`) posiada stany, rezerwacje, przyjęcia i bazę `inventory.db`. Outbound (`app.outbound:app`, port 8102, `OUTBOUND-SERVICE`) posiada zamówienia i bazę `outbound.db`. Każdy proces tworzy wyłącznie własne tabele i nie otwiera bazy drugiej usługi. Wspólny kod fabryki i repozytoriów nie oznacza wspólnego procesu ani dostępu do cudzych danych. Obecnie `app.main` udostępnia wyłącznie fabrykę; `both` pozostało dla testów, a `uvicorn app.main:app` nie jest punktem startowym usług.

Nowy endpoint `GET /orders/stock-check/{stock_id}` w Outbound wywołuje `do_service("INVENTORY-SERVICE", "/stocks/{id}", return_type="json", prefer_ip=True, timeout=3)`. Wynik zawiera `stock` oraz `available_quantity = quantity - suma rezerwacji`. Jest to informacyjny odczyt, nie potwierdzenie rezerwacji zamówienia. Odpowiedzi zależności mapowane są na: `404` — brak stanu; `502` — nieoczekiwany błąd HTTP lub niepoprawne dane; `503` — brak instancji, błąd połączenia albo timeout. `/health` Outbound pozostaje dostępny przy awarii Inventory. Komunikacja wewnętrzna biegnie bezpośrednio do adresu z Eureki, bez bramki.

Nie dodano Shipping, Auth, JWT, brokera, frontendu ani wspólnego Compose dla usług i bramki. Nadawanie zamówieniu `RESERVED`, zwalnianie rezerwacji i pełna realizacja zamówienia nadal wymagają kolejnego etapu. Inbound pozostaje modułem Inventory.

### Architektura i odpowiedzialność

```mermaid
flowchart TB
    C[Klient HTTP] -->|/api/...| G[API Gateway :8080]
    G -->|lb://INVENTORY-SERVICE| I[Inventory :8101]
    G -->|lb://OUTBOUND-SERVICE| O[Outbound :8102]
    O -->|HTTP GET /stocks/id przez do_service| I
    I --> IDB[(inventory.db: stocks, receipts)]
    O --> ODB[(outbound.db: orders)]
    I --> R[(Redis :6379, osobne prefiksy)]
    O --> R
    I -. rejestracja i heartbeat .-> E[Eureka :8761]
    O -. rejestracja, heartbeat i kopia rejestru .-> E
    G -. fetch-registry .-> E
```

Bramka wybiera trasę, usuwa segment `/api`, odnajduje instancję i przekazuje HTTP. Nie ma logiki magazynowej, bazy ani połączenia Redis. Eureka przechowuje nazwy, adresy i statusy instancji; nie pośredniczy w ruchu domenowym. Dane i reguły pozostają w usługach. BFF byłby interfejsem dostosowanym do konkretnego klienta; tutaj jedna wspólna bramka udostępnia API domenowe.

### Kontrakt zewnętrzny

| Metoda i ścieżka klienta `:8080` | Id trasy | URI | Ścieżka po `StripPrefix=1` | Właściciel i uzasadnienie |
|---|---|---|---|---|
| GET `/api/stocks`, GET/PATCH `/api/stocks/{id}` | inventory-stocks | `lb://INVENTORY-SERVICE` | `/stocks`, `/stocks/{id}` | Inventory: podgląd i korekta stanów |
| POST `/api/stocks/{id}/reservations` | inventory-stocks | `lb://INVENTORY-SERVICE` | `/stocks/{id}/reservations` | Inventory: atomowa kontrola dostępności i rezerwacja |
| POST/GET `/api/receipts`, GET `/api/receipts/{id}` | inventory-receipts | `lb://INVENTORY-SERVICE` | `/receipts`, `/receipts/{id}` | Inventory: rejestr i szczegóły dostaw |
| POST `/api/receipts/{id}/accept`, `/post`, `/cancel` | inventory-receipts | `lb://INVENTORY-SERVICE` | `/receipts/{id}/accept`, `/post`, `/cancel` | Inventory: cykl przyjęcia i księgowanie we własnej transakcji |
| POST/GET `/api/orders`, GET `/api/orders/{id}` | outbound-orders | `lb://OUTBOUND-SERVICE` | `/orders`, `/orders/{id}` | Outbound: dane zamówień |
| POST `/api/orders/{id}/pack` | outbound-orders | `lb://OUTBOUND-SERVICE` | `/orders/{id}/pack` | Outbound: reguła pakowania tylko RESERVED |
| GET `/api/orders/stock-check/{stock_id}` | outbound-orders | `lb://OUTBOUND-SERVICE` | `/orders/stock-check/{stock_id}` | Outbound: odczyt dostępności z Inventory przez Eurekę |

Warunki `Path` obejmują jawnie kolekcję i podścieżki. Gateway zachowuje metodę, JSON, query string, kody i nagłówek `X-Cache` z usługi. Odpowiedzi usług przekazywane przez skonfigurowaną trasę otrzymują `X-Gateway: lab4`. Nieobsługiwana metoda nadal daje `405` z usługi.

Endpointy techniczne każdej usługi `/health` (życie procesu), `/ready` (baza i Redis), `/docs`, `/redoc` i `/openapi.json` pozostają na portach bezpośrednich. Nie ma dla nich tras `/api/...`, ponieważ nie są operacjami domenowymi. Własny `/health` bramki zwraca `{"status":"ok","service":"API-GATEWAY"}` bez `X-Gateway`. Trasy są jawne: `discovery.locator.enabled: false`. Samo istnienie bramki nie blokuje bezpośrednich portów usług.

Bramka ma `register-with-eureka: false` i `fetch-registry: true`; nie powinna pojawić się w rejestrze jako trzecia usługa. `defaultZone` musi kończyć się `/eureka/`. `lb://...` wykorzystuje lokalną kopię rejestru i LoadBalancer zamiast wpisanego na stałe portu. Klient Python również utrzymuje kopię rejestru. Domyślne odświeżanie trwa około 30 s; rejestr serwera i cache LoadBalancera mogą wydłużać obserwowane opóźnienie. Nie jest to natychmiastowy monitoring procesów.

Limity bramki: `connect-timeout: 1000` ms i `response-timeout: 5s`. Wywołanie Outbound → Inventory ma `timeout=3` s dla pojedynczej instancji, więc zwykle zdąży zwrócić kontrolowane `503` przed limitem bramki. Timeout biblioteki HTTP nie stanowi ścisłego limitu całego łańcucha; przy wielu instancjach i ponowieniach czas może wzrosnąć. Testowano po jednej instancji każdej usługi.

### Przygotowanie i uruchomienie

Wymagania: Python 3.12+, JDK 21, Maven 3.9.x oraz Redis. W pakietach prowadzącego oba POM-y wskazują Spring Boot **4.1.1**, Spring Cloud **2025.1.3**; zachowano te wersje. Rozwiązane zależności: Gateway Server WebFlux **5.0.3**, Eureka **5.0.2**. Klient Python: `py-eureka-client==0.13.3`.

Na Debianie przygotuj Python i narzędzia Java/Maven, ustaw `JAVA_HOME` oraz `PATH`, potem w głównym katalogu repozytorium:

```bash
java -version
mvn -version
bash scripts/setup.sh --setup-only --test
mvn -f eureka-server/pom.xml package
mvn -f gateway/pom.xml package
```

Pierwsze budowanie wymaga internetu. Na Windows przygotuj JDK 21 i Maven 3.9.x w PATH i zamiast skryptu Bash wykonaj:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -SetupOnly -Test
mvn -f eureka-server/pom.xml package
mvn -f gateway/pom.xml package
```

Poniższe pięć komponentów i przykłady bez JWT opisują historyczny stan LAB4. Obecne skrypty uruchamiają cały zestaw lub wybraną usługę, a komendy dla aktualnej wersji z Auth znajdują się na początku README. Każdy serwer pierwszoplanowy działa w osobnym terminalu z głównego katalogu repozytorium.

1. Redis: `docker compose up -d redis`, następnie `docker compose exec redis redis-cli PING` (oczekiwane PONG). Można zamiast tego użyć własnego lokalnego serwera Redis.
2. Eureka: `mvn -f eureka-server/pom.xml spring-boot:run`. Panel: `http://127.0.0.1:8761/`.
3. Inventory (Bash):

```bash
export DATABASE_URL='sqlite:///./inventory.db'
export REDIS_URL='redis://127.0.0.1:6379/0'
export CACHE_PREFIX='lab4:local:warehouse'
export CACHE_TTL_SECONDS=60
export EUREKA_SERVER_URL='http://127.0.0.1:8761'
export SERVICE_PORT=8101
.venv/bin/python -m uvicorn app.inventory:app --host 127.0.0.1 --port 8101
```

4. Outbound w kolejnym terminalu (Bash):

```bash
export OUTBOUND_DATABASE_URL='sqlite:///./outbound.db'
export REDIS_URL='redis://127.0.0.1:6379/0'
export CACHE_PREFIX='lab4:local:warehouse'
export CACHE_TTL_SECONDS=60
export EUREKA_SERVER_URL='http://127.0.0.1:8761'
export SERVICE_PORT=8102
.venv/bin/python -m uvicorn app.outbound:app --host 127.0.0.1 --port 8102
```

Na Windows kroki 3 i 4 wykonaj w osobnych oknach PowerShell:

```powershell
# Inventory
$env:DATABASE_URL = 'sqlite:///./inventory.db'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:CACHE_PREFIX = 'lab4:local:warehouse'
$env:CACHE_TTL_SECONDS = '60'
$env:EUREKA_SERVER_URL = 'http://127.0.0.1:8761'
$env:SERVICE_PORT = '8101'
.\.venv\Scripts\python.exe -m uvicorn app.inventory:app --host 127.0.0.1 --port 8101
```

```powershell
# Outbound — osobny terminal
$env:OUTBOUND_DATABASE_URL = 'sqlite:///./outbound.db'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:CACHE_PREFIX = 'lab4:local:warehouse'
$env:CACHE_TTL_SECONDS = '60'
$env:EUREKA_SERVER_URL = 'http://127.0.0.1:8761'
$env:SERVICE_PORT = '8102'
.\.venv\Scripts\python.exe -m uvicorn app.outbound:app --host 127.0.0.1 --port 8102
```

5. Gateway: `mvn -f gateway/pom.xml spring-boot:run`. Poczekaj, aż w Eurece będą dwa wpisy UP i obie trasy zaczną zwracać 200. Gateway nie rejestruje się jako instancja.

| Zmienna | Domyślna wartość | Zastosowanie |
|---|---|---|
| `EUREKA_SERVER_URL` | `http://127.0.0.1:8761` | Klienci Python; sufiks `/eureka` dodaje adapter |
| `SERVICE_PORT` | Inventory 8101, Outbound 8102 | Port zgłaszany przez usługę; musi zgadzać się z `uvicorn --port` |
| `EUREKA_PORT` | 8761 | Port serwera Eureka |
| `GATEWAY_EUREKA_URL` | `http://127.0.0.1:8761/eureka/` | Rejestr pobierany przez bramkę |
| `GATEWAY_PORT` | 8080 | Port bramki |
| `GATEWAY_URL` | `http://127.0.0.1:8080` | Adres bramki dla testów |

Zmienne baz i cache zachowują znaczenie z LAB2. `.env.example` jest wzorem; nie jest automatycznie wczytywany. Zmiana portu Eureki wymaga aktualizacji URL w obu usługach i bramce. Po zmianie YAML zatrzymaj i ponownie uruchom Gateway.

Weryfikacja ręczna (Windows: `curl.exe` zamiast `curl`):

```bash
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8080/api/stocks
# Podstaw UUID z listy stanów:
curl -i http://127.0.0.1:8080/api/stocks/<UUID>
curl -i http://127.0.0.1:8080/api/orders/stock-check/<UUID>
```

Przy pustej tabeli Inventory tworzy początkowy SKU-001, A-01, 10 sztuk. Aby przygotować dodatkowy zasób, użyj `/api/receipts`: utwórz przyjęcie z unikalnym SKU i lokalizacją, zatwierdź `/accept`, zaksięguj `/post`, następnie odszukaj SKU w `/api/stocks`. Nie kasuj bazy. Testy integracyjne stosują właśnie ten przepływ, zachowują dane testowe oznaczone `LAB4-...` i można uruchamiać je wielokrotnie. Wymagają działającego Redis, aby zweryfikować faktyczne MISS → HIT; BYPASS nie zastępuje sprawdzenia cache.

Zatrzymanie: `Ctrl+C` kolejno w terminalach Gateway, Outbound, Inventory i Eureka. Uvicorn wyrejestrowuje usługę w `lifespan`; nie zamykaj go przez `kill -9` w zwykłej demonstracji. Redis uruchomiony dla projektu zatrzymaj `docker compose stop redis`. Zachowaj pliki baz. Awaria przez nagłe zabicie procesu może pozostawić wpis aż do wygaśnięcia dzierżawy, a zachowanie self-preservation Eureki dodatkowo opóźnia jego usunięcie.

### Testy i powtarzalne eksperymenty

Szybkie testy (atrapy Redis i Eureki, bez uruchomionych serwerów):

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Testy integracyjne wymagające pięciu komponentów uruchomionych według powyższej instrukcji:

```bash
.venv/bin/python -m pytest -o addopts= tests/integration -q
```

Na Windows użyj `.\.venv\Scripts\python.exe`. `pytest.ini` domyślnie pomija katalog integracyjny; `-o addopts=` usuwa to pominięcie. Dzięki temu brak działającego systemu nie psuje szybkich testów. Nie ma automatycznego pomijania nieudanych połączeń w testach integracyjnych.

| Test integracyjny | Weryfikacja |
|---|---|
| `test_inventory_read_has_gateway_and_cache_headers` | Pełny JSON stanu, 200, X-Gateway, MISS → HIT |
| `test_outbound_read_executes_discovered_inventory_call` | Wywołanie Inventory przez Outbound i obliczona dostępność |
| `test_write_invalidates_cache_and_read_returns_committed_change` | PATCH po HIT, zmiana ilości, MISS → HIT po commit |
| `test_validation_and_conflict_are_forwarded_without_change` | Dokładne 422/409, detail, X-Gateway i brak zmiany stanu |
| `test_domain_404_differs_from_missing_gateway_route` | 404 FastAPI z nagłówkiem vs 404 WebFlux bez nagłówka |
| `test_gateway_does_not_publish_technical_endpoints` | Health Gateway oraz brak tras do health/ready/docs/schematu usług |
| `test_outbound_order_write_and_read` | POST 201 i GET zamówienia z cache i zachowanym JSON |

Dodatkowe testy `tests/test_discovery.py` obejmują start/stop klienta Eureki, użycie nazwy logicznej i timeoutu 3 s, mapowanie błędów, niepoprawne dane zależności, walidację UUID i niezależność tabel, plików baz oraz tras obu procesów.

Pełna automatyczna weryfikacja na Debianie/WSL (JDK 21 i `redis-server` w PATH, JAR-y zbudowane, porty 8080/8101/8102/8761 wolne):

```bash
.venv/bin/python scripts/verify_lab4.py
```

Opcjonalne `--java /ścieżka/do/bin/java` wybiera Javę. Skrypt uruchamia własny Redis na wolnym porcie oraz Eureka, obie usługi i Gateway jako prawdziwe procesy, wykonuje testy HTTP i sześć eksperymentów, a następnie zatrzymuje tylko własne procesy. Korzysta z tymczasowych baz i unikalnego prefiksu, nie czyści współdzielonego cache. Wynik zapisuje w `reports/lab4-verification.json`. Porty można zmienić argumentami `--gateway-port`, `--inventory-port`, `--outbound-port`, `--eureka-port`. Eksperymenty trwają kilka minut ze względu na rzeczywiste odświeżanie rejestru. Jest to skrypt dla środowiska z lokalnym `redis-server`; na Windows można użyć WSL lub wykonać eksperymenty ręcznie na systemie uruchomionym powyżej.

### Rzeczywiste wyniki (19.11.2026)

Środowisko weryfikacji: Debian, Python **3.13.5**, Amazon Corretto **21.0.13.1**, Maven **3.9.9**, lokalny Redis **8.0.2**. Plik Compose nadal wskazuje Redis 7; rzeczywiste eksperymenty wykonano na osobnym lokalnym Redis 8.0.2. Oba komponenty Java: **BUILD SUCCESS**. Szybkie testy: **83 passed** (73 wcześniejsze + 10 przypadków związanych z rozdzieleniem usług i discovery). Integracyjne: **7 passed**. `pip check`: brak niespójności zależności. Ostrzeżenie Starlette/AnyIO dotyczy przestarzałego aliasu TestClient, nie niepowodzenia testów.

Pełne odpowiedzi, nagłówki, stan rejestru i wynik pytest znajdują się w [raporcie rzeczywistego uruchomienia](reports/lab4-verification.json). W Eurece były wyłącznie `INVENTORY-SERVICE` i `OUTBOUND-SERVICE`, obie UP.

| Scenariusz | Żądanie i stan procesów | Oczekiwany wynik | Rzeczywisty wynik i nagłówki | Wniosek |
|---|---|---|---|---|
| Odczyt Inventory | Dwa GET `/api/stocks/{id}` | 200, cache i X-Gateway | 200, X-Gateway=lab4; początkowo HIT/HIT, ponieważ odczyt przez Outbound wcześniej napełnił cache. Test integracyjny na nowym SKU potwierdził MISS/HIT | Cache należy do Inventory i działa także dla wywołań wewnętrznych |
| Zapis i unieważnienie | PATCH quantity=12, dwa GET | 200, nowy stan i MISS/HIT | 200, quantity=12, X-Gateway=lab4, kolejno X-Cache=MISS i HIT | Commit i usunięcie kopii działają przez bramkę |
| Walidacja i reguła | PATCH quantity=-1; rezerwacja 13 przy stanie 12 | 422 i 409 | 422 z błędem `body.quantity`; 409 z `Insufficient available stock`; oba z X-Gateway=lab4, bez X-Cache | Gateway zachowuje błędy domeny |
| Odczyt Outbound | GET `/api/orders/stock-check/{id}` | 200 z danymi Inventory | 200, stock.quantity=12, available_quantity=12, X-Gateway=lab4; brak X-Cache w odpowiedzi złożonej Outbound | Outbound odkrywa Inventory bez udziału Gateway w połączeniu wewnętrznym |
| **1. Brak zasobu** | GET `/api/stocks/{losowy UUID}` | 404 z FastAPI | 404, `{"detail":"Stock not found"}`, X-Gateway=lab4, brak X-Cache | Trasa istnieje, zasób nie istnieje |
| **2. Brak trasy** | GET `/api/health` | 404 z Gateway | 404 WebFlux: timestamp/path/status/error/requestId, bez X-Gateway i X-Cache | Żądanie nie pasuje do żadnej trasy |
| **3. Zatrzymanie Outbound** | Wyłączenie Outbound, GET jego trasy i stanu Inventory | Błąd tylko trasy Outbound | Natychmiast 500 WebFlux, po odświeżeniu 503 WebFlux, oba bez X-Gateway/X-Cache. Inventory 200, X-Gateway=lab4, X-Cache=HIT; jego /health 200 | Najpierw nieaktualny adres, następnie brak instancji w kopii rejestru. Zmiana 500 → 503 po około 35 s |
| **4. Zatrzymanie Inventory** | Po przywróceniu Outbound wyłączono Inventory | Jego trasa nie działa, Outbound zwraca kontrolowane 503 | Trasa Inventory: natychmiast 500, po około 47 s 503 WebFlux, bez nagłówków. Outbound: 503 `{"detail":"Inventory Service is unavailable"}`, X-Gateway=lab4, brak X-Cache; /health Outbound 200 | Rozróżniamy awarię routingu od błędu zależności obsłużonego w usłudze |
| **5. Ponowne uruchomienie** | Start Inventory na tych samych danych; oba GET przez istniejącą bramkę | Powrót 200 bez restartu Gateway | Obie trasy 200 po **49,14 s** od rozpoczęcia startu; PID Gateway nie zmienił się. X-Gateway=lab4 na obu, X-Cache=HIT na szczegółach Inventory; UUID i quantity=12 zachowane | Rejestr i jego kopie odświeżają się asynchronicznie; cache może przetrwać restart usługi |
| **6. Zatrzymanie Gateway** | Wyłączono bramkę, pozostawiono usługi i Eurekę | Odmowa TCP, usługi bezpośrednio działają | HTTPX ConnectError na :8080; bez odpowiedzi HTTP i nagłówków. Bezpośrednie Inventory i przepływ Outbound → Inventory: 200; Inventory X-Cache=HIT, brak X-Gateway | Pojedyncza bramka jest punktem awarii ruchu klienta, nie komunikacji wewnętrznej |

Czasy około 35 s i 47 s wyliczono z timestampów pierwszej i końcowej odpowiedzi w eksperymentach 3 i 4. Czas 49,14 s zmierzono zegarem monotonicznym w skrypcie, łącznie ze startem procesu. Nie jest to gwarantowany czas odtwarzania; zależy od fazy odświeżania i cache rejestru.

| Wynik widoczny u klienta | Miejsce powstania i znaczenie |
|---|---|
| 404 z `detail` i X-Gateway | FastAPI: brak zasobu; możliwe również 404 mapowane przez Outbound przy braku stanu w Inventory |
| 404 WebFlux, brak X-Gateway | Brak pasującej trasy w Gateway |
| 422 / 409 z X-Gateway | Walidacja / reguła biznesowa w usłudze |
| 502 z X-Gateway | Outbound: nieoczekiwany błąd HTTP lub niepoprawna odpowiedź Inventory; pokryte testami z atrapami |
| 503 z `detail` i X-Gateway | Outbound: brak dostępnego Inventory, timeout albo błąd połączenia |
| 503 WebFlux | Gateway LoadBalancer: brak instancji; w wykonanych eksperymentach bez X-Gateway |
| 500 / potencjalne 504 WebFlux | Błąd połączenia ze starym adresem / przekroczenie response-timeout; 500 zmierzono, 504 nie symulowano |
| ConnectError bez HTTP | Nie działa proces nasłuchujący na porcie bramki |

Brak X-Gateway sam w sobie nie dowodzi braku trasy: w wykonanych eksperymentach filtr nie dołączał go również do błędów połączenia i braku instancji generowanych przez Gateway. Należy porównać kod, ciało odpowiedzi i logi. X-Cache występuje na szczegółach stanów i zamówień, nie na listach, zapisach ani odpowiedzi złożonej stock-check. Samo `/health` nie dowodzi dostępności zależności. Nie testowano wielu instancji, nagłego zabicia procesu bez wyrejestrowania ani wymuszonego timeoutu 504. Ograniczenia cache-aside i SQLite z LAB2 pozostają aktualne.

### Źródła techniczne (EK_03)

Materiały prowadzącego: [instrukcja LAB4](https://pawoom.lazysolutions.pl/2026_2027/lab4/), [pakiet LAB4](https://pawoom.lazysolutions.pl/2026_2027/lab4/lab4-support.zip) i [pakiet LAB3](https://pawoom.lazysolutions.pl/2026_2027/lab3/lab3-support.zip). Z pakietów przeniesiono infrastrukturę Java (POM-y, klasy startowe) i dostosowano YAML do własnej domeny. Układ sekcji dokumentacji oparto na szablonie README-LAB4. Nie przenoszono usług demonstracyjnych ani ich modeli.

| Oficjalna dokumentacja i wersja | Rozwiązywany problem | Zastosowanie | Weryfikacja |
|---|---|---|---|
| [Spring Cloud Gateway Server WebFlux — starter](https://docs.spring.io/spring-cloud-gateway/reference/spring-cloud-gateway-server-webflux/starter.html), linia 5.0 (runtime 5.0.3) | Dobór reaktywnego serwera | `spring-cloud-starter-gateway-server-webflux` oraz LoadBalancer w POM | Maven package, uruchomienie Netty i żądania przez trzy trasy |
| [StripPrefix GatewayFilter](https://docs.spring.io/spring-cloud-gateway/reference/spring-cloud-gateway-server-webflux/gatewayfilter-factories/stripprefix-factory.html), linia 5.0 | Zachowanie dotychczasowych ścieżek FastAPI za prefiksem /api | `spring.cloud.gateway.server.webflux.routes`, `StripPrefix=1` | GET/PATCH/POST przez /api, zachowany JSON, 404/409/422, nagłówki w testach |
| [HTTP timeouts](https://docs.spring.io/spring-cloud-gateway/reference/spring-cloud-gateway-server-webflux/http-timeouts-configuration.html), linia 5.0 | Ograniczenie oczekiwania na zależność | connect-timeout=1000 ms, response-timeout=5s; krótsze timeout=3 w Pythonie | W eksperymencie awarii Outbound zwrócił własne 503 przez bramkę; nie deklarujemy pomiaru 504 |
| [Spring Cloud Netflix — Eureka Client](https://docs.spring.io/spring-cloud-netflix/reference/spring-cloud-netflix.html), linia 5.0 (runtime 5.0.2) | Pobieranie instancji bez rejestracji Gateway | fetch-registry=true, register-with-eureka=false, defaultZone z /eureka/ | Raport z dwoma wpisami UP, routowanie lb:// i odzyskanie tras bez restartu bramki |

Dokumentacja online opisuje linię 5.0 i może wskazywać nowszy patch niż zależności BOM z pakietu prowadzącego. Działanie konfiguracji zweryfikowano na wersjach rzeczywiście zbudowanych z POM-ów.


Wersja do przekazania pozostaje w tym repozytorium. `.gitignore` wyklucza `.venv`, cache Pythona, lokalne bazy, `.env` i katalogi `target` obu projektów Maven. Nie utworzono automatycznie commita, tagu ani publikacji. Po przeglądzie można utworzyć commit etapu i tag `lab4` zgodnie z instrukcją; istniejącego tagu nie należy nadpisywać.

<a id="lab5"></a>

## 12. LAB5 — komunikacja przez RabbitMQ

Wdrożono jedno zdarzenie: **Shipping informuje Outbound o wydaniu przesyłki kurierowi**. Rezerwacje Inventory nadal działają przez HTTP. Zachowano SQLite, Redis, Eurekę, Gateway i JWT z wcześniejszych etapów. Zakres według [instrukcji LAB5](https://pawoom.lazysolutions.pl/2026_2027/lab5/).

### Kontrakt i przebieg

| Element | Wartość i znaczenie |
|---|---|
| Producent | Shipping, `POST /api/shipments/{id}/hand-over` |
| Zdarzenie | `ShipmentHandedOver` — fakt wydania kurierowi |
| Exchange | `warehouse.events`, `topic`, `durable=True` |
| Routing key | `shipment.handed_over` |
| Kolejka | `outbound.shipment-events`, `durable=True` |
| Konsument | Outbound, ręczne `POST /api/orders/consume-events?limit=50` |
| Skutek | Zamówienie `PACKED` → `SHIPPED`, zapis `event_id`, usunięcie kopii z Redis |
| Uprawnienia HTTP | SHIPPING lub ADMIN dla wydania, ponowienia publikacji i odbioru |

Przykładowa wiadomość JSON:

```json
{
  "event_id": "c9309d11-9d5c-59a9-bd43-51b8b808710d",
  "event_type": "ShipmentHandedOver",
  "shipment_id": "11111111-1111-4111-8111-111111111111",
  "order_id": "22222222-2222-4222-8222-222222222222",
  "occurred_at": "2026-11-22T20:00:00Z"
}
```

`event_id` jest UUID wyliczanym z identyfikatora przesyłki i rodzaju zdarzenia. Ponowienie `/sync` po restarcie generuje ten sam identyfikator i czas wydania. Konsument sprawdza wymagane pola, a dodatkowe ignoruje (Tolerant Reader). Wiadomość nie przenosi haseł ani JWT.

```mermaid
flowchart TB
    C[Klient z JWT] --> G[Gateway :8080]
    G -->|hand-over| S[Shipping :8103]
    S -->|HTTP: sprawdzenie PACKED| O[Outbound :8102]
    S --> SD[(shipping.db: wydanie i notification_pending)]
    S -->|ShipmentHandedOver| B[RabbitMQ :5672]
    B --> Q[(outbound.shipment-events)]
    G -->|orders/consume-events| O
    Q -->|odbiór i ACK po przetworzeniu| O
    O --> OD[(outbound.db: zamówienie i processed_events)]
    O -->|DEL kopii zamówienia| R[(Redis :6379)]
    S -. rejestracja i odkrywanie .-> E[Eureka :8761]
    O -. rejestracja .-> E
    G -. pobieranie rejestru .-> E
```

Odpowiedzi na pytania projektowe:

1. **Dlaczego asynchronicznie?** Wydanie kurierowi już nastąpiło; Outbound może odnotować je później. Sprawdzenie PACKED przed wydaniem pozostaje synchroniczne przez Eurekę. Jest to choreografia zdarzeń, bez centralnego koordynatora Sagi.
2. **Kiedy publikujemy?** Po walidacji i sprawdzeniu reguł Shipping zapisuje HANDED_OVER, czas i `notification_pending=true` w jednej transakcji. Dopiero po COMMIT publikuje persistent JSON. Po potwierdzeniu przyjęcia przez RabbitMQ zapisuje false. Błędy 404/409/422 nie publikują zdarzenia.
3. **Co robi konsument?** W jednej transakcji zapisuje SHIPPED, powiązanie przesyłki i `processed_events`. Następnie usuwa cache zamówienia i wysyła ACK. Nieudane przetwarzanie daje NACK z `requeue=True`. Nieudane DEL daje 503; duplikat po ponowieniu ponownie usuwa cache, naprawiając przerwę po COMMIT.
4. **Jak rozpoznaje duplikat?** `event_id` jest kluczem głównym w SQLite. Ten sam identyfikator i dane nie zmieniają stanu ponownie; niezgodne dane pod tym samym ID są odrzucane. Zapis przetrwa restart konsumenta. ACK następuje dopiero po poprawnej obsłudze.
5. **Co przy wyłączonym odbiorcy?** Wiadomość czeka we wcześniej zadeklarowanej kolejce. Po starcie Outbound trzeba wywołać odbiór. Jeżeli wyłączony Outbound uniemożliwia wcześniejsze sprawdzenie PACKED, eksperyment wykonujemy przez bezpośrednią publikację przygotowanego zdarzenia, zgodnie z instrukcją laba.

### Wspólna obsługa i zmiany w kodzie

`app/messaging.py` oparto na adapterze `lab5-support/demo/shared/messaging.py`. Dodano publisher confirms i `mandatory=True`, aby brak pasującej kolejki nie kończył się pozornym sukcesem. Wiadomości mają `delivery_mode=2`. Połączenie powstaje dopiero podczas operacji i jest zamykane po niej; wywołania blokujące w asynchronicznym Shipping są wykonywane w puli wątków.

Wszystkie cztery usługi dostają `app.state.event_broker` i opcjonalny parametr `event_broker`. Inventory i Auth nie tworzą kolejek ani nie wykonują połączeń z RabbitMQ przy zwykłych operacjach. Outbound wiąże swoją kolejkę przy starcie; brak brokera nie blokuje startu jego HTTP. Odbiór ponawia deklarację kolejki po odzyskaniu brokera.

Modelem zdarzenia jest `app/models/events.py`. Producent działa w `app/services/shipping.py`, konsument w `app/services/orders.py`, a atomowy zapis w `app/repositories.py`. Nowa tabela `processed_events` znajduje się wyłącznie w bazie Outbound i tworzy się przy starcie; istniejące kolumny baz pozostają bez zmian. Dotychczasowy HTTP `/orders/{id}/dispatch` pozostaje dostępny, ale zwykły przepływ Shipping korzysta z RabbitMQ.

**Odbiór jest ręczny, nie działa w tle.** W Swaggerze Outbound wybierz `POST /orders/consume-events`. Przez Gateway jest to `/api/orders/consume-events`; istniejąca trasa `/api/orders/**` już go obsługuje. Odpowiedź zawiera `consumed_messages` i `unique_events_processed`, np. 2 odebrane wiadomości i 1 nowe zdarzenie. Parametr `limit` ma zakres 1–500.

### Uruchomienie i pokaz w Swaggerze

Sprawdzone wersje: Python 3.13.9, pika 1.5.0, obraz `rabbitmq:3.13-management-alpine`. Konfiguracja `.env`:

```dotenv
RABBITMQ_URL=amqp://guest:guest@127.0.0.1:5672/%2F
RABBITMQ_EXCHANGE=warehouse.events
```

Pozostałe zmienne baz, Redis, JWT i Eureki opisano na początku README. RabbitMQ ma port AMQP 5672 i panel [Management](http://127.0.0.1:15672/) (lokalnie `guest` / `guest`). Compose przypina porty do 127.0.0.1 i przechowuje dane brokera w wolumenie `rabbitmq-data` ze stałą nazwą hosta.

Z głównego katalogu projektu uruchom cały zestaw przez `scripts/setup.ps1` albo `bash scripts/setup.sh`. Przy starcie ręcznym najpierw wykonaj `docker compose up -d redis rabbitmq`, następnie uruchom Eurekę, Auth, Inventory, Outbound, Shipping i Gateway według instrukcji na początku README. Kolejka musi zostać utworzona przez Outbound przed pierwszą publikacją. Zatrzymanie ręczne: Ctrl+C w terminalach oraz `docker compose stop redis rabbitmq`; wolumen i bazy pozostają zachowane.

Pokaz przepływu:

1. Zaloguj się w Auth i użyj tokenu w Authorize. Przygotuj towar, zamówienie RESERVED → PACKED i przesyłkę NEW → READY.
2. Dwa razy odczytaj zamówienie przez Gateway: X-Cache MISS → HIT.
3. Wykonaj `/shipments/{id}/hand-over`. Shipping ma HANDED_OVER, ale zamówienie nadal PACKED; w kolejce jest Ready: 1.
4. Wykonaj `/orders/consume-events`. Otrzymasz 1 odebraną wiadomość i 1 nowe zdarzenie, a kolejka będzie pusta.
5. Odczytaj zamówienie: SHIPPED oraz MISS, a przy kolejnym odczycie HIT. Odpowiedzi tras mają X-Gateway: lab4.

Przy wyłączonym brokerze publikacja i odbiór zwracają **503**. Wydanie kurierowi pozostaje zapisane z `notification_pending=true`; po przywróceniu RabbitMQ wywołaj `/shipments/{id}/sync`, a potem odbiór w Outbound. False oznacza przyjęcie wiadomości przez broker, a nie zmianę zamówienia przez konsumenta.

### Testy i wyniki eksperymentów

```powershell
.\.venv\Scripts\python.exe -m pytest -q
# W izolowanym środowisku z działającymi usługami i RabbitMQ:
$env:GATEWAY_TOKEN = '<token-administratora>'
.\.venv\Scripts\python.exe -m pytest -o addopts= tests/integration -q
```

Podstawowe testy używają `InMemoryEventBroker`, bez sieci i kontenerów. Testy integracyjne wymagają tych samych `RABBITMQ_URL` i `RABBITMQ_EXCHANGE` co aplikacje; uruchamiaj je na odrębnych bazach, prefiksie Redis i vhoście RabbitMQ, ponieważ odbiór przetwarza całą powiązaną kolejkę. Weryfikację z 22.11.2026 wykonano na takich tymczasowych danych, po czym zatrzymano własne procesy i usunięto testowy vhost.

Wynik: **131 testów jednostkowych i komponentowych oraz 12 integracyjnych przeszło**. Wyniki rzeczywistego wykonania zapisano w [raporcie LAB5](reports/lab5-verification.json).

Oryginalne demo prowadzącego: 4 testy przeszły, 1 nie przeszedł. W `test_missing_destination_locker_returns_404_without_publishing` sprawdzenie `fake_eureka.stops == 1` znajduje się jeszcze wewnątrz aktywnego kontekstu lifespan, przed zatrzymaniem klienta. Pakietu prowadzącego nie zmieniano; jest to osobny wynik od testów naszego projektu.

| Scenariusz | Działanie i oczekiwanie | Rzeczywisty wynik | Wniosek |
|---|---|---|---|
| Publikacja przez Gateway | Wydanie READY, oczekiwana wiadomość w kolejce | HTTP 200, Ready: 1, zamówienie nadal PACKED | Producent nie czeka na konsumenta |
| Odbiór i cache | Odbiór, SHIPPED, DEL i ACK | 1 wiadomość / 1 zdarzenie, Ready: 0; MISS → HIT przed operacją i MISS → HIT po niej | Odczyt po odbiorze pochodzi z aktualnej bazy |
| Duplikat | Dwie wiadomości z tym samym event_id | 2 odebrane / 1 nowe zdarzenie; dodatkowe pole JSON zaakceptowane | Idempotentność i Tolerant Reader |
| Odrzucenie operacji | Zły stan, nieistniejąca przesyłka, zła waga | 409, 404, 422; Ready: 0 | Odrzucone operacje nie publikują |
| Wyłączony Outbound | Bezpośrednia publikacja przygotowanego zdarzenia przy zatrzymanym procesie | Ready: 1; po starcie 1 wiadomość / 1 zdarzenie, SHIPPED i Ready: 0 | Wiadomość czeka na odbiorcę |
| Wyłączony RabbitMQ | Próba wydania i odbioru | Obie operacje 503; HANDED_OVER i notification_pending=true | Fakt wydania nie ginie, publikacja wymaga ponowienia |
| Powrót RabbitMQ | Start kontenera, /sync i odbiór | Sync 200, 1 wiadomość / 1 zdarzenie, SHIPPED | Powiadomienie zostaje dokończone |

Własne testy to m.in. `test_event_waits_for_consume_and_invalidates_cache`, `test_duplicate_and_extra_fields_apply_once`, `test_invalid_operations_do_not_publish` i `test_broker_outage_keeps_pending_fact_and_sync_recovers`. Sprawdzono też trwałość deduplikacji po restarcie, naprawę cache po błędzie DEL, brak ACK przed powodzeniem handlera i dostęp do operacji odbioru wyłącznie dla SHIPPING/ADMIN. Wcześniejsze reguły biznesowe zachowano; testy wysyłki uwzględniają teraz osobny moment odbioru.

### Ograniczenia i dokumentacja

Zapis Shipping i publikacja nie tworzą jednej transakcji rozproszonej. Pending oraz odtwarzalny event_id pozwalają ponowić powiadomienie po przerwaniu procesu; nie ma pełnej tabeli Transactional Outbox ani automatycznego publikatora. Dostarczenie może nastąpić więcej niż raz. Błędna wiadomość zatrzymuje bieżącą partię, pozostaje w kolejce i wymaga interwencji; nie ma DLQ. Zastosowano pojedynczy broker, bez klastra i bez gwarantowanego czasu przetworzenia. Eureka i Gateway pozostają poza Dockerem.

| Źródło | Rozwiązany problem | Zastosowanie i weryfikacja |
|---|---|---|
| [Pakiet LAB5](https://pawoom.lazysolutions.pl/2026_2027/lab5/lab5-support.zip) | Wspólny adapter oraz atrapa do testów | Dostosowano RabbitEventBroker i InMemoryEventBroker do własnej domeny; testy projektu |
| [RabbitMQ — ACK i publisher confirms](https://www.rabbitmq.com/docs/confirms) | Potwierdzenie po przetworzeniu i wykrywanie nieudanej publikacji | auto_ack=False, ACK po zapisie/cache, NACK, confirms i mandatory; testy duplikatu i awarii |
| [RabbitMQ — queues](https://www.rabbitmq.com/docs/queues) | Trwała kolejka i buforowanie | durable, persistent, wolumen; rzeczywisty test zatrzymanego konsumenta i restart brokera |
| [Pika — BlockingConnection/Channel](https://pika.readthedocs.io/en/stable/modules/adapters/blocking.html) | Publikacja i ręczny odbiór AMQP | basic_publish/basic_get/basic_ack/basic_nack, timeouty i zamykanie połączeń; pika 1.5.0 |

