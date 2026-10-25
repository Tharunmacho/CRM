import asyncio
import logging
import os
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.api.routers import applications, auth, candidates, data, files, ocr, inbox, users, reports, workflow, whatsapp, document_jobs, attendance, staff_directory
from app.api.routers import document_bundles, events, intake_acknowledgements, settings as agency_settings_router, whatsapp_chat
from app.core.config import get_settings
from app.core.timing import ApiTimingMiddleware
from app.db.models import User
from app.db.session import SessionLocal, connect_with_retry, engine, warm_pool
from app.db.schema_readiness import UPDATE_REQUIRED, schema_status
from app.integrations.redis import close_redis, redis_health
from app.integrations.ocr.inspection import find_tesseract
from app.services.bootstrap import bootstrap_state, ensure_bootstrap
from app.services.live_hub import hub as live_hub

settings = get_settings()
log = logging.getLogger("uvicorn.error")

app = FastAPI(
    title="Recruitment CRM",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.add_middleware(ApiTimingMiddleware)


class _CompressWorkspaceSnapshot:
    """Gzip the large JSON snapshots and the built JS/CSS; documents and streams are left untouched.

    The built bundle is ~1.4 MB of JavaScript (~465 kB gzipped). Behind a proxy that does not compress,
    the first visit downloaded all of it uncompressed; hashed asset files never change, so compressing them
    on the way out costs little and each browser fetches each file once (see the immutable cache header).
    """

    paths = ("/api/bootstrap", "/api/auth/session")
    text_assets = (".js", ".css", ".svg", ".json", ".html")

    def __init__(self, app):
        self.app = app
        self.compressed = GZipMiddleware(app, minimum_size=1024, compresslevel=5)

    async def __call__(self, scope, receive, send):
        path = scope["path"] if scope["type"] == "http" else ""
        wanted = path in self.paths or (path.startswith("/assets/") and path.endswith(self.text_assets))
        await (self.compressed if wanted else self.app)(scope, receive, send)


app.add_middleware(_CompressWorkspaceSnapshot)

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
        with connect_with_retry() as connection:
            schema = schema_status(connection)
        if schema["status"] == "ok":
            with SessionLocal() as db:
                ensure_bootstrap(db)
            log.info("PostgreSQL connected; application state ready.")
        else:
            log.error("Database migration required: current=%s expected=%s. No startup data was changed.", schema["current"], schema["expected"])
    except Exception:  # reported by /api/health; account login never seeds data
        log.error("Application state initialization failed.")

    threading.Thread(target=warm_pool, name="db-pool-warmup", daemon=True).start()

    state = redis_health()
    if state["status"] == "ok":
        log.info("Redis connected.")
    elif state["status"] == "disabled":
        log.info("Redis disabled (REDIS_URL is not set).")
    else:
        log.warning("Redis unavailable. Check REDIS_URL and the Redis service.")

    if find_tesseract(settings.tesseract_cmd):
        log.info("Local OCR: Tesseract available; languages=%s. Documents are inspected before Veris.", settings.ocr_languages)
    else:
        log.warning("Local OCR: Tesseract unavailable. Scanned pages will need review; install it or set TESSERACT_CMD.")


app.include_router(auth.router)
app.include_router(applications.router)
app.include_router(candidates.router)
app.include_router(data.router)
app.include_router(files.router)
app.include_router(document_jobs.router)
app.include_router(ocr.router)
app.include_router(inbox.router)
app.include_router(users.router)
app.include_router(staff_directory.router)
app.include_router(reports.router)
app.include_router(reports.dashboard_router)
app.include_router(workflow.router)
app.include_router(whatsapp.router)
app.include_router(whatsapp_chat.router)
app.include_router(document_bundles.router)
app.include_router(intake_acknowledgements.router)
app.include_router(attendance.router)
app.include_router(events.router)
app.include_router(agency_settings_router.router)


@app.on_event("startup")
async def start_live_updates() -> None:
    """Listen for database change notifications so open browsers are told about commits at once (see events.py)."""
    if engine.dialect.name != "postgresql" or (os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("CRM_LIVE_EVENTS") != "force"):
        return
    conninfo = engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    live_hub.start(asyncio.get_running_loop(), conninfo)


@app.on_event("shutdown")
def on_shutdown() -> None:
    live_hub.stop()  # ends open event streams so the server can exit
    close_redis()


@app.get("/api/health")
def health(response: Response = None) -> dict:
    """Readiness probe: return 503 when a required dependency is unavailable."""
    accounts = None
    schema = {"status": "unavailable", "current": [], "expected": []}
    try:
        with engine.connect() as conn:
            conn.execute(text("select 1"))
            schema = schema_status(conn)
            try:
                accounts = conn.execute(select(func.count()).select_from(User)).scalar()
            except Exception:
                # Connectivity is reported independently of schema readiness.
                accounts = None
        db_ok = True
        db_error = None
    except Exception:
        db_ok = False
        db_error = "Database readiness could not be checked. Check the server configuration and database connection."

    if db_ok and schema["status"] == "ok":
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
    ready = db_ok and schema["status"] == "ok" and bootstrap_state()["seeded"] and redis_ready
    # FastAPI injects the response; direct internal callers can still read the
    # readiness dictionary without constructing an HTTP response.
    if response is not None:
        response.status_code = 200 if ready else 503
    return {
        "status": "ok" if ready else "degraded",
        "database": db_ok,
        "schema": schema,
        "error": db_error,
        "build": BUILD,
        "accounts": accounts,
        "redis": redis_state,
        **bootstrap_state(),
    }


@app.exception_handler(DBAPIError)
def database_failure(request: Request, error: DBAPIError):
    """Explain schema drift without leaking SQL, record values or credentials."""
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate in {"42P01", "42703"}:
        try:
            with engine.connect() as connection:
                schema = schema_status(connection)
            if schema["status"] == "migration_required":
                return JSONResponse(status_code=503, content={"detail": UPDATE_REQUIRED}, headers={"Cache-Control": "no-store"})
        except Exception:
            pass
    log.error("Database request failed: %s (SQLSTATE %s).", type(error).__name__, sqlstate or "unavailable")
    return JSONResponse(status_code=500, content={"detail": "The database request could not be completed. Please contact your administrator."}, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- the SPA
# One image serves both the API and the built front end, so Dokploy runs a single
# service on a single domain.

BUILD = "unknown"
_stamp = Path("/app/BUILD")
if _stamp.is_file():
    BUILD = _stamp.read_text(encoding="utf-8").strip()

static_dir = Path(settings.static_dir).resolve()

# Vite names every file under /assets after its content hash, so a changed file always has a new URL and
# the old one can be cached for good: a revisit then makes no request (and no 304 round trip) per chunk.
# index.html and the un-hashed public files must be re-checked, or a deploy would never reach returning users.
_IMMUTABLE = "public, max-age=31536000, immutable"
_REVALIDATE = "no-cache"
_PUBLIC_FILE = "public, max-age=86400"


class _HashedAssets(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = _IMMUTABLE
        return response


if static_dir.is_dir():
    app.mount("/assets", _HashedAssets(directory=static_dir / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # Any path that is not an API route is handled by the client-side router,
        # so every one of them returns index.html.
        requested = Path(full_path)
        if requested.anchor or ".." in requested.parts or "\\" in full_path:
            raise HTTPException(404, "Not found")
        try:
            candidate = (static_dir / requested).resolve()
            index = (static_dir / "index.html").resolve()
        except (OSError, RuntimeError, ValueError):
            raise HTTPException(404, "Not found") from None
        # Resolve symlinks as well as dot segments before serving public files.
        # The fallback index must satisfy the same boundary as ordinary assets.
        if not candidate.is_relative_to(static_dir) or not index.is_relative_to(static_dir):
            raise HTTPException(404, "Not found")
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": _REVALIDATE if candidate == index else _PUBLIC_FILE})
        if not index.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(index, headers={"Cache-Control": _REVALIDATE})
