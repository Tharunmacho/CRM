"""Local tunnel safety/lifecycle checks with no SSH or database access."""
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("db_tunnel", Path(__file__).parents[1] / "db-tunnel.py")
tunnel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tunnel)


@pytest.fixture
def configured(tmp_path, monkeypatch):
    key, hosts = tmp_path / "key", tmp_path / "known_hosts"
    key.touch()
    hosts.touch()
    path = tmp_path / ".db-tunnel.json"
    path.write_text(json.dumps({"ssh_host": "example.test", "ssh_user": "root",
                               "service_name": "crm-postgres", "local_port": 55436,
                               "identity_file": str(key), "known_hosts_file": str(hosts)}))
    monkeypatch.setattr(tunnel.shutil, "which", lambda _: "ssh")
    # Any test that forgets to replace a subprocess/network call must fail closed.
    def forbidden(*args, **kwargs):
        pytest.fail("Real subprocess/network access is forbidden in tunnel tests")
    monkeypatch.setattr(tunnel.subprocess, "run", forbidden)
    monkeypatch.setattr(tunnel.subprocess, "Popen", forbidden)
    monkeypatch.setattr(tunnel.socket, "create_connection", forbidden)
    monkeypatch.setattr(tunnel, "process_identity", lambda _: None)
    monkeypatch.setattr(tunnel.time, "sleep", lambda _: None)
    return path


def saved_state(path, **updates):
    config = tunnel.load_config(path)
    state = {"pid": 123, "identity": "created-at-123:ssh", "local_port": 55436,
             "fingerprint": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
             "remote_host": "172.18.0.5", **updates}
    path.with_name(".db-tunnel.state.json").write_text(json.dumps(state))
    return state


def test_reuses_only_recorded_live_process_and_refreshes_remote_address(configured, monkeypatch):
    state = saved_state(configured)
    monkeypatch.setattr(tunnel, "process_identity", lambda pid: state["identity"] if pid == 123 else None)
    monkeypatch.setattr(tunnel, "listening", lambda _: True)
    inspected = []
    def endpoint(config):
        inspected.append(config["service_name"])
        return state["remote_host"]
    monkeypatch.setattr(tunnel, "remote_endpoint", endpoint)
    assert "ready at 127.0.0.1:55436" in tunnel.ensure_tunnel(configured)
    assert inspected == ["crm-postgres"]


@pytest.mark.parametrize("old_identity", [None, "different-process-start-time"])
def test_never_adopts_an_unrelated_listener_or_reused_pid(configured, monkeypatch, old_identity):
    saved_state(configured)
    monkeypatch.setattr(tunnel, "process_identity", lambda _: old_identity)
    monkeypatch.setattr(tunnel, "listening", lambda _: True)
    with pytest.raises(RuntimeError, match="unrecognized process"):
        tunnel.ensure_tunnel(configured)


def test_changed_configuration_does_not_reuse_or_kill_existing_tunnel(configured, monkeypatch):
    state = saved_state(configured, fingerprint="old-settings")
    monkeypatch.setattr(tunnel, "process_identity", lambda _: state["identity"])
    with pytest.raises(RuntimeError, match="different settings"):
        tunnel.ensure_tunnel(configured)


def test_remote_discovery_uses_bytes_and_pinned_key(configured, monkeypatch):
    captured = {}
    def run(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(returncode=0, stdout=b'{"host":"172.18.0.9"}')
    monkeypatch.setattr(tunnel.subprocess, "run", run)
    assert tunnel.remote_endpoint(tunnel.load_config(configured)) == "172.18.0.9"
    assert isinstance(captured["input"], bytes)
    assert b"\r" not in captured["input"]
    assert b"docker_gwbridge" in captured["input"]
    assert b"nsenter" in captured["input"]
    assert "StrictHostKeyChecking=yes" in captured["command"]
    assert "BatchMode=yes" in captured["command"]
    assert captured["command"][-3:] == ["root@example.test", "python3", "-"]


def test_new_tunnel_is_loopback_only_and_state_records_creation_identity(configured, monkeypatch):
    monkeypatch.setattr(tunnel, "remote_endpoint", lambda _: "172.18.0.9")
    checks = iter([False, True])
    monkeypatch.setattr(tunnel, "listening", lambda _: next(checks))
    monkeypatch.setattr(tunnel, "process_identity", lambda _: "new-start-time:ssh")
    captured = {}
    def popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        return SimpleNamespace(pid=456, poll=lambda: None)
    monkeypatch.setattr(tunnel.subprocess, "Popen", popen)
    assert "started at 127.0.0.1:55436" in tunnel.ensure_tunnel(configured)
    assert "127.0.0.1:55436:172.18.0.9:5432" in captured["command"]
    assert "ExitOnForwardFailure=yes" in captured["command"]
    assert "ServerAliveInterval=15" in captured["command"]
    state = json.loads(configured.with_name(".db-tunnel.state.json").read_text())
    assert state["pid"] == 456
    assert state["identity"] == "new-start-time:ssh"


def test_redeploy_replaces_only_the_owned_stale_tunnel(configured, monkeypatch):
    state = saved_state(configured)
    monkeypatch.setattr(tunnel, "process_identity", lambda pid: state["identity"] if pid == 123 else "new")
    checks = iter([True, False, True])
    monkeypatch.setattr(tunnel, "listening", lambda _: next(checks))
    monkeypatch.setattr(tunnel, "remote_endpoint", lambda _: "172.18.0.9")
    stopped = []
    monkeypatch.setattr(tunnel, "stop_owned", lambda value: stopped.append(value["pid"]))
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=456, poll=lambda: None))
    assert "started" in tunnel.ensure_tunnel(configured)
    assert stopped == [123]


def test_failed_ssh_does_not_create_success_state(configured, monkeypatch):
    monkeypatch.setattr(tunnel, "remote_endpoint", lambda _: "172.18.0.9")
    monkeypatch.setattr(tunnel, "listening", lambda _: False)
    monkeypatch.setattr(tunnel.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=456, poll=lambda: 255))
    with pytest.raises(RuntimeError, match="failed to start"):
        tunnel.ensure_tunnel(configured)
    assert not configured.with_name(".db-tunnel.state.json").exists()


def test_stop_owned_refuses_pid_reuse(monkeypatch):
    monkeypatch.setattr(tunnel, "process_identity", lambda _: "another-process")
    monkeypatch.setattr(tunnel.os, "kill", lambda *_: pytest.fail("Unowned process must not be stopped"))
    with pytest.raises(RuntimeError, match="refusing to stop"):
        tunnel.stop_owned({"pid": 123, "identity": "our-original-process"})
