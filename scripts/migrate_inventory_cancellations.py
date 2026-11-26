"""Dodanie RELEASED w SQLite Inventory. Najpierw zatrzymaj Inventory."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


OLD_COLUMNS = {"order_id", "items"}
SHIPMENT_COLUMNS = OLD_COLUMNS | {"status", "shipment_id", "consumed_stock_ids"}
NEW_COLUMNS = SHIPMENT_COLUMNS | {"released_stock_ids"}


def migrate(database):
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(reservation_batches)")}
        if not columns:
            return {"status": "no_reservation_batches_table"}
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='reservation_batches'").fetchone()[0]
        if columns == NEW_COLUMNS and "RELEASED" in schema:
            return {"status": "already_migrated"}
        if columns not in (OLD_COLUMNS, SHIPMENT_COLUMNS, NEW_COLUMNS):
            raise ValueError("Unexpected reservation_batches schema; migration aborted")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.with_name(f"{database.stem}-before-cancellation-events-{stamp}.db")
        with sqlite3.connect(backup) as target:
            db.backup(target)
        db.execute("BEGIN IMMEDIATE")
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM reservation_batches")]
        db.execute("""CREATE TABLE reservation_batches_new (
            order_id CHAR(32) NOT NULL PRIMARY KEY, items JSON NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'RESERVED', shipment_id CHAR(32) UNIQUE,
            consumed_stock_ids JSON NOT NULL DEFAULT '[]', released_stock_ids JSON NOT NULL DEFAULT '[]',
            CONSTRAINT reservation_batch_status_valid CHECK (status IN ('RESERVED','CONSUMED','RELEASED'))
        )""")
        db.executemany("INSERT INTO reservation_batches_new VALUES (?,?,?,?,?,?)", [
            (row['order_id'], row['items'], row.get('status', 'RESERVED'), row.get('shipment_id'),
             row.get('consumed_stock_ids', '[]'), row.get('released_stock_ids', '[]')) for row in rows])
        if db.execute("SELECT count(*) FROM reservation_batches_new").fetchone()[0] != len(rows):
            raise ValueError("Reservation batch count mismatch")
        db.execute("DROP TABLE reservation_batches")
        db.execute("ALTER TABLE reservation_batches_new RENAME TO reservation_batches")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != 'ok' or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database integrity check failed")
    return {"status": "migrated", "reservation_batches": len(rows), "backup": str(backup)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    print(migrate(parser.parse_args().database))
