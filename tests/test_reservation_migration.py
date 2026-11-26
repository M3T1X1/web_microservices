import json
import sqlite3
from uuid import uuid4

import pytest

from scripts.migrate_reservations import migrate, NEW_COLUMNS


def test_migration_preserves_orders_related_data_and_new_statuses(tmp_path):
    path = tmp_path / 'outbound.db'
    order_id = uuid4().hex
    original = (order_id, 'Test', '1', 'Address', 'PACKED', json.dumps([{'sku': 'A', 'quantity': 1}]))
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE orders (id CHAR(32) PRIMARY KEY, recipient_name VARCHAR NOT NULL,
            recipient_phone VARCHAR NOT NULL, recipient_address VARCHAR NOT NULL, status VARCHAR NOT NULL,
            items JSON NOT NULL, CHECK (status IN ('NEW','RESERVED','PACKED','SHIPPED','CANCELLED')))""")
        db.execute('INSERT INTO orders VALUES (?,?,?,?,?,?)', original)
        db.execute('CREATE TABLE order_dispatches (order_id CHAR(32) PRIMARY KEY, shipment_id CHAR(32))')
        db.execute('INSERT INTO order_dispatches VALUES (?,?)', (order_id, uuid4().hex))
    result = migrate(path)
    assert result['orders'] == 1
    with sqlite3.connect(path) as db:
        assert {row[1] for row in db.execute('PRAGMA table_info(orders)')} == NEW_COLUMNS
        assert db.execute('SELECT * FROM orders').fetchone() == (*original, None, None, 0)
        assert db.execute('SELECT count(*) FROM order_dispatches').fetchone()[0] == 1
        db.execute("UPDATE orders SET status='RESERVATION_PENDING'")
        db.execute("UPDATE orders SET status='RESERVATION_REJECTED'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE orders SET status='BOGUS'")
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT * FROM orders').fetchone() == original
    assert migrate(path) == {'status': 'already_migrated'}
