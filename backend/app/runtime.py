"""Supervise the API and configured workers against a matching database schema."""
import argparse
import os
import signal
import subprocess
import sys
import threading

from app.core.config import get_settings
from app.db.schema_readiness import UPDATE_REQUIRED, is_behind, schema_status, upgrade_to_head


class _WindowsProcessJob:
    """Keep every owned descendant in a kill-on-close Windows Job Object."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_uint64 * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        class ThreadEntry(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("id", wintypes.DWORD),
                        ("owner", wintypes.DWORD), ("priority", wintypes.LONG),
                        ("delta", wintypes.LONG), ("flags", wintypes.DWORD)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for name in ("Thread32First", "Thread32Next"):
            function = getattr(kernel, name)
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
            function.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        self._kernel, self._ctypes = kernel, ctypes
        self._thread_entry = ThreadEntry
        # An unnamed, non-inheritable handle belongs only to this supervisor.
        self._handle = kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, child):
        process = self._kernel.OpenProcess(0x0101, False, child.pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            if not self._kernel.AssignProcessToJobObject(self._handle, process):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
        finally:
            self._kernel.CloseHandle(process)

    def resume(self, child):
        # Popen closes CreateProcess's primary-thread handle. The new process is
        # still suspended, so locate and open only its own initial thread.
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == self._ctypes.c_void_p(-1).value:
            raise self._ctypes.WinError(self._ctypes.get_last_error())
        try:
            entry = self._thread_entry()
            entry.size = self._ctypes.sizeof(entry)
            more = self._kernel.Thread32First(snapshot, self._ctypes.byref(entry))
            while more:
                if entry.owner == child.pid:
                    thread = self._kernel.OpenThread(0x2, False, entry.id)  # THREAD_SUSPEND_RESUME
                    if not thread:
                        raise self._ctypes.WinError(self._ctypes.get_last_error())
                    try:
                        if self._kernel.ResumeThread(thread) != 1:
                            raise OSError("The supervised Windows process could not be resumed")
                    finally:
                        self._kernel.CloseHandle(thread)
                    return
                more = self._kernel.Thread32Next(snapshot, self._ctypes.byref(entry))
            raise OSError("The supervised Windows process has no initial thread")
        finally:
            self._kernel.CloseHandle(snapshot)

    def close(self):
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            self._handle = None


def _new_process_job():
    return _WindowsProcessJob() if os.name == "nt" else None


def _start_child(command, options, job):
    if job is None:
        return subprocess.Popen(command, **options)
    # Even the Windows venv executable can spawn another process. Suspend it
    # before any code executes, attach the whole future tree, then resume.
    options = {**options, "creationflags": options.get("creationflags", 0) | 0x4}  # CREATE_SUSPENDED
    child = subprocess.Popen(command, **options)
    try:
        job.assign(child)
        job.resume(child)
    except BaseException:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=15)
        raise
    return child


def require_current_schema():
    # Also protect direct/local launches; workers must never run against old tables.
    from app.db.session import connect_with_retry, engine
    try:
        with connect_with_retry() as connection:
            state = schema_status(connection)
    except Exception:
        raise SystemExit("Cannot check the CRM database. Check the server database configuration.") from None
    if state["status"] == "ok":
        return
    current, required = ", ".join(state["current"]) or "unversioned", ", ".join(state["expected"])
    if not is_behind(state["current"]):
        raise SystemExit(
            f"The database is at {current}, which this version of the CRM cannot upgrade from "
            f"(it expects {required}). The database was prepared by newer code or is not a CRM database: "
            "update this checkout to the matching version, or restore a matching database backup."
        )
    if not getattr(get_settings(), "crm_auto_migrate", False):
        raise SystemExit(
            f"The database is at {current} and this version needs {required}. "
            "Apply the pending migrations with 'npm run db:migrate' (from the project root), "
            "or set CRM_AUTO_MIGRATE=true in backend/.env to apply them automatically at startup. "
            "If a deployed copy of the CRM uses this same database, deploy the matching version "
            "right after migrating; until then that copy reports 'update required'."
        )
    print(f"[crm] Database is at {current}; applying migrations up to {required} (CRM_AUTO_MIGRATE is on)...", flush=True)
    try:
        upgrade_to_head()
        with engine.connect() as connection:
            state = schema_status(connection)
    except Exception as exc:
        raise SystemExit(f"Migration failed and was rolled back ({type(exc).__name__}). Fix the cause, then start again.") from None
    if state["status"] != "ok":
        raise SystemExit(f"{UPDATE_REQUIRED} Current: {', '.join(state['current'])}; required: {', '.join(state['expected'])}.")
    print("[crm] Migrations applied.", flush=True)


def main(*, host="0.0.0.0", port=None, reload_api=False):
    print("[crm] Checking database schema before starting services...", flush=True)
    require_current_schema()
    print("[crm] Database schema is ready.", flush=True)
    stop = threading.Event()
    children = []
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    # CREATE_NO_WINDOW keeps workers hidden on Windows; pass the supervisor's
    # handles explicitly so their logs still reach the npm terminal.
    # A windowless child cannot write to its parent's console, so when this runs in a terminal the
    # services share it (no extra windows open) and their logs stay visible.
    hidden = 0 if sys.stdout.isatty() else getattr(subprocess, "CREATE_NO_WINDOW", 0)
    options = {"creationflags": hidden,
               "stdout": sys.stdout, "stderr": sys.stderr,
               "env": {**os.environ, "PYTHONUNBUFFERED": "1"}}
    listen_port = str(port or os.environ.get("PORT", "8000"))
    api_command = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host,
                   "--port", listen_port]
    if reload_api:
        api_command.extend(["--reload", "--reload-dir", "app"])
    commands = [
        api_command,
        [sys.executable, "-m", "app.workflow_worker"],
        [sys.executable, "-m", "app.presence_worker"],
        [sys.executable, "-m", "app.document_extraction_worker"],
    ]
    if get_settings().email_ingestion_enabled:
        commands.append([sys.executable, "-m", "app.email_worker"])
        commands.append([sys.executable, "-m", "app.email_extraction_worker"])
    if get_settings().whatsapp_enabled:
        commands.append([sys.executable, "-m", "app.whatsapp_worker"])
    job = _new_process_job()
    try:
        for command in commands:
            service = "API" if command is api_command else command[2]
            target = f" at http://{host}:{listen_port}" if command is api_command else ""
            print(f"[crm] Starting {service}{target}...", flush=True)
            children.append(_start_child(command, options, job))
        while not stop.wait(0.5):
            exited = next((child for child in children if child.poll() is not None), None)
            if exited:
                command = commands[children.index(exited)]
                service = "API" if command is api_command else command[2]
                print(f"[crm] {service} exited (code {exited.returncode}); stopping remaining services.",
                      file=sys.stderr, flush=True)
                raise SystemExit(exited.returncode or 1)
    finally:
        if job is not None:
            # Includes grandchildren whose reload parent already exited. Closing
            # this non-inherited handle on supervisor death also cleans the tree.
            job.close()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the CRM API and all configured workflow workers.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int)
    parser.add_argument("--reload", action="store_true", help="Reload the development API; restart this command after worker code changes.")
    args = parser.parse_args()
    main(host=args.host, port=args.port, reload_api=args.reload)
