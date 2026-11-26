# Prezentacja Outbound w Swagger UI

Wykonuj kroki po kolei. Główna prezentacja pokazuje tworzenie zamówienia, walidację, rezerwację przez Inventory, pakowanie i cache. Na końcu są dodatkowe pokazy awarii i odbioru zdarzenia wysyłki.

## 1. Zaloguj się na stronie Auth

Otwórz w przeglądarce:

```text
http://127.0.0.1:8080/docs/auth
```

1. Rozwiń **POST /auth/login**.
2. Kliknij **Try it out**.
3. Usuń przykładowy JSON i wklej:

```json
{
  "email": "lab6-admin@example.com",
  "password": "6c4eDpfVceIk5-d4sXml6xqDVuO7pgh0E7OhqMknfpQ"
}
```

4. Kliknij **Execute**.
5. Pod **Response body** znajdź `access_token`.
6. Skopiuj cały tekst z jego wartości, zaczynający się od `eyJ`. Bez cudzysłowów.

**Hasło służy tylko do logowania. Do przycisku Authorize wklejasz otrzymany access_token, a nie hasło.**

## 2. Przygotuj towar w Inventory

Otwórz w nowej karcie:

```text
http://127.0.0.1:8080/docs/inventory
```

1. Kliknij **Authorize** u góry strony.
2. Jeśli jest **Logout**, kliknij go.
3. Do pola **Value** wklej skopiowany access_token, bez słowa Bearer.
4. Kliknij **Authorize**, następnie **Close**.

Rozwiń **POST /receipts → Try it out**. Wklej:

```json
{
  "supplier": "Prezentacja Outbound",
  "items": [
    {
      "sku": "DEMO-OUTBOUND-001",
      "quantity_received": 100,
      "target_location": "A-01"
    }
  ]
}
```

Kliknij **Execute**. Oczekiwane: **201**, status **NEW**. Skopiuj `id` przyjęcia.

Następnie:

| Co rozwinąć | Co wpisać po Try it out | Wynik po Execute |
|---|---|---|
| **POST /receipts/{receipt_id}/accept** | W pole receipt_id wklej id przyjęcia | **200**, ACCEPTED |
| **POST /receipts/{receipt_id}/post** | To samo id przyjęcia | **200**, POSTED |
| **GET /stocks** | Bez parametrów | **200**, lista stanów |

Na liście znajdź towar z `sku: DEMO-OUTBOUND-001`. Zapisz jego **id stanu** — będzie potrzebne w polu `stock_id`.

Przy ponownej prezentacji użyj nowego SKU, np. DEMO-OUTBOUND-002, zarówno tutaj, jak i w zamówieniu. Dzięki temu ilości nie będą zależały od poprzednich testów.

## 3. Otwórz Outbound i ustaw autoryzację

Otwórz:

```text
http://127.0.0.1:8080/docs/outbound
```

Kliknij **Authorize → wklej access_token w Value → Authorize → Close**. Jeśli wcześniej wpisano inny tekst, najpierw kliknij **Logout**.

To osobna strona: autoryzację trzeba ustawić także tutaj. Napis Authorized oznacza, że Swagger zapamiętał tekst; poprawność tokenu sprawdzisz żądaniem.

Rozwiń **GET /orders → Try it out → Execute**.

Oczekiwane: **200** i lista zamówień, która może zawierać wcześniejsze testy. Jeśli jest **401**, wykonaj ponownie logowanie z kroku 1 i wstaw nowy access_token do Authorize na stronie Outbound. Token jest ważny 15 minut.

Powiedz prowadzącemu:

> Outbound zarządza zamówieniami, ich rezerwacją, pakowaniem i potwierdzaniem wysyłki. Ma własną bazę SQLite. Rezerwację wykonuje przez HTTP do Inventory, a zdarzenia wysyłki odbiera przez RabbitMQ.

## 4. Pokaż walidację błędnego zamówienia

Rozwiń **POST /orders → Try it out**. Wklej:

```json
{
  "recipient_name": "Jan Testowy",
  "recipient_phone": "123456789",
  "recipient_address": "Testowa 1",
  "items": [
    {
      "sku": "DEMO-OUTBOUND-001",
      "quantity": 0
    }
  ]
}
```

Kliknij **Execute**. Oczekiwane: **422**. Ilość musi być większa od zera; zamówienie nie zostaje utworzone.

## 5. Utwórz poprawne zamówienie

W tym samym formularzu zmień `quantity` z **0** na **2** i kliknij **Execute**.

Oczekiwane: **201**, status **NEW**. Skopiuj `id` zamówienia i zachowaj je do końca prezentacji.

Rozwiń **GET /orders/{order_id} → Try it out**. W pole **order_id** wklej id zamówienia i kliknij **Execute**.

Oczekiwane: **200**, status **NEW**. W **Response headers** pokaż `x-gateway: lab4` — żądanie przeszło przez Gateway.

## 6. Pokaż regułę biznesową: nie można pakować bez rezerwacji

Rozwiń **POST /orders/{order_id}/pack → Try it out**. Wklej id zamówienia w pole **order_id** i kliknij **Execute**.

Oczekiwane: **409**:

```json
{
  "detail": "Order must be reserved before packing"
}
```

Powiedz:

> Poprawny JSON nie wystarcza. Reguła biznesowa wymaga najpierw rezerwacji towaru.

## 7. Pokaż komunikację Outbound → Inventory

Rozwiń **GET /orders/stock-check/{stock_id} → Try it out**.

Do pola **stock_id** wklej **id stanu towaru z Inventory**, a nie id zamówienia. Kliknij **Execute**.

Oczekiwane: **200**, `available_quantity: 100`.

Powiedz:

> To endpoint Outbound, ale dane pobiera z Inventory. Outbound odnajduje Inventory przez Eurekę i wywołuje jego API przez HTTP. Nie czyta cudzej bazy.

## 8. Zarezerwuj zamówienie

Rozwiń **POST /orders/{order_id}/reserve → Try it out**. W pole **order_id** wklej id zamówienia i kliknij **Execute**.

Oczekiwane: **202**, status **RESERVATION_PENDING**. To zlecenie wysłane do RabbitMQ, jeszcze bez rezerwacji towaru. Ponowne kliknięcie zachowuje to samo `reservation_attempt_id`.

Teraz wykonaj dwa ręczne odbiory:

1. W Swaggerze Inventory: **POST /reservations/consume-events → Try it out → Execute**. Inventory odbiera zlecenie, wykonuje Allocation i publikuje wynik. Towar jest już zarezerwowany, ale Outbound nadal ma RESERVATION_PENDING.
2. W Swaggerze Outbound: **POST /orders/consume-reservation-events → Try it out → Execute**. Następnie **GET /orders/{order_id}** — oczekiwane **RESERVED**. Dopiero teraz wolno pakować.

Jeżeli zabraknie towaru, wynik zmieni status na **RESERVATION_REJECTED** i uzupełni `reservation_error`. Po uzupełnieniu towaru ponowne `/reserve` rozpoczyna nową próbę z innym ID. Błąd publikacji naprawiasz ręcznie: Outbound `/orders/{id}/sync-reservation`, Inventory `/reservations/sync-events`.

Powiedz:

> Outbound zleca rezerwację przez RabbitMQ. Inventory odbiera wiadomość i używa Allocation, a potem wysyła wynik. Outbound ustawia RESERVED dopiero po ręcznym odebraniu potwierdzenia. Ten sam identyfikator próby nie rezerwuje towaru drugi raz.

Wróć do **GET /orders/stock-check/{stock_id}** i kliknij **Execute**.

Oczekiwane: `available_quantity: 98`. Ilość fizyczna `quantity` nadal wynosi 100, a dwie sztuki są zarezerwowane.

Dodatkowo możesz ponownie wykonać **POST /orders/{order_id}/reserve** — odpowiedź **202** z istniejącym statusem RESERVED — a potem stock-check. Dostępność powinna nadal wynosić **98** — ponowienie nie tworzy drugiej rezerwacji.

## 9. Spakuj zamówienie i pokaż cache

Wróć do **POST /orders/{order_id}/pack** i kliknij **Execute**.

Oczekiwane: **200**, status **PACKED**.

Teraz wykonaj dwa razy **GET /orders/{order_id}**. Pomiędzy odczytami nie czekaj dłużej niż 60 sekund.

| Odczyt | Co pokazać |
|---|---|
| Pierwszy po pakowaniu | Status PACKED i nagłówek `x-cache: MISS` |
| Drugi | Status PACKED i nagłówek `x-cache: HIT` |

Nagłówki są pod odpowiedzią w **Response headers**.

Powiedz:

> Pakowanie zapisuje nowy status w bazie i usuwa starą kopię z Redis. Pierwszy odczyt pobiera dane z bazy, drugi korzysta z cache.

To koniec głównego pokazu Outbound. Dalsze kroki są dodatkowymi demonstracjami.

## 10. Dodatkowo: awaria Inventory

Tylko te dwa polecenia wykonujesz w terminalu. Żądania nadal wykonujesz w Swaggerze Outbound.

```bash
docker compose stop inventory
```

Na stronie Outbound:

| Endpoint | Wynik |
|---|---|
| **GET /orders/{order_id}** z id zamówienia | **200**, PACKED — Outbound działa i ma własne dane |
| **GET /orders/stock-check/{stock_id}** z id stanu | **503**, Inventory Service is unavailable |

Powiedz:

> Awaria Inventory nie wyłącza Outbound. Działa odczyt zamówienia, ale operacja potrzebująca danych Inventory zwraca kontrolowany błąd.

Przywróć Inventory:

```bash
docker compose start inventory
```

Odczekaj na powrót usługi i ponów stock-check. Oczekiwane: **200**. Jeśli rejestr jeszcze się odświeża, ponów po chwili.

## 11. Dodatkowo: Outbound odbiera zdarzenie wysyłki

Zamówienie musi nadal mieć **PACKED**. Najpierw przygotuj zdarzenie na stronie Shipping:

```text
http://127.0.0.1:8080/docs/shipping
```

Kliknij **Authorize**, wklej ważny **access_token**, kliknij **Authorize → Close**.

Rozwiń **POST /shipments → Try it out**. Wklej JSON, zastępując ID_ZAMOWIENIA rzeczywistym id prezentowanego zamówienia:

```json
{
  "order_id": "ID_ZAMOWIENIA",
  "destination_address": "Warszawa, Testowa 1",
  "courier_id": "22222222-2222-4222-8222-222222222222",
  "weight_kg": 1.5
}
```

Kliknij **Execute**. Oczekiwane: **201**, NEW. Skopiuj `id` przesyłki.

| Endpoint w Shipping | Co wpisać | Wynik |
|---|---|---|
| **POST /shipments/{shipment_id}/ready** | Id przesyłki | **200**, READY |
| **POST /shipments/{shipment_id}/hand-over** | To samo id przesyłki | **200**, HANDED_OVER; publikacja zdarzenia do RabbitMQ |

Wróć do Swaggera **Outbound**:

1. Wykonaj **GET /orders/{order_id}** — zamówienie nadal ma **PACKED**.
2. Wykonaj **POST /orders/consume-events → Try it out → Execute**. Pozostaw `limit: 50`.
3. Odpowiedź pokazuje odebrane wiadomości i liczbę unikalnie przetworzonych zdarzeń. W izolowanym przebiegu obie wartości wynoszą 1.
4. Wykonaj **GET /orders/{order_id}** — teraz ma **SHIPPED**, a pierwszy odczyt po odbiorze ma `x-cache: MISS`.
5. Ponownie wykonaj **POST /orders/consume-events** — jeśli nie ma innych wiadomości, obie wartości wynoszą 0.

Powiedz:

> Shipping opublikował zdarzenie, a Outbound odebrał je przez RabbitMQ i zmienił własne zamówienie na SHIPPED. Odbiór uruchamiamy ręcznie. Deduplikacja zdarzeń jest zapisywana w bazie.
