"""A preview file must never inherit connection settings from backend/.env."""
import os

import pytest

from app.core.config import Settings, get_settings


@pytest.fixture
def default_env(tmp_path, monkeypatch):
    # Use only synthetic temporary configuration, irrespective of the developer's
    # shell or real backend/.env. Clearing the cache restores isolation per test.
    names = {name.casefold() for name in Settings.model_fields} | {"crm_env_file"}
    for name in list(os.environ):
        if name.casefold() in names:
            monkeypatch.delenv(name)
    path = tmp_path / "default.env"
    path.write_text(
        "DATABASE_URL=postgresql://default:example@default.invalid/care_india\n"
        "REDIS_URL=redis://default.invalid:6379/0\n"
        "EMAIL_INGESTION_ENABLED=true\n"
        "WHATSAPP_ENABLED=true\n"
        "IMAP_SERVER=imap.default.invalid\n"
        "IMAP_PASSWORD=example-default-password\n"
        "SMTP_SERVER=smtp.default.invalid\n"
        "MAIL_TOKEN_ENCRYPTION_KEY=example-default-key\n"
        "VERIS_OCR_API_KEY=example-default-ocr-key\n"
        "S3_BUCKET=example-default-bucket\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(Settings.model_config, "env_file", path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def test_alternate_file_excludes_default_connections_and_preserves_blanks(default_env, tmp_path, monkeypatch):
    preview = tmp_path / "preview.env"
    preview.write_text(
        "DATABASE_URL=postgresql://preview:example@127.0.0.1:55435/crm_local_preview\n"
        "REDIS_URL=\n"
        "EMAIL_INGESTION_ENABLED=false\n"
        "WHATSAPP_ENABLED=false\n"
        "EMAIL_ACCOUNTS_JSON=[]\n"
        "MAIL_TOKEN_ENCRYPTION_KEY=\n"
        "IMAP_PASSWORD=\n"
        "VERIS_OCR_API_KEY=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CRM_ENV_FILE", str(preview))

    settings = get_settings()

    assert settings.database_url == "postgresql+psycopg://preview:example@127.0.0.1:55435/crm_local_preview"
    assert settings.redis_url == ""
    assert settings.mail_token_encryption_key == ""
    assert settings.imap_password == ""
    assert settings.veris_ocr_api_key == ""
    assert settings.email_accounts_json == "[]"
    assert settings.email_ingestion_enabled is False
    assert settings.whatsapp_enabled is False
    # Fields absent from the override use model defaults, never the default file.
    assert settings.imap_server is None
    assert settings.smtp_server is None
    assert settings.s3_bucket is None
    assert Settings.model_config["env_file"] == default_env


def test_without_override_existing_default_file_behavior_is_unchanged(default_env):
    settings = get_settings()

    assert settings.database_url == "postgresql+psycopg://default:example@default.invalid/care_india"
    assert settings.redis_url == "redis://default.invalid:6379/0"
    assert settings.email_ingestion_enabled is True
    assert settings.whatsapp_enabled is True
    assert settings.imap_server == "imap.default.invalid"


@pytest.mark.parametrize("override", ["", "   ", "missing"])
def test_invalid_explicit_override_fails_without_loading_default(default_env, tmp_path, monkeypatch, override):
    value = str(tmp_path / "missing.env") if override == "missing" else override
    monkeypatch.setenv("CRM_ENV_FILE", value)

    with pytest.raises(ValueError, match="CRM_ENV_FILE must name an existing environment file"):
        get_settings()


def test_explicit_process_environment_still_takes_precedence(default_env, tmp_path, monkeypatch):
    preview = tmp_path / "preview.env"
    preview.write_text("DATABASE_URL=postgresql://preview:example@127.0.0.1/file_preview\n", encoding="utf-8")
    monkeypatch.setenv("CRM_ENV_FILE", str(preview))
    monkeypatch.setenv("DATABASE_URL", "postgresql://preview:example@127.0.0.1/process_preview")

    assert get_settings().database_url == "postgresql+psycopg://preview:example@127.0.0.1/process_preview"
