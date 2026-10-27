"""Ownerless intake: why a candidate waits, how an assignment clears it, and the WhatsApp link.

Isolated PostgreSQL only; every message and number here is synthetic."""
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.api.routers import candidates as candidate_routes
from app.api.routers.data import ReassignIn, reassign
from app.core.deps import current_user
from app.db.models import (AuditEntry, Candidate, CandidateStatus, Notification, Source, Task, User,
                           WhatsAppChannel, WhatsAppConversation, WhatsAppMessage)
from app.db.session import get_db
from app.services import whatsapp_chat as chat
from app.services.candidate_review import WAITING_TITLE, assign_waiting_candidates
from app.services.candidate_search import STATUS_LABELS, Search, pool_counts, search_candidates
from app.services.whatsapp_intake import process_message, receive_payload
from test_database_naming import schema_database
from test_email_intake import ORIGINAL, extraction
from test_whatsapp_intake import payload, receipt, setup  # noqa: F401  (fixture)
from test_workflow_scheduler import workspace  # noqa: F401  (fixture)

PHONE = "12345"
SENDER = "919999999999"


def ingest(sessions, settings, **fields):
    """Run one WhatsApp document through intake and return its candidate."""
    message_id = receipt(sessions, settings)
    warnings = fields.pop("warnings", None)
    output = extraction(fields={"name": "Waiting Person", "phone": "9000000042", "email": "waiting@example.test", **fields},
                        warnings=warnings)
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: deepcopy(output))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        return db.get(Candidate, row.candidate_id)


def nobody_eligible(sessions):
    with sessions() as db:
        for user in db.scalars(select(User).where(User.id.in_(["owner", "other"]))):
            user.receives_auto_assign = False
        db.commit()


def run_reassign(sessions, candidate_ids, to="owner"):
    with sessions() as db:
        db.get(User, to).receives_auto_assign = True
        reassign(ReassignIn(candidateIds=candidate_ids, toUserId=to, reason="Supervisor assignment"), db, db.get(User, "admin"))


# ----------------------------------------------------------------- C2: review reasons

def test_ownerless_intake_waits_and_supervisors_get_a_distinct_notice(setup):
    sessions, settings = setup
    nobody_eligible(sessions)
    candidate = ingest(sessions, settings)
    assert candidate.owner_id is None and candidate.needs_review and candidate.status == CandidateStatus.needs_review
    with sessions() as db:
        notices = list(db.scalars(select(Notification).where(Notification.link_to == f"/candidates/{candidate.id}")))
        assert {n.user_id for n in notices} == {"admin", "manager"}
        assert {n.title for n in notices} == {WAITING_TITLE}
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_assigned_intake_keeps_the_ordinary_receipt_notice(setup):
    sessions, settings = setup
    candidate = ingest(sessions, settings)
    with sessions() as db:
        notices = list(db.scalars(select(Notification).where(Notification.link_to == f"/candidates/{candidate.id}")))
        assert {n.title for n in notices} == {"Candidate received by WhatsApp"}
        # PRD: "The owner and the admin are notified" of an assignment; managers are not.
        assert {n.user_id for n in notices} == {candidate.owner_id, "admin"}


def test_reassign_clears_review_when_the_missing_owner_was_the_only_reason(setup):
    sessions, settings = setup
    nobody_eligible(sessions)
    candidate = ingest(sessions, settings)
    run_reassign(sessions, [candidate.id])
    with sessions() as db:
        saved = db.get(Candidate, candidate.id)
        assert saved.owner_id == saved.intake_owner_id == "owner"
        assert saved.needs_review is False and saved.status == CandidateStatus.new
        task = db.scalar(select(Task).where(Task.candidate_id == candidate.id))
        assert task.owner_id == "owner" and (task.due_at - task.sla_started_at).total_seconds() == 48 * 3600


@pytest.mark.parametrize("fields,reason", [({"warnings": ["Low confidence"]}, "ocr warning"), ({"phone": ""}, "missing phone")])
def test_reassign_keeps_review_for_quality_problems(setup, fields, reason):
    sessions, settings = setup
    nobody_eligible(sessions)
    candidate = ingest(sessions, settings, **fields)
    assert candidate.needs_review, reason
    run_reassign(sessions, [candidate.id])
    with sessions() as db:
        saved = db.get(Candidate, candidate.id)
        assert saved.owner_id == "owner" and saved.needs_review and saved.status == CandidateStatus.needs_review
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate.id)) == 1


def test_reassigning_an_owned_candidate_never_clears_review(setup):
    sessions, settings = setup
    candidate = ingest(sessions, settings, warnings=["Check the passport page"])
    assert candidate.owner_id == "owner" and candidate.needs_review
    run_reassign(sessions, [candidate.id], to="other")
    with sessions() as db:
        assert db.get(Candidate, candidate.id).needs_review


# -------------------------------------------------------------------- waiting sweep

def add_waiting(sessions, count=3):
    with sessions() as db:
        for number in range(count):
            db.add(Candidate(id=f"wait-{number}", ref=f"CND-WAIT-{number}", name=f"Waiting {number}", phone=f"9100000{number:03d}",
                             source=Source.email, needs_review=True, status=CandidateStatus.needs_review,
                             created_at=datetime(2026, 10, 1, number, tzinfo=timezone.utc)))
        db.commit()


def test_sweep_assigns_round_robin_in_one_transaction_with_tasks_notices_and_audit(setup):
    sessions, _ = setup
    add_waiting(sessions)
    with sessions() as db:
        for identifier in ("owner", "other"):
            db.get(User, identifier).receives_auto_assign = True
        db.commit()
        result = assign_waiting_candidates(db, "admin", "Admin")
        db.commit()
        assert result == {"assigned": 3, "reviewCleared": 3, "remaining": 0}
        owners = [db.get(Candidate, f"wait-{n}").owner_id for n in range(3)]
        assert owners[0] != owners[1] and owners[0] == owners[2] and set(owners) == {"owner", "other"}
        assert all(not db.get(Candidate, f"wait-{n}").needs_review for n in range(3))
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id.like("wait-%"))) == 3
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == "Candidate assigned from waiting list")) == 3
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.title == "Candidate assigned")) >= 3
        assert "manager" not in set(db.scalars(select(Notification.user_id).where(Notification.title == "Candidate assigned")))
        assert assign_waiting_candidates(db, "admin", "Admin")["assigned"] == 0


def test_sweep_stops_cleanly_when_nobody_is_eligible(setup):
    sessions, _ = setup
    nobody_eligible(sessions)
    add_waiting(sessions, 2)
    with sessions() as db:
        assert assign_waiting_candidates(db, "admin", "Admin") == {"assigned": 0, "reviewCleared": 0, "remaining": 2}


def test_assign_waiting_endpoint_is_supervisor_only_and_reports_counts(setup):
    sessions, _ = setup
    add_waiting(sessions, 2)
    with sessions() as db:
        db.get(User, "owner").receives_auto_assign = True
        db.commit()

    def client(actor):
        app = FastAPI(); app.include_router(candidate_routes.router)
        def database():
            with sessions() as session: yield session
        app.dependency_overrides[get_db] = database
        def user():
            with sessions() as session: return session.get(User, actor)
        app.dependency_overrides[current_user] = user
        return TestClient(app)

    assert client("owner").post("/api/candidates/assign-waiting").status_code == 403
    response = client("manager").post("/api/candidates/assign-waiting")
    assert response.status_code == 200 and response.json() == {"assigned": 2, "reviewCleared": 2, "remaining": 0}
    counts = client("manager").get("/api/candidates/pool-counts").json()
    assert counts["unassigned"] == 0 and counts["needs_review"] == 0


# ----------------------------------------------------------------- C3: Data Bank pools

def seed_pools(sessions):
    with sessions() as db:
        db.add_all([Candidate(id="mine", ref="CND-M", name="Owned", phone="9200000001", source=Source.walk_in, owner_id="owner"),
                    Candidate(id="reviewed", ref="CND-R", name="Reviewed", phone="9200000002", source=Source.email, owner_id="owner",
                              needs_review=True, status=CandidateStatus.needs_review),
                    Candidate(id="nobody", ref="CND-N", name="Nobody", phone="9200000003", source=Source.email,
                              needs_review=True, status=CandidateStatus.needs_review),
                    Candidate(id="declined", ref="CND-D", name="Declined", phone="9200000004", source=Source.walk_in,
                              owner_id="other", status=CandidateStatus.not_interested)])
        db.commit()


def listed(db, **filters):
    return {row["id"] for row in search_candidates(db, Search(**filters), 0, 50)["items"]}


def test_unassigned_and_needs_review_pools_and_counts(setup):
    sessions, _ = setup
    seed_pools(sessions)
    with sessions() as db:
        assert listed(db, pool="unassigned") == {"nobody"}
        assert listed(db, pool="needs_review") == {"reviewed", "nobody"}
        counts = pool_counts(db)
        assert counts["unassigned"] == 1 and counts["needs_review"] == 2 and counts["not_interested"] == 1
        assert counts["all"] == db.scalar(select(func.count()).select_from(Candidate))
        assert pool_counts(db, "owner")["unassigned"] == 0 and pool_counts(db, "owner")["all"] == 3


@pytest.mark.parametrize("term", ["Needs assignment", "needs assign", "Unassigned", "unassigned"])
def test_owner_column_filter_accepts_the_label_users_see_and_the_stored_one(setup, term):
    sessions, _ = setup
    seed_pools(sessions)
    with sessions() as db:
        assert listed(db, column_filters='{"owner": "%s"}' % term) == {"nobody"}


def test_status_filter_accepts_labels_and_raw_values_and_sorting_still_works(setup):
    sessions, _ = setup
    seed_pools(sessions)
    assert STATUS_LABELS["needs_review"] == "Needs review"
    with sessions() as db:
        for term in ("Needs review", "needs_review", "needs"):
            assert listed(db, column_filters='{"status": "%s"}' % term) == {"reviewed", "nobody"}
        assert listed(db, column_filters='{"status": "Not interested"}') == {"declined"}
        ordered = [row["id"] for row in search_candidates(db, Search(sort="owner", direction="asc"), 0, 50)["items"]]
        assert set(ordered) >= {"mine", "reviewed", "nobody", "declined"}
        assert ordered[0] == "nobody"  # "Needs assignment" sorts before "Other" and "Owner"


# ------------------------------------------------------------ C6: WhatsApp conversation link

def test_whatsapp_intake_links_the_senders_conversation_to_the_candidate(setup):
    sessions, settings = setup
    candidate = ingest(sessions, settings)
    with sessions() as db:
        conversation = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.peer_number == SENDER))
        assert conversation is not None and conversation.candidate_id == candidate.id
        assert db.scalar(select(AuditEntry).where(AuditEntry.entity_id == conversation.id)).meta["candidateId"] == candidate.id
        # The recruiter who owns the candidate may now message them.
        assert chat._send_permission(db, conversation, db.get(User, "owner"))[0]
        assert not chat._send_permission(db, conversation, db.get(User, "other"))[0]


def test_intake_never_replaces_an_existing_supervisor_link(setup):
    sessions, settings = setup
    with sessions() as db:
        db.add(WhatsAppConversation(id="existing", phone_number_id=PHONE, peer_number=SENDER, candidate_id="candidate"))
        db.commit()
    ingest(sessions, settings)
    with sessions() as db:
        assert db.get(WhatsAppConversation, "existing").candidate_id == "candidate"


def test_matching_by_number_links_unlinked_conversations_and_ignores_ambiguity(setup):
    sessions, _ = setup
    with sessions() as db:
        db.add_all([WhatsAppConversation(id="by-phone", phone_number_id=PHONE, peer_number="919300000001"),
                    WhatsAppConversation(id="by-whatsapp", phone_number_id=PHONE, peer_number="919300000002"),
                    WhatsAppConversation(id="shared", phone_number_id=PHONE, peer_number="919300000003"),
                    WhatsAppConversation(id="unrelated", phone_number_id=PHONE, peer_number="919399999999")])
        first = Candidate(id="num-a", ref="CND-NA", name="By phone", phone="+91 93000 00001", source=Source.email)
        second = Candidate(id="num-b", ref="CND-NB", name="By WhatsApp", phone="9301111111", whatsapp="+91 93000-00002", source=Source.email)
        twins = [Candidate(id="num-c", ref="CND-NC", name="Twin one", phone="9302222221", whatsapp="9300000003", source=Source.email),
                 Candidate(id="num-d", ref="CND-ND", name="Twin two", phone="9302222222", whatsapp="9300000003", source=Source.email)]
        db.add_all([first, second, *twins]); db.flush()
        for candidate in (first, second, *twins):
            assert chat.link_conversations_for_candidate(db, candidate)
        db.commit()
        links = {row.id: row.candidate_id for row in db.scalars(select(WhatsAppConversation))}
        assert links == {"by-phone": "num-a", "by-whatsapp": "num-b", "shared": None, "unrelated": None}


def test_inbound_message_from_a_known_candidate_number_links_the_conversation(setup):
    sessions, settings = setup
    with sessions() as db:
        db.add(Candidate(id="known", ref="CND-K", name="Known sender", phone="+91 99999 99999", source=Source.walk_in, owner_id="owner"))
        db.commit()
    body = payload("wamid.known")
    with sessions() as db:
        assert receive_payload(db, body, settings) == 1
        chat.record_inbound_payload(db, body, settings)
        db.commit()
    with sessions() as db:
        conversation = db.scalar(select(WhatsAppConversation).where(WhatsAppConversation.peer_number == SENDER))
        assert conversation.candidate_id == "known"


def test_candidate_conversation_lookup_is_cheap_and_empty_when_unlinked(setup, monkeypatch):
    sessions, settings = setup
    from app.api.routers import whatsapp_chat
    app = FastAPI(); app.include_router(whatsapp_chat.router)
    def database():
        with sessions() as session: yield session
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[current_user] = lambda: User(id="owner", role=chat.Role.staff, active=True)
    app.dependency_overrides[whatsapp_chat.current_user] = app.dependency_overrides[current_user]
    app.dependency_overrides[whatsapp_chat.get_db] = database
    monkeypatch.setattr(whatsapp_chat, "get_settings", lambda: settings)
    with sessions() as db:
        db.add(WhatsAppConversation(id="linked", phone_number_id=PHONE, peer_number="919400000001", candidate_id="candidate"))
        db.commit()
    client = TestClient(app)
    found = client.get("/api/whatsapp/candidates/candidate/conversation").json()["conversation"]
    assert found["id"] == "linked" and found["candidate"]["id"] == "candidate"
    assert client.get("/api/whatsapp/candidates/no-conversation/conversation").json() == {"conversation": None}
