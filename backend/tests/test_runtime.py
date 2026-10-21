"""Process supervision, including isolated Windows reload servers without CRM data."""
from types import SimpleNamespace
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import pytest

from app import runtime


@pytest.fixture(autouse=True)
def isolated_schema_check(monkeypatch):
    monkeypatch.setattr(runtime, "require_current_schema", lambda: None)
    monkeypatch.setattr(runtime, "_new_process_job", lambda: None)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("whatsapp", [False, True])
def test_supervisor_starts_worker_only_when_enabled_and_stops_children(monkeypatch, enabled, whatsapp):
    children = []
    calls = []

    class Child:
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            assert self.returncode is not None
            return self.returncode

    def spawn(command, **options):
        calls.append(command)
        child = Child()
        children.append(child)
        return child

    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=enabled, whatsapp_enabled=whatsapp))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: True))
    runtime.main()
    assert len(children) == 4 + (2 if enabled else 0) + (1 if whatsapp else 0)
    assert calls[0][1:4] == ["-m", "uvicorn", "app.main:app"]
    assert calls[1][1:] == ["-m", "app.workflow_worker"]
    assert calls[2][1:] == ["-m", "app.presence_worker"]
    assert calls[3][1:] == ["-m", "app.document_extraction_worker"]
    if enabled:
        assert calls[4][1:] == ["-m", "app.email_worker"]
        assert calls[5][1:] == ["-m", "app.email_extraction_worker"]
    if whatsapp:
        assert calls[-1][1:] == ["-m", "app.whatsapp_worker"]
    assert all(child.returncode == -15 for child in children)


def test_worker_exit_stops_api_and_fails_container(monkeypatch):
    children = []

    class Child:
        def __init__(self, code):
            self.returncode = code
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -15
        def wait(self, timeout=None):
            return self.returncode

    def spawn(command, **options):
        child = Child(7 if command[-1] == "app.email_worker" else None)
        children.append(child)
        return child

    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=True, whatsapp_enabled=False))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: False))
    with pytest.raises(SystemExit) as stopped:
        runtime.main()
    assert stopped.value.code == 7
    assert children[0].returncode == -15


def test_schema_mismatch_prevents_every_child_from_starting(monkeypatch):
    def mismatch():
        raise SystemExit("Database migration required")
    monkeypatch.setattr(runtime, "require_current_schema", mismatch)
    def unexpected(*args, **kwargs):
        pytest.fail("An old database must not start API or worker processes")
    monkeypatch.setattr(runtime.subprocess, "Popen", unexpected)
    with pytest.raises(SystemExit, match="Database migration required"):
        runtime.main()


def test_development_reload_keeps_required_workers_and_loopback_binding(monkeypatch):
    commands = []
    class Child:
        returncode = None
        def poll(self): return self.returncode
        def terminate(self): self.returncode = -15
        def wait(self, timeout=None): return self.returncode
    def spawn(command, **options):
        commands.append(command)
        return Child()
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=False, whatsapp_enabled=False))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: True))
    runtime.main(host="127.0.0.1", port=8123, reload_api=True)
    assert commands[0][4:] == ["--host", "127.0.0.1", "--port", "8123", "--reload", "--reload-dir", "app"]
    assert [command[-1] for command in commands[1:]] == ["app.workflow_worker", "app.presence_worker", "app.document_extraction_worker"]


def test_supervisor_closes_owned_job_after_worker_exit(monkeypatch):
    job = SimpleNamespace(closed=False)
    def close(): job.closed = True
    job.close = close
    monkeypatch.setattr(runtime, "_new_process_job", lambda: job)
    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=False, whatsapp_enabled=False))
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: False))
    children = []
    def start(command, options, owner):
        assert owner is job
        child = SimpleNamespace(returncode=7 if command[-1] == "app.workflow_worker" else None)
        child.poll = lambda: child.returncode
        def terminate():
            assert job.closed
            child.returncode = -15
        child.terminate = terminate
        child.wait = lambda timeout=None: child.returncode
        children.append(child)
        return child
    monkeypatch.setattr(runtime, "_start_child", start)
    with pytest.raises(SystemExit) as stopped:
        runtime.main()
    assert stopped.value.code == 7 and job.closed
    assert all(child.returncode is not None for child in children)


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
@pytest.mark.parametrize("failure", ["assign", "resume"])
def test_failed_windows_job_start_never_runs_target_module(failure):
    children = []
    def fail(child):
        children.append(child)
        raise OSError("Synthetic job startup failure")
    job = SimpleNamespace(assign=lambda child: None, resume=lambda child: None)
    setattr(job, failure, fail)
    with pytest.raises(OSError, match="Synthetic job"):
        runtime._start_child([sys.executable, "-m", "this"],
                             {"creationflags": subprocess.CREATE_NO_WINDOW, "stdout": subprocess.PIPE},
                             job)
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.read() == b""  # The target would print the Zen of Python.
    children[0].stdout.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
@pytest.mark.parametrize("parent_exits_first", [False, True])
def test_windows_job_stops_real_uvicorn_reload_descendant(tmp_path, parent_exits_first):
    """Synthetic loopback Starlette app: no CRM settings, database or providers."""
    with socket.socket() as chosen:
        chosen.bind(("127.0.0.1", 0))
        port = chosen.getsockname()[1]
    messages = queue.Queue()
    server_pid = None
    child = None
    job = runtime._WindowsProcessJob()
    assign = job.assign
    def delayed_assignment(child):
        # Force time for a venv launcher to escape ordinary post-Popen assignment.
        time.sleep(0.3)
        assign(child)
    job.assign = delayed_assignment

    def responds():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=0.5) as response:
                return response.status == 404
        except urllib.error.HTTPError as error:
            return error.code == 404
        except (OSError, urllib.error.URLError):
            return False

    try:
        child = runtime._start_child([sys.executable, "-m", "uvicorn", "starlette.applications:Starlette", "--factory",
            "--reload", "--reload-dir", str(tmp_path), "--host", "127.0.0.1", "--port", str(port)],
            {"creationflags": subprocess.CREATE_NO_WINDOW, "stdout": subprocess.PIPE, "stderr": subprocess.STDOUT}, job)
        def read_output():
            for raw in child.stdout:
                messages.put(raw.decode(errors="replace"))
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=0.2)
                found = re.search(r"Started server process \[(\d+)\]", line)
                if found: server_pid = int(found.group(1))
            except queue.Empty:
                pass
            if server_pid and responds():
                break
        assert server_pid and responds(), "Synthetic reload server did not start"
        if parent_exits_first:
            child.terminate()
            child.wait(timeout=5)
            assert responds(), "Reproduction must leave the descendant running before job cleanup"
        job.close()
        child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while responds() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not responds(), "API descendant survived supervisor job closure"
        reader.join(timeout=5)
        assert not reader.is_alive(), "A descendant still holds the inherited output pipe"
    finally:
        job.close()
        # A regression must not leak our test server. Only the PID reported by
        # this synthetic Uvicorn process is eligible for the fallback cleanup.
        if server_pid and responds():
            subprocess.run(["taskkill", "/PID", str(server_pid), "/T", "/F"],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if child is not None:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
            child.stdout.close()
