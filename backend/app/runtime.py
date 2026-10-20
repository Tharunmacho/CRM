"""Supervise the API and optional incoming-mail worker in a shared-storage container."""
import os
import signal
import subprocess
import sys
import threading

from app.core.config import get_settings


def main():
    stop = threading.Event()
    children = []
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    options = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    commands = [
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0",
         "--port", os.environ.get("PORT", "8000")],
    ]
    if get_settings().email_ingestion_enabled:
        commands.append([sys.executable, "-m", "app.email_worker"])
    try:
        for command in commands:
            children.append(subprocess.Popen(command, **options))
        while not stop.wait(0.5):
            exited = next((child for child in children if child.poll() is not None), None)
            if exited:
                raise SystemExit(exited.returncode or 1)
    finally:
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
    main()
