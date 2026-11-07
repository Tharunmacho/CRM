"""Shared switch for tests that exercise the round-robin: new candidates are only handed to a recruiter
automatically when the agency setting `autoAssign` is on (it is off by default). Use it as
``pytestmark = pytest.mark.usefixtures("round_robin_on")`` after ``from auto_assign import round_robin_on``."""
import pytest


@pytest.fixture
def round_robin_on(monkeypatch):
    monkeypatch.setattr("app.services.agency_settings.auto_assign_enabled", lambda db: True)
