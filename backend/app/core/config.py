from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """
    Configuration comes from the environment. In Dokploy these are set on the service;
    locally they come from backend/.env, independent of the working directory.
    """

    model_config = SettingsConfigDict(env_file=BACKEND_ROOT / ".env", extra="ignore")

    # postgresql+psycopg://user:password@host:5432/care_india
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/care_india"

    # The agency's timezone decides what "today" and "end of day" mean for the
    # 48-hour SLA, the daily-remark check and the escalation scan.
    agency_timezone: str = "Asia/Kolkata"

    # Where the built SPA lives inside the image.
    static_dir: str = str(BACKEND_ROOT / "static")

    # Uploaded files. Must be a mounted volume in production, or every redeploy
    # discards the passport scans and receipts.
    storage_dir: str = str(BACKEND_ROOT / "data" / "uploads")

    cors_origins: str = "http://localhost:5173"

    # Optional Redis connection. Blank leaves Redis disabled; credentials stay
    # out of Settings repr and are never included in the health response.
    redis_url: str | None = Field(default=None, repr=False)

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
    return Settings()
