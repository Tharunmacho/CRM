import logging
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text

from app.api.routers import applications, auth, candidates, data, files, ocr
from app.core.config import get_settings
from app.core.timing import ApiTimingMiddleware
from app.db.models import User
from app.db.session import SessionLocal, engine
from app.integrations.redis import close_redis, redis_health
from app.services.bootstrap import bootstrap_state, ensure_bootstrap

settings = get_settings()
log = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="Care India Overseas CRM",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(ApiTimingMiddleware)

# Only needed when the SPA is served from a different origin, i.e. `npm run dev`.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    """Initialize the assignment cursor without modifying user accounts."""
    try:
        with SessionLocal() as db:
            ensure_bootstrap(db)
    except Exception:  # reported by /api/health; account login never seeds data
        log.error("Application state initialization failed.")
    else:
        log.info("PostgreSQL connected; application state ready.")

    state = redis_health()
    if state["status"] == "ok":
        log.info("Redis connected.")
    elif state["status"] == "disabled":
        log.info("Redis disabled (REDIS_URL is not set).")
    else:
        log.warning("Redis unavailable. Check REDIS_URL and the Redis service.")


app.include_router(auth.router)
app.include_router(applications.router)
app.include_router(candidates.router)
app.include_router(data.router)
app.include_router(files.router)
app.include_router(ocr.router)


@app.on_event("shutdown")
def on_shutdown() -> None:
    close_redis()


@app.get("/api/health")
def health(response: Response = None) -> dict:
    """Readiness probe: return 503 when a required dependency is unavailable."""
    accounts = None
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            try:
                accounts = conn.execute(select(func.count()).select_from(User)).scalar()
            except Exception:
                # Connectivity is reported independently of schema readiness.
                accounts = None
        db_ok = True
        db_error = None
    except Exception as exc:  # surfaced so a bad DATABASE_URL is obvious in Dokploy logs
        db_ok = False
        db_error = str(exc)[:300]

    if db_ok:
        # Retry after a transient database outage during process startup.
        # Initialization only ensures the assignment cursor; it never touches users.
        if not bootstrap_state()["seeded"]:
            try:
                with SessionLocal() as db:
                    ensure_bootstrap(db)
            except Exception:
                pass  # bootstrap_state exposes the failed readiness condition

    redis_state = redis_health()
    redis_ready = redis_state["status"] in {"disabled", "ok"}
    ready = db_ok and bootstrap_state()["seeded"] and redis_ready
    # FastAPI injects the response; direct internal callers can still read the
    # readiness dictionary without constructing an HTTP response.
    if response is not None:
        response.status_code = 200 if ready else 503
    return {
        "status": "ok" if ready else "degraded",
        "database": db_ok,
        "error": db_error,
        "build": BUILD,
        "accounts": accounts,
        "redis": redis_state,
        **bootstrap_state(),
    }


# ---------------------------------------------------------------- the SPA
# One image serves both the API and the built front end, so Dokploy runs a single
# service on a single domain.

BUILD = "unknown"
_stamp = Path("/app/BUILD")
if _stamp.is_file():
    BUILD = _stamp.read_text(encoding="utf-8").strip()

static_dir = Path(settings.static_dir)

if static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # Any path that is not an API route is handled by the client-side router,
        # so every one of them returns index.html.
        candidate = static_dir / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
