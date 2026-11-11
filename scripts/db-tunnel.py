"""Keep the optional local PostgreSQL SSH tunnel available for workspace commands."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "backend" / ".db-tunnel.json"


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    for key, pattern in (("ssh_host", r"[A-Za-z0-9][A-Za-z0-9.:-]*"),
                         ("ssh_user", r"[A-Za-z_][A-Za-z0-9_-]*"),
                         ("service_name", r"[A-Za-z0-9][A-Za-z0-9_.-]*")):
        if not isinstance(config.get(key), str) or not re.fullmatch(pattern, config[key]):
            raise ValueError(f"Invalid tunnel setting: {key}")
    for key in ("identity_file", "known_hosts_file"):
        value = Path(config[key]).expanduser()
        value = value if value.is_absolute() else path.parent / value
        if not value.is_file():
            raise ValueError(f"Tunnel {key} does not exist")
        config[key] = str(value.resolve())
    for key, default in (("local_port", None), ("remote_port", 5432)):
        value = config.get(key, default)
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise ValueError(f"Invalid tunnel setting: {key}")
        config[key] = value
    config["ssh_executable"] = shutil.which("ssh.exe" if os.name == "nt" else "ssh")
    if not config["ssh_executable"]:
        raise ValueError("OpenSSH is not installed or is not on PATH")
    return config


def ssh_arguments(config: dict) -> list[str]:
    null = "NUL" if os.name == "nt" else "/dev/null"
    return [config["ssh_executable"], "-F", null, "-i", config["identity_file"],
            "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=yes",
            "-o", "UserKnownHostsFile=" + config["known_hosts_file"],
            "-o", "GlobalKnownHostsFile=" + null, "-o", "ConnectTimeout=10"]


def remote_endpoint(config: dict) -> str:
    # Resolve the current task's host-reachable address, which can change on redeploy.
    # All input travels as UTF-8 bytes: text-mode stdin adds CRLF on Windows.
    script = f"""import ipaddress,json,socket,subprocess
def output(args):
    return subprocess.check_output(args,timeout=10).decode().strip()
service={config['service_name']!r}
containers=output(['docker','ps','-q','--filter','label=com.docker.swarm.service.name='+service]).split()
if len(containers)!=1: raise RuntimeError('Expected one database container')
pid=output(['docker','inspect','--format','{{{{.State.Pid}}}}',containers[0]])
if not pid.isdigit() or int(pid)<1: raise RuntimeError('Database container is not running')
networks=json.loads(output(['docker','network','inspect','--format','{{{{json .IPAM.Config}}}}','docker_gwbridge']))
subnets=[ipaddress.ip_network(item['Subnet']) for item in networks if item.get('Subnet')]
interfaces=json.loads(output(['nsenter','-t',pid,'-n','ip','-j','-4','addr']))
addresses={{entry['local'] for interface in interfaces if interface['ifname']!='lo'
           for entry in interface.get('addr_info',[]) if entry.get('family')=='inet'
           and any(ipaddress.ip_address(entry['local']) in network for network in subnets)}}
if len(addresses)!=1: raise RuntimeError('Expected one database gateway address')
address=next(iter(addresses))
with socket.create_connection((address,{config['remote_port']}),timeout=3): pass
print(json.dumps({{'host':address}}))
"""
    command = [*ssh_arguments(config), config["ssh_user"] + "@" + config["ssh_host"], "python3", "-"]
    result = subprocess.run(command, input=script.encode(), capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("Cannot inspect the database through SSH; check server access and the database service")
    address = json.loads(result.stdout)["host"]
    return str(ipaddress.ip_address(address))


def process_identity(pid: int) -> str | None:
    """A PID alone is unsafe to reuse: include its creation time and executable."""
    if os.name == "nt":
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                    wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
                return None
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                return None
            return f"{times[0].dwHighDateTime}:{times[0].dwLowDateTime}:{name.value.casefold()}"
        finally:
            kernel.CloseHandle(handle)
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "args="],
                            capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else None


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def stop_owned(state: dict) -> None:
    if process_identity(state["pid"]) != state["identity"]:
        raise RuntimeError("The database tunnel process changed; refusing to stop it")
    os.kill(state["pid"], signal.SIGTERM)
    deadline = time.monotonic() + 5
    while process_identity(state["pid"]) == state["identity"] and time.monotonic() < deadline:
        time.sleep(0.1)
    if process_identity(state["pid"]) == state["identity"]:
        raise RuntimeError("The old database tunnel did not stop")


def ensure_tunnel(config_path: Path) -> str:
    config = load_config(config_path)
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    state_path = config_path.with_name(".db-tunnel.state.json")
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    owned = bool(state.get("identity") and process_identity(state.get("pid", 0)) == state["identity"])
    if owned and state.get("fingerprint") != fingerprint:
        raise RuntimeError("An existing database tunnel uses different settings; stop that tunnel before changing them")
    port_open = listening(config["local_port"])
    if port_open and not owned:
        raise RuntimeError("The database tunnel port is already occupied by an unrecognized process")
    address = remote_endpoint(config)
    if owned:
        if port_open and state.get("remote_host") == address:
            return f"Database tunnel ready at 127.0.0.1:{config['local_port']}"
        stop_owned(state)
        if listening(config["local_port"]):
            raise RuntimeError("The old database tunnel port is still occupied; retry after it closes")
    command = [*ssh_arguments(config), "-N", "-o", "ExitOnForwardFailure=yes",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-L", f"127.0.0.1:{config['local_port']}:{address}:{config['remote_port']}",
               config["ssh_user"] + "@" + config["ssh_host"]]
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    with config_path.with_name(".db-tunnel.log").open("wb") as log:
        child = subprocess.Popen(command, stderr=log, **options)
    try:
        deadline = time.monotonic() + 15
        while child.poll() is None and time.monotonic() < deadline:
            if listening(config["local_port"]):
                time.sleep(0.25)  # Give ExitOnForwardFailure time to report a competing bind.
                if child.poll() is not None:
                    break
                identity = process_identity(child.pid)
                if not identity:
                    raise RuntimeError("Cannot verify the new database tunnel process")
                state = {"pid": child.pid, "identity": identity, "fingerprint": fingerprint,
                         "local_port": config["local_port"], "remote_host": address}
                temporary = state_path.with_name(f".db-tunnel.state.{os.getpid()}.tmp")
                temporary.write_text(json.dumps(state, indent=2), encoding="utf-8")
                temporary.replace(state_path)
                return f"Database tunnel started at 127.0.0.1:{config['local_port']}"
            time.sleep(0.1)
        raise RuntimeError("SSH tunnel failed to start; see backend/.db-tunnel.log")
    except BaseException:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        print("[crm] " + ensure_tunnel(args.config), flush=True)
        return 0
    except Exception as error:
        # Do not print config values or remote exception output.
        reason = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
        print("[crm] Database tunnel unavailable: " + reason, file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
