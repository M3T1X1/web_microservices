import json
import sqlite3
from uuid import uuid4

import pytest

from scripts.migrate_inventory_cancellations import migrate, NEW_COLUMNS


@pytest.mark.parametrize('with_consumption', [False, True])
def test_migration_preserves_reservations_and_consumed_history(tmp_path, with_consumption):
    path = tmp_path / 'inventory.db'
    order_id, shipment_id = uuid4().hex, uuid4().hex
    items = json.dumps([{'sku': 'A', 'quantity': 3}])
    with sqlite3.connect(path) as db:
        if with_consumption:
            db.execute("""CREATE TABLE reservation_batches (order_id CHAR(32) PRIMARY KEY, items JSON NOT NULL,
                status VARCHAR NOT NULL CHECK (status IN ('RESERVED','CONSUMED')), shipment_id CHAR(32) UNIQUE,
                consumed_stock_ids JSON NOT NULL)""")
            db.execute('INSERT INTO reservation_batches VALUES (?,?,?,?,?)', (order_id, items, 'CONSUMED', shipment_id, '["stock"]'))
        else:
            db.execute('CREATE TABLE reservation_batches (order_id CHAR(32) PRIMARY KEY, items JSON NOT NULL)')
            db.execute('INSERT INTO reservation_batches VALUES (?,?)', (order_id, items))
        db.execute('CREATE TABLE stocks (quantity INTEGER, reservations JSON)')
        db.execute('INSERT INTO stocks VALUES (10,?)', ('[]',))
    result = migrate(path)
    assert result['reservation_batches'] == 1
    with sqlite3.connect(path) as db:
        assert {row[1] for row in db.execute('PRAGMA table_info(reservation_batches)')} == NEW_COLUMNS
        expected = (order_id, items, 'CONSUMED', shipment_id, '["stock"]', '[]') if with_consumption else (
            order_id, items, 'RESERVED', None, '[]', '[]')
        assert db.execute('SELECT * FROM reservation_batches').fetchone() == expected
        assert db.execute('SELECT * FROM stocks').fetchone() == (10, '[]')
        db.execute("UPDATE reservation_batches SET status='RELEASED'")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE reservation_batches SET status='WRONG'")
    with sqlite3.connect(result['backup']) as db:
        assert db.execute('SELECT order_id,items FROM reservation_batches').fetchone() == (order_id, items)
    assert migrate(path) == {'status': 'already_migrated'}
