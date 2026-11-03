# Magazyn — LAB2

Kontynuacja projektu magazynu z LAB1: Inventory prowadzi stany i rezerwacje, Outbound przechowuje zamówienia i sprawdza warunki pakowania. Pracownikiem korzystającym z API jest magazynier. Dane obu modułów są teraz trwałe w osobnych bazach SQLite, a wybrane odczyty korzystają z opcjonalnego Redis.

Wymagania: [instrukcja LAB2](https://pawoom.lazysolutions.pl/2026_2027/lab2/). Zachowano endpointy, typy UUID, JSON, błędy i reguły LAB1. Dodano `PATCH /stocks/{id}`, nagłówek `X-Cache` przy odczycie szczegółów oraz opcjonalne `/ready`. Nie implementowano domeny paczkomatów.

## Uruchomienie

Python 3.12+, pip, opcjonalnie Docker Compose do uruchomienia Redis. Testy automatyczne nie wymagają serwera Redis. Wszystkie polecenia wykonuj z katalogu repozytorium.

Redis przez Docker (Debian i Windows z Docker Desktop):

```bash
docker compose up -d redis
docker compose exec redis redis-cli PING
```

Oczekiwany wynik: `PONG`. Redis jest dostępny tylko lokalnie na porcie 6379. Jeżeli masz już własny serwer na tym porcie, użyj go i ustaw odpowiedni `REDIS_URL`. Nie zatrzymuj współdzielonego serwera na potrzeby eksperymentu. Redis w Compose nie zapisuje cache na dysku; po odtworzeniu procesu dopuszczalny jest pusty cache.

Debian:

```bash
export DATABASE_URL='sqlite:///./inventory.db'
export OUTBOUND_DATABASE_URL='sqlite:///./outbound.db'
export REDIS_URL='redis://127.0.0.1:6379/0'
export CACHE_PREFIX='lab2:UZUPELNIJ:warehouse'
export CACHE_TTL_SECONDS='60'
bash scripts/setup.sh --test
```

Przed uruchomieniem zastąp `UZUPELNIJ` swoim numerem albumu. Skrypt tworzy `.venv`, instaluje zależności, wykonuje testy i uruchamia serwer na `http://127.0.0.1:8101`. Debian wymaga pakietów `python3` i `python3-venv`. Zatrzymanie serwera: `Ctrl+C`.

Windows 11, PowerShell, Python 3.12+ z launcherem `py`:

```powershell
$env:DATABASE_URL = 'sqlite:///./inventory.db'
$env:OUTBOUND_DATABASE_URL = 'sqlite:///./outbound.db'
$env:REDIS_URL = 'redis://127.0.0.1:6379/0'
$env:CACHE_PREFIX = 'lab2:UZUPELNIJ:warehouse'
$env:CACHE_TTL_SECONDS = '60'
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Test
```

`Bypass` dotyczy tylko tego procesu PowerShell. Dostępne opcje skryptów: `--port 8200` / `-Port 8200`, `--setup-only` / `-SetupOnly`, `--test` / `-Test`. Nie kopiuj `.venv` pomiędzy komputerami.

Ręczne uruchomienie w już przygotowanym środowisku:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8101
```

Na Windows użyj `.\.venv\Scripts\python.exe` zamiast `.venv/bin/python`. Dokumentacja API: `http://127.0.0.1:8101/docs`. Do demonstracji restartów uruchamiaj bez `--reload`.

| Zmienna | Domyślna wartość | Znaczenie |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./inventory.db` | Baza Inventory |
| `OUTBOUND_DATABASE_URL` | `sqlite:///./outbound.db` | Osobna baza Outbound |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Adres opcjonalnego cache |
| `CACHE_PREFIX` | `lab2:local:warehouse` | Własna przestrzeń kluczy; na zajęciach ustaw numer albumu |
| `CACHE_TTL_SECONDS` | `60` | Dodatnia liczba całkowita sekund |

Ścieżki SQLite są względne do katalogu uruchomienia; skrypty setup przechodzą do katalogu projektu. Wskazuj dwa różne pliki baz. Nowa baza z tym samym Redis powinna otrzymać nowy prefiks, aby nie odczytywać kopii innego zbioru danych. `.env.example` jest wzorem konfiguracji; aplikacja nie wczytuje `.env` automatycznie. Po zmianie zmiennych zatrzymaj i uruchom Uvicorn ponownie. Sam `--reload` nie przejmuje środowiska terminala.

Schemat tworzy się przy starcie przez `metadata.create_all()`. Ta operacja nie usuwa danych i nie zmienia istniejących kolumn. Zmiany schematu wymagają migracji; Alembic jest rozszerzeniem poza tym etapem. Bazy, pliki pomocnicze SQLite, środowisko i `.env` są wykluczone z Git.

## Ciągłość LAB1 i granice usług

Wersja wyjściowa: commit `69fce81`, **22 testy przechodzące przed zmianami**. [Archiwalny README LAB1](docs/lab1.md) zachowuje wcześniejszą dokumentację, modele Inbound/Shipping i wyniki. Nowy eksperyment na archiwalnym kodzie potwierdził przez HTTP, że po restarcie LAB1 znikały rezerwacje i zamówienia, zmieniał się UUID produktu, a stary UUID dawał 404. W LAB2 restart zachowuje UUID i zapisane dane.

Pojęcia: SKU — kod produktu; lokalizacja — miejsce składowania; rezerwacja — sztuki przydzielone zamówieniu; dostępność — `quantity - suma(reservations.quantity)`.

| Moduł/usługa | Właściciel danych i operacji | Stan realizacji |
|---|---|---|
| Inventory | Stany, lokalizacje, rezerwacje; odczyt, korekta ilości, rezerwacja | Implementacja i osobny `inventory.db` |
| Outbound | Zamówienia, odbiorcy, pozycje i pakowanie | Implementacja i osobny `outbound.db` |
| Inbound | Dostawcy i przyjęcia; zgłaszanie zmiany stanu do Inventory | Plan z LAB1 |
| Shipping | Przesyłki i przekazanie kurierowi | Plan z LAB1 |

Inventory nie przechowuje danych odbiorcy ani statusu zamówienia. Outbound nie zapisuje stanów magazynowych. `order_id` w rezerwacji jest odwołaniem do innej domeny, bez klucza obcego pomiędzy bazami. Nie sprawdzamy istnienia zamówienia w bazie Outbound podczas rezerwacji — zachowano zachowanie LAB1.

Oba moduły nadal działają w jednym procesie FastAPI. Baza i Redis nie tworzą automatycznie niezależnych mikrousług: do tego potrzebne będą osobne procesy, wdrażanie i komunikacja przez API. Rozdzielone bazy i repozytoria ułatwiają późniejsze wydzielenie. Druga usługa nie powinna pisać bezpośrednio do tabel pierwszej.

Plan integracji: **Outbound → Inventory**, `POST /stocks/{id}/reservations`, JSON `{"order_id":"<UUID>","quantity":3}`. Po potwierdzeniu `201` Outbound będzie mógł przejść z `NEW` do `RESERVED`. Przy braku potwierdzenia pozostaje `NEW`; trzeba będzie obsłużyć timeouty, ponowienia i idempotencję, aby nie dublować rezerwacji. LAB2 nie dodaje tej integracji ani publicznego endpointu dowolnego ustawiania statusu. Poprawne pakowanie stanu `RESERVED` w testach jest przygotowywane bezpośrednio w bazie jako symulacja przyszłego potwierdzenia.

## Model i transakcje

Modele Pydantic w `app/models/` określają kontrakt API. Modele ORM w `app/db_models.py` określają tabele. Każda domena ma osobne `metadata`, silnik i fabrykę sesji.

| Baza/tabela | Kolumny | Powiązania |
|---|---|---|
| Inventory / `stocks` | `id` UUID PK, `sku`, `location`, `quantity`, `reservations` JSON | Rezerwacje zawierają `order_id` UUID oraz dodatnią `quantity` |
| Outbound / `orders` | `id` UUID PK, `recipient_name`, `recipient_phone`, `recipient_address`, `status`, `items` JSON | Pozycje zawierają `sku` i dodatnią `quantity` |

Każda usługa zachowuje model jednej tabeli z LAB1; listy JSON są przepisywane jako nowe wartości, aby SQLAlchemy wykrywał zmiany. Baza chroni unikalność kluczy głównych, `NOT NULL`, nieujemną ilość stanu i dozwolone statusy zamówienia. Pydantic sprawdza niepuste teksty, typy UUID, wymagane pola i ilości całkowite (odrzuca także `true`, ułamki i teksty liczbowe). Nie dodano nowej reguły unikalności SKU, której nie było w LAB1.

Reguły domenowe:

- Inventory: suma rezerwacji nie przekracza ilości. Przy 10 sztukach i rezerwacji 7 kolejna rezerwacja 4 daje `409`, bez zmiany danych.
- Korekta stanu: nowa ilość nie może być mniejsza od sumy rezerwacji. Przy rezerwacji 3 ilość 3 jest dozwolona, a 2 daje `409`.
- Outbound: tylko zamówienie `RESERVED` można spakować. `NEW` i ponowne pakowanie `PACKED` dają `409`, bez zmiany danych.

`lifespan` tworzy zasoby i schemat przed obsługą żądań; `ExitStack` zamyka klienta Redis i oba silniki także po błędzie startu. Import modułu nie tworzy baz. Repozytorium otwiera i zamyka osobną sesję na operację. Lista, szczegóły i zapis korzystają z tych samych trwałych danych; słowniki LAB1 zostały usunięte.

`write_transaction()` używa `sessions.begin()`: poprawne wyjście zatwierdza **commit**, wyjątek powoduje **rollback** i zamknięcie sesji. Zapis w SQLite zaczyna się od `BEGIN IMMEDIATE`, przed odczytem dostępności. To blokuje konkurencyjny zapis także z innej instancji aplikacji; test dwóch instancji wykazuje jeden sukces i jeden konflikt przy dwóch rezerwacjach po 7 z 10 sztuk. Timeout oczekiwania SQLite wynosi 5 s. `PRAGMA foreign_keys=ON` jest włączone, choć obecne tabele nie mają relacji FK.

Produkt początkowy `SKU-001`, `A-01`, ilość 10, bez rezerwacji, dostaje stabilny demonstracyjny UUID `11111111-1111-4111-8111-111111111111`. Seed dodaje go tylko przy braku tego rekordu i nigdy nie resetuje ilości ani rezerwacji. Zamówienia otrzymują UUID nadawane przez serwer. Zmiana losowego identyfikatora początkowego przy każdym starcie LAB1 na trwały identyfikator LAB2 wynika z persystencji; format UUID pozostaje ten sam.

## Kontrakt API

| Metoda | Ścieżka | Sukces | Błędy i zachowanie |
|---|---|---|---|
| GET | `/health` | `200`, `{"status":"ok","service":"replace-me"}` | Zachowany dokładny kontrakt LAB1; tylko liveness |
| GET | `/ready` | `200`, `ok` lub `degraded` | `503` przy awarii dowolnej bazy |
| GET | `/stocks` | `200`, lista `Stock` | Bez cache |
| GET | `/stocks/{id}` | `200`, `Stock`, `X-Cache` | `404`, `422` |
| POST | `/stocks/{id}/reservations` | `201`, JSON rezerwacji | `404`, `409`, `422` |
| PATCH | `/stocks/{id}` | `200`, zaktualizowany `Stock` | `404`, `409`, `422`; body `{"quantity":14}` |
| POST | `/orders` | `201`, zamówienie ze statusem `NEW` | `422`; `409` przy kolizji serwerowego UUID |
| GET | `/orders` | `200`, lista `Order` | Bez cache |
| GET | `/orders/{id}` | `200`, `Order`, `X-Cache` | `404`, `422` |
| POST | `/orders/{id}/pack` | `200`, zamówienie `PACKED` | `404`, `409`, `422` |

Rozpoznana kolizja klucza głównego zamówienia w SQLite daje `409`, `{"detail":"Order already exists"}` bez nadpisania istniejących danych. Pozostałe błędy bazy podczas żądania dają `503`, `{"detail":"Database unavailable"}`; nie są zamieniane na konflikt ani 404. Przy błędzie inicjalizacji bazy aplikacja nie rozpocznie obsługi żądań. `/ready` sprawdza połączenia, lecz `SELECT 1` nie dowodzi poprawności całego schematu ani gotowości do zapisu.

`Stock` zachowuje pola `id`, `sku`, `location`, `quantity`, `reservations`. Przykład rezerwacji: `{"order_id":"22222222-2222-4222-8222-222222222222","quantity":3}`. Nieistniejący stan: `404`, `{"detail":"Stock not found"}`; zamówienie: `404`, `{"detail":"Order not found"}`. Brak dostępności: `409`, `{"detail":"Insufficient available stock"}`. Niedozwolone pakowanie: `409`, `{"detail":"Order must be reserved before packing"}`. Korekta poniżej rezerwacji: `409`, `{"detail":"Quantity cannot be lower than reserved stock"}`. Walidacja FastAPI zwraca `422` z listą błędów w `detail`.

Przykładowe utworzenie zamówienia:

```json
{
  "recipient_name": "Jan Kowalski",
  "recipient_phone": "123456789",
  "recipient_address": "ul. Polna 1, Warszawa",
  "items": [{"sku": "SKU-001", "quantity": 3}]
}
```

Odpowiedź zawiera te same dane oraz serwerowe `id` i `status: "NEW"`.

## Cache i spójność

Cache-aside obejmuje tylko `GET /stocks/{id}` i `GET /orders/{id}`. Przechowujemy JSON odpowiedzi Pydantic, nigdy sesje ani obiekty ORM. Odczyt sprawdza zgodność schematu i identyfikatora; uszkodzona kopia jest odtwarzana z bazy. Nie cache’ujemy 404 ani list.

| Odczyt | Klucz przy prefiksie `lab2:UZUPELNIJ:warehouse` | TTL | Unieważnienie |
|---|---|---|---|
| Stan | `lab2:UZUPELNIJ:warehouse:inventory:stock:<UUID>` | 60 s | Rezerwacja i korekta ilości |
| Zamówienie | `lab2:UZUPELNIJ:warehouse:outbound:order:<UUID>` | 60 s | Poprawne spakowanie |

Nagłówek `X-Cache`: `MISS` — brak kopii, odczyt z bazy i próba zapisania kopii; `HIT` — kopia bez odczytu repozytorium; `BYPASS` — nieudane GET/SET Redis, odpowiedź z bazy. Przy 404 nagłówek nie jest wymagany. Nieudane DELETE jest logowane; odpowiedź zapisu nadal oznacza zatwierdzenie bazy.

Kolejność zmiany: odczyt z bazy → reguła → zapis → **commit** → **DEL cache**. Przy 409, 422 lub rollbacku nie usuwamy poprawnej kopii. Limity połączenia i operacji Redis wynoszą po 0,2 s; klient nie ponawia automatycznie operacji. Błąd GET, SET lub DELETE nie blokuje działania sprawnej bazy ani nie cofa zatwierdzonej zmiany.

Odbiorca toleruje opóźnienie do 60 s w informacyjnym podglądzie szczegółów. Przy nieudanym unieważnieniu stara kopia może wrócić po odzyskaniu połączenia i pozostać do końca TTL, mimo poprawnego zapisu bazy. Zmiana konfiguracji TTL nie skraca życia istniejących wpisów. Podczas równoległego GET i zapisu możliwe jest też ponowne wstawienie starej kopii; prosty cache-aside nie zapewnia ścisłej spójności. Baza i Redis nie uczestniczą we wspólnej transakcji.

Rezerwacja i pakowanie podejmują decyzje wyłącznie na danych w transakcji bazy. Stary podgląd dostępności nie pozwala zarezerwować niedostępnych sztuk. Gdy wymagany jest bezwzględnie aktualny widok, trzeba ominąć cache lub wdrożyć mocniejszy mechanizm spójności. Listy odczytują bezpośrednio bazę i mogą służyć do sprawdzenia bieżącego stanu. Podczas awarii bazy szczegóły mogą jeszcze pochodzić z cache, ale zapis i odczyt listy nie będą działać; `/ready` pokaże 503.

## Testy i dowody działania

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
```

Każdy test ma osobny katalog `tmp_path`, dwa pliki baz, własny prefiks i nowy `MemoryRedis`. Nowe `create_app()` z tym samym adresem bazy zachowuje stan — sama nowa instancja nie resetuje danych. `TestClient` działa w bloku `with`, co uruchamia start i sprzątanie zasobów. Atrapa umożliwia awarię GET, SET i DELETE oraz kontrolowany upływ czasu; rzeczywiste wygaśnięcie potwierdzono także na serwerze Redis.

Wynik końcowy: **50 passed**. `pip check`: brak niezgodnych zależności. Pozostaje ostrzeżenie deprecacyjne Starlette dotyczące aliasu `BlockingPortal` w zależności TestClient; nie powoduje błędów testów.

Weryfikowane są: scenariusze LAB1, trwałość obu domen z pustym cache po restarcie, MISS/HIT z licznikiem wywołań repozytorium, unieważnianie po commit, rollback po rzeczywistym `flush()` i symulowanym błędzie commit, błędy walidacji i konflikty bez zmiany stanu, konkurencyjne rezerwacje dwóch instancji, uszkodzona kopia, awarie cache, stary HIT po nieudanym DELETE oraz gotowość przy awarii zależności. Licznik repozytorium dowodzi pominięcia odczytu przy HIT; nie twierdzimy na tej podstawie, że Redis jest szybszy od lokalnego SQLite.

Powtarzalna weryfikacja HTTP na rzeczywistym Redis, Debian/WSL z `redis-server` w PATH:

```bash
.venv/bin/python scripts/verify_lab2.py --baseline-ref 69fce81 --output reports/lab2-verification.json
```

Skrypt uruchamia własne procesy Redis i Uvicorna na wolnych portach, tworzy tymczasowe bazy i prefiks, a na końcu zamyka procesy oraz usuwa katalog tymczasowy. Archiwalny LAB1 pobiera przez odczyt z Git bez zmiany checkoutu. Nie wymaga działającego Dockera, nie dotyka baz użytkownika ani współdzielonego Redis, nie wykonuje `FLUSHALL` ani `FLUSHDB`. Do demonstracji TTL używa 2 s, a w eksperymencie nieudanego unieważnienia 5 s; normalna konfiguracja aplikacji pozostaje 60 s. Bez `--baseline-ref` sprawdza tylko LAB2.

Wykonano 03.11.2026: Python 3.13.5, SQLAlchemy 2.0.55, redis-py 6.4.0, rzeczywisty Redis 8.0.2. [Raport z wykonania](reports/lab2-verification.json) zawiera wartości i wyniki poniższych eksperymentów. `compose.yaml` udostępnia alternatywnie Redis 7; raport dotyczy lokalnego serwera 8.0.2.

| Scenariusz | Stan i żądanie | Oczekiwany wynik | Rzeczywisty wynik | Reset |
|---|---|---|---|---|
| LAB1 przed przebudową | Rezerwacja 3, zamówienie, restart procesu archiwalnego kodu | Utrata zmian, nowy UUID, stary UUID 404 | Zgodny | Osobny katalog tymczasowy |
| Health / PING | GET `/health`, Redis PING | 200 i dokładny kontrakt; PONG | Zgodny | Nowe procesy |
| MISS → HIT | Dwa GET szczegółów przy pustym kluczu | MISS, potem HIT z tym samym JSON | Zgodny, TTL 2 | Usunięcie własnego klucza |
| TTL | Wpis z TTL 2 s; oczekiwanie na wygaśnięcie; GET | TTL -2, potem MISS | Zgodny | Wygaśnięcie klucza |
| Rezerwacja / korekta | HIT; POST rezerwacji 3; PATCH ilości 14 | 201/200, DEL, nowy GET z aktualnymi danymi | Zgodny | Nowe bazy tymczasowe |
| Reguła Inventory | Stan 14, rezerwacja 3; rezerwacja dodatkowych 12 | 409, nadal tylko rezerwacja 3 | Zgodny | Nowa baza |
| Outbound | POST zamówienia; GET dwa razy; POST pakowania NEW | 201, MISS/HIT, 409 bez zmiany zamówienia | Zgodny | Nowa baza |
| Restart LAB2 | Dane zapisane; stop Uvicorn; DEL obu własnych kluczy; start i GET | Obie domeny zachowane, odczyty MISS | Zgodny | Nowe procesy z tymi samymi plikami |
| Awaria Redis | Stop własnego procesu; GET, PATCH ilości 18, POST zamówienia | BYPASS, zapis działa; ready degraded 200 | Zgodny | Start własnego Redis |
| Przywrócenie Redis | Start serwera z pustym cache; dwa GET | MISS, HIT, ilość 18 | Zgodny | Nowy proces Redis |
| Nieudane DEL | Cache ilości 20; restart API z niedostępnym portem; PATCH 25; restart z właściwym adresem | Zapis 25; początkowo stary HIT 20; po TTL MISS 25 | Zgodny | Wygaśnięcie TTL i sprzątnięcie procesów |

Dodatkowy ręczny podgląd dla aplikacji uruchomionej przez Compose i setup (Debian):

```bash
curl -i http://127.0.0.1:8101/stocks/11111111-1111-4111-8111-111111111111
curl -i http://127.0.0.1:8101/stocks/11111111-1111-4111-8111-111111111111
curl -i -X PATCH http://127.0.0.1:8101/stocks/11111111-1111-4111-8111-111111111111 -H 'Content-Type: application/json' -d '{"quantity":14}'
docker compose exec redis redis-cli TTL 'lab2:UZUPELNIJ:warehouse:inventory:stock:11111111-1111-4111-8111-111111111111'
```

Na Windows użyj Swagger `/docs`, `curl.exe` albo `Invoke-RestMethod`. Do wyświetlenia nagłówków: `curl.exe -i http://127.0.0.1:8101/stocks/11111111-1111-4111-8111-111111111111`. Do korekty w PowerShell: `Invoke-RestMethod -Method Patch -Uri 'http://127.0.0.1:8101/stocks/11111111-1111-4111-8111-111111111111' -ContentType 'application/json' -Body '{"quantity":14}'`.

Reset demonstracji: zatrzymaj aplikację i wybierz nowe ścieżki baz oraz nowy prefiks. Nie usuwaj baz przy zwykłym restarcie. Jeżeli chcesz wyczyścić kopię, wykonaj `DEL` wyłącznie pełnego własnego klucza. Na serwerze współdzielonym nie stosuj globalnego czyszczenia.

## Porównanie i ograniczenia

| Cecha | Pamięć procesu LAB1 | SQLite LAB2 | Redis jako cache |
|---|---|---|---|
| Rola | Źródło danych w procesie | Źródło zatwierdzonych danych | Odtwarzalna kopia odpowiedzi |
| Restart aplikacji | Utrata danych | Dane zostają w pliku | Osobny proces może zachować wpis |
| Utrata danych | Wymaga ponownego utworzenia | Wymaga odtworzenia bazy z kopii | Odczyt z bazy odbudowuje kopię |
| Awaria | Usługa niedostępna | Zapisy i listy nie działają | BYPASS; baza działa dalej |
| Główne ryzyko | Utrata stanu | Transakcje, schemat, blokady, backup | Nieaktualność, TTL, awarie połączenia |

Dla klienta zachowano metody, odpowiedzi i błędy, a zapis przestał znikać po restarcie. Wewnątrz doszły repozytoria, sesje, transakcje, pliki baz i opcjonalny cache. W porównaniu z monolitem mikrousługi pozwolą na niezależne wdrażanie, ale dołożą awarie sieci i problemy uzgadniania operacji między usługami. Obecna wspólna aplikacja pozostaje prostsza w uruchomieniu.

Migracja do PostgreSQL wymaga sterownika, adresów połączeń, schematu/migracji, sprawdzenia UUID i JSON, ograniczeń, transakcji, blokad i testów współbieżności. `BEGIN IMMEDIATE` jest właściwe dla SQLite; zapisy poza SQLite korzystają z blokad `SELECT ... FOR UPDATE`, ale wariant PostgreSQL nie został zweryfikowany. ORM sam nie gwarantuje zgodności. `create_all()` nie zastępuje migracji.

Nie ma jeszcze komunikacji HTTP między modułami, uwierzytelniania, zwalniania rezerwacji, obsługi przyjęć i wysyłek ani mechanizmu naprawy nieudanych unieważnień poza TTL. Dane odbiorców w cache wymagają ograniczenia dostępu do Redis; do laboratorium Redis jest lokalny. Wersja nie deklaruje gotowości produkcyjnej.

## Dokumentacja, autorzy i AI

| Oficjalny fragment dokumentacji | Problem | Zastosowanie | Weryfikacja |
|---|---|---|---|
| [SQLAlchemy 2.0 — sesje i transakcje](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block) | Zatwierdzenie albo wycofanie całej operacji | `sessions.begin()` w `write_transaction()`, osobne sesje, sprzątanie | Testy błędu po `flush()` dla korekty i utworzenia zamówienia, następny zapis działa |
| [SQLAlchemy 2.0 — SQLite](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html#foreign-key-support) | Konfiguracja połączeń i ograniczeń SQLite | `PRAGMA foreign_keys=ON`, świadome transakcje SQLite | Test dwóch instancji rezerwujących ten sam stan, kontrola schematu |
| [redis-py 6.4 — połączenia](https://redis.readthedocs.io/en/v6.4.0/connections.html#redis.Redis.from_url) | URL, timeouty i retry klienta | `Redis.from_url`, dekodowanie tekstu, timeouty 0,2 s i brak ponowień | Awaria GET/SET/DELETE w testach i zatrzymanie rzeczywistego serwera |

Oficjalne fragmenty przeczytano przed implementacją. Nie instalowano nieoficjalnych skilli SQLAlchemy. Izolację testów uzyskano przez parametry fabryki aplikacji, dwa adresy SQLite i wstrzyknięcie klienta cache; nie używamy globalnej sesji ani produkcyjnego Redis w testach.

Autorzy i numery albumów: **do osobistego uzupełnienia**. Podział pracy autorów: **do osobistego uzupełnienia**. Prefiks `UZUPELNIJ` należy zastąpić przed demonstracją na współdzielonym Redis.

AI: Codex pomógł przeanalizować instrukcję i LAB1, przygotować persystencję obu modułów, cache, testy, weryfikację HTTP i dokumentację. Weryfikacja objęła testy LAB1 przed zmianami, testy LAB2, sprawdzenie zależności, przegląd diff oraz uruchomienie prawdziwych procesów Redis i Uvicorna. Raport opisuje faktycznie wykonane eksperymenty. Użycie AI nie zastępuje samodzielnego wyjaśnienia transakcji, reguł i ograniczeń spójności przez autora projektu.

## Odbiór i wersja LAB2

Do rozmowy technicznej: pokaż zachowanie rezerwacji po restarcie z pustym cache, wskaż `sessions.begin()` i DEL po powrocie repozytorium, pokaż MISS/HIT/TTL/BYPASS, wyjaśnij stary HIT po nieudanym DELETE i wskaż test pomijania repozytorium. Przykładowa samodzielna zmiana na odbiorze: zmień TTL na 30 s i dostosuj oczekiwanie wygaśnięcia w teście z kontrolowanym zegarem. Wkładu ani samodzielnej modyfikacji studenta nie deklarujemy jako wykonanych przez AI.

Wersja LAB2 powinna zostać oznaczona commitem i tagiem `lab2`. W repozytorium pozostają kod, testy, konfiguracja, archiwalny opis LAB1 i raport; lokalne bazy, cache Pythona, `.venv` i sekrety są ignorowane. Nie nadpisuj istniejącego tagu. Wysyłka po oznaczeniu etapu:

```bash
git push origin HEAD
git push origin refs/tags/lab2
```

Przy odrzuceniu wysyłki zintegruj zmiany bez `--force`.
