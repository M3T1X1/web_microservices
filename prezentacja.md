# Najprostsze komendy Postman CLI

Wpisuj komendy pojedynczo w terminalu. Nie wklejaj wszystkich naraz.

W miejsce TOKEN wklej access_token otrzymany po logowaniu. W miejsca ID_PRZYJECIA, ID_STANU, ID_ZAMOWIENIA i ID_PRZESYLKI wklej odpowiednie id z odpowiedzi. Zachowaj cudzysłowy.

## 1. Uruchom projekt

```bash
docker compose up -d
```

```bash
postman request GET http://127.0.0.1:8080/health --no-report-events
```

Oczekiwane: 200 i status ok. Jeśli system dopiero startuje, odczekaj chwilę.

## 2. Auth — zaloguj się

Komenda zawiera email i hasło testowego administratora. Skopiuj ją w całości i uruchom.

```bash
postman request POST http://127.0.0.1:8080/api/auth/login -H "Content-Type:application/json" --body '{"email":"lab6-admin@example.com","password":"6c4eDpfVceIk5-d4sXml6xqDVuO7pgh0E7OhqMknfpQ"}' --no-report-events --response-only
```

Komenda wyświetli sam JSON bez ramki. Skopiuj wyłącznie wartość access_token: bez otaczających cudzysłowów, spacji i znaków │. Token musi być jednym ciągiem. Wklej go w miejsce TOKEN, pozostawiając cudzysłowy w kolejnej komendzie.

```bash
postman request GET http://127.0.0.1:8080/api/auth/me --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i rola ADMIN. Jeśli token wygaśnie po 15 minutach, zaloguj się ponownie.

## 3. Inventory — dodaj towar

```bash
postman request POST http://127.0.0.1:8080/api/receipts --auth-bearer-token "TOKEN" -H "Content-Type:application/json" --body '{"supplier":"Test","items":[{"sku":"DEMO-001","quantity_received":10,"target_location":"A-01"}]}' --no-report-events
```

Oczekiwane: 201 i NEW. Skopiuj id przyjęcia jako ID_PRZYJECIA.

```bash
postman request POST http://127.0.0.1:8080/api/receipts/ID_PRZYJECIA/accept --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i ACCEPTED.

```bash
postman request POST http://127.0.0.1:8080/api/receipts/ID_PRZYJECIA/post --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i POSTED.

```bash
postman request GET http://127.0.0.1:8080/api/stocks --auth-bearer-token "TOKEN" --no-report-events
```

Znajdź towar DEMO-001 i skopiuj jego id jako ID_STANU. Przy pierwszym przebiegu powinien mieć ilość 10. Przy powtarzaniu prezentacji użyj nowego SKU, np. DEMO-002, zarówno w przyjęciu, jak i zamówieniu.

```bash
postman request GET http://127.0.0.1:8080/api/stocks/ID_STANU --auth-bearer-token "TOKEN" --script-post-request 'console.log("Cache:",pm.response.headers.get("X-Cache"));' --no-report-events
```

Wykonaj tę samą komendę dwa razy. Pierwszy odczyt nowego rekordu: MISS. Drugi: HIT. Nie czekaj dłużej niż 60 sekund pomiędzy odczytami.

## 4. Inventory — pokaż błędną ilość

```bash
postman request PATCH http://127.0.0.1:8080/api/stocks/ID_STANU --auth-bearer-token "TOKEN" -H "Content-Type:application/json" --body '{"quantity":-1}' --no-report-events
```

Oczekiwane: 422. Nie można ustawić ujemnej ilości.

## 5. Outbound — sprawdź komunikację z Inventory

```bash
postman request GET http://127.0.0.1:8080/api/orders/stock-check/ID_STANU --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i available_quantity 10. Outbound pobiera dane z Inventory przez HTTP, korzystając z Eureki.

## 6. Outbound — utwórz zamówienie

```bash
postman request POST http://127.0.0.1:8080/api/orders --auth-bearer-token "TOKEN" -H "Content-Type:application/json" --body '{"recipient_name":"Jan Testowy","recipient_phone":"123456789","recipient_address":"Testowa 1","items":[{"sku":"DEMO-001","quantity":2}]}' --no-report-events
```

Oczekiwane: 201 i NEW. Skopiuj id jako ID_ZAMOWIENIA.

Spróbuj spakować przed rezerwacją:

```bash
postman request POST http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA/pack --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 409. Najpierw trzeba zarezerwować towar.

```bash
postman request POST http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA/reserve --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i RESERVED. Outbound rezerwuje towar w Inventory.

```bash
postman request POST http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA/pack --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i PACKED.

## 7. Shipping — utwórz przesyłkę

```bash
postman request POST http://127.0.0.1:8080/api/shipments --auth-bearer-token "TOKEN" -H "Content-Type:application/json" --body '{"order_id":"ID_ZAMOWIENIA","carrier":"Kurier","weight_kg":1.5}' --no-report-events
```

Oczekiwane: 201 i NEW. Shipping sprawdza w Outbound, czy zamówienie jest spakowane. Skopiuj id przesyłki jako ID_PRZESYLKI.

```bash
postman request POST http://127.0.0.1:8080/api/shipments/ID_PRZESYLKI/ready --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i READY.

```bash
postman request POST http://127.0.0.1:8080/api/shipments/ID_PRZESYLKI/hand-over --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 200 i HANDED_OVER. Shipping publikuje zdarzenie do RabbitMQ.

## 8. RabbitMQ — pokaż zmianę zamówienia

```bash
postman request GET http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA --auth-bearer-token "TOKEN" --no-report-events
```

Zamówienie nadal ma PACKED. Wiadomość czeka na odebranie.

```bash
postman request POST http://127.0.0.1:8080/api/orders/consume-events --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: odebranie zdarzenia.

```bash
postman request GET http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA --auth-bearer-token "TOKEN" --no-report-events
```

Zamówienie ma teraz SHIPPED.

## 9. Auth — pokaż brak dostępu bez tokenu

```bash
postman request GET http://127.0.0.1:8080/api/stocks --no-report-events
```

Oczekiwane: 401.

## 10. Awaria Inventory — inne usługi nadal działają

```bash
docker compose stop inventory
```

```bash
postman request GET http://127.0.0.1:8080/api/orders/ID_ZAMOWIENIA --auth-bearer-token "TOKEN" --no-report-events
```

Odczyt zamówienia działa: 200.

```bash
postman request GET http://127.0.0.1:8080/api/auth/me --auth-bearer-token "TOKEN" --no-report-events
```

Auth działa: 200.

```bash
postman request GET http://127.0.0.1:8080/api/orders/stock-check/ID_STANU --auth-bearer-token "TOKEN" --no-report-events
```

Oczekiwane: 503. Outbound działa, ale nie może połączyć się z Inventory.

Przywróć usługę:

```bash
docker compose start inventory
```

Po chwili ponów stock-check. Powinien zwrócić 200.
