import json
import sqlite3
from uuid import UUID, uuid4

import pytest

from scripts.migrate_shipping import migrate, NEW_COLUMNS


def legacy_database(tmp_path):
    path = tmp_path / 'shipping.db'
    shipment_id, order_id = uuid4().hex, uuid4().hex
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE shipments (
            id CHAR(32) PRIMARY KEY, order_id CHAR(32) UNIQUE NOT NULL,
            carrier VARCHAR NOT NULL, weight_kg FLOAT NOT NULL,
            status VARCHAR NOT NULL, handed_over_at VARCHAR,
            notification_pending BOOLEAN NOT NULL)''')
        original = (shipment_id, order_id, 'Test', 2.5, 'HANDED_OVER', '2026-11-25T10:00:00+00:00', 1)
        db.execute('INSERT INTO shipments VALUES (?,?,?,?,?,?,?)', original)
    return path, original


def test_migration_preserves_shipment_and_backup(tmp_path):
    path, original = legacy_database(tmp_path)
    result = migrate(path, {str(UUID(original[1])): ' Warszawa, Testowa 1 '})
    assert result['shipments'] == 1
    with sqlite3.connect(path) as db:
        assert {row[1] for row in db.execute('PRAGMA table_info(shipments)')} == NEW_COLUMNS
        row = db.execute('SELECT * FROM shipments').fetchone()
        assert row == (*original[:2], 'Warszawa, Testowa 1', UUID(result['courier_ids']['Test']).hex, *original[3:])
        with pytest.raises(sqlite3.IntegrityError):
            db.execute('UPDATE shipments SET weight_kg = 0')
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT * FROM shipments').fetchone() == original
    assert json.loads(open(result['courier_mapping']).read())['courier_ids'] == result['courier_ids']
    assert migrate(path, {}) == {'status': 'already_migrated'}


def test_missing_address_does_not_change_database(tmp_path):
    path, original = legacy_database(tmp_path)
    with pytest.raises(ValueError, match='Missing destination address'):
        migrate(path, {})
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT * FROM shipments').fetchone() == original
        assert db.execute("SELECT name FROM sqlite_master WHERE name='shipments_migrated'").fetchone() is None
