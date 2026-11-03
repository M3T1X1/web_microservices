"""Przepływ CachedLockers.get z demo.py ZIP; walidacja własnego kontraktu API."""
from pydantic import ValidationError


def cached_read(resource_id, repository, cache, model):
    value, source = cache.get(resource_id)
    if value is not None:
        try:
            result = model.model_validate(value)
            if result.id != resource_id:
                raise ValueError("Cached resource id mismatch")
            return result, source
        except (ValidationError, ValueError):
            # Adapter ZIP sprawdza JSON; tutaj sprawdzamy również schemat domeny.
            source = "MISS" if cache.delete(resource_id) else "BYPASS"
    result = repository.get(resource_id)
    if source != "BYPASS" and not cache.put(resource_id, result.model_dump(mode="json")):
        source = "BYPASS"
    return result, source
