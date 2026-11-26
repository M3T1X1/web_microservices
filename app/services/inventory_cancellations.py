"""Reczny odbior anulowan; zwalnia rezerwacje bez zmiany fizycznej ilosci."""
from fastapi import HTTPException
from pydantic import ValidationError

from app.db_models import ProcessedEventRow
from app.messaging import BrokerUnavailableError
from app.models.events import INVENTORY_CANCELLATION_QUEUE, CANCELLATION_ROUTING_KEY, OrderCancelled
from app.repositories import write_transaction


class InventoryCancellationService:
    def __init__(self, allocation, cache, broker):
        self.allocation, self.cache, self.broker = allocation, cache, broker
        self.sessions = allocation.sessions

    def handle(self, payload):
        event = OrderCancelled.model_validate(payload)
        canonical = event.model_dump(mode="json")
        with write_transaction(self.sessions) as db:
            previous = db.get(ProcessedEventRow, event.event_id)
            if previous is not None and previous.payload != canonical:
                raise HTTPException(409, "Event ID already processed with different data")
            stock_ids = self.allocation.release_in_transaction(db, event)
            applied = previous is None
            if applied:
                db.add(ProcessedEventRow(event_id=event.event_id, event_type=event.event_type, payload=canonical))
        # Zapisane ID pozwalaja naprawic cache rowniez po duplikacie lub restarcie.
        for stock_id in stock_ids:
            if not self.cache.delete(stock_id):
                raise HTTPException(503, "Cache invalidation failed; cancellation remains queued")
        return applied

    def consume(self, limit=50):
        unique = 0

        def handle(payload):
            nonlocal unique
            unique += int(self.handle(payload))

        try:
            self.broker.bind_queue(INVENTORY_CANCELLATION_QUEUE, CANCELLATION_ROUTING_KEY)
            consumed = self.broker.consume_available(INVENTORY_CANCELLATION_QUEUE, handle, limit=limit)
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid cancellation event; message remains queued") from exc
        return {"consumed_messages": consumed, "unique_events_processed": unique}
