"""Kontrolowany Redis: osobny na test, z zegarem bez czekania."""
from redis.exceptions import ConnectionError


class MemoryRedis:
    def __init__(self):
        self.values = {}
        self.now = 0
        self.down = False
        self.fail = set()
        self.closed = False

    def _check(self, operation):
        if self.down or operation in self.fail:
            raise ConnectionError("Simulated Redis outage")

    def get(self, key):
        self._check("get")
        value = self.values.get(key)
        if value is None:
            return None
        payload, deadline = value
        if deadline <= self.now:
            self.values.pop(key)
            return None
        return payload

    def set(self, key, value, *, ex):
        self._check("set")
        self.values[key] = (value, self.now + ex)
        return True

    def delete(self, key):
        self._check("delete")
        return int(self.values.pop(key, None) is not None)

    def ping(self):
        self._check("ping")
        return True

    def close(self):
        self.closed = True
