"""Stage 6 as the client's tracker sheet needs it: advance status, the amount, the optional total service charge, and the Advance cell text."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import ApplicationStage, Role
from app.domain.stages import StageOptions, stage_by_no, validate_payload
from app.services import bulk_candidate_import as bulk
from app.services.advance_text import advance_cell, advance_status, format_advance, short_amount
from app.services.daily_work import payment_fields, stage_facts
from pydantic import ValidationError

# The DB-backed cases reuse the application workflow fixtures (a disposable PostgreSQL schema, never a shared database).
from test_application_workflow import attachment, complete, mapped, reach, save, version, workflow  # noqa: F401
from test_database_naming import schema_database  # noqa: F401
from test_reporting import FILTERS, NOW, STAFF, dataset, report_data  # noqa: F401


# ------------------------------------------------------------------ the cell text (pure)

@pytest.mark.parametrize(("value", "text"), [
    (60000, "60k"), (10000, "10k"), (12500, "12.5k"), (1000, "1k"), (1100, "1.1k"), (100000, "100k"), (250000, "250k"),
    (500, "500"), (999, "999"), (12345, "12345"), (10050, "10050"), (1500.75, "1500.75"), ("40000.00", "40k"), (0, ""), (None, ""), ("x", ""),
])
def test_amounts_are_written_with_k_for_round_thousands_only(value, text):
    assert short_amount(value) == text


@pytest.mark.parametrize(("status", "amount", "total", "text"), [
    ("collected", 60000, None, "60k Collected"),
    ("collected", 10000, 50000, "10k collected/50k"),
    ("collected", 5000, 40000, "5k collected/40k"),
    ("collected", 60000, 60000, "60k collected/60k"),
    ("collected", 12500, 50000, "12.5k collected/50k"),
    ("collected", 12345, 50000, "12345 collected/50k"),
    ("collected", None, None, "Collected"),
    ("collected", None, 50000, "Collected/50k"),
    ("pending", 10000, None, "Pending/10k"),          # the advance expected
    ("pending", 10000, 45000, "Pending/45k"),         # the total, when it is known
    ("pending", None, 45000, "Pending/45k"),
    ("pending", None, None, "Pending"),
    (None, 60000, None, "60k Collected"),             # an amount alone was always the advance collected
    (None, 10000, 50000, "10k collected/50k"),
    (None, None, None, None),
    (None, None, 50000, None),
])
def test_the_advance_cell_follows_the_owners_style(status, amount, total, text):
    assert format_advance(status, amount, total, "INR", "INR") == text


def test_the_currency_code_follows_the_text_only_when_it_is_not_the_agency_default():
    assert format_advance("collected", 60000, None, "INR", "INR") == "60k Collected"
    assert format_advance("collected", 60000, None, "inr", " INR ") == "60k Collected"
    assert format_advance("collected", 60000, None, "AED", "INR") == "60k Collected AED"
    assert format_advance("collected", 600, 1500, "USD", "INR") == "600 collected/1.5k USD"
    assert format_advance("pending", 10000, None, "USD", "INR") == "Pending/10k USD"
    assert format_advance("pending", None, None, "USD", "INR") == "Pending"       # no figure, no code
    assert format_advance("collected", 60000, None, None, "INR") == "60k Collected"
    assert format_advance("collected", 60000, None, "AED", None) == "60k Collected AED"


def test_the_status_of_a_record_reads_old_records_as_before():
    assert advance_status({"advanceStatus": "pending", "amount": 10000}) == "pending"
    assert advance_status({"advanceStatus": "collected"}) == "collected"
    assert advance_status({"amount": 5000}) == "collected"                              # an old record with only an amount
    assert advance_status({"collected": "yes"}) == "collected"
    assert advance_status({"collected": "no"}) == "pending"
    assert advance_status({"collected": "no", "amount": 5000}) == "collected"           # what the Tasks screen has always read
    assert advance_status({}) is None and advance_status(None) is None
    assert advance_status({"stop": "not_paid", "collected": "no"}) is None
    assert advance_status({"collected": "yes", "stop": "declined"}) is None
    assert advance_status({"advanceStatus": "collected", "amount": 1}, 1) == "collected"


# ------------------------------------------------------------------ the stage rules (pure)

def test_stage_6_validates_status_amounts_and_total():
    out = validate_payload(6, {"advanceStatus": "collected", "amount": "10000", "serviceChargeTotal": "50000.50", "amountCurrency": "USD"},
                           StageOptions(currencies=frozenset({"INR", "USD"}), default_currency="INR"))
    assert out == {"advanceStatus": "collected", "collected": "yes", "amount": 10000.0, "serviceChargeTotal": 50000.5, "amountCurrency": "USD"}
    # Pending: the amount is the advance expected; the old yes/no answer follows the status.
    pending = validate_payload(6, {"advanceStatus": "pending", "amount": 10000, "collected": "yes"})
    assert pending["collected"] == "no" and pending["amount"] == 10000.0 and pending["amountCurrency"] == "INR"
    # A record written before the status existed is left alone: no status is invented.
    assert validate_payload(6, {"amount": "10.25"}) == {"amount": 10.25, "amountCurrency": "INR"}
    assert validate_payload(6, {"collected": "yes", "amount": 5000}) == {"collected": "yes", "amount": 5000.0, "amountCurrency": "INR"}
    # Both amounts are optional, and the total alone is fine.
    assert validate_payload(6, {"advanceStatus": "pending"})["collected"] == "no"
    assert validate_payload(6, {"advanceStatus": "pending", "serviceChargeTotal": 45000})["serviceChargeTotal"] == 45000.0
    assert validate_payload(6, {"advanceStatus": "collected", "amount": 50000, "serviceChargeTotal": 50000})["amount"] == 50000.0
    # Blank is unset.
    assert "serviceChargeTotal" not in validate_payload(6, {"advanceStatus": "pending", "serviceChargeTotal": ""})


@pytest.mark.parametrize("data", [
    {"advanceStatus": "later"}, {"advanceStatus": "collected", "amount": 0}, {"advanceStatus": "collected", "amount": -5},
    {"advanceStatus": "collected", "amount": 100, "serviceChargeTotal": 0}, {"serviceChargeTotal": -1}, {"serviceChargeTotal": True},
    {"serviceChargeTotal": "NaN"}, {"serviceChargeTotal": 1.234}, {"serviceChargeTotal": "lots"},
    {"advanceStatus": "collected", "amount": 60000, "serviceChargeTotal": 50000},
    {"advanceStatus": "pending", "amount": 60000, "serviceChargeTotal": 50000},
    {"advanceStatus": "collected", "amount": 100, "amountCurrency": "XXX"},
])
def test_stage_6_refuses_bad_amounts_and_a_total_below_the_advance(data):
    with pytest.raises(ValidationError):
        validate_payload(6, data)
    assert not stage_by_no(6).evaluate({"feedback": "Checked.", **data}).can_complete


def test_the_total_below_the_advance_is_reported_against_its_own_field():
    with pytest.raises(ValidationError) as caught:
        validate_payload(6, {"amount": 60000, "serviceChargeTotal": 50000})
    assert [error["loc"] for error in caught.value.errors()] == [("serviceChargeTotal",)]


def test_completing_stage_6_needs_nothing_new_and_names_the_outcome():
    ok = lambda data: stage_by_no(6).evaluate({"feedback": "Checked.", **data})
    assert ok({"collected": "yes", "amount": 20}).outcome == "Collected"          # an old flow
    assert ok({}).outcome == "Completed manually"                                  # an old flow with nothing
    assert ok({"collected": "no"}).outcome == "Completed manually"
    assert ok({"advanceStatus": "collected"}).outcome == "Collected"
    assert ok({"advanceStatus": "pending"}).outcome == "Advance pending"
    assert ok({"advanceStatus": "pending", "amount": 10000, "serviceChargeTotal": 50000}).can_complete
    stopped = ok({"advanceStatus": "pending", "stop": "not_paid"})
    assert stopped.closes == "advance_not_paid" and stopped.outcome == "Not paid"


# ------------------------------------------------------------------ the fact reader the Tasks screen uses (pure)

def row(app, stage=6, *, attempt=1, amount=None, currency=None, data=None):
    return (app, stage, attempt, datetime(2026, 11, 3, 10, attempt, tzinfo=timezone.utc), amount, currency, data)


def test_stage_facts_carry_status_total_and_the_expected_advance():
    facts = stage_facts([
        row("a1", amount=10000, currency="INR", data={"advanceStatus": "collected", "amount": 10000, "serviceChargeTotal": 50000, "amountCurrency": "INR"}),
        row("a2", amount=None, data={"advanceStatus": "pending", "amount": 10000, "amountCurrency": "USD", "serviceChargeTotal": 45000}),
        row("a3", amount=5000, currency="INR", data={"collected": "yes", "amount": 5000}),                      # old: only an amount
        row("a4", data={"collected": "no"}),
        row("a5", data={}),
        row("a6", data={"advanceStatus": "pending"}),
        row("a7", amount=7000, currency="AED", data={"collected": "yes"}),
        row("a8", attempt=1, amount=999, currency="INR", data={"collected": "yes"}),
        row("a8", attempt=2, data={"advanceStatus": "pending", "amount": 2000}),                                # the latest attempt wins
        row("a9", stage=10, data={"googleReview": True}),
    ])
    summary = {k: (v["advance"], v["status"], v["total"], v["expected"], v["currency"]) for k, v in facts.items()}
    assert summary == {
        "a1": ((10000.0, "INR"), "collected", 50000.0, None, "INR"),
        "a2": (None, "pending", 45000.0, 10000.0, "USD"),
        "a3": ((5000.0, "INR"), "collected", None, None, "INR"),
        "a4": (None, "pending", None, None, "INR"),
        "a5": (None, None, None, None, "INR"),
        "a6": (None, "pending", None, None, "INR"),
        "a7": ((7000.0, "AED"), "collected", None, None, "AED"),
        "a8": (None, "pending", None, 2000.0, "INR"),
        "a9": (None, None, None, None, None),
    }
    assert facts["a9"]["review"] is True


def test_payment_fields_keep_the_old_keys_and_add_the_new_ones():
    facts = stage_facts([
        row("a1", amount=10000, currency="INR", data={"advanceStatus": "collected", "amount": 10000, "serviceChargeTotal": 50000}),
        row("a2", data={"advanceStatus": "pending", "amount": 10000, "amountCurrency": "AED"}),
    ])
    one = payment_fields(SimpleNamespace(id="a1", current_stage=6), facts)
    assert one == {"advanceAmount": 10000.0, "advanceCurrency": "INR", "googleReview": None,
                   "advanceStatus": "collected", "advanceExpected": None, "serviceChargeTotal": 50000.0, "travelDate": None}
    two = payment_fields(SimpleNamespace(id="a2", current_stage=6), facts)
    assert two["advanceAmount"] is None and two["advanceStatus"] == "pending" and two["advanceExpected"] == 10000.0
    assert two["advanceCurrency"] == "AED"
    none = payment_fields(SimpleNamespace(id="zz", current_stage=3), facts)
    assert none == {"advanceAmount": None, "advanceCurrency": None, "googleReview": None,
                    "advanceStatus": None, "advanceExpected": None, "serviceChargeTotal": None, "travelDate": None}


def test_the_sheet_cell_is_written_from_a_fact_entry():
    facts = stage_facts([
        row("a1", amount=10000, currency="INR", data={"advanceStatus": "collected", "amount": 10000, "serviceChargeTotal": 50000}),
        row("a2", data={"advanceStatus": "pending", "amount": 10000}),
        row("a3", data={"advanceStatus": "pending", "amount": 10000, "serviceChargeTotal": 45000}),
        row("a4", amount=60000, currency="INR", data={"amount": 60000}),
        row("a5", data={}),
        row("a6", amount=600, currency="USD", data={"collected": "yes"}),
    ])
    cells = {k: advance_cell(v, "INR") for k, v in facts.items()}
    assert cells == {"a1": "10k collected/50k", "a2": "Pending/10k", "a3": "Pending/45k", "a4": "60k Collected", "a5": None, "a6": "600 Collected USD"}
    assert advance_cell(None, "INR") is None and advance_cell({}, "INR") is None


# ------------------------------------------------------------------ the reports keep to money collected (pure)

def test_a_pending_advance_is_not_an_advance_collection(dataset):
    stages = {s["id"]: s for s in dataset["stages"]}
    stages["receipt-inr"]["amount"] = None
    stages["receipt-inr"]["data"] = {"advanceStatus": "pending", "amount": 100.25}
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert {r["id"] for r in result["advances"]} == {"receipt-aed"}
    stages["receipt-inr"]["data"] = {"amount": 100.25}                               # an old record with only the data amount still counts
    result = report_data(dataset, STAFF, FILTERS, now=NOW)
    assert {r["id"] for r in result["advances"]} == {"receipt-inr", "receipt-aed"}


# ------------------------------------------------------------------ the bulk import sheet (pure)

def test_the_import_sheet_accepts_a_status_and_a_total_service_charge():
    csv = ("Folder,Phone,Advance amount,Advance status,Total service charge\n"
           "Asha,9876543210,10000,,50000\n"
           "Bina,9876543211,10000,Pending,\n"
           "Cara,9876543212,,Collected,\n").encode()
    sheet = bulk.read_manifest(csv, "sheet.csv")
    assert "Advance status" in sheet["recognised"] and "Total service charge" in sheet["recognised"]
    options = StageOptions(currencies=frozenset({"INR"}), default_currency="INR")
    asha, bina, cara = (bulk.stage_inputs(r, {}, options) for r in sheet["rows"])
    assert asha[0][6] == {"collected": "yes", "advanceStatus": "collected", "amount": "10000", "serviceChargeTotal": "50000"}   # collected by default
    assert bina[0][6] == {"collected": "no", "advanceStatus": "pending", "amount": "10000"}                                   # amount = expected
    assert cara[0][6] == {"collected": "yes", "advanceStatus": "collected"}
    assert not asha[1] and not bina[1] and not cara[1]
    for built in (asha, bina, cara):
        assert stage_by_no(6).evaluate(built[0][6], options).can_complete


@pytest.mark.parametrize("header", ["Total service charge", "Service charge", "total_service_charge", "Service fee"])
def test_the_total_column_is_found_by_its_usual_names(header):
    sheet = bulk.read_manifest(f"Phone,Advance amount,{header}\n9876543210,5000,40000\n".encode(), "sheet.csv")
    assert sheet["rows"][0]["service_charge_total"] == "40000"


def test_the_import_reports_a_total_below_the_advance_and_an_unreadable_status():
    options = StageOptions(currencies=frozenset({"INR"}), default_currency="INR")
    _, problems = bulk.stage_inputs({"advance_amount": "60000", "service_charge_total": "50000"}, {}, options)
    assert [p["level"] for p in problems] == ["error"] and "Total service charge" in problems[0]["message"]
    stages, problems = bulk.stage_inputs({"advance_amount": "60000", "advance_status": "maybe"}, {}, options)
    assert [p["level"] for p in problems] == ["warning"] and stages[6]["advanceStatus"] == "collected"
    _, problems = bulk.stage_inputs({"advance_amount": "60000", "service_charge_total": "lots"}, {}, options)
    assert [p["level"] for p in problems] == ["error"]
    # No amount and no status: the stage reads as it always did.
    assert bulk.stage_inputs({}, {}, options)[0][6] == {"collected": "yes"}


@pytest.mark.parametrize(("text", "status"), [("Pending", "pending"), ("not paid", "pending"), ("Due", "pending"), ("Collected", "collected"),
                                              ("paid", "collected"), ("", None), ("maybe", None)])
def test_the_status_cell_is_read_in_the_words_the_office_uses(text, status):
    assert bulk.parse_advance_status(text) == status


# ------------------------------------------------------------------ save, complete, reopen through the real endpoints

def complete_8(env, app_id, data):
    return complete(env, app_id, data)


def stage_row(env, app_id, attempt=None):
    with env.sessions() as db:
        query = select(ApplicationStage).where(ApplicationStage.application_id == app_id, ApplicationStage.stage == 6).order_by(ApplicationStage.attempt)
        rows = list(db.scalars(query))
        return rows[-1] if attempt is None else rows[attempt - 1]


def test_a_collected_advance_with_a_total_is_saved_in_the_stage_record(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    complete_8(env, app_id, {"advanceStatus": "collected", "amount": "10000", "serviceChargeTotal": "50000", "mode": "upi"})
    row = stage_row(env, app_id)
    assert row.outcome == "Collected" and float(row.amount) == 10000.0 and row.amount_currency == "INR"
    assert row.data["advanceStatus"] == "collected" and row.data["collected"] == "yes"
    assert row.data["amount"] == 10000.0 and row.data["serviceChargeTotal"] == 50000.0 and row.data["amountCurrency"] == "INR"


def test_a_pending_advance_moves_on_and_never_counts_as_collected_money(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    result = complete_8(env, app_id, {"advanceStatus": "pending", "amount": "10000", "serviceChargeTotal": "45000"})
    assert result["currentStage"] == 7
    row = stage_row(env, app_id)
    assert row.outcome == "Advance pending" and row.amount is None            # the report column holds collected money only
    assert row.data["amount"] == 10000.0 and row.data["serviceChargeTotal"] == 45000.0 and row.data["collected"] == "no"
    facts = stage_facts([(row.application_id, 6, row.attempt, row.completed_at, row.amount, row.amount_currency, row.data)])
    assert advance_cell(facts[app_id], "INR") == "Pending/45k"
    assert facts[app_id]["advance"] is None and facts[app_id]["expected"] == 10000.0


def test_a_pending_advance_needs_no_amount_at_all(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    complete_8(env, app_id, {"advanceStatus": "pending"})
    row = stage_row(env, app_id)
    assert row.outcome == "Advance pending" and row.amount is None


def test_an_old_amount_only_stage_6_is_still_valid_and_reads_as_collected(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    complete_8(env, app_id, {"collected": "yes", "amount": "1500.25", "mode": "upi"})
    row = stage_row(env, app_id)
    assert row.outcome == "Collected" and float(row.amount) == 1500.25
    assert "advanceStatus" not in row.data and "serviceChargeTotal" not in row.data
    facts = stage_facts([(row.application_id, 6, row.attempt, row.completed_at, row.amount, row.amount_currency, row.data)])
    assert facts[app_id]["status"] == "collected" and advance_cell(facts[app_id], "INR") == "1500.25 Collected"


def test_the_api_refuses_a_total_below_the_advance_when_saving_and_completing(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    bad = {"advanceStatus": "collected", "amount": 60000, "serviceChargeTotal": 50000}
    refused = save(env, app_id, bad)
    assert refused.status_code == 422
    assert any("serviceChargeTotal" in item["field"] for item in refused.json()["detail"]["errors"])
    done = env.client.post(f"/api/applications/{app_id}/complete", json={"lock_version": version(env, app_id), "data": {"feedback": "Moving on.", **bad}})
    assert done.status_code == 422
    assert save(env, app_id, {"advanceStatus": "pending", "amount": 10000, "serviceChargeTotal": 45000}).status_code == 200


def test_a_draft_keeps_the_status_and_the_total_and_a_reopened_stage_returns_them(workflow):
    env = workflow
    app_id = mapped(env)
    reach(env, app_id, 6)
    draft = {"advanceStatus": "pending", "amount": 10000, "serviceChargeTotal": 45000}
    assert save(env, app_id, draft).status_code == 200
    saved = env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    assert saved["advanceStatus"] == "pending" and saved["serviceChargeTotal"] == 45000.0 and saved["collected"] == "no"
    complete_8(env, app_id, draft)
    env.actor = SimpleNamespace(id="admin", role=Role.admin)
    reopened = env.client.post(f"/api/applications/{app_id}/reopen", json={"stage": 6, "reason": "The candidate paid", "lock_version": version(env, app_id)})
    assert reopened.status_code == 200, reopened.text
    back = env.client.get(f"/api/applications/{app_id}/draft").json()["draft"]
    assert back["advanceStatus"] == "pending" and back["serviceChargeTotal"] == 45000.0 and back["amount"] == 10000.0
    # The record is corrected: it was collected after all.
    complete_8(env, app_id, {**back, "advanceStatus": "collected", "feedback": "Paid in full."})
    latest = stage_row(env, app_id)
    assert latest.attempt == 2 and latest.outcome == "Collected" and float(latest.amount) == 10000.0
    first = stage_row(env, app_id, attempt=1)
    assert first.data["advanceStatus"] == "pending" and first.reopened_reason == "The candidate paid"
    facts = stage_facts([(r.application_id, 6, r.attempt, r.completed_at, r.amount, r.amount_currency, r.data) for r in (first, latest)])
    assert advance_cell(facts[app_id], "INR") == "10k collected/45k"
