from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
from fastapi.testclient import TestClient
import jwt
import json
import pytest
from sqlalchemy import inspect, select

from app.auth import create_auth_app
from app.database import Base, make_database
from app.db_models import UserRow
from app.main import create_app
from app.models.user import Role, UserCreate
from app.security import TokenVerifier, issue_token
from app.services.auth import AuthService
from tests.fakes import ControlledRedis

PASSWORD = "Test password 12345!"


@pytest.fixture(scope="module")
def keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return private, TokenVerifier(public)


class Bridge:
    def __init__(self):
        self.clients = {}
        self.calls = []
        self.errors = {}
        self.lose_dispatch_response = False
        self.lose_reservation_response = False

    async def do_service(self, name, path, **kwargs):
        self.calls.append((name, path, kwargs))
        error = self.errors.get((name, path))
        if error:
            raise error
        client = self.clients[name]
        response = client.request(kwargs.get("method", "GET"), path,
                                  headers=kwargs.get("headers", {}),
                                  content=json.dumps(kwargs["data"]) if "data" in kwargs else None)
        if response.status_code >= 400:
            raise HTTPError(path, response.status_code, response.text, response.headers, None)
        if path == "/reservations" and self.lose_reservation_response:
            raise URLError("Lost response after successful reservation")
        if path.endswith("/dispatch") and self.lose_dispatch_response:
            raise URLError("Lost response after successful commit")
        return response.json()


@pytest.fixture
def world(tmp_path, keys):
    private, verifier = keys
    bridge = Bridge()
    auth = create_auth_app(database_url=f"sqlite:///{tmp_path}/auth.db", private_key=private,
                           token_verifier=verifier, discovery_enabled=False)
    with ExitStack() as stack:
        clients = {"auth": stack.enter_context(TestClient(auth))}
        admin = auth.state.auth_service.create_user(UserCreate(email="admin@example.com", password=PASSWORD,
                                                               role=Role.ADMIN), bootstrap=True)
        for domain in ("inventory", "outbound", "shipping"):
            app = create_app(domain=domain, database_url=f"sqlite:///{tmp_path}/inventory.db",
                             outbound_database_url=f"sqlite:///{tmp_path}/outbound.db",
                             shipping_database_url=f"sqlite:///{tmp_path}/shipping.db", auth_enabled=True,
                             token_verifier=verifier, redis_client=ControlledRedis(), eureka_client=bridge)
            clients[domain] = stack.enter_context(TestClient(app))
            bridge.clients[f"{domain.upper()}-SERVICE"] = clients[domain]
        tokens = {}
        for role in Role:
            if role != Role.ADMIN:
                auth.state.auth_service.create_user(UserCreate(email=f"{role.value.lower()}@example.com", password=PASSWORD, role=role))
            response = clients["auth"].post("/auth/login", json={"email": f"{role.value.lower()}@example.com", "password": PASSWORD})
            assert response.status_code == 200, response.text
            tokens[role.value] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        yield clients, tokens, bridge, admin


def packed_order(world):
    clients, tokens, _, _ = world
    warehouse = tokens["WAREHOUSE"]
    body = {"recipient_name": "Test", "recipient_phone": "123456789", "recipient_address": "Warszawa",
            "items": [{"sku": "SKU-001", "quantity": 3}]}
    response = clients["outbound"].post("/orders", json=body, headers=warehouse)
    assert response.status_code == 201
    order = response.json()
    path = f"/orders/{order['id']}"
    response = clients["outbound"].post(path + "/reserve", headers=warehouse)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "RESERVED"
    response = clients["outbound"].post(path + "/pack", headers=warehouse)
    assert response.status_code == 200
    return order


def new_shipment(world):
    clients, tokens, _, _ = world
    order = packed_order(world)
    response = clients["shipping"].post("/shipments", headers=tokens["SHIPPING"],
        json={"order_id": order["id"], "carrier": "Kurier testowy", "weight_kg": 2.5})
    assert response.status_code == 201, response.text
    return order, response.json()


def test_auth_hashes_password_and_never_returns_it(world):
    clients, tokens, _, admin = world
    me = clients["auth"].get("/auth/me", headers=tokens["ADMIN"])
    assert me.status_code == 200
    assert me.json() == {"id": str(admin.id), "email": "admin@example.com", "role": "ADMIN", "is_active": True}
    with clients["auth"].app.state.auth_service.sessions() as db:
        row = db.scalar(select(UserRow).where(UserRow.id == admin.id))
        assert row.password_hash.startswith("$argon2id$")
        assert PASSWORD not in row.password_hash
    bad = clients["auth"].post("/auth/login", json={"email": admin.email, "password": "incorrect"})
    missing = clients["auth"].post("/auth/login", json={"email": "missing@example.com", "password": "incorrect"})
    assert bad.status_code == missing.status_code == 401
    assert bad.json() == missing.json() == {"detail": "Invalid credentials"}


def test_admin_creates_normalized_unique_user_and_other_roles_cannot(world):
    clients, tokens, _, _ = world
    client = clients["auth"]
    body = {"email": "New.User@EXAMPLE.COM", "password": PASSWORD, "role": "WAREHOUSE"}
    for role in ("WAREHOUSE", "SHIPPING"):
        assert client.post("/auth/users", headers=tokens[role], json=body).status_code == 403
    created = client.post("/auth/users", headers=tokens["ADMIN"], json=body)
    assert created.status_code == 201
    assert created.json()["email"] == "new.user@example.com"
    assert "password" not in created.text and "hash" not in created.text
    assert client.post("/auth/users", headers=tokens["ADMIN"], json={**body, "email": "new.user@example.com"}).status_code == 409


def test_deactivation_blocks_login_and_profile_and_preserves_last_admin(world):
    clients, tokens, _, admin = world
    client = clients["auth"]
    user = client.get("/auth/me", headers=tokens["WAREHOUSE"]).json()
    assert client.patch(f"/auth/users/{user['id']}", headers=tokens["ADMIN"], json={"is_active": False}).status_code == 200
    assert client.get("/auth/me", headers=tokens["WAREHOUSE"]).status_code == 401
    assert client.post("/auth/login", json={"email": user['email'], "password": PASSWORD}).status_code == 401
    assert client.patch(f"/auth/users/{admin.id}", headers=tokens["ADMIN"], json={"is_active": False}).status_code == 409


def test_role_boundaries_and_public_health(world):
    clients, tokens, _, _ = world
    for domain, path, allowed, denied in (
        ("inventory", "/stocks", "WAREHOUSE", "SHIPPING"),
        ("outbound", "/orders", "WAREHOUSE", "SHIPPING"),
        ("shipping", "/shipments", "SHIPPING", "WAREHOUSE"),
    ):
        assert clients[domain].get(path).status_code == 401
        assert clients[domain].get(path, headers=tokens[allowed]).status_code == 200
        assert clients[domain].get(path, headers=tokens[denied]).status_code == 403
        assert clients[domain].get(path, headers=tokens["ADMIN"]).status_code == 200
        assert clients[domain].get("/health").status_code == 200


def test_jwt_rejects_expired_wrong_issuer_audience_signature_and_algorithm(world, keys):
    clients, tokens, _, admin = world
    private, verifier = keys
    original = issue_token(admin, private, verifier)
    claims = jwt.decode(original, verifier.public_key, algorithms=["RS256"], audience=verifier.audience)
    cases = ["invalid", jwt.encode({**claims, "exp": 1}, private, algorithm="RS256"),
             jwt.encode({**claims, "iss": "other"}, private, algorithm="RS256"),
             jwt.encode({**claims, "aud": "other"}, private, algorithm="RS256"),
             jwt.encode({k: v for k, v in claims.items() if k != "exp"}, private, algorithm="RS256"),
             jwt.encode(claims, "x" * 32, algorithm="HS256")]
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cases.append(jwt.encode(claims, other, algorithm="RS256"))
    for token in cases:
        response = clients["inventory"].get("/stocks", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_complete_order_and_shipping_flow_and_duplicate_dispatch(world):
    clients, tokens, bridge, _ = world
    order, shipment = new_shipment(world)
    shipping, headers = clients["shipping"], tokens["SHIPPING"]
    path = f"/shipments/{shipment['id']}"
    assert shipment["status"] == "NEW" and shipment["handed_over_at"] is None
    assert shipping.post(path + "/hand-over", headers=headers).status_code == 409
    order_path = f"/orders/{order['id']}"
    clients['outbound'].get(order_path, headers=tokens['WAREHOUSE'])
    assert clients['outbound'].get(order_path, headers=tokens['WAREHOUSE']).headers['x-cache'] == 'HIT'
    assert shipping.post(path + "/ready", headers=headers).json()["status"] == "READY"
    result = shipping.post(path + "/hand-over", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "HANDED_OVER"
    assert result.json()["handed_over_at"] is not None
    assert result.json()["notification_pending"] is False
    read = clients['outbound'].get(order_path, headers=tokens['WAREHOUSE'])
    assert read.json()['status'] == 'SHIPPED' and read.headers['x-cache'] == 'MISS'
    assert shipping.post(path + "/hand-over", headers=headers).status_code == 409
    assert shipping.post(path + "/cancel", headers=headers).status_code == 409
    assert shipping.post(path + "/sync", headers=headers).json() == result.json()
    for _ in range(2):
        response = clients["outbound"].post(f"/orders/{order['id']}/dispatch", headers=headers, json={"shipment_id": shipment['id']})
        assert response.status_code == 200
    assert clients["outbound"].post(f"/orders/{order['id']}/dispatch", headers=headers, json={"shipment_id": str(uuid4())}).status_code == 409
    assert all(call[2].get("headers", {}).get("Authorization") for call in bridge.calls)


def test_lost_dispatch_response_can_be_retried_without_double_dispatch(world):
    clients, tokens, bridge, _ = world
    order, shipment = new_shipment(world)
    client, headers = clients["shipping"], tokens["SHIPPING"]
    path = f"/shipments/{shipment['id']}"
    client.post(path + "/ready", headers=headers)
    bridge.lose_dispatch_response = True
    response = client.post(path + "/hand-over", headers=headers)
    assert response.status_code == 200
    assert response.json()["notification_pending"] is True
    timestamp = response.json()["handed_over_at"]
    assert clients["outbound"].get(f"/orders/{order['id']}", headers=tokens["WAREHOUSE"]).json()["status"] == "SHIPPED"
    bridge.lose_dispatch_response = False
    response = client.post(path + "/sync", headers=headers)
    assert response.status_code == 200
    assert response.json()["notification_pending"] is False
    assert response.json()["handed_over_at"] == timestamp


def test_shipping_enforces_packed_order_and_duplicate_shipment(world):
    clients, tokens, _, _ = world
    order, shipment = new_shipment(world)
    body = {"order_id": order["id"], "carrier": "Test", "weight_kg": 1}
    assert clients["shipping"].post("/shipments", headers=tokens["SHIPPING"], json=body).status_code == 409
    body["order_id"] = str(uuid4())
    assert clients["shipping"].post("/shipments", headers=tokens["SHIPPING"], json=body).status_code == 404
    new = clients["outbound"].post("/orders", headers=tokens["WAREHOUSE"], json={"recipient_name": "A", "recipient_phone": "1",
        "recipient_address": "B", "items": [{"sku": "SKU-001", "quantity": 1}]}).json()
    body["order_id"] = new['id']
    assert clients["shipping"].post("/shipments", headers=tokens["SHIPPING"], json=body).status_code == 409
    assert len(clients["shipping"].get("/shipments", headers=tokens["SHIPPING"]).json()) == 1


def test_allocation_rolls_back_all_items_and_retry_is_idempotent(world):
    clients, tokens, _, _ = world
    client, headers = clients["inventory"], tokens["WAREHOUSE"]
    original = client.get("/stocks", headers=headers).json()
    body = {"order_id": str(uuid4()), "items": [{"sku": "SKU-001", "quantity": 3}, {"sku": "ZZ-MISSING", "quantity": 1}]}
    assert client.post("/reservations", headers=headers, json=body).status_code == 409
    assert client.get("/stocks", headers=headers).json() == original
    body['items'] = [{"sku": "SKU-001", "quantity": 3}]
    for _ in range(2):
        assert client.post("/reservations", headers=headers, json=body).status_code == 200
    stock = client.get("/stocks", headers=headers).json()[0]
    assert len(stock['reservations']) == 1 and stock['reservations'][0]['quantity'] == 3
    body['items'][0]['quantity'] = 4
    assert client.post("/reservations", headers=headers, json=body).status_code == 409


def test_auth_and_shipping_own_only_their_database_tables(world):
    clients, _, _, _ = world
    assert set(inspect(clients['auth'].app.state.engine).get_table_names()) == {'users'}
    assert set(inspect(clients['shipping'].app.state.databases['shipping']).get_table_names()) == {'shipments'}


def test_auth_validation_does_not_echo_password_and_rejects_invalid_fields(world):
    clients, tokens, _, _ = world
    password = "short-secret"
    response = clients['auth'].post('/auth/users', headers=tokens['ADMIN'],
        json={'email': 'invalid', 'password': password, 'role': 'SUPERADMIN'})
    assert response.status_code == 422
    assert password not in response.text
    assert all('input' not in error for error in response.json()['detail'])
    assert clients['auth'].post('/auth/users', headers=tokens['ADMIN'],
        json={'email': 'a@example.com', 'password': PASSWORD, 'role': 'WAREHOUSE', 'is_active': False}).status_code == 422


def test_shipping_can_cancel_and_invalid_input_does_not_create_rows(world):
    clients, tokens, _, _ = world
    order, shipment = new_shipment(world)
    headers = tokens['SHIPPING']
    path = f"/shipments/{shipment['id']}"
    cancelled = clients['shipping'].post(path + '/cancel', headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'CANCELLED'
    for action in ('ready', 'hand-over', 'sync', 'cancel'):
        assert clients['shipping'].post(path + '/' + action, headers=headers).status_code == 409
    for weight in (0, -1, True, 'invalid'):
        response = clients['shipping'].post('/shipments', headers=headers,
            json={'order_id': str(uuid4()), 'carrier': 'Test', 'weight_kg': weight})
        assert response.status_code == 422
    assert len(clients['shipping'].get('/shipments', headers=headers).json()) == 1


def test_shipping_dependency_errors_do_not_change_local_state(world):
    clients, tokens, bridge, _ = world
    order, shipment = new_shipment(world)
    path = f"/shipments/{shipment['id']}"
    headers = tokens['SHIPPING']
    for error, status in ((URLError('offline'), 503), (TimeoutError(), 503),
                          (HTTPError('', 500, 'error', {}, None), 502)):
        bridge.errors[('OUTBOUND-SERVICE', f"/orders/{order['id']}/shipping-check")] = error
        response = clients['shipping'].post(path + '/ready', headers=headers)
        assert response.status_code == status
        assert clients['shipping'].get(path, headers=headers).json() == shipment
        assert clients['shipping'].get('/health').status_code == 200


def test_outbound_failed_reservation_stays_new_and_retry_does_not_double_reserve(world):
    clients, tokens, bridge, _ = world
    headers = tokens['WAREHOUSE']
    body = {'recipient_name': 'A', 'recipient_phone': '1', 'recipient_address': 'B',
            'items': [{'sku': 'SKU-001', 'quantity': 4}]}
    order = clients['outbound'].post('/orders', headers=headers, json=body).json()
    path = f"/orders/{order['id']}"
    bridge.errors[('INVENTORY-SERVICE', '/reservations')] = URLError('offline')
    assert clients['outbound'].post(path + '/reserve', headers=headers).status_code == 503
    assert clients['outbound'].get(path, headers=headers).json()['status'] == 'NEW'
    bridge.errors.clear()
    for _ in range(2):
        assert clients['outbound'].post(path + '/reserve', headers=headers).status_code == 200
    stock = clients['inventory'].get('/stocks', headers=headers).json()[0]
    assert stock['reservations'] == [{'order_id': order['id'], 'quantity': 4}]


def test_lost_reservation_confirmation_is_safe_to_retry(world):
    clients, tokens, bridge, _ = world
    headers = tokens['WAREHOUSE']
    order = clients['outbound'].post('/orders', headers=headers, json={
        'recipient_name': 'A', 'recipient_phone': '1', 'recipient_address': 'B',
        'items': [{'sku': 'SKU-001', 'quantity': 4}]}).json()
    path = f"/orders/{order['id']}"
    bridge.lose_reservation_response = True
    assert clients['outbound'].post(path + '/reserve', headers=headers).status_code == 503
    assert clients['outbound'].get(path, headers=headers).json()['status'] == 'NEW'
    bridge.lose_reservation_response = False
    assert clients['outbound'].post(path + '/reserve', headers=headers).status_code == 200
    stock = clients['inventory'].get('/stocks', headers=headers).json()[0]
    assert stock['reservations'] == [{'order_id': order['id'], 'quantity': 4}]


def test_default_security_fails_closed_without_public_key(tmp_path, monkeypatch):
    monkeypatch.setenv('JWT_PUBLIC_KEY_FILE', str(tmp_path / 'missing.pem'))
    app = create_app(domain='inventory', redis_client=ControlledRedis(), database_url=f'sqlite:///{tmp_path}/unused.db')
    with pytest.raises(FileNotFoundError):
        with TestClient(app):
            pass
    assert not (tmp_path / 'unused.db').exists()


def test_bootstrap_does_not_overwrite_keys_or_echo_invalid_password(tmp_path, monkeypatch, capsys):
    from scripts import init_auth
    keys_dir = tmp_path / 'keys'
    init_auth.generate_keys(keys_dir)
    original = (keys_dir / 'jwt-private.pem').read_bytes()
    init_auth.generate_keys(keys_dir)
    assert (keys_dir / 'jwt-private.pem').read_bytes() == original
    monkeypatch.setattr('sys.argv', ['init_auth.py', '--email', 'invalid'])
    monkeypatch.setattr(init_auth, 'getpass', lambda prompt: 'short-secret')
    with pytest.raises(SystemExit) as caught:
        init_auth.main()
    assert 'short-secret' not in str(caught.value)
    assert 'short-secret' not in capsys.readouterr().out


def test_same_shipment_cannot_dispatch_two_orders(world):
    clients, tokens, _, _ = world
    first = packed_order(world)
    second = packed_order(world)
    shipment_id = str(uuid4())
    for order, expected in ((first, 200), (second, 409)):
        response = clients['outbound'].post(f"/orders/{order['id']}/dispatch", headers=tokens['SHIPPING'],
                                            json={'shipment_id': shipment_id})
        assert response.status_code == expected
    assert clients['outbound'].get(f"/orders/{second['id']}", headers=tokens['WAREHOUSE']).json()['status'] == 'PACKED'
