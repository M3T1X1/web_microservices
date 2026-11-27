"""Reczne odebranie zlecen rezerwacji i publikacja ich trwalych wynikow."""
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.db_models import OutboxEventRow, ProcessedEventRow
from app.messaging import BrokerUnavailableError
from app.models.events import (
    INVENTORY_RESERVATION_QUEUE, RESERVATION_REQUEST_KEY, RESERVATION_RESERVED_KEY,
    RESERVATION_REJECTED_KEY, OrderReservationRequested, OrderStockReserved,
    OrderStockReservationRejected, reservation_result, reservation_result_id,
)
from app.models.order import normalized_items
from app.outbox import enqueue_event, publish_event
from app.repositories import write_transaction


class ReservationEventService:
    def __init__(self, allocation, cache, broker):
        self.allocation, self.cache, self.broker = allocation, cache, broker
        self.sessions = allocation.sessions

    def invalidate(self, result):
        if isinstance(result, OrderStockReserved):
            for stock_id in result.stock_ids:
                if not self.cache.delete(stock_id):
                    raise HTTPException(503, "Cache invalidation failed; reservation result remains saved")

    def handle(self, payload):
        event = OrderReservationRequested.model_validate(payload)
        canonical = event.model_dump(mode="json")
        with write_transaction(self.sessions) as db:
            previous = db.get(ProcessedEventRow, event.event_id)
            if previous is not None:
                if previous.payload != canonical:
                    raise HTTPException(409, "Reservation attempt already processed with different data")
                outgoing = db.get(OutboxEventRow, reservation_result_id(event.attempt_id))
                if outgoing is None:
                    raise HTTPException(503, "Saved reservation result is missing")
                result = reservation_result(outgoing.payload)
                applied = False
            else:
                fields = dict(event_id=reservation_result_id(event.attempt_id),
                              attempt_id=event.attempt_id, order_id=event.order_id,
                              items=normalized_items(event.items), occurred_at=datetime.now(timezone.utc))
                try:
                    # Niedobor cofa cala alokacje, ale wynik odmowy zapisujemy w zewnetrznej transakcji.
                    with db.begin_nested():
                        stock_ids = self.allocation.reserve_in_transaction(db, event)
                    result = OrderStockReserved(event_type="OrderStockReserved", stock_ids=stock_ids, **fields)
                    routing_key = RESERVATION_RESERVED_KEY
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise
                    result = OrderStockReservationRejected(event_type="OrderStockReservationRejected",
                                                          reason=str(exc.detail), **fields)
                    routing_key = RESERVATION_REJECTED_KEY
                enqueue_event(db, routing_key, result)
                db.add(ProcessedEventRow(event_id=event.event_id, event_type=event.event_type, payload=canonical))
                applied = True
        # COMMIT rezerwacji i wyniku nastapil PRZED publikacja; duplikat nie uruchamia alokacji.
        self.invalidate(result)
        publish_event(self.sessions, self.broker, result.event_id)
        return applied

    def consume(self, limit=50):
        unique = 0

        def handle(payload):
            nonlocal unique
            unique += int(self.handle(payload))

        try:
            self.broker.bind_queue(INVENTORY_RESERVATION_QUEUE, RESERVATION_REQUEST_KEY)
            consumed = self.broker.consume_available(INVENTORY_RESERVATION_QUEUE, handle, limit=limit)
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable; reservation results remain saved") from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, "Invalid reservation request; message remains queued") from exc
        return {"consumed_messages": consumed, "unique_events_processed": unique}

    def sync(self, limit=50):
        with self.sessions() as db:
            messages = [(row.event_id, reservation_result(row.payload)) for row in db.scalars(
                select(OutboxEventRow).where(OutboxEventRow.published.is_(False)).order_by(OutboxEventRow.event_id).limit(limit))]
        published = 0
        try:
            for event_id, result in messages:
                self.invalidate(result)
                published += int(publish_event(self.sessions, self.broker, event_id))
        except BrokerUnavailableError as exc:
            raise HTTPException(503, "RabbitMQ broker is unavailable; reservation results remain saved") from exc
        return {"published_messages": published}
