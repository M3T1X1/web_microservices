'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const uuid = () => crypto.randomUUID();
  const json = value => JSON.stringify(value, null, 2);
  const state = {view:'scenarios', token:'', user:null, history:[], resources:{stocks:[],receipts:[],orders:[],shipments:[]},
    selected:{}, operation:null, collection:'stocks', busy:false, running:false, checks:[], status:{}};
  const labels = {inventory:'Inventory',outbound:'Outbound',shipping:'Shipping',auth:'Auth',scenarios:'Scenariusze'};
  const titles = {inventory:'Stany i przyjęcia towaru.',outbound:'Zamówienia i rezerwacje.',shipping:'Przesyłki i wydanie kurierowi.',auth:'Tożsamość i uprawnienia.',scenarios:'Sprawdź cały proces.'};
  const subtitles = {inventory:'Przyjmij towar, odbierz rezerwacje, a po wydaniu przesyłki ręcznie rozlicz towar.',outbound:'Zleć rezerwację → odbierz wynik → RESERVED → PACKED → SHIPPED. Po odmowie możesz ponowić próbę.',shipping:'NEW → READY → HANDED_OVER. Wysyłka wymaga spakowanego zamówienia.',auth:'Zaloguj się, utwórz konto lub zmień jego aktywność. Testuj dostęp różnymi rolami.',scenarios:'Poprawna ścieżka i warunki brzegowe. Rzeczywiste żądania, widoczne odpowiedzi.'};
  const collectionNames = {stocks:'Stany magazynowe',receipts:'Przyjęcia',orders:'Zamówienia',shipments:'Przesyłki'};
  const collections = {inventory:['stocks','receipts'],outbound:['orders'],shipping:['shipments'],auth:[]};
  const op = (label, method, path, body=null, expected='', hint='') => ({label,method,path,body,expected,hint});
  const catalog = {
    inventory:[
      op('Lista stanów','GET','/api/stocks',null,200),
      op('Szczegóły stanu / cache','GET','/api/stocks/{stock_id}',null,200),
      op('Zmień ilość','PATCH','/api/stocks/{stock_id}',()=>({quantity:10}),200,'Mniej niż zarezerwowano → 409'),
      op('Rezerwuj stan','POST','/api/stocks/{stock_id}/reservations',()=>({order_id:state.selected.order_id||uuid(),quantity:1}),201,'Ponad dostępność → 409; ilość ≤ 0 → 422'),
      op('Odbierz zlecenia rezerwacji','POST','/api/reservations/consume-events?limit=50',null,200,'Wykonuje Allocation i publikuje sukces albo odmowę. Potem odbierz wynik w Outbound.'),
      op('Ponów wysłanie wyników rezerwacji','POST','/api/reservations/sync-events?limit=50',null,200,'Ręczna naprawa publikacji po awarii RabbitMQ; nie rezerwuje drugi raz.'),
      op('Odbierz wydania przesyłek','POST','/api/reservations/consume-shipment-events?limit=50',null,200,'Odejmuje wydane sztuki i usuwa ich aktywne rezerwacje. Osobna kolejka; Outbound odbiera swoją kopię zdarzenia.'),
      op('Lista przyjęć','GET','/api/receipts',null,200),
      op('Utwórz przyjęcie','POST','/api/receipts',()=>({supplier:'Panel testowy',items:[{sku:'UI-'+uuid().slice(0,8),quantity_received:10,target_location:'UI-TEST'}]}),201),
      op('Szczegóły przyjęcia','GET','/api/receipts/{receipt_id}',null,200),
      op('Zatwierdź przyjęcie','POST','/api/receipts/{receipt_id}/accept',null,200,'Tylko NEW'),
      op('Zaksięguj przyjęcie','POST','/api/receipts/{receipt_id}/post',null,200,'Tylko ACCEPTED; NEW → 409'),
      op('Anuluj przyjęcie','POST','/api/receipts/{receipt_id}/cancel',null,200,'Tylko NEW lub ACCEPTED')
    ],
    outbound:[
      op('Lista zamówień','GET','/api/orders',null,200),
      op('Utwórz zamówienie','POST','/api/orders',()=>({recipient_name:'Test panelu',recipient_phone:'123456789',recipient_address:'Warszawa, Testowa 1',items:[{sku:state.selected.sku||'SKU-001',quantity:1}]}),201),
      op('Szczegóły / cache','GET','/api/orders/{order_id}',null,200),
      op('Rezerwuj produkty','POST','/api/orders/{order_id}/reserve',null,202,'NEW / RESERVATION_REJECTED → RESERVATION_PENDING. Odbierz zlecenie w Inventory, potem wynik tutaj. Ponowienie PENDING zachowuje ID próby.'),
      op('Ponów wysłanie zlecenia rezerwacji','POST','/api/orders/{order_id}/sync-reservation',null,200,'Po awarii RabbitMQ wyślij tę samą zapisaną próbę. Nową próbę po odmowie rozpoczyna Rezerwuj produkty.'),
      op('Odbierz wyniki rezerwacji','POST','/api/orders/consume-reservation-events?limit=50',null,200,'Wynik Inventory zmieni status na RESERVED lub RESERVATION_REJECTED. WAREHOUSE / ADMIN.'),
      op('Spakuj zamówienie','POST','/api/orders/{order_id}/pack',null,200,'Tylko RESERVED; NEW → 409'),
      op('Sprawdź stan magazynowy','GET','/api/orders/stock-check/{stock_id}',null,200),
      op('Sprawdź gotowość wysyłki','GET','/api/orders/{order_id}/shipping-check',null,200,'Odczyt dla SHIPPING / ADMIN'),
      op('Odbierz potwierdzenia wydania przesyłek','POST','/api/orders/consume-events?limit=50',null,200,'Ustawia SHIPPED. Stan towaru rozliczysz osobno w Inventory: Odbierz wydania przesyłek.'),
      op('Potwierdź wysyłkę ręcznie','POST','/api/orders/{order_id}/dispatch',()=>({shipment_id:state.selected.shipment_id||uuid()}),200,'SHIPPING / ADMIN; zamówienie musi być PACKED')
    ],
    shipping:[
      op('Lista przesyłek','GET','/api/shipments',null,200),
      op('Utwórz przesyłkę','POST','/api/shipments',()=>({order_id:state.selected.order_id||'',destination_address:state.selected.address||'Warszawa, Testowa 1',courier_id:uuid(),weight_kg:2.5}),201,'Zamówienie PACKED; jedna przesyłka na zamówienie'),
      op('Szczegóły przesyłki','GET','/api/shipments/{shipment_id}',null,200),
      op('Przygotuj do wydania','POST','/api/shipments/{shipment_id}/ready',null,200,'Tylko NEW, zamówienie PACKED'),
      op('Wydaj kurierowi','POST','/api/shipments/{shipment_id}/hand-over',null,200,'Tylko READY; NEW → 409'),
      op('Anuluj przesyłkę','POST','/api/shipments/{shipment_id}/cancel',null,200,'Tylko NEW lub READY'),
      op('Ponów publikację zdarzenia','POST','/api/shipments/{shipment_id}/sync',null,200,'Tylko HANDED_OVER; ponowienie jest idempotentne')
    ],
    auth:[
      op('Bieżący użytkownik','GET','/api/auth/me',null,200),
      op('Logowanie / zmiana konta','POST','/api/auth/login',()=>({email:'',password:''}),200,'Poprawna odpowiedź ustawi token sesji'),
      op('Utwórz użytkownika','POST','/api/auth/users',()=>({email:'test-'+uuid().slice(0,8)+'@example.com',password:'',role:'WAREHOUSE'}),201,'ADMIN; role: ADMIN, WAREHOUSE, SHIPPING; hasło 12–128 znaków'),
      op('Zmień aktywność konta','PATCH','/api/auth/users/{user_id}',()=>({is_active:false}),200,'ADMIN; tokeny są ważne do wygaśnięcia'),
      op('Sprawdź API Gateway','GET','/health',null,200)
    ]
  };
  const scenarioDefinitions = [
    {id:'flow',kicker:'OUTBOUND + SHIPPING + RABBITMQ',title:'Najpierw zarezerwuj, potem wyślij',description:'Pakowanie bez rezerwacji, przesyłka bez spakowania i wydanie przed READY muszą zwrócić 409. Potem pełny poprawny proces.',tag:'ADMIN · nowy towar i zamówienie'},
    {id:'stock',kicker:'INVENTORY',title:'Towaru nie może zabraknąć',description:'Rezerwacja ponad stan, zmniejszenie ilości poniżej rezerwacji i nieprawidłowe ilości. Sprawdź też dostępność równą zero.',tag:'WAREHOUSE / ADMIN · nowy towar'},
    {id:'receipt',kicker:'INVENTORY / PRZYJĘCIA',title:'Zatwierdź przed zaksięgowaniem',description:'Księgowanie NEW, podwójne księgowanie i anulowanie przyjęcia POSTED. Tylko poprawne księgowanie zwiększa stan.',tag:'WAREHOUSE / ADMIN · nowe przyjęcie'},
    {id:'auth',kicker:'AUTH + OCHRONA API',title:'Sprawdź granice dostępu',description:'Brak tokenu, niepoprawny token i błędne dane logowania. Jeśli jesteś zalogowany, sprawdź też dostęp swojej roli.',tag:'Dowolna rola · bez nowych danych'}
  ];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }
  function notify(message, error=false) {
    $('notice').textContent = message; $('notice').hidden = !message;
    $('notice').className = 'notice'+(error?' error':'');
  }
  function redact(value) {
    if (Array.isArray(value)) return value.map(redact);
    if (value && typeof value==='object') {
      const sensitiveInput=Array.isArray(value.loc) && value.loc.some(part=>/password|token|secret/i.test(String(part)));
      return Object.fromEntries(Object.entries(value).map(([key,item]) => [key,/password|token|authorization|secret/i.test(key) || (key==='input' && sensitiveInput)?'[ukryto]':redact(item)]));
    }
    return value;
  }
  function displayEntry(entry) {
    $('response-meta').textContent = `${entry.method} ${entry.path} · HTTP ${entry.status || 'błąd połączenia'} · ${entry.ms} ms${entry.cache?' · X-Cache: '+entry.cache:''}`;
    $('response-body').textContent = json({żądanie:entry.request,odpowiedź:entry.response,...(entry.expected?{oczekiwany_HTTP:entry.expected,zgodny:entry.status===entry.expected}:{})});
    for (const node of $('history').children) node.classList.toggle('selected',node.dataset.entry===String(entry.id));
  }
  function logEntry(entry) {
    entry.id = (state.history[0]?.id||0)+1;
    state.history.unshift(entry); state.history = state.history.slice(0,100);
    renderHistory(); displayEntry(entry);
  }
  function renderHistory() {
    $('history').replaceChildren();
    if (!state.history.length) {$('history').append(element('p','empty','Tu pojawią się wykonane żądania.'));return;}
    for (const entry of state.history) {
      const node = element('button','history-entry'); node.dataset.entry=entry.id;
      const okay = entry.expected ? entry.status===entry.expected : entry.status>=200 && entry.status<300;
      node.append(element('span','history-time',entry.time+' · '+entry.ms+' ms'),element('span','badge '+(okay?'ok':'error'),entry.status||'BŁĄD'),element('span','history-path',entry.method+' '+entry.path));
      node.addEventListener('click',()=>displayEntry(entry)); $('history').append(node);
    }
  }
  async function request(method,path,body=null,{expected=null,auth=true,token=state.token,record=true}={}) {
    // Keep editable requests on this Gateway; never send the session token to another origin.
    if (!(path==='/health' || /^\/api\/[a-z][a-z0-9\-/?=&%._{}]*$/i.test(path)) || path.includes('..')) throw new Error('Użyj ścieżki /api/… lub /health na tym Gateway.');
    const headers = {};
    if (auth && token && path!=='/api/auth/login') headers.Authorization='Bearer '+token;
    if (body!==null && method!=='GET') headers['Content-Type']='application/json';
    const started = performance.now();
    let status=0, data, cache='';
    try {
      const response = await fetch(path,{method,headers,body:body!==null && method!=='GET'?json(body):undefined,signal:AbortSignal.timeout(18000),cache:'no-store',redirect:'error'});
      status=response.status; cache=response.headers.get('X-Cache')||'';
      const raw = await response.text();
      try {data=raw?JSON.parse(raw):null;} catch {data=raw;}
    } catch (error) {data={detail:error.name==='TimeoutError'?'Przekroczono czas oczekiwania (18 s). Stan operacji mógł zostać zapisany; sprawdź dane przed ponowieniem.':error.message};}
    const result={status,data,cache};
    if (record) logEntry({method,path,status,ms:Math.round(performance.now()-started),time:new Date().toLocaleTimeString('pl-PL'),request:redact(method==='GET'?null:body),response:redact(data),cache,expected:Number(expected)||null});
    return result;
  }
  function updateSession() {
    $('session-status').textContent = state.token ? (state.user?`${state.user.email} · ${state.user.role}`:'Token sesji aktywny'):'Brak sesji';
    $('session-open').textContent=state.token?'Zmień konto':'Zaloguj się';
    $('logout').hidden=!state.token;
  }
  async function acceptLogin(result) {
    if (result.status===200 && result.data?.access_token) {
      state.token=result.data.access_token; state.user=null;
      const me=await request('GET','/api/auth/me',null,{record:false});
      if (me.status===200) state.user=me.data;
      updateSession(); await refreshData(); return true;
    }
    return false;
  }
  function renderCards() {
    $('service-cards').replaceChildren();
    for (const [service,collection] of [['inventory','stocks'],['outbound','orders'],['shipping','shipments'],['auth',null]]) {
      const result=state.status[service];
      const card=element('article','service-card'), head=element('div','service-card-head');
      head.append(element('h2','',labels[service]),element('span','badge '+(result?.status===200?'ok':result?'expected':''),result?'HTTP '+(result.status||'—'):'Nie sprawdzono'));
      const value=result?.status===200?(collection?`${state.resources[collection].length} rekordów`:(result.data?.role||'Połączono')):(result?.status===401?'Wymaga logowania':result?.status===403?'Brak uprawnień':result?'Niedostępne':'Oczekuje');
      card.append(head,element('div','service-value',value),element('p','',service==='auth'?'Tożsamość i role':service==='inventory'?'Stany magazynowe':service==='outbound'?'Zamówienia':'Przesyłki'));
      $('service-cards').append(card);
    }
  }
  async function refreshData() {
    $('refresh').disabled=true;
    const sources=[['inventory','stocks'],['inventory','receipts'],['outbound','orders'],['shipping','shipments'],['auth',null]];
    const results=await Promise.allSettled(sources.map(async ([service,collection])=> {
      const result=await request('GET',collection?'/api/'+collection:'/api/auth/me',null,{record:false});
      if (collection) state.resources[collection]=result.status===200 && Array.isArray(result.data)?result.data:[];
      if (collection!=='receipts') state.status[service]=result;
      if (service==='auth') {state.user=result.status===200?result.data:null;updateSession();}
    }));
    if(results.some(result=>result.status==='rejected')) notify('Nie udało się odświeżyć wszystkich danych.',true);
    renderCards(); renderData(); $('refresh').disabled=false;
  }
  function remember(collection,row) {
    const key={stocks:'stock_id',receipts:'receipt_id',orders:'order_id',shipments:'shipment_id'}[collection];
    if (key) state.selected[key]=row.id;
    if (row.order_id) state.selected.order_id=row.order_id;
    if (row.sku) state.selected.sku=row.sku;
    if (row.recipient_address||row.destination_address) state.selected.address=row.recipient_address||row.destination_address;
  }
  function columnsFor(collection) {
    if(collection==='stocks') return [['sku','SKU'],['location','Lokalizacja'],['quantity','Stan'],['reserved','Rezerwacja'],['available','Dostępne'],['id','ID']];
    if(collection==='receipts') return [['supplier','Dostawca'],['status','Status'],['items','Pozycje'],['id','ID']];
    if(collection==='orders') return [['recipient_name','Odbiorca'],['status','Status'],['reservation_error','Powód odmowy'],['reservation_notification_pending','Zlecenie oczekuje wysłania'],['reservation_attempt_id','ID próby'],['recipient_address','Adres'],['items','Pozycje'],['id','ID']];
    return [['status','Status'],['destination_address','Adres docelowy'],['courier_id','ID kuriera'],['weight_kg','Waga kg'],['notification_pending','Publikacja oczekuje'],['order_id','ID zamówienia'],['id','ID']];
  }
  function renderData() {
    $('data-tabs').replaceChildren(); $('data-content').replaceChildren();
    if(state.view==='scenarios')return;
    const options=collections[state.view];
    for (const collection of options) {
      const button=element('button','button small'+(collection===state.collection?' active':''),collectionNames[collection]);
      button.addEventListener('click',()=>{state.collection=collection;renderData();}); $('data-tabs').append(button);
    }
    if(!options.length) {
      $('data-title').textContent='Bieżący użytkownik';
      $('data-content').append(element('pre','empty mono',state.user?json(state.user):'Zaloguj się, aby pobrać dane konta. API nie udostępnia listy użytkowników. ID nowego konta znajdziesz w odpowiedzi POST /api/auth/users.'));
      return;
    }
    $('data-title').textContent=collectionNames[state.collection];
    const rows=state.resources[state.collection];
    if(!rows.length) {$('data-content').append(element('p','empty','Brak rekordów lub brak uprawnień do ich odczytu. Sprawdź status mikroserwisu powyżej.'));return;}
    const table=element('table'),thead=element('thead'),head=element('tr'),tbody=element('tbody');
    const columns=columnsFor(state.collection);
    for(const [,title] of columns)head.append(element('th','',title));
    thead.append(head);table.append(thead,tbody);
    const selectedKey={stocks:'stock_id',receipts:'receipt_id',orders:'order_id',shipments:'shipment_id'}[state.collection];
    for (const row of rows) {
      const tr=element('tr',state.selected[selectedKey]===row.id?'selected-row':'');tr.dataset.id=row.id;tr.tabIndex=0;tr.setAttribute('aria-label','Wybierz rekord '+row.id);
      const reserved=row.reservations?.reduce((total,item)=>total+item.quantity,0)||0;
      for(const [key] of columns) {
        let value=key==='reserved'?reserved:key==='available'?row.quantity-reserved:row[key];
        if(key==='items') value=row.items.map(item=>`${item.sku} × ${item.quantity??item.quantity_received}`).join(', ');
        if(typeof value==='boolean')value=value?'Tak':'Nie';
        const td=element('td',/id$/.test(key)?'mono':'');
        if(key==='status')td.append(element('span','badge',value));else td.textContent=String(value??'—');
        td.title=String(value??'');tr.append(td);
      }
      const select=()=>{remember(state.collection,row);if(state.operation)selectOperation(state.operation);renderData();notify('Wybrano rekord '+row.id+'. Identyfikatory w edytorze zostały uzupełnione.');};
      tr.addEventListener('click',select);tr.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select();}});tbody.append(tr);
    }
    $('data-content').append(table);
  }
  function selectOperation(operation) {
    state.operation=operation; $('operation-title').textContent=operation.label; $('operation-hint').textContent=operation.hint;
    $('request-method').value=operation.method; $('expected-status').value=String(operation.expected||'');
    const body=typeof operation.body==='function'?operation.body():operation.body;
    $('request-body').value=body===null?'':json(body);
    // Password editing is masked in login dialog; the raw JSON editor is deliberate.
    $('request-body').placeholder=operation.method==='GET'?'GET nie wysyła treści.':'Brak treści. Możesz wpisać własny JSON.';
    $('context-fields').replaceChildren();
    const fields=[...operation.path.matchAll(/\{([^}]+)\}/g)].map(match=>match[1]);
    const updatePath=()=>{$('request-path').value=operation.path.replace(/\{([^}]+)\}/g,(match,key)=>$('field-'+key)?.value||match);};
    for(const field of fields) {
      const label=element('label','',field);const input=element('input');input.id='field-'+field;input.value=state.selected[field]||'';input.placeholder='Wpisz UUID lub wybierz rekord';input.spellcheck=false;
      input.addEventListener('input',()=>{state.selected[field]=input.value;updatePath();});label.append(input);$('context-fields').append(label);
    }
    updatePath();
    for (const button of $('operations').children)button.classList.toggle('selected',button.dataset.operation===operation.label);
  }
  function showView(view) {
    state.view=view; $('breadcrumb').textContent=labels[view];$('page-title').textContent=titles[view];$('page-subtitle').textContent=subtitles[view];
    $('scenarios-view').hidden=view!=='scenarios';$('service-view').hidden=view==='scenarios';
    for(const button of document.querySelectorAll('[data-view]'))button.classList.toggle('active',button.dataset.view===view);
    if(view==='scenarios')return;
    state.collection=collections[view][0]||'';$('required-role').textContent=view==='inventory'?'WAREHOUSE / ADMIN':view==='shipping'?'SHIPPING / ADMIN':view==='outbound'?'Rola zależy od operacji':'ADMIN zarządza kontami';
    $('operations').replaceChildren();
    for(const operation of catalog[view]) {
      const button=element('button','operation');button.dataset.operation=operation.label;
      button.append(element('span','method '+operation.method,operation.method),element('span','',operation.label));
      button.addEventListener('click',()=>selectOperation(operation));$('operations').append(button);
    }
    selectOperation(catalog[view][0]);renderData();
  }

  async function executeManual(event) {
    event.preventDefault(); if(state.busy||state.running)return;
    let body=null;
    try {if($('request-body').value.trim())body=JSON.parse($('request-body').value);}catch(error){notify('Nieprawidłowy JSON: '+error.message,true);return;}
    const path=$('request-path').value.trim();
    if(path.includes('{')){notify('Uzupełnij identyfikator w ścieżce żądania.',true);return;}
    state.busy=true;$('send-request').disabled=true;notify('');
    try {
      const method=$('request-method').value;
      const result=await request(method,path,body,{expected:$('expected-status').value,auth:$('send-auth').checked});
      if(path==='/api/auth/login')await acceptLogin(result);
      const match=path.match(/^\/api\/(stocks|receipts|orders|shipments)(?:\?|\/|$)/);
      if(result.status>=200 && result.status<300 && result.data?.id && match)remember(match[1],result.data);
      if(result.status>=200 && result.status<300 && path==='/api/auth/users' && result.data?.id)state.selected.user_id=result.data.id;
      const expected=Number($('expected-status').value);
      notify(expected?(result.status===expected?`Zgodnie z oczekiwaniem: HTTP ${result.status}.`:`Oczekiwano HTTP ${expected}, otrzymano ${result.status||'błąd połączenia'}.`):`Odpowiedź: HTTP ${result.status||'błąd połączenia'}.`,expected?result.status!==expected:result.status<200||result.status>=300);
      await refreshData();
    }catch(error){notify(error.message,true);}finally{state.busy=false;$('send-request').disabled=false;}
  }

  function renderChecks() {
    $('scenario-results').replaceChildren();
    for(const check of state.checks){const row=element('tr');row.append(element('td','',check.label),element('td','mono',check.expected),element('td','mono',check.actual));const status=element('td');status.append(element('span','badge '+(check.ok?'ok':'error'),check.ok?'PASS':'FAIL'));row.append(status);$('scenario-results').append(row);}
  }
  async function step(label,method,path,body,expected,options={}) {
    const result=await request(method,path,body,{...options,expected});
    const okay=result.status===expected;
    state.checks.push({label,expected:'HTTP '+expected,actual:'HTTP '+(result.status||'—'),ok:okay});renderChecks();
    if(!okay)throw new Error(`${label}: oczekiwano HTTP ${expected}, otrzymano ${result.status||'błąd połączenia'}. Szczegóły w dzienniku.`);
    return result.data;
  }
  function check(label,okay,actual) {
    state.checks.push({label,expected:'Warunek spełniony',actual:actual||String(okay),ok:Boolean(okay)});renderChecks();
    if(!okay)throw new Error(label+': wynik niezgodny z oczekiwaniem.');
  }
  async function supply({edges=false}={}) {
    const sku='UI-'+uuid();
    const receipt=await step('Nowe przyjęcie','POST','/api/receipts',{supplier:'Panel testowy',items:[{sku,quantity_received:10,target_location:'UI-TEST'}]},201);
    remember('receipts',receipt);
    const path='/api/receipts/'+receipt.id;
    if(edges)await step('Księgowanie NEW bez zatwierdzenia','POST',path+'/post',null,409);
    await step('Zatwierdzenie przyjęcia','POST',path+'/accept',null,200);
    await step('Księgowanie ACCEPTED','POST',path+'/post',null,200);
    if(edges){
      await step('Ponowne księgowanie POSTED','POST',path+'/post',null,409);
      await step('Anulowanie POSTED','POST',path+'/cancel',null,409);
    }
    const stocks=await step('Odczyt przyjętego towaru','GET','/api/stocks',null,200);
    const stock=stocks.find(item=>item.sku===sku);
    check('Stan po przyjęciu: dokładnie 10 sztuk',stock?.quantity===10,String(stock?.quantity));
    remember('stocks',stock);return stock;
  }
  async function flowScenario() {
    const stock=await supply();
    const body={recipient_name:'Test panelu',recipient_phone:'123456789',recipient_address:'Warszawa, Testowa 1',items:[{sku:stock.sku,quantity:2}]};
    const order=await step('Nowe zamówienie','POST','/api/orders',body,201);remember('orders',order);
    const orderPath='/api/orders/'+order.id;
    const shipmentBody={order_id:order.id,destination_address:body.recipient_address,courier_id:uuid(),weight_kg:2.5};
    await step('Pakowanie NEW bez rezerwacji','POST',orderPath+'/pack',null,409);
    await step('Przesyłka dla niespakowanego zamówienia','POST','/api/shipments',shipmentBody,409);
    const pending=await step('Zlecenie rezerwacji przez RabbitMQ','POST',orderPath+'/reserve',null,202);
    check('Zamówienie oczekuje na wynik',pending.status==='RESERVATION_PENDING',pending.status);
    const repeated=await step('Ponowienie oczekującej próby','POST',orderPath+'/reserve',null,202);
    check('Ponowienie zachowuje ID próby',repeated.reservation_attempt_id===pending.reservation_attempt_id,repeated.reservation_attempt_id);
    await step('Pakowanie przed potwierdzeniem rezerwacji','POST',orderPath+'/pack',null,409);
    await step('Inventory odbiera zlecenia i wykonuje Allocation','POST','/api/reservations/consume-events?limit=500',null,200);
    const waiting=await step('Outbound przed odbiorem wyniku','GET',orderPath,null,200);
    check('Status nadal oczekujący',waiting.status==='RESERVATION_PENDING',waiting.status);
    await step('Outbound odbiera wyniki rezerwacji','POST','/api/orders/consume-reservation-events?limit=500',null,200);
    const reserved=await step('Odczyt potwierdzonego zamówienia','GET',orderPath,null,200);
    check('Zamówienie ma status RESERVED',reserved.status==='RESERVED',reserved.status);
    await step('Pakowanie zarezerwowanego zamówienia','POST',orderPath+'/pack',null,200);
    const shipment=await step('Utworzenie przesyłki dla PACKED','POST','/api/shipments',shipmentBody,201);remember('shipments',shipment);
    await step('Druga przesyłka dla tego samego zamówienia','POST','/api/shipments',shipmentBody,409);
    const shipmentPath='/api/shipments/'+shipment.id;
    await step('Wydanie przesyłki NEW przed READY','POST',shipmentPath+'/hand-over',null,409);
    await step('Przygotowanie przesyłki','POST',shipmentPath+'/ready',null,200);
    const handed=await step('Wydanie kurierowi i publikacja zdarzenia','POST',shipmentPath+'/hand-over',null,200);
    check('Wydanie zapisane, publikacja nie oczekuje',handed.status==='HANDED_OVER' && Boolean(handed.handed_over_at) && handed.notification_pending===false,handed.status);
    const before=await step('Zamówienie przed odbiorem zdarzenia','GET',orderPath,null,200);
    check('Przed odbiorem zamówienie nadal PACKED',before.status==='PACKED',before.status);
    const consumed=await step('Ręczny odbiór zdarzeń z RabbitMQ','POST','/api/orders/consume-events?limit=500',null,200);
    check('Odebrano co najmniej jedno zdarzenie',consumed.consumed_messages>=1,String(consumed.consumed_messages));
    const after=await step('Odczyt zamówienia po zdarzeniu','GET',orderPath,null,200);
    check('Po odbiorze zamówienie SHIPPED',after.status==='SHIPPED',after.status);
    const beforeSettlement=await step('Stan przed odbiorem wydania w Inventory','GET','/api/stocks/'+stock.id,null,200);
    check('Przed rozliczeniem fizyczna ilość nadal 10',beforeSettlement.quantity===10,String(beforeSettlement.quantity));
    await step('Inventory rozlicza wydane sztuki','POST','/api/reservations/consume-shipment-events?limit=500',null,200);
    const settled=await step('Stan po rozliczeniu wydania','GET','/api/stocks/'+stock.id,null,200);
    check('Odjęto 2 sztuki i usunięto rezerwację',settled.quantity===8 && !settled.reservations.some(item=>item.order_id===order.id),json(settled));
    await step('Ponowne wydanie HANDED_OVER','POST',shipmentPath+'/hand-over',null,409);
    await step('Anulowanie wydanej przesyłki','POST',shipmentPath+'/cancel',null,409);
    await step('Synchronizacja już opublikowanego zdarzenia','POST',shipmentPath+'/sync',null,200);
  }
  async function stockScenario() {
    const stock=await supply(),path='/api/stocks/'+stock.id,orderId=uuid();
    await step('Rezerwacja 11 przy dostępnych 10','POST',path+'/reservations',{order_id:orderId,quantity:11},409);
    await step('Rezerwacja ilości zero','POST',path+'/reservations',{order_id:orderId,quantity:0},422);
    await step('Ujemny stan magazynowy','PATCH',path,{quantity:-1},422);
    await step('Rezerwacja całego stanu: 10 sztuk','POST',path+'/reservations',{order_id:orderId,quantity:10},201);
    await step('Stan poniżej rezerwacji','PATCH',path,{quantity:9},409);
    await step('Dodatkowa rezerwacja przy braku wolnego towaru','POST',path+'/reservations',{order_id:uuid(),quantity:1},409);
    const current=await step('Odczyt stanu końcowego','GET',path,null,200);
    const reserved=current.reservations.reduce((sum,item)=>sum+item.quantity,0);
    check('Stan 10, zarezerwowane 10, dostępne 0',current.quantity===10 && reserved===10,`${current.quantity} / ${reserved} / ${current.quantity-reserved}`);
  }
  async function authScenario() {
    await step('Inventory bez tokenu','GET','/api/stocks',null,401,{auth:false});
    await step('Outbound bez tokenu','GET','/api/orders',null,401,{auth:false});
    await step('Shipping bez tokenu','GET','/api/shipments',null,401,{auth:false});
    await step('Auth /me bez tokenu','GET','/api/auth/me',null,401,{auth:false});
    await step('Niepoprawny token','GET','/api/stocks',null,401,{token:'invalid-test-token'});
    await step('Błędne dane logowania','POST','/api/auth/login',{email:'missing-'+uuid()+'@example.com',password:'wrong-password'},401,{auth:false});
    await step('Niepoprawny format e-mail','POST','/api/auth/login',{email:'invalid',password:'test'},422,{auth:false});
    if(state.token && state.user){
      const role=state.user.role;
      await step('Dostęp bieżącej roli do Inventory','GET','/api/stocks',null,role==='SHIPPING'?403:200);
      await step('Dostęp bieżącej roli do Outbound','GET','/api/orders',null,role==='SHIPPING'?403:200);
      await step('Dostęp bieżącej roli do Shipping','GET','/api/shipments',null,role==='WAREHOUSE'?403:200);
      if(role==='ADMIN')await step('Użytkownik ze zbyt krótkim hasłem','POST','/api/auth/users',{email:'invalid@example.com',password:'short',role:'WAREHOUSE'},422);
      else await step('Tworzenie konta bez roli ADMIN','POST','/api/auth/users',{email:'denied@example.com',password:'Valid password 123!',role:'WAREHOUSE'},403);
    }
  }
  async function runScenario(definition) {
    if(state.running||state.busy)return;
    if(definition.id!=='auth' && !state.token){notify('Zaloguj się przed uruchomieniem scenariusza. Błędy braku tokenu sprawdzisz w teście dostępu.',true);$('session-dialog').showModal();return;}
    state.running=true;state.checks=[];renderChecks();notify('');$('scenario-results-panel').hidden=false;
    $('scenario-result-title').textContent=definition.title;$('scenario-summary').textContent='Trwa test…';
    for(const button of document.querySelectorAll('[data-scenario]'))button.disabled=true;
    $('send-request').disabled=true;$('logout').disabled=true;$('session-open').disabled=true;
    let failed=false;
    try {
      if(definition.id==='flow')await flowScenario();
      else if(definition.id==='stock')await stockScenario();
      else if(definition.id==='receipt')await supply({edges:true});
      else await authScenario();
      notify('Scenariusz zakończony. Wszystkie sprawdzenia zgodne z oczekiwaniem.');
    }catch(error){failed=true;notify(error.message,true);}
    finally {
      state.running=false;
      $('scenario-summary').textContent=`${failed?'FAIL':'PASS'} · ${state.checks.filter(item=>item.ok).length}/${state.checks.length} sprawdzeń`;
      $('scenario-summary').className='badge '+(failed?'error':'ok');
      for(const button of document.querySelectorAll('[data-scenario]'))button.disabled=false;
      $('send-request').disabled=false;$('logout').disabled=false;$('session-open').disabled=false;
      await refreshData();
    }
  }
  for(const definition of scenarioDefinitions) {
    const card=element('article','scenario-card');card.append(element('span','scenario-kicker',definition.kicker),element('h3','',definition.title),element('p','',definition.description));
    const bottom=element('div','scenario-bottom'),button=element('button','button', 'Uruchom test');button.dataset.scenario=definition.id;
    button.addEventListener('click',()=>runScenario(definition));bottom.append(element('span','tag',definition.tag),button);card.append(bottom);$('scenario-cards').append(card);
  }
  for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>showView(button.dataset.view));
  $('request-form').addEventListener('submit',executeManual);
  $('refresh').addEventListener('click',refreshData);
  $('clear-history').addEventListener('click',()=>{state.history=[];renderHistory();$('response-meta').textContent='Dziennik wyczyszczony.';$('response-body').textContent='{ }';});
  $('session-open').addEventListener('click',()=>{$('login-message').textContent='';$('session-dialog').showModal();$('login-email').focus();});
  $('session-close').addEventListener('click',()=>$('session-dialog').close());
  $('logout').addEventListener('click',()=>{state.token='';state.user=null;state.selected={};$('request-body').value='';state.history=[];renderHistory();$('response-body').textContent='{ }';$('response-meta').textContent='Sesja zakończona.';updateSession();refreshData();notify('Wylogowano z panelu.');});
  $('login-form').addEventListener('submit',async event=>{
    event.preventDefault();$('login-submit').disabled=true;$('login-message').textContent='Łączenie…';
    try {
      const result=await request('POST','/api/auth/login',{email:$('login-email').value,password:$('login-password').value},{auth:false});
      if(await acceptLogin(result)){$('session-dialog').close();$('login-password').value='';notify('Zalogowano. Wybierz mikroserwis lub uruchom scenariusz.');}
      else $('login-message').textContent=`Logowanie nieudane: HTTP ${result.status||'błąd połączenia'}. Szczegóły w dzienniku.`;
    }catch(error){$('login-message').textContent=error.message;}finally{$('login-submit').disabled=false;}
  });
  renderCards();refreshData();
})();
