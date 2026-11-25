"""Migrate legacy SQLite shipments; read order-address mapping as JSON from stdin.

Stop Shipping before running. A database backup and courier-name mapping are
stored beside the database. Generated UUIDs identify legacy carrier names;
they do not refer to Auth users or an external courier registry.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
from uuid import NAMESPACE_URL, UUID, uuid5


LEGACY_COLUMNS = {
    'id', 'order_id', 'carrier', 'weight_kg', 'status',
    'handed_over_at', 'notification_pending',
}
NEW_COLUMNS = (LEGACY_COLUMNS - {'carrier'}) | {'destination_address', 'courier_id'}


def migrate(database, addresses):
    database = Path(database).resolve(strict=True)
    addresses = {UUID(key).hex: value for key, value in addresses.items()}
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(shipments)')}
        if columns == NEW_COLUMNS:
            return {'status': 'already_migrated'}
        if columns != LEGACY_COLUMNS:
            raise ValueError('Unexpected shipments schema; migration aborted')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = database.with_name(f'{database.stem}-before-address-courier-{stamp}.db')
        with sqlite3.connect(backup) as target:
            db.backup(target)
        db.execute('BEGIN IMMEDIATE')
        db.row_factory = sqlite3.Row
        rows = db.execute('SELECT * FROM shipments').fetchall()
        for row in rows:
            address = addresses.get(UUID(row['order_id']).hex)
            if not isinstance(address, str) or not address.strip():
                raise ValueError(f"Missing destination address for order {row['order_id']}")
        couriers = {
            name: str(uuid5(NAMESPACE_URL, f'warehouse/legacy-carriers/{name}'))
            for name in sorted({row['carrier'] for row in rows})
        }
        # Persist the historical names before removing the old column.
        mapping = backup.with_suffix('.json')
        mapping.write_text(json.dumps({'backup': str(backup), 'courier_ids': couriers},
                                     ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        db.execute('''CREATE TABLE shipments_migrated (
            id CHAR(32) NOT NULL PRIMARY KEY,
            order_id CHAR(32) NOT NULL UNIQUE,
            destination_address VARCHAR NOT NULL,
            courier_id CHAR(32) NOT NULL,
            weight_kg FLOAT NOT NULL,
            status VARCHAR NOT NULL,
            handed_over_at VARCHAR,
            notification_pending BOOLEAN NOT NULL,
            CONSTRAINT shipment_positive_weight CHECK (weight_kg > 0),
            CONSTRAINT shipment_valid_status CHECK (status IN ('NEW','READY','HANDED_OVER','CANCELLED'))
        )''')
        db.executemany('INSERT INTO shipments_migrated VALUES (?,?,?,?,?,?,?,?)', [
            (row['id'], row['order_id'], addresses[UUID(row['order_id']).hex].strip(),
             UUID(couriers[row['carrier']]).hex, row['weight_kg'], row['status'],
             row['handed_over_at'], row['notification_pending'])
            for row in rows
        ])
        if db.execute('SELECT count(*) FROM shipments_migrated').fetchone()[0] != len(rows):
            raise ValueError('Shipment count mismatch')
        db.execute('DROP TABLE shipments')
        db.execute('ALTER TABLE shipments_migrated RENAME TO shipments')
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity check failed')
        if db.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Database foreign key check failed')
    return {'status': 'migrated', 'shipments': len(rows), 'backup': str(backup),
            'courier_mapping': str(mapping), 'courier_ids': couriers}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    args = parser.parse_args()
    result = migrate(args.database, json.load(sys.stdin))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
