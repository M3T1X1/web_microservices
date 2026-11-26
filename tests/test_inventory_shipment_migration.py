import json
import sqlite3
from uuid import uuid4

import pytest

from scripts.migrate_inventory_shipments import migrate, NEW_COLUMNS


def test_migration_preserves_batches_stocks_and_adds_consumption_metadata(tmp_path):
    path = tmp_path / 'inventory.db'
    order_id = uuid4().hex
    original = (order_id, json.dumps([{'sku': 'A', 'quantity': 3}]))
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE reservation_batches (order_id CHAR(32) PRIMARY KEY, items JSON NOT NULL)')
        db.execute('INSERT INTO reservation_batches VALUES (?,?)', original)
        db.execute('CREATE TABLE stocks (id TEXT PRIMARY KEY, quantity INTEGER, reservations JSON)')
        db.execute('INSERT INTO stocks VALUES (?,?,?)', ('stock', 10, '[{"quantity":3}]'))
    result = migrate(path)
    assert result['reservation_batches'] == 1
    with sqlite3.connect(path) as db:
        assert {row[1] for row in db.execute('PRAGMA table_info(reservation_batches)')} == NEW_COLUMNS
        assert db.execute('SELECT * FROM reservation_batches').fetchone() == (*original, 'RESERVED', None, '[]')
        assert db.execute('SELECT * FROM stocks').fetchone() == ('stock', 10, '[{"quantity":3}]')
        db.execute("UPDATE reservation_batches SET status='CONSUMED'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE reservation_batches SET status='INVALID'")
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT * FROM reservation_batches').fetchone() == original
    assert migrate(path) == {'status': 'already_migrated'}
