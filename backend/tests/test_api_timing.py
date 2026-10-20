"""Timing must be observable without exposing identifiers or request contents."""

import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import timing


def test_slow_api_logs_route_template_and_sets_browser_timing(monkeypatch, caplog):
    ticks = iter([10.0, 11.25])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    app = FastAPI()
    app.add_middleware(timing.ApiTimingMiddleware)

    @app.get("/api/records/{record_id}")
    def record(record_id: str):
        return {"ok": True}

    with caplog.at_level(logging.INFO, logger="uvicorn.error"), TestClient(app) as client:
        response = client.get(
            "/api/records/private-id?token=private-query",
            headers={"Cookie": "crm_session=private-cookie"},
        )
    assert response.status_code == 200
    assert response.headers["server-timing"] == "app;dur=1250.0"
    records = [r.getMessage() for r in caplog.records if r.name == "uvicorn.error"]
    assert records == ["Slow API: GET /api/records/{record_id} status=200 duration=1250ms"]
    assert "private-" not in "".join(records)


def test_non_api_resources_are_not_timed(monkeypatch):
    def unexpected_timer():
        raise AssertionError("Static requests should bypass API timing.")

    monkeypatch.setattr(timing, "perf_counter", unexpected_timer)
    app = FastAPI()
    app.add_middleware(timing.ApiTimingMiddleware)

    @app.get("/")
    def index():
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "server-timing" not in response.headers
