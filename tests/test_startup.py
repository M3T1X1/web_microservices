"""Start procesow sprawdzamy bez uruchamiania Dockera ani Mavena."""
import os
import socket
import sys
import time
from types import SimpleNamespace

import pytest

from scripts import run_services as startup


def test_dotenv_and_terminal_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "PROJECT_DIR", tmp_path)
    monkeypatch.delenv("CACHE_PREFIX", raising=False)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "99")
    (tmp_path / ".env").write_text("CACHE_PREFIX=local-test\nCACHE_TTL_SECONDS=12\n", encoding="utf-8")
    environment = startup.load_environment()
    assert environment["CACHE_PREFIX"] == "local-test"
    assert environment["CACHE_TTL_SECONDS"] == "99"


@pytest.mark.parametrize("service", startup.SERVICE_PORTS)
def test_selected_service_advertises_actual_port(service):
    original = {"SERVICE_PORT": "9999", "JWT_PRIVATE_KEY_FILE": "private.pem"}
    command, environment = startup.service_command(service, 8120, original)
    assert command[command.index("uvicorn") + 1] == f"app.{service}:app"
    assert command[-2:] == ["--port", "8120"]
    assert environment["SERVICE_PORT"] == "8120"
    assert ("JWT_PRIVATE_KEY_FILE" in environment) == (service == "auth")
    assert original["SERVICE_PORT"] == "9999"


def test_occupied_port_is_reported_before_starting():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        with pytest.raises(RuntimeError, match="jest zajety"):
            startup.require_free_ports([listener.getsockname()[1]])


def test_all_with_single_port_is_rejected_before_run(monkeypatch):
    monkeypatch.setattr(startup, "run", lambda *_: pytest.fail("Nie powinien rozpoczac startu"))
    with pytest.raises(SystemExit) as error:
        startup.main(["--port", "8120"])
    assert error.value.code == 2


@pytest.fixture
def simulated_stack(monkeypatch):
    events = []
    commands = []

    class FakeProcesses:
        def start(self, name, command, environment):
            events.append(("start", name, environment.copy()))

        def check(self):
            pass

        def stop(self):
            events.append(("stop",))

    def command_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout="container-id" if "ps" in command else "", returncode=0)

    monkeypatch.setattr(startup, "load_environment", lambda: {"SERVICE_PORT": "9999"})
    monkeypatch.setattr(startup, "require_free_ports", lambda *_: None)
    monkeypatch.setattr(startup, "require_maven", lambda *_: "mvn")
    monkeypatch.setattr(startup, "prepare_auth", lambda *_: None)
    monkeypatch.setattr(startup, "Processes", FakeProcesses)
    monkeypatch.setattr(startup, "http_ready", lambda *_: True)
    monkeypatch.setattr(startup, "registered", lambda *_: True)
    monkeypatch.setattr(startup, "redis_ready", lambda *_: True)
    monkeypatch.setattr(startup, "rabbitmq_ready", lambda *_: True)
    monkeypatch.setattr(startup.shutil, "which", lambda *_: "docker")
    monkeypatch.setattr(startup.subprocess, "run", command_run)
    monkeypatch.setattr(startup.time, "sleep", lambda *_: startup.interrupt())
    return events, commands


def test_whole_stack_start_order_and_cleanup(simulated_stack):
    events, commands = simulated_stack
    with pytest.raises(KeyboardInterrupt):
        startup.run("all", None)
    assert [event[1] for event in events if event[0] == "start"] == [
        "eureka", "auth", "inventory", "outbound", "shipping", "gateway",
    ]
    for event in events:
        if event[0] == "start" and event[1] in startup.SERVICE_PORTS:
            assert event[2]["SERVICE_PORT"] == str(startup.SERVICE_PORTS[event[1]])
    assert events[-1] == ("stop",)
    assert any("up" in command for command in commands)
    assert not any("stop" in command for command in commands)  # Wczesniej dzialajacy kontener zostaje.


def test_failed_service_start_stops_previous_processes(simulated_stack, monkeypatch):
    events, _ = simulated_stack

    def readiness(_, url, status=200):
        if ":8102/" in url:
            raise RuntimeError("Blad Outbound")
        return True

    monkeypatch.setattr(startup, "http_ready", readiness)
    with pytest.raises(RuntimeError, match="Blad Outbound"):
        startup.run("all", None)
    assert [event[1] for event in events if event[0] == "start"] == ["eureka", "auth", "inventory", "outbound"]
    assert events[-1] == ("stop",)


@pytest.mark.parametrize("already_running", [False, True])
def test_containers_are_stopped_only_when_started_here(simulated_stack, monkeypatch, already_running):
    _, commands = simulated_stack
    monkeypatch.setattr(startup.shutil, "which", lambda *_: "docker")

    def command_run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout="container-id" if already_running else "", returncode=0)

    monkeypatch.setattr(startup.subprocess, "run", command_run)
    with pytest.raises(KeyboardInterrupt):
        startup.run("all", None)
    assert any("up" in command for command in commands)
    assert any("stop" in command for command in commands) == (not already_running)
    stopped = [command[-1] for command in commands if "stop" in command]
    assert stopped == ([] if already_running else ["rabbitmq", "redis"])


def test_shutdown_terminates_real_child_process(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "PROJECT_DIR", tmp_path)
    processes = startup.Processes()
    try:
        processes.start("smoke", [sys.executable, "-u", "-c", "import time; print('ready'); time.sleep(60)"], {})
        _, child, _, log_path = processes.children[0]
        deadline = time.monotonic() + 5
        while "ready" not in log_path.read_text(encoding="utf-8"):
            processes.check()
            if time.monotonic() > deadline:
                pytest.fail("Proces testowy nie wystartowal")
            time.sleep(0.05)
    finally:
        processes.stop()
    assert child.poll() is not None


@pytest.mark.skipif(os.name != "nt", reason="Obsluga uprawnien Windows")
def test_failed_taskkill_uses_owned_process_handle(monkeypatch, capsys):
    child = SimpleNamespace(pid=123, poll=lambda: None, kill=lambda: killed.append(True))
    killed = []
    monkeypatch.setattr(startup.subprocess, "run", lambda *_, **__: SimpleNamespace(returncode=1))
    startup.Processes.force_stop("eureka", child)
    assert killed == [True]
    assert "sprawdz proces Java" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="Maven jest plikiem .cmd na Windows")
def test_windows_batch_stops_descendant_too(tmp_path, monkeypatch):
    import ctypes

    monkeypatch.setattr(startup, "PROJECT_DIR", tmp_path)
    batch_dir = tmp_path / "maven with spaces"
    batch_dir.mkdir()
    batch = batch_dir / "mvn.cmd"
    batch.write_text(f'@echo off\n"{sys.executable}" -u -c "import os,time; print(os.getpid()); time.sleep(60)"\n')
    processes = startup.Processes()
    descendant_pid = None
    try:
        processes.start("eureka", [str(batch)], os.environ.copy())
        _, _, _, log_path = processes.children[0]
        deadline = time.monotonic() + 5
        while not log_path.read_text(encoding="utf-8").strip():
            processes.check()
            if time.monotonic() > deadline:
                pytest.fail("Proces potomny nie wystartowal")
            time.sleep(0.05)
        descendant_pid = int(log_path.read_text(encoding="utf-8").strip())
    finally:
        processes.stop()
    # Sprawdzamy potomka, nie tylko proces cmd.exe rodzica.
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.restype = ctypes.c_void_p
    handle = kernel.OpenProcess(0x1000, False, descendant_pid)
    if handle:
        try:
            code = ctypes.c_ulong()
            assert kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            assert code.value != 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
