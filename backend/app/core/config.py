from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    Configuration comes from the environment. In Dokploy these are set on the service;
    locally they come from backend/.env, independent of the working directory.
    get_settings() can select a separate file through CRM_ENV_FILE for an isolated
    process, without merging the default file into that configuration.
    """

    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    # postgresql+psycopg://user:password@host:5432/care_india
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/care_india"

    # The agency's timezone decides what "today" and "end of day" mean for the
    # 48-hour SLA, the daily-remark check and the escalation scan.
    agency_timezone: str = "Asia/Kolkata"

    whatsapp_enabled: bool = False
    whatsapp_phone_number_id: str | None = None
    whatsapp_business_account_id: str | None = None
    whatsapp_graph_version: str | None = None
    whatsapp_access_token: str | None = Field(default=None, repr=False)
    whatsapp_app_secret: str | None = Field(default=None, repr=False)
    whatsapp_verify_token: str | None = Field(default=None, repr=False)

    # Optional S3-compatible storage; existing local keys remain readable.
    storage_backend: str = "local"
    s3_bucket: str | None = None
    s3_region: str = "us-east-1"
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = Field(default=None, repr=False)
    s3_secret_access_key: str | None = Field(default=None, repr=False)
    s3_prefix: str = "crm"

    # Where the built SPA lives inside the image.
    static_dir: str = str(BACKEND_ROOT / "static")

    # Uploaded files. Must be a mounted volume in production, or every redeploy
    # discards the passport scans and receipts.
    storage_dir: str = str(BACKEND_ROOT / "data" / "uploads")

    cors_origins: str = "http://localhost:5173"

    # Development convenience: when the database only lacks newer migrations, apply them
    # when the backend starts instead of refusing to start. Leave off for any database a
    # deployed copy of the CRM also uses, because a migrated schema is newer than that
    # copy and it will report "update required" until it is redeployed.
    crm_auto_migrate: bool = False

    # Where signed-in users reach the CRM, used only to send browsers back after a
    # mailbox OAuth consent. Blank keeps the redirect on the API's own origin, which is
    # correct in production where one service serves both the API and the SPA.
    app_base_url: str = ""

    # Optional Redis connection. Blank leaves Redis disabled; credentials stay
    # out of Settings repr and are never included in the health response.
    redis_url: str | None = Field(default=None, repr=False)

    # Names this installation in the shared queue tables. Blank: the host name plus a hash of the storage location
    # (services/instance_guard.py). Set it when two installations share one database but keep their files apart.
    instance_name: str | None = None

    # Veris OCR, which reads resumes and passports on the add-candidate form. Left
    # unset, the form is simply filled by hand. Same names the veriis SDK reads.
    veris_ocr_base_url: str | None = None
    veris_ocr_api_key: str | None = None

    # Local inspection always runs before a specialist Veris extraction.
    # A blank command discovers Tesseract from PATH or its usual Windows location.
    tesseract_cmd: str | None = None
    ocr_languages: str = "eng"
    ocr_max_pages: int = Field(default=300, ge=1, le=1000)
    ocr_page_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    ocr_document_timeout_seconds: float = Field(default=600.0, ge=10, le=1800)
    # Pages read at the same time by the local OCR (one Tesseract process per page).
    ocr_page_workers: int = Field(default_factory=lambda: max(1, min(8, os.cpu_count() or 2)), ge=1, le=32)
    # How long a worker keeps checking an accepted Veris job before it hands the slot back
    # and revisits the job later. Zero keeps the single-poll behaviour.
    ocr_provider_wait_seconds: float = Field(default=20.0, ge=0, le=120)
    # Veris jobs sent side by side (the kinds of one document, and the chunks of a long one).
    veris_parallel_jobs: int = Field(default=4, ge=1, le=16)
    # A document with more pages than this is split into jobs of this many pages ...
    veris_chunk_pages: int = Field(default=20, ge=2, le=200)
    # ... and a single resume repeats this many pages between neighbouring chunks, so a job
    # or an education entry that straddles a boundary is read whole by at least one chunk.
    veris_chunk_overlap_pages: int = Field(default=1, ge=0, le=3)
    # Veris fields that have no place on the candidate or the form are always listed for a person
    # to read (Unmapped). True also sends the extraction to review for them, not only for
    # pages not sent, chunk conflicts, rows beyond the form's capacity or fields this app has never seen.
    veris_unmapped_needs_review: bool = False
    # Send the pages that the quick first look has already settled to Veris while the rest are still being read.
    veris_early_dispatch: bool = True

    # Reading queue. Every uploaded or emailed document is a durable job; workers claim jobs one by one
    # (FOR UPDATE SKIP LOCKED plus a lease) and read them side by side.
    # Threads inside one worker process (default 8). Parallelism comes from threads, not processes: a reading is mostly
    # waiting on the document reader, and each extra process costs memory plus its own polling against the database.
    extraction_concurrency: int = Field(default=8, ge=1, le=16)
    # Worker processes the supervisor starts for the upload queue (and for the e-mail queue). One is enough for the
    # default 8 side-by-side readings; raise it (up to 4) only when the machine has the memory and cores for it.
    extraction_workers: int = Field(default=1, ge=1, le=4)
    # Hard cap on documents read at once by all the worker processes of one queue together
    # (workers x threads). A larger product is scaled down per process so the machine and the
    # document reader are never flooded; 12 CPU-bound readings per queue is already a lot.
    extraction_max_parallel: int = Field(default=16, ge=1, le=32)
    # A claimed job is re-claimed by another worker when its lease runs out without a heartbeat.
    extraction_lease_seconds: int = Field(default=180, ge=30, le=3600)
    # SIGTERM: how long running jobs may finish before their claims are released.
    extraction_shutdown_seconds: int = Field(default=30, ge=0, le=600)

    # Incoming mail only. Credentials remain in the environment, never API output.
    email_ingestion_enabled: bool = False
    email_poll_seconds: int = Field(default=120, ge=30, le=3600)
    imap_server: str | None = None
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_username: str | None = None
    imap_password: str | None = Field(default=None, repr=False)
    imap_use_ssl: bool = True
    imap_folder: str = "INBOX"
    # Optional multiple accounts using the same keys as the single IMAP settings.
    email_accounts_json: str | None = Field(default=None, repr=False)
    # OAuth encryption uses a persistent Fernet key. Tokens never leave the server.
    mail_token_encryption_key: str | None = Field(default=None, repr=False)
    gmail_client_id: str | None = None
    gmail_client_secret: str | None = Field(default=None, repr=False)
    gmail_redirect_uri: str | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: str | None = Field(default=None, repr=False)
    microsoft_redirect_uri: str | None = None
    microsoft_tenant: str = "common"
    smtp_server: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_use_ssl: bool = False

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3(cls, v: str) -> str:
        """
        Hosting dashboards hand out `postgresql://` (and sometimes the older
        `postgres://`). SQLAlchemy reads both as a request for psycopg2, which this
        image does not carry — only psycopg3. Normalise rather than make whoever sets
        the variable remember a driver suffix.
        """
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+psycopg://" + v[len("postgresql://") :]
        return v

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    override = os.environ.get("CRM_ENV_FILE")
    if override is None:
        return Settings()
    if not override.strip() or not Path(override).is_file():
        raise ValueError("CRM_ENV_FILE must name an existing environment file.")
    return Settings(_env_file=Path(override))
