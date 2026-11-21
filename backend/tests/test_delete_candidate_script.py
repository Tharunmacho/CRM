"""scripts/delete_candidate_permanently.py on the guarded disposable PostgreSQL database (migrated to head)."""
import importlib.util
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import candidates as route
from app.db import models as m
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "delete_candidate_permanently.py"
spec = importlib.util.spec_from_file_location("delete_candidate_permanently", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

NOW = datetime(2026, 5, 1, tzinfo=timezone.utc)
REF = "CND-01006"
CARE_NUMBERS = __import__("itertools").count(1)
PHRASE = f"DELETE {REF} PERMANENTLY"


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    # The app's own file removal reads its storage settings through this function.
    monkeypatch.setattr(route, "get_settings", lambda: SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local"))
    # The shared-database guard is faked as "a plain local database" (the real one looks at backend/.env and the SSH tunnel, which a
    # test must never open); the tests of the guard itself replace it again.
    monkeypatch.setattr(script, "shared_database_reason", lambda: None)
    url = schema_database.url.render_as_string(hide_password=False)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin One", email="a-admin@example.test", role=m.Role.admin),
                    m.User(id="admin2", name="Admin Two", email="b-admin@example.test", role=m.Role.admin),
                    m.User(id="staff", name="Recruiter Raj", email="staff@example.test", role=m.Role.staff)])
        db.add(m.Client(id="cl", name="Supreme Packaging"))
        db.flush()
        db.add_all([m.Company(id="co", client_id="cl", name="Supreme Packaging LLC"), m.Designation(id="de", title="Machine Tool Operator")])
        db.flush()
        db.add_all([m.Order(id="o36", ref="ORD-2026-00036", company_id="co", designation_id="de", openings=2),
                    m.Order(id="o37", ref="ORD-2026-00037", company_id="co", designation_id="de", openings=1)])
        db.commit()
        yield SimpleNamespace(db=db, url=url, storage=tmp_path, engine=schema_database)


def candidate(db, key, ref, **values):
    db.add(m.Candidate(id=key, ref=ref, name=f"Person {key}", phone=f"90000{abs(hash(key)) % 100000:05d}", care_id=f"CI-{next(CARE_NUMBERS):03d}",
                       source=m.Source.walk_in, owner_id="staff", **values))
    db.flush()


def application(db, key, candidate_id, order_id, status=m.ApplicationStatus.active, stage=1):
    db.add(m.Application(id=key, candidate_id=candidate_id, order_id=order_id, status=status, owner_id="staff", current_stage=stage,
                         closure_reason=m.ClosureReason.unmapped if status is m.ApplicationStatus.closed else None))
    db.flush()
    for number in range(1, stage + 1):
        db.add(m.ApplicationStage(application_id=key, stage=number, data={}))
    db.flush()


def stored_file(env, entity, entity_id, name):
    path = env.storage / name
    path.write_bytes(b"data")
    env.db.add(m.Attachment(entity_type=entity, entity_id=entity_id, slot=name.split(".")[0], file_name=name, object_key=name, uploaded_by="staff"))
    env.db.flush()
    return path


def seed(env):
    """CND-01006 active at stage 1 on ORD-2026-00036 with documents, tasks and links; neighbours that must survive."""
    db = env.db
    candidate(db, "target", REF)
    candidate(db, "mate", "CND-01005")      # another candidate on the same order
    candidate(db, "bystander", "CND-01007")  # a different reference on another order
    application(db, "a-target", "target", "o36")
    application(db, "a-mate", "mate", "o36", stage=3)
    application(db, "a-by", "bystander", "o37")
    db.add_all([m.Task(id="t-target", kind=m.TaskKind.stage, title="Stage 1", candidate_id="target", application_id="a-target", owner_id="staff"),
                m.Task(id="t-mate", kind=m.TaskKind.stage, title="Stage", candidate_id="mate", application_id="a-mate", owner_id="staff")])
    db.flush()
    db.add_all([m.Remark(task_id="t-target", body="Called", author_id="staff"),
                m.Remark(task_id="t-mate", body="Keep", author_id="staff"),
                m.Notification(user_id="staff", kind="task", title="Gone task", link_to="/tasks?task=t-target"),
                m.Notification(user_id="staff", kind="candidate_assignment", title="Gone cand", link_to="/candidates/target"),
                m.Notification(user_id="staff", kind="candidate_assignment", title="Keep", link_to="/candidates/mate"),
                m.TodoItem(id="todo", owner_id="staff", title="Follow up", due_at=NOW, link_to="/candidates/target", link_label="Person"),
                m.DailyWorkItem(id="dw-target", owner_id="staff", work_date=date(2026, 5, 1), kind="stage", task_id="t-target", application_id="a-target",
                                candidate_id="target", stage=1, slot_no=1, session="morning", due_at=NOW),
                m.DailyWorkItem(id="dw-mate", owner_id="staff", work_date=date(2026, 5, 1), kind="stage", task_id="t-mate", application_id="a-mate",
                                candidate_id="mate", stage=3, slot_no=2, session="morning", due_at=NOW)])
    resume = stored_file(env, "candidate", "target", "resume.pdf")
    stage_doc = stored_file(env, "application", "a-target", "stage1.pdf")
    kept = stored_file(env, "candidate", "mate", "mate.pdf")
    kept_other = stored_file(env, "candidate", "bystander", "by.pdf")
    resume_row = db.scalar(select(m.Attachment.id).where(m.Attachment.file_name == "resume.pdf"))
    db.add(m.DocumentExtractionJob(id="job", owner_id="staff", group_id="g", kind="resume", filename="resume.pdf", content_type="application/pdf",
                                   object_key="resume.pdf", content_hash="h", size_kb=1, candidate_id="target", attachment_id=resume_row))
    db.commit()
    return SimpleNamespace(resume=resume, stage_doc=stage_doc, kept=kept, kept_other=kept_other)


def count(db, model, *where):
    return db.scalar(select(func.count()).select_from(model).where(*where))


def snapshot(env):
    """Row counts of everything the delete could touch, to prove a refusal or a dry run changed nothing."""
    db = env.db
    db.expire_all()
    return tuple(count(db, model) for model in (m.Candidate, m.Application, m.ApplicationStage, m.Task, m.Remark, m.Attachment,
                                                m.DocumentExtractionJob, m.Notification, m.TodoItem, m.DailyWorkItem, m.AuditEntry)) + (
        db.get(m.Order, "o36").status, tuple(sorted(p.name for p in env.storage.iterdir())))


def run(env, *argv, typed=None, interactive=True):
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        if typed is None:
            raise EOFError
        return typed

    code = script.main(["--database-url", env.url, *argv], input_fn=fake_input, is_interactive=interactive)
    return code, prompts


def test_help_runs_and_hard_codes_the_reference(capsys):
    with pytest.raises(SystemExit) as stop:
        script.main(["--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert "--candidate" in text and "--execute" in text and "CND-01006" in text
    assert script.ALLOWED_CANDIDATE_REF == "CND-01006" and script.CONFIRMATION_PHRASE == PHRASE


def test_dry_run_changes_nothing_and_previews_without_personal_numbers(env, capsys):
    seed(env)
    before = snapshot(env)
    code, prompts = run(env, "--candidate", REF)
    assert code == 0 and prompts == []
    text = capsys.readouterr().out
    assert before == snapshot(env)
    assert "dry run" in text.lower() and "DRY RUN: nothing was changed" in text
    assert f"{env.engine.url.host}:{env.engine.url.port}/{env.engine.url.database}" in text
    assert "Person target" in text and REF in text and "Care India ID: CI-0" in text and "Recruiter Raj" in text
    assert "ORD-2026-00036" in text and "Machine Tool Operator" in text and "Supreme Packaging" in text and "stage 1" in text
    assert "0 of 2 before, 0 of 2 after (free openings 2 before, 2 after)" in text
    assert "documents / attachments: 2" in text and "reading (OCR / extraction) jobs: 1" in text
    assert "tasks: 1" in text and "remarks: 1" in text and "notifications: 2" in text and "daily-work items: 1" in text
    assert "90000" not in text and "@example.test" not in text and "postgres:" not in text


def test_the_shared_database_is_refused_without_allow_remote_even_for_the_dry_run_and_before_any_connection(env, monkeypatch, capsys):
    seed(env)
    before = snapshot(env)

    def no_connection(*args, **kwargs):
        raise AssertionError("the guard must refuse before an engine is created")

    monkeypatch.setattr(script, "shared_database_reason", lambda: "the database is on port 55436, the SSH tunnel to the shared database.")
    with monkeypatch.context() as no_engine:
        no_engine.setattr(script, "create_engine", no_connection)
        for flags in ((), ("--execute",)):
            code, prompts = run(env, "--candidate", REF, *flags, typed=PHRASE)
            assert code == 2 and prompts == []
    captured = capsys.readouterr()
    assert "Refused: the database is on port 55436" in captured.err and "--allow-remote" in captured.err and captured.out == ""
    assert before == snapshot(env)
    # On purpose (--allow-remote) the dry run and the delete work as before.
    code, prompts = run(env, "--candidate", REF, "--allow-remote")
    assert code == 0 and prompts == [] and "DRY RUN: nothing was changed" in capsys.readouterr().out
    assert before == snapshot(env)
    code, _ = run(env, "--candidate", REF, "--execute", "--allow-remote", typed=PHRASE)
    assert code == 0
    env.db.expire_all()
    assert env.db.get(m.Candidate, "target") is None


def test_the_real_guard_refuses_when_no_env_file_was_chosen(env, monkeypatch, capsys):
    """backend/.env (the shared database) is what the app uses when CRM_ENV_FILE is not set: the real guard must refuse that."""
    import import_travelled_candidates     # the scripts folder is on sys.path since the script was loaded
    seed(env)
    before = snapshot(env)
    monkeypatch.setattr(script, "shared_database_reason", import_travelled_candidates.shared_database_reason)     # the real one
    monkeypatch.delenv("CRM_ENV_FILE", raising=False)
    assert script.shared_database_reason() is not None
    code, prompts = run(env, "--candidate", REF)
    assert code == 2 and prompts == [] and "Refused:" in capsys.readouterr().err
    assert before == snapshot(env)


def test_a_database_url_on_a_remote_host_or_the_tunnel_port_is_refused_without_allow_remote(env, monkeypatch, tmp_path, capsys):
    def no_connection(*args, **kwargs):
        raise AssertionError("the guard must refuse before an engine is created")

    monkeypatch.setattr(script, "create_engine", no_connection)
    tunnel = tmp_path / ".db-tunnel.json"
    tunnel.write_text('{"local_port": 55436}', encoding="utf-8")
    monkeypatch.setattr(script, "TUNNEL_FILE", tunnel)
    for url, expected in (("postgresql+psycopg://u@db.example.test:5432/crm", "'db.example.test'"),
                          ("postgresql+psycopg://u@127.0.0.1:55436/crm", "port 55436")):
        code = script.main(["--candidate", REF, "--database-url", url])
        assert code == 2 and expected in capsys.readouterr().err
    assert script.url_reason(script.make_url(env.url)) is None           # a plain local database is fine


def test_wrong_reference_is_refused_even_if_that_candidate_exists(env, capsys):
    seed(env)
    before = snapshot(env)
    for wrong in ("CND-01007", "CND-01005", "cnd-01006", "CND-01006 ", ""):
        code, prompts = run(env, "--candidate", wrong, "--execute", typed=PHRASE)
        assert code == 2 and prompts == []
    assert before == snapshot(env)
    assert "only deletes CND-01006" in capsys.readouterr().err


def test_missing_candidate_stops(env, capsys):
    code, _ = run(env, "--candidate", REF)
    assert code == 1 and "0 candidates have the reference" in capsys.readouterr().err


def test_duplicate_reference_cannot_exist_but_two_matches_would_stop(env, monkeypatch, capsys):
    seed(env)
    row = script.find_candidate(env.db, REF)[0]
    monkeypatch.setattr(script, "find_candidate", lambda db, ref: [row, row])
    before = snapshot(env)
    code, _ = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    assert code == 1 and "2 candidates have the reference" in capsys.readouterr().err
    assert before == snapshot(env)


@pytest.mark.parametrize("typed", [None, "", "yes", "delete CND-01006 permanently", PHRASE + " ", f"DELETE {REF}", "DELETE CND-01007 PERMANENTLY"])
def test_wrong_or_missing_confirmation_deletes_nothing(env, capsys, typed):
    seed(env)
    before = snapshot(env)
    code, prompts = run(env, "--candidate", REF, "--execute", typed=typed)
    assert code == 1 and len(prompts) == 1
    assert "confirmation was not typed exactly" in capsys.readouterr().err
    assert before == snapshot(env)
    assert env.db.get(m.Candidate, "target") is not None


def test_non_interactive_input_is_refused_in_process(env, capsys):
    seed(env)
    before = snapshot(env)
    code, prompts = run(env, "--candidate", REF, "--execute", typed=PHRASE, interactive=False)
    assert code == 1 and prompts == []
    assert "interactive terminal" in capsys.readouterr().err
    assert before == snapshot(env)


def test_non_interactive_stdin_is_refused_by_the_real_command(env, tmp_path_factory):
    seed(env)
    before = snapshot(env)
    env_file = tmp_path_factory.mktemp("envfile") / "scratch.env"
    env_file.write_text(f"DATABASE_URL={env.url}\n", encoding="utf-8")
    child_env = {**os.environ, "CRM_ENV_FILE": str(env_file), "PYTHONPATH": str(BACKEND)}
    result = subprocess.run([sys.executable, str(SCRIPT), "--candidate", REF, "--execute"], input=PHRASE + "\n", text=True, capture_output=True,
                            cwd=BACKEND, env=child_env, timeout=90)
    assert result.returncode == 1, result.stderr
    assert "interactive terminal" in result.stderr
    assert before == snapshot(env)
    # And its dry run works against the database given on the command line, printing host:port/db first.
    dry = subprocess.run([sys.executable, str(SCRIPT), "--candidate", REF, "--database-url", env.url], stdin=subprocess.DEVNULL, text=True,
                         capture_output=True, cwd=BACKEND, env=child_env, timeout=90)
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout.startswith(f"Database: {env.engine.url.host}:{env.engine.url.port}/{env.engine.url.database}")
    assert before == snapshot(env)


def test_confirmed_execute_deletes_only_the_target_and_frees_the_order(env, capsys):
    files = seed(env)
    db = env.db
    code, prompts = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    text = capsys.readouterr().out
    assert code == 0, text
    assert len(prompts) == 1
    db.expire_all()
    # The candidate and everything under it is gone.
    assert db.get(m.Candidate, "target") is None and count(db, m.Candidate, m.Candidate.ref == REF) == 0
    assert count(db, m.Application, m.Application.candidate_id == "target") == 0
    assert count(db, m.ApplicationStage, m.ApplicationStage.application_id == "a-target") == 0
    assert db.get(m.Task, "t-target") is None and count(db, m.Remark, m.Remark.task_id == "t-target") == 0
    assert db.get(m.DailyWorkItem, "dw-target") is None and db.get(m.DocumentExtractionJob, "job") is None
    assert count(db, m.Attachment, m.Attachment.entity_id.in_(["target", "a-target"])) == 0
    assert {n.title for n in db.scalars(select(m.Notification))} == {"Keep"}
    assert db.get(m.TodoItem, "todo").link_to is None
    assert not files.resume.exists() and not files.stage_doc.exists()
    # Everyone else is untouched: the other candidate on the same order, and a different CND reference.
    for key, ref in (("mate", "CND-01005"), ("bystander", "CND-01007")):
        assert db.get(m.Candidate, key).ref == ref
    assert db.get(m.Application, "a-mate").status is m.ApplicationStatus.active and db.get(m.Application, "a-mate").current_stage == 3
    assert db.get(m.Application, "a-by") is not None and db.get(m.Task, "t-mate") is not None and db.get(m.DailyWorkItem, "dw-mate") is not None
    assert count(db, m.ApplicationStage, m.ApplicationStage.application_id == "a-mate") == 3
    assert count(db, m.Remark, m.Remark.task_id == "t-mate") == 1
    assert count(db, m.Attachment, m.Attachment.entity_id.in_(["mate", "bystander"])) == 2
    assert files.kept.exists() and files.kept_other.exists()
    # The opening is free: nothing deployed, the order stays open, only the other candidate is still working on it.
    order = db.get(m.Order, "o36")
    assert order.status is m.OrderStatus.open and order.openings == 2
    assert count(db, m.Application, m.Application.order_id == "o36", m.Application.status == m.ApplicationStatus.deployed) == 0
    assert count(db, m.Application, m.Application.order_id == "o36") == 1
    audit = db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == "Deleted candidate"))
    assert audit.actor_id == "admin" and audit.entity_id == "target"          # first active admin by email order
    assert audit.meta["withdrawnFromOrders"] == ["ORD-2026-00036"] and audit.meta["ref"] == REF and audit.meta["documents"] == 2
    assert count(db, m.AuditEntry, m.AuditEntry.action == "Deleted candidate") == 1
    assert "Recorded under admin: Admin One" in text and "candidate CND-01006 gone: yes" in text
    assert "ORD-2026-00036: status open, 0 of 2 openings filled, 2 free, 1 candidate(s) still working on it" in text
    assert "RESULT: candidate permanently deleted" in text
    assert "90000" not in text and "@example.test" not in text


def test_actor_email_chooses_the_admin_and_a_non_admin_is_refused(env, capsys):
    seed(env)
    code, _ = run(env, "--candidate", REF, "--execute", "--actor-email", "staff@example.test", typed=PHRASE)
    assert code == 1 and "no active admin" in capsys.readouterr().err and env.db.get(m.Candidate, "target") is not None
    code, _ = run(env, "--candidate", REF, "--execute", "--actor-email", "B-ADMIN@example.test", typed=PHRASE)
    assert code == 0
    env.db.expire_all()
    assert env.db.scalar(select(m.AuditEntry.actor_id).where(m.AuditEntry.action == "Deleted candidate")) == "admin2"


def test_a_filled_order_that_the_removal_leaves_with_a_free_opening_opens_again(env, capsys):
    seed(env)
    db = env.db
    candidate(db, "flown", "CND-09999")
    application(db, "a-flown", "flown", "o36", m.ApplicationStatus.deployed, stage=13)
    db.get(m.Order, "o36").status = m.OrderStatus.filled      # filled while 1 of 2 are deployed: the app reopens it on a withdraw
    db.commit()
    code, _ = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    text = capsys.readouterr().out
    assert code == 0, text
    db.expire_all()
    assert db.get(m.Order, "o36").status is m.OrderStatus.open
    assert "order status filled -> open" in text
    assert "ORD-2026-00036: status open, 1 of 2 openings filled, 1 free" in text
    assert db.get(m.Application, "a-flown").status is m.ApplicationStatus.deployed


def test_a_genuinely_full_order_stays_filled(env, capsys):
    seed(env)
    db = env.db
    for key in ("f1", "f2"):
        candidate(db, key, f"CND-0999{key[1]}")
        application(db, f"a-{key}", key, "o36", m.ApplicationStatus.deployed, stage=13)
    db.get(m.Order, "o36").status = m.OrderStatus.filled
    db.commit()
    assert run(env, "--candidate", REF, "--execute", typed=PHRASE)[0] == 0
    db.expire_all()
    assert db.get(m.Order, "o36").status is m.OrderStatus.filled and db.get(m.Candidate, "target") is None


def test_deployed_candidate_is_refused(env, capsys):
    seed(env)
    application(env.db, "a-dep", "target", "o37", m.ApplicationStatus.deployed, stage=13)
    env.db.commit()
    before = snapshot(env)
    for flags in ((), ("--execute",)):
        code, prompts = run(env, "--candidate", REF, *flags, typed=PHRASE)
        assert code == 1 and prompts == []
    assert "was deployed through ORD-2026-00037" in capsys.readouterr().err
    assert before == snapshot(env)


def test_imported_and_travelled_candidate_is_refused_although_the_plain_admin_delete_would_allow_it(env, capsys):
    seed(env)
    target = env.db.get(m.Candidate, "target")
    target.source_detail = "Bulk import"
    target.traveller_id = "CI-T-001"
    env.db.commit()
    before = snapshot(env)
    code, prompts = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    assert code == 1 and prompts == []
    assert "Traveller ID" in capsys.readouterr().err
    assert before == snapshot(env)


@pytest.mark.parametrize("record", ["reserved", "payment"])
def test_candidate_with_an_invoice_or_payment_is_refused(env, capsys, record):
    seed(env)
    db = env.db
    invoice = m.CandidateInvoice(id="inv", candidate_id="target", application_id="a-target", scope_key="a-target", service_fee=5000,
                                 number="TEST/009" if record == "reserved" else None, status="pending_generation")
    db.add(invoice)
    db.flush()
    if record == "payment":
        db.add(m.InvoicePayment(invoice_id="inv", amount=1000, paid_on=NOW.date(), method="cash", idempotency_key="r", created_by="admin"))
    db.commit()
    before = snapshot(env)
    code, prompts = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    assert code == 1 and prompts == []
    assert "invoice" in capsys.readouterr().err
    assert before == snapshot(env)
    assert env.db.get(m.Candidate, "target") is not None


def test_failure_in_the_delete_leaves_everything_in_place(env, monkeypatch, capsys):
    seed(env)
    before = snapshot(env)
    real = script.candidate_routes._delete_candidate_rows

    def broken(*args, **kwargs):
        real(*args, **kwargs)      # the delete statement runs, then the step fails before the commit
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(script.candidate_routes, "_delete_candidate_rows", broken)
    code, _ = run(env, "--candidate", REF, "--execute", typed=PHRASE)
    assert code == 3 and "simulated failure" in capsys.readouterr().err
    assert before == snapshot(env)
    assert env.db.get(m.Candidate, "target") is not None
