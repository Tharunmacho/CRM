"""WhatsApp configuration safety: what counts as incomplete, and that nothing is sent or downloaded with a bad Graph version (pure unit tests, no database)."""
from types import SimpleNamespace

import pytest

from app.integrations import whatsapp as wa

COMPLETE = dict(whatsapp_phone_number_id="123456789", whatsapp_graph_version="v24.0", whatsapp_access_token="synthetic-token",
                whatsapp_app_secret="synthetic-secret", whatsapp_verify_token="synthetic-verify")


def settings(**over):
    return SimpleNamespace(**{**COMPLETE, **over})


def test_a_complete_configuration_has_nothing_missing():
    assert wa.missing_settings(settings()) == []


@pytest.mark.parametrize("name", ["whatsapp_phone_number_id", "whatsapp_graph_version", "whatsapp_access_token", "whatsapp_app_secret", "whatsapp_verify_token"])
def test_each_required_setting_is_reported_by_name_only(name):
    missing = wa.missing_settings(settings(**{name: None}))
    assert missing == [name.upper()]
    assert all("synthetic" not in item for item in missing)


@pytest.mark.parametrize("version", ["latest", "24", "v24", "24.0", "v24.0.1", "V24.0"])
def test_a_malformed_graph_version_counts_as_missing(version):
    assert wa.missing_settings(settings(whatsapp_graph_version=version)) == ["WHATSAPP_GRAPH_VERSION (format vN.N)"]


def test_a_settings_object_without_the_graph_version_does_not_crash():
    assert "WHATSAPP_GRAPH_VERSION" in wa.missing_settings(SimpleNamespace())


def test_graph_url_needs_a_numeric_id_and_a_valid_version():
    assert wa.graph_url(settings(), "123", "messages") == "https://graph.facebook.com/v24.0/123/messages"
    for bad in (settings(whatsapp_graph_version=None), settings(whatsapp_graph_version="latest")):
        with pytest.raises(wa.WhatsAppError):
            wa.graph_url(bad, "123", "messages")
    for identifier in (None, "", "12/3", "abc"):
        with pytest.raises(wa.WhatsAppError):
            wa.graph_url(settings(), identifier, "messages")


def test_the_default_graph_version_is_valid():
    from app.core.config import Settings
    assert wa.valid_graph_version(Settings(_env_file=None))


def test_a_send_with_a_bad_version_is_not_sent_rather_than_unknown():
    class Boom:
        def post(self, *args, **kwargs):
            raise AssertionError("nothing may leave when the settings are wrong")
    for bad in (settings(whatsapp_graph_version=None), settings(whatsapp_phone_number_id=None)):
        with pytest.raises(wa.SendRejected) as caught:
            wa.send_message(bad, {"to": "919000000001"}, client=Boom())
        assert not isinstance(caught.value, wa.SendUnknown)


def test_downloading_media_with_a_bad_version_never_calls_out():
    class Boom:
        def get(self, *args, **kwargs):
            raise AssertionError("no request may be made")
    with pytest.raises(wa.WhatsAppError):
        wa.download_media(settings(whatsapp_graph_version="nope"), "12345", client=Boom())


def test_signature_check_fails_closed_without_a_secret_or_header():
    assert wa.verify_signature(b"{}", None, "secret") is False
    assert wa.verify_signature(b"{}", "sha256=" + "0" * 64, "") is False
    assert wa.verify_signature(b"{}", "sha256=" + "0" * 64, "secret") is False


def test_an_empty_graph_version_means_the_default_and_a_set_one_wins(tmp_path):
    from app.core.config import Settings
    default = Settings(_env_file=None).whatsapp_graph_version
    empty = tmp_path / "empty.env"
    empty.write_text("WHATSAPP_GRAPH_VERSION=" + chr(10), encoding="utf-8")
    blank = tmp_path / "blank.env"
    blank.write_text("WHATSAPP_GRAPH_VERSION=   " + chr(10), encoding="utf-8")
    chosen = tmp_path / "chosen.env"
    chosen.write_text("WHATSAPP_GRAPH_VERSION=v99.1" + chr(10), encoding="utf-8")
    assert Settings(_env_file=empty).whatsapp_graph_version == default == "v24.0"
    assert Settings(_env_file=blank).whatsapp_graph_version == default
    assert Settings(_env_file=chosen).whatsapp_graph_version == "v99.1"
    assert wa.valid_graph_version(Settings(_env_file=empty))
