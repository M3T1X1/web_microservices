"""MemoryRedis z tests/test_storage.py ZIP LAB2 oraz rozszerzenie do naszych testów."""
from redis.exceptions import ConnectionError


class MemoryRedis:
    """Test double only; does not replace the real Redis exercise."""
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.down = False

    def check(self):
        if self.down:
            raise ConnectionError("Simulated outage")

    def get(self, key):
        self.check()
        return self.values.get(key)

    def set(self, key, value, ex):
        self.check()
        self.values[key] = value
        self.expirations[key] = ex

    def delete(self, key):
        self.check()
        self.values.pop(key, None)
        self.expirations.pop(key, None)

    def ping(self):
        self.check()
        return True

    def close(self):
        # No sockets to release; compatible with FastAPI lifespan cleanup.
        pass


class ControlledRedis(MemoryRedis):
    """Dodatkowe awarie poszczególnych poleceń, zegar TTL i obserwacja close."""
    def __init__(self):
        super().__init__()
        self.fail = set()
        self.now = 0
        self.deadlines = {}
        self.closed = False

    def check_operation(self, operation):
        self.check()
        if operation in self.fail:
            raise ConnectionError(f"Simulated {operation} failure")

    def get(self, key):
        self.check_operation("get")
        if key in self.deadlines and self.deadlines[key] <= self.now:
            super().delete(key)
            self.deadlines.pop(key)
        return super().get(key)

    def set(self, key, value, ex):
        self.check_operation("set")
        super().set(key, value, ex)
        self.deadlines[key] = self.now + ex

    def delete(self, key):
        self.check_operation("delete")
        super().delete(key)
        self.deadlines.pop(key, None)

    def close(self):
        self.closed = True
