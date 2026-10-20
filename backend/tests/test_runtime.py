"""Production process supervision without launching servers or touching settings."""
from types import SimpleNamespace

import pytest

from app import runtime


@pytest.mark.parametrize("enabled", [False, True])
def test_supervisor_starts_worker_only_when_enabled_and_stops_children(monkeypatch, enabled):
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

    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=enabled))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: True))
    runtime.main()
    assert len(children) == (2 if enabled else 1)
    assert calls[0][1:4] == ["-m", "uvicorn", "app.main:app"]
    if enabled:
        assert calls[1][1:] == ["-m", "app.email_worker"]
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

    monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=True))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    monkeypatch.setattr(runtime.signal, "signal", lambda *_: None)
    monkeypatch.setattr(runtime.threading, "Event", lambda: SimpleNamespace(wait=lambda _: False))
    with pytest.raises(SystemExit) as stopped:
        runtime.main()
    assert stopped.value.code == 7
    assert children[0].returncode == -15
