"""Trwale wiadomosci; publikacja tylko podczas jawnego zadania HTTP, bez workera."""
from fastapi import HTTPException

from app.db_models import OutboxEventRow
from app.repositories import write_transaction


def enqueue_event(db, routing_key, event):
    db.add(OutboxEventRow(event_id=event.event_id, routing_key=routing_key,
                         payload=event.model_dump(mode="json"), published=False))


def publish_event(sessions, broker, event_id):
    with sessions() as db:
        row = db.get(OutboxEventRow, event_id)
        if row is None:
            raise HTTPException(503, "Saved outgoing event is missing")
        if row.published:
            return False
        routing_key, payload = row.routing_key, row.payload
    broker.publish(routing_key, payload)
    # Utrata potwierdzenia lub blad tego zapisu oznacza bezpieczna duplikacje przy ponowieniu.
    with write_transaction(sessions) as db:
        db.get(OutboxEventRow, event_id).published = True
    return True
