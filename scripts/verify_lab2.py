"""Własne scenariusze HTTP LAB2 z prawdziwym Redis, bez operacji Git.

Uruchamia własne procesy na wolnych portach oraz używa tymczasowych baz.
Wymaga redis-server w PATH (Debian/WSL). Nie dotyka wspólnego Redis.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile

import httpx
from redis import Redis
from redis.exceptions import RedisError


ROOT = Path(__file__).resolve().parents[1]
RESERVATION = {"order_id": "22222222-2222-4222-8222-222222222222", "quantity": 3}
ORDER = {"recipient_name": "Test LAB2", "recipient_phone": "123456789",
         "recipient_address": "Magazyn testowy", "items": [{"sku": "SKU-001", "quantity": 3}]}


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def wait_until(check, process, label):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited with {process.returncode}")
        try:
            if check():
                return
        except (httpx.HTTPError, RedisError):
            pass
        time.sleep(0.05)
    raise RuntimeError(f"Timeout waiting for {label}")


def expire(redis, key, timeout):
    deadline = time.monotonic() + timeout
    while redis.exists(key) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert redis.ttl(key) == -2


def verify(baseline_dir=None):
    if shutil.which("redis-server") is None:
        raise SystemExit("Wymagany redis-server w PATH (Debian/WSL).")
    archive = ROOT / "materials/lab2-support.zip"
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "python": sys.version.split()[0], "checks": []}
    report["source"] = {"url": "https://pawoom.lazysolutions.pl/2026_2027/lab2/lab2-support.zip",
                        "zip_sha256": sha256(archive.read_bytes()).hexdigest(), "unchanged_files": {}}
    with ZipFile(archive) as source:
        for name in ("database.py", "cache.py"):
            data = source.read("lab2-support/" + name)
            assert data == (ROOT / "app" / name).read_bytes()
            report["source"]["unchanged_files"][name] = sha256(data).hexdigest()

    def record(name, **details):
        report["checks"].append({"name": name, "result": "PASS", **details})
        print(f"PASS: {name}", flush=True)

    with tempfile.TemporaryDirectory(prefix="warehouse-lab2-") as directory:
        temporary = Path(directory)
        api_port, redis_port, offline_port = free_port(), free_port(), free_port()
        while len({api_port, redis_port, offline_port}) != 3:
            redis_port, offline_port = free_port(), free_port()
        prefix = f"lab2:verification:{temporary.name}:warehouse"
        redis_url = f"redis://127.0.0.1:{redis_port}/0"
        redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=0.3,
                               socket_connect_timeout=0.3)
        server = redis_process = None
        environment = {**os.environ,
            "DATABASE_URL": "sqlite:///" + (temporary / "inventory.db").as_posix(),
            "OUTBOUND_DATABASE_URL": "sqlite:///" + (temporary / "outbound.db").as_posix(),
            "REDIS_URL": redis_url, "CACHE_PREFIX": prefix, "CACHE_TTL_SECONDS": "2"}
        with (temporary / "processes.log").open("w+") as log, httpx.Client(
                base_url=f"http://127.0.0.1:{api_port}", timeout=5, trust_env=False) as api:
            def start_api(url=redis_url, ttl=2, source_dir=ROOT):
                nonlocal server
                stop(server)
                server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", str(api_port)], cwd=source_dir,
                    env={**environment, "REDIS_URL": url, "CACHE_TTL_SECONDS": str(ttl)},
                    stdout=log, stderr=log)
                wait_until(lambda: api.get("/health").status_code == 200, server, "Uvicorn")

            def start_redis():
                nonlocal redis_process
                redis_process = subprocess.Popen(["redis-server", "--bind", "127.0.0.1",
                    "--port", str(redis_port), "--save", "", "--appendonly", "no",
                    "--dir", directory], stdout=log, stderr=log)
                wait_until(redis.ping, redis_process, "Redis")

            try:
                if baseline_dir is not None:
                    # Kopiujemy wskazany początkowy kod do własnego katalogu tymczasowego.
                    baseline = temporary / "lab1"
                    shutil.copytree(baseline_dir / "app", baseline / "app",
                                    ignore=shutil.ignore_patterns("__pycache__"))
                    start_api(source_dir=baseline)
                    initial = api.get("/stocks").json()[0]
                    path = "/stocks/" + initial["id"]
                    assert api.post(path + "/reservations", json=RESERVATION).status_code == 201
                    before = api.get(path).json()
                    assert before["reservations"] == [RESERVATION]
                    assert api.post("/orders", json=ORDER).status_code == 201
                    start_api(source_dir=baseline)
                    after = api.get("/stocks").json()[0]
                    assert after["id"] != initial["id"] and after["reservations"] == []
                    assert api.get(path).status_code == 404
                    assert api.get("/orders").json() == []
                    report["lab1_baseline"] = {"result": "PASS", "before_restart": before,
                        "after_restart": after, "old_stock_status": 404, "orders_after_restart": []}
                    print("PASS: LAB1 loses Inventory and Outbound state after restart", flush=True)

                start_redis()
                start_api()
                report["redis_version"] = redis.info("server")["redis_version"]
                health = api.get("/health")
                assert health.json() == {"status": "ok", "service": "replace-me"}
                assert redis.ping()
                record("health and real Redis PING", health=health.json(), redis="PONG")
                stock_id = api.get("/stocks").json()[0]["id"]
                path = "/stocks/" + stock_id
                stock_key = f"{prefix}:inventory:stock:{stock_id}"
                first, second = api.get(path), api.get(path)
                assert first.status_code == second.status_code == 200
                assert first.headers["X-Cache"] == "MISS" and second.headers["X-Cache"] == "HIT"
                assert first.json() == second.json()
                ttl = redis.ttl(stock_key)
                assert 0 < ttl <= 2
                record("MISS -> HIT with TTL", request=f"GET {path}", x_cache=["MISS", "HIT"], ttl=ttl)
                expire(redis, stock_key, 5)
                assert api.get(path).headers["X-Cache"] == "MISS"
                record("real TTL expiration -> MISS", expired_ttl=-2)

                start_api(ttl=30)
                redis.delete(stock_key)
                assert api.get(path).headers["X-Cache"] == "MISS"
                assert api.get(path).headers["X-Cache"] == "HIT"
                assert api.post(path + "/reservations", json=RESERVATION).status_code == 201
                assert redis.get(stock_key) is None
                fresh = api.get(path)
                assert fresh.headers["X-Cache"] == "MISS"
                assert fresh.json()["reservations"] == [RESERVATION]
                assert api.patch(path, json={"quantity": 14}).status_code == 200
                assert redis.get(stock_key) is None
                stock = api.get(path).json()
                assert stock["quantity"] == 14
                record("reservation and PATCH invalidate cache after commit", stock=stock)

                conflict = api.post(path + "/reservations", json={**RESERVATION, "quantity": 12})
                assert conflict.status_code == 409 and api.get("/stocks").json() == [stock]
                record("business conflict preserves state", status=409, body=conflict.json())

                response = api.post("/orders", json=ORDER)
                assert response.status_code == 201
                order = response.json()
                order_path = "/orders/" + order["id"]
                assert api.get(order_path).headers["X-Cache"] == "MISS"
                assert api.get(order_path).headers["X-Cache"] == "HIT"
                assert api.post(order_path + "/pack").status_code == 409
                assert api.get("/orders").json() == [order]
                record("Outbound write, cache and packing rule", order=order, packing_status=409)

                stop(server)
                redis.delete(stock_key, f"{prefix}:outbound:order:{order['id']}")
                start_api(ttl=30)
                for resource_path, expected in ((path, stock), (order_path, order)):
                    response = api.get(resource_path)
                    assert response.headers["X-Cache"] == "MISS" and response.json() == expected
                record("Uvicorn process restart, both domains, empty cache", x_cache="MISS", state="unchanged")

                stop(redis_process)
                response = api.get(path)
                assert response.headers["X-Cache"] == "BYPASS" and response.json() == stock
                assert api.get(order_path).headers["X-Cache"] == "BYPASS"
                assert api.patch(path, json={"quantity": 18}).status_code == 200
                assert api.post("/orders", json=ORDER).status_code == 201
                assert api.get("/ready").json()["status"] == "degraded"
                assert api.get(path).json()["quantity"] == 18
                record("Redis stopped: reads and writes still work", x_cache="BYPASS", quantity=18, readiness="degraded")

                start_redis()
                response = api.get(path)
                assert response.headers["X-Cache"] == "MISS" and response.json()["quantity"] == 18
                assert api.get(path).headers["X-Cache"] == "HIT"
                record("Redis restarted", x_cache=["MISS", "HIT"], quantity=18)

                start_api(ttl=5)
                redis.delete(stock_key)
                assert api.patch(path, json={"quantity": 20}).status_code == 200
                assert api.get(path).headers["X-Cache"] == "MISS" and redis.ttl(stock_key) > 0
                start_api(url=f"redis://127.0.0.1:{offline_port}/0", ttl=5)
                assert api.get(path).headers["X-Cache"] == "BYPASS"
                assert api.patch(path, json={"quantity": 25}).status_code == 200
                assert api.get(path).json()["quantity"] == 25
                start_api(ttl=5)
                stale = api.get(path)
                assert stale.headers["X-Cache"] == "HIT" and stale.json()["quantity"] == 20
                expire(redis, stock_key, 7)
                fresh = api.get(path)
                assert fresh.headers["X-Cache"] == "MISS" and fresh.json()["quantity"] == 25
                record("failed invalidation: stale HIT until TTL, then current MISS",
                       old_quantity=20, database_quantity=25, x_cache=["BYPASS", "HIT", "MISS"])
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                stop(server)
                stop(redis_process)
                redis.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Opcjonalny raport JSON")
    parser.add_argument("--baseline-dir", type=Path, help="Opcjonalna kopia początkowego projektu LAB1")
    args = parser.parse_args()
    result = verify(args.baseline_dir)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(result['checks'])} scenarios using real Redis and HTTP.")
