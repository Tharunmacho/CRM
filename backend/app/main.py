import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.api.routers import applications, auth, candidates, data, files, ocr, inbox, users, reports, workflow, whatsapp, document_jobs, attendance
from app.core.config import get_settings
from app.core.timing import ApiTimingMiddleware
from app.db.models import User
from app.db.session import SessionLocal, engine
from app.db.schema_readiness import UPDATE_REQUIRED, schema_status
from app.integrations.redis import close_redis, redis_health
from app.integrations.ocr.inspection import find_tesseract
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
        with engine.connect() as connection:
            schema = schema_status(connection)
        if schema["status"] == "ok":
            with SessionLocal() as db:
                ensure_bootstrap(db)
            log.info("PostgreSQL connected; application state ready.")
        else:
            log.error("Database migration required: current=%s expected=%s. No startup data was changed.", schema["current"], schema["expected"])
    except Exception:  # reported by /api/health; account login never seeds data
        log.error("Application state initialization failed.")

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
app.include_router(reports.router)
app.include_router(reports.dashboard_router)
app.include_router(workflow.router)
app.include_router(whatsapp.router)
app.include_router(attendance.router)


@app.on_event("shutdown")
def on_shutdown() -> None:
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

if static_dir.is_dir():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

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
            return FileResponse(candidate)
        if not index.is_file():
            raise HTTPException(404, "Not found")
        return FileResponse(index)
