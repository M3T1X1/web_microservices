"""Opcjonalny cache JSON; awaria Redis nie cofa transakcji bazy."""
import json
import logging
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry


logger = logging.getLogger(__name__)
Model = TypeVar("Model", bound=BaseModel)


def make_redis(url: str) -> Redis:
    return Redis.from_url(url, decode_responses=True, socket_timeout=0.2,
                          socket_connect_timeout=0.2, retry=Retry(NoBackoff(), 0),
                          retry_on_timeout=False)


class JsonCache:
    def __init__(self, client, *, prefix: str, resource: str, ttl: int):
        if not prefix.strip() or ttl <= 0:
            raise ValueError("CACHE_PREFIX must be nonempty and CACHE_TTL_SECONDS must be positive")
        self.client = client
        self.prefix = prefix
        self.resource = resource
        self.ttl = ttl

    def key(self, resource_id: UUID) -> str:
        return f"{self.prefix}:{self.resource}:{resource_id}"

    def get(self, resource_id: UUID, model: type[Model]) -> tuple[Model | None, str]:
        try:
            raw = self.client.get(self.key(resource_id))
        except RedisError:
            logger.warning("Cache GET unavailable (%s)", self.resource)
            return None, "BYPASS"
        if raw is None:
            return None, "MISS"
        try:
            result = model.model_validate_json(raw)
            if result.id != resource_id:
                raise ValueError("Cached resource id mismatch")
            return result, "HIT"
        except (ValidationError, ValueError):
            return None, "MISS" if self.delete(resource_id) else "BYPASS"

    def put(self, resource_id: UUID, payload: BaseModel) -> bool:
        try:
            return bool(self.client.set(self.key(resource_id),
                                        json.dumps(payload.model_dump(mode="json")), ex=self.ttl))
        except RedisError:
            logger.warning("Cache SET unavailable (%s)", self.resource)
            return False

    def delete(self, resource_id: UUID) -> bool:
        try:
            self.client.delete(self.key(resource_id))
            return True
        except RedisError:
            logger.warning("Cache DELETE unavailable (%s); stale data may survive until TTL", self.resource)
            return False
