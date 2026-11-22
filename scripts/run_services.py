"""Start calego systemu lub jednej uslugi; Ctrl+C zatrzymuje uruchomione procesy."""
import argparse
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time

import httpx
from dotenv import dotenv_values
from redis import Redis, RedisError

PROJECT_DIR = Path(__file__).resolve().parents[1]
SERVICE_PORTS = {"inventory": 8101, "outbound": 8102, "shipping": 8103, "auth": 8104}
START_ORDER = ("auth", "inventory", "outbound", "shipping")
sys.path.insert(0, str(PROJECT_DIR))
from app.messaging import BrokerUnavailableError, RabbitEventBroker


def port_number(value):
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError("Port musi nalezec do zakresu 1-65535")
    return number


def load_environment():
    # Tak jak --env-file Uvicorna: zmienne terminala maja pierwszenstwo.
    environment = {key: value for key, value in dotenv_values(PROJECT_DIR / ".env").items() if value is not None}
    environment.update(os.environ)
    eureka_port = port_number(environment.get("EUREKA_PORT", "8761"))
    environment.setdefault("EUREKA_SERVER_URL", f"http://127.0.0.1:{eureka_port}")
    environment.setdefault("GATEWAY_EUREKA_URL", f"http://127.0.0.1:{eureka_port}/eureka/")
    return environment


def service_command(service, port, environment):
    child_environment = environment.copy()
    child_environment["SERVICE_PORT"] = str(port)
    if service != "auth":
        child_environment.pop("JWT_PRIVATE_KEY_FILE", None)
    return ([sys.executable, "-m", "uvicorn", f"app.{service}:app",
             "--host", "127.0.0.1", "--port", str(port)], child_environment)


def require_free_ports(ports):
    if len(ports) != len(set(ports)):
        raise RuntimeError("Uslugi musza miec rozne porty.")
    for port in ports:
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError as exc:
                raise RuntimeError(f"Port {port} jest zajety. Zatrzymaj poprzedni proces lub zmien port.") from exc


def require_maven(environment):
    java_home = environment.get("JAVA_HOME")
    if java_home:
        environment["PATH"] = str(Path(java_home) / "bin") + os.pathsep + environment.get("PATH", "")
    maven = shutil.which("mvn", path=environment.get("PATH"))
    if not maven:
        raise RuntimeError("Do startu calego systemu zainstaluj Maven 3.9.x i dodaj mvn do PATH.")
    java = shutil.which("java", path=environment.get("PATH"))
    javac = shutil.which("javac", path=environment.get("PATH"))
    if not java or not javac:
        raise RuntimeError("Zainstaluj JDK 21 lub nowszy i ustaw JAVA_HOME/PATH.")
    version = subprocess.run([java, "-version"], env=environment, capture_output=True, text=True, check=True)
    match = re.search(r'version "(\d+)', version.stdout + version.stderr)
    if not match or int(match[1]) < 21:
        raise RuntimeError("Eureka i Gateway wymagaja JDK 21 lub nowszego. Popraw JAVA_HOME/PATH.")
    return maven


def prepare_auth(environment, include_auth):
    public = Path(environment.get("JWT_PUBLIC_KEY_FILE", ".local/jwt-public.pem"))
    private = Path(environment.get("JWT_PRIVATE_KEY_FILE", ".local/jwt-private.pem"))
    if not include_auth:
        if not public.is_file():
            raise RuntimeError("Brak klucza JWT. Najpierw uruchom caly system lub scripts/init_auth.py --email TWOJ_EMAIL.")
        return

    from sqlalchemy import inspect, select
    from app.database import make_database
    from app.db_models import UserRow

    database_url = environment.get("AUTH_DATABASE_URL", "sqlite:///./auth.db")
    engine, sessions = make_database(database_url)
    try:
        with sessions() as db:
            has_users = inspect(engine).has_table("users") and db.scalar(select(UserRow.id).limit(1)) is not None
    finally:
        engine.dispose()
    if has_users:
        if not public.is_file() or not private.is_file():
            raise RuntimeError("Auth ma juz konta, ale brakuje kluczy JWT. Przywroc istniejace klucze; skrypt ich nie zastapi.")
        return
    if public.exists() != private.exists():
        raise RuntimeError("Niepelna para kluczy JWT. Przywroc brakujacy plik przed uruchomieniem.")
    if public.name != "jwt-public.pem" or private.name != "jwt-private.pem" or public.parent != private.parent:
        raise RuntimeError("Pierwsze konto przygotuj przez init_auth.py; automatyczny start wymaga standardowych nazw kluczy w jednym katalogu.")
    if not sys.stdin.isatty():
        raise RuntimeError("Pierwszy start wymaga terminala: podaj email i haslo administratora przez scripts/init_auth.py --email TWOJ_EMAIL.")
    print("Pierwszy start Auth: przygotowanie kluczy i administratora.", flush=True)
    email = input("Email administratora: ").strip()
    subprocess.run([sys.executable, "scripts/init_auth.py", "--email", email,
                    "--keys-dir", str(private.parent), "--database-url", database_url],
                   env=environment, check=True)


class Processes:
    def __init__(self):
        self.children = []

    def start(self, name, command, environment):
        log_path = PROJECT_DIR / ".local" / "logs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w", encoding="utf-8")
        options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True})
        try:
            process = subprocess.Popen(command, cwd=PROJECT_DIR, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, **options)
        except BaseException:
            log.close()
            raise
        self.children.append((name, process, log, log_path))
        print(f"Start {name}; log: {log_path}", flush=True)

    def check(self):
        for name, process, _, log_path in self.children:
            if process.poll() is not None:
                raise RuntimeError(f"Proces {name} zakonczyl sie (kod {process.returncode}). Sprawdz {log_path}")

    @staticmethod
    def force_stop(name, process):
        if os.name == "nt":
            if name in {"eureka", "gateway"}:
                result = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                if result.returncode and process.poll() is None:
                    print(f"Nie udalo sie zamknac calego drzewa {name}; sprawdz proces Java.", file=sys.stderr)
            if process.poll() is None:
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def stop(self):
        for name, process, log, _ in reversed(self.children):
            try:
                if os.name == "nt":
                    if process.poll() is None:
                        if name in {"eureka", "gateway"}:
                            # mvn.cmd ma potomny proces Java; konczymy cale drzewo.
                            self.force_stop(name, process)
                        else:
                            process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=15)
            except (OSError, subprocess.TimeoutExpired):
                self.force_stop(name, process)
                process.wait(timeout=5)
            finally:
                log.close()
            print(f"Zatrzymano {name}.", flush=True)


def wait_until(description, check, processes, timeout=180):
    print(f"Czekam: {description}", flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes.check()
        if check():
            return
        time.sleep(0.5)
    raise RuntimeError(f"Przekroczono czas oczekiwania: {description}. Sprawdz .local/logs/.")


def http_ready(client, url, status=200):
    try:
        return client.get(url).status_code == status
    except httpx.HTTPError:
        return False


def registered(client, url):
    try:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        applications = response.json()["applications"].get("application", [])
        if isinstance(applications, dict):
            applications = [applications]
        active = set()
        for application in applications:
            instances = application.get("instance", [])
            if isinstance(instances, dict):
                instances = [instances]
            if any(instance.get("status") == "UP" for instance in instances):
                active.add(application["name"])
        return {f"{service.upper()}-SERVICE" for service in SERVICE_PORTS} <= active
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return False


def redis_ready(url):
    try:
        with Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) as client:
            return bool(client.ping())
    except RedisError:
        return False


def rabbitmq_ready(environment):
    try:
        return RabbitEventBroker(amqp_url=environment.get("RABBITMQ_URL"),
                                 exchange_name=environment.get("RABBITMQ_EXCHANGE")).ping()
    except BrokerUnavailableError:
        return False


def run(service, port):
    os.chdir(PROJECT_DIR)
    environment = load_environment()
    selected = START_ORDER if service == "all" else (service,)
    ports = {name: port or SERVICE_PORTS[name] for name in selected}
    eureka_port = port_number(environment.get("EUREKA_PORT", "8761"))
    gateway_port = port_number(environment.get("GATEWAY_PORT", "8080"))
    require_free_ports([*ports.values(), *([eureka_port, gateway_port] if service == "all" else [])])
    maven = require_maven(environment) if service == "all" else None
    prepare_auth(environment, "auth" in selected)
    processes = Processes()
    started_containers = []
    compose = ["docker", "compose", "-f", str(PROJECT_DIR / "compose.yaml")]
    try:
        if service == "all":
            if not shutil.which("docker"):
                raise RuntimeError("Do startu calego systemu zainstaluj i uruchom Docker Desktop.")
            redis_url = environment.get("REDIS_URL", "redis://127.0.0.1:6379/0")
            for name, ready, timeout in (
                ("redis", lambda: redis_ready(redis_url), 30),
                ("rabbitmq", lambda: rabbitmq_ready(environment), 90),
            ):
                existing = subprocess.run([*compose, "ps", "--status", "running", "-q", name],
                                          capture_output=True, text=True, env=environment, check=True)
                if not existing.stdout.strip():
                    started_containers.append(name)
                subprocess.run([*compose, "up", "-d", name], env=environment, check=True)
                wait_until(name, ready, processes, timeout=timeout)
            processes.start("eureka", [maven, "-f", "eureka-server/pom.xml", "spring-boot:run"], environment)
        with httpx.Client(timeout=2, trust_env=False) as client:
            eureka_url = f"http://127.0.0.1:{eureka_port}"
            if service == "all":
                wait_until("Eureka", lambda: http_ready(client, eureka_url + "/"), processes, timeout=300)
            for name in selected:
                command, child_environment = service_command(name, ports[name], environment)
                processes.start(name, command, child_environment)
                url = f"http://127.0.0.1:{ports[name]}"
                wait_until(name, lambda url=url: http_ready(client, url + "/ready"), processes)
                print(f"Dokumentacja {name}: {url}/docs", flush=True)
            if service == "all":
                wait_until("cztery uslugi UP w Eurece", lambda: registered(client, eureka_url + "/eureka/apps"), processes)
                processes.start("gateway", [maven, "-f", "gateway/pom.xml", "spring-boot:run"], environment)
                gateway_url = f"http://127.0.0.1:{gateway_port}"
                wait_until("Gateway", lambda: http_ready(client, gateway_url + "/health"), processes, timeout=300)
                wait_until("trasy Gateway", lambda: all(http_ready(client, gateway_url + path, 401)
                           for path in ("/api/auth/me", "/api/stocks", "/api/orders", "/api/shipments")), processes)
                print(f"Gateway: {gateway_url}; Eureka: {eureka_url}", flush=True)
            else:
                print("Uruchomiono jedna usluge; pozostala infrastruktura musi dzialac osobno.", flush=True)
            if service == "all":
                print("RabbitMQ Management: http://127.0.0.1:15672 (guest/guest)", flush=True)
            print("Gotowe. Zatrzymanie: Ctrl+C. Logi: .local/logs/", flush=True)
            while True:
                processes.check()
                time.sleep(0.5)
    finally:
        try:
            processes.stop()
        finally:
            for name in reversed(started_containers):
                subprocess.run([*compose, "stop", name], env=environment, check=False)


def interrupt(*_):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=("all", *SERVICE_PORTS), default="all")
    parser.add_argument("--port", type=port_number, help="Port jednej wybranej uslugi")
    args = parser.parse_args(argv)
    if args.service == "all" and args.port is not None:
        parser.error("--port wymaga wybrania jednej uslugi przez --service")
    signal.signal(signal.SIGTERM, interrupt)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, interrupt)
    try:
        run(args.service, args.port)
    except KeyboardInterrupt:
        print("Zatrzymano uruchomiony zestaw.", flush=True)
        return 0
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Blad uruchamiania: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
