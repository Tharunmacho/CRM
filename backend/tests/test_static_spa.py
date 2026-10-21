"""Public SPA files stay inside their build directory; no DB or providers used."""
import importlib.util
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from app.core.config import Settings


@pytest.fixture
def web(tmp_path):
    public = tmp_path / "static"
    (public / "assets").mkdir(parents=True)
    (public / "fonts").mkdir()
    (public / "index.html").write_text("<html>Synthetic CRM shell</html>", encoding="utf-8")
    (public / "assets" / "app.js").write_text("window.synthetic = true", encoding="utf-8")
    (public / "fonts" / "poppins.woff2").write_bytes(b"synthetic-font")
    private = tmp_path / "private-probe.txt"
    private.write_text("synthetic-private-evidence", encoding="utf-8")
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://unused@127.0.0.1:1/static_tests",
                        static_dir=str(public), storage_dir=str(tmp_path / "uploads"), redis_url=None)
    # Load a separate actual main module so its static mount uses this fixture;
    # don't replace app.main or change routes used by the other test modules.
    spec = importlib.util.spec_from_file_location("app._static_spa_test", Path(__file__).parents[1] / "app" / "main.py")
    module = importlib.util.module_from_spec(spec)
    with patch("app.core.config.get_settings", return_value=settings):
        spec.loader.exec_module(module)
    # Not entering TestClient's context deliberately avoids startup workers/DB.
    with patch.object(module.engine, "connect", side_effect=AssertionError("Static requests must not open a database connection")):
        client = TestClient(module.app)
        try:
            yield client, public, private
        finally:
            client.close()


@pytest.mark.parametrize("route", ["/", "/dashboard", "/candidates/example", "/attendance?date=2026-10-21"])
def test_spa_navigation_returns_the_public_shell(web, route):
    client, _, _ = web
    response = client.get(route)
    assert response.status_code == 200
    assert response.text == "<html>Synthetic CRM shell</html>"


@pytest.mark.parametrize("route,body", [("/assets/app.js", b"window.synthetic = true"),
                                       ("/fonts/poppins.woff2", b"synthetic-font")])
def test_built_assets_and_public_fonts_are_served(web, route, body):
    response = web[0].get(route)
    assert response.status_code == 200
    assert response.content == body


@pytest.mark.parametrize("route", ["/%2e%2e/private-probe.txt", "/..%2fprivate-probe.txt",
                                    "/fonts/%2e%2e/%2e%2e/private-probe.txt",
                                    "/%2e%2e%5cprivate-probe.txt",
                                    "/assets/%2e%2e/%2e%2e/private-probe.txt"])
def test_encoded_traversal_cannot_return_private_sibling_files(web, route):
    response = web[0].get(route)
    assert response.status_code == 404
    assert b"synthetic-private-evidence" not in response.content


def test_absolute_path_cannot_replace_the_public_root(web):
    client, _, private = web
    # Preserve the encoded leading slash or drive prefix through HTTP routing.
    route = "/" + quote(str(private.resolve()), safe="")
    response = client.get(route)
    assert response.status_code == 404
    assert b"synthetic-private-evidence" not in response.content


@pytest.mark.parametrize("relative", ["leaked.txt", "fonts/leaked.txt", "assets/leaked.txt", "index.html"])
def test_symlinks_cannot_expose_files_outside_the_public_root(web, relative):
    client, public, private = web
    link = public / relative
    if link.exists():
        link.unlink()
    try:
        link.symlink_to(private)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows requires symlink privilege for this regression")
        raise
    response = client.get("/dashboard" if relative == "index.html" else "/" + relative)
    assert response.status_code == 404
    assert b"synthetic-private-evidence" not in response.content
