"""Aktualizacja SQLite Outbound bez usuwania danych. Najpierw zatrzymaj Outbound."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


OLD_COLUMNS = {"id", "recipient_name", "recipient_phone", "recipient_address", "status", "items"}
NEW_COLUMNS = OLD_COLUMNS | {"reservation_attempt_id", "reservation_error", "reservation_notification_pending"}


def migrate(database):
    database = Path(database).resolve(strict=True)
    with sqlite3.connect(database) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(orders)")}
        if not columns:
            return {"status": "no_orders_table"}
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='orders'").fetchone()[0]
        if columns == NEW_COLUMNS and "RESERVATION_PENDING" in schema and "RESERVATION_REJECTED" in schema:
            return {"status": "already_migrated"}
        if columns not in (OLD_COLUMNS, NEW_COLUMNS):
            raise ValueError("Unexpected orders schema; migration aborted")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = database.with_name(f"{database.stem}-before-reservation-events-{stamp}.db")
        with sqlite3.connect(backup) as target:
            db.backup(target)
        db.execute("BEGIN IMMEDIATE")
        db.row_factory = sqlite3.Row
        rows = [dict(row) for row in db.execute("SELECT * FROM orders")]
        db.execute("""CREATE TABLE orders_reservations_new (
            id CHAR(32) NOT NULL PRIMARY KEY,
            recipient_name VARCHAR NOT NULL, recipient_phone VARCHAR NOT NULL,
            recipient_address VARCHAR NOT NULL, status VARCHAR NOT NULL, items JSON NOT NULL,
            reservation_attempt_id CHAR(32), reservation_error VARCHAR,
            reservation_notification_pending BOOLEAN NOT NULL DEFAULT 0,
            CONSTRAINT order_status_valid CHECK (status IN (
                'NEW','RESERVATION_PENDING','RESERVATION_REJECTED','RESERVED','PACKED','SHIPPED','CANCELLED'))
        )""")
        db.executemany("INSERT INTO orders_reservations_new VALUES (?,?,?,?,?,?,?,?,?)", [
            (row["id"], row["recipient_name"], row["recipient_phone"], row["recipient_address"],
             row["status"], row["items"], row.get("reservation_attempt_id"), row.get("reservation_error"),
             row.get("reservation_notification_pending", 0)) for row in rows])
        if db.execute("SELECT count(*) FROM orders_reservations_new").fetchone()[0] != len(rows):
            raise ValueError("Order count mismatch")
        db.execute("DROP TABLE orders")
        db.execute("ALTER TABLE orders_reservations_new RENAME TO orders")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or db.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Database integrity check failed")
    return {"status": "migrated", "orders": len(rows), "backup": str(backup)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    print(migrate(parser.parse_args().database))
