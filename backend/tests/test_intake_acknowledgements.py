"""Real transaction/HTTP tests; all provider delivery is replaced by local fakes."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.routers.intake_acknowledgements import router
from app.core.security import SESSION_COOKIE
from app.db.models import (AuditEntry, Base, Candidate, DocumentPersonGroup, EmailIngestionJob, IntakeAcknowledgement,
    IntakeAcknowledgementSettings, MailAccount, MailMessage, MultiPersonIntake, Role,
    Session as LoginSession, Source, User, WhatsAppChannel, WhatsAppMessage, WhatsAppSendAttempt, WhatsAppTemplate)
from app.db.session import get_db
from app.integrations.mail.imap import MailConfig
from app.integrations.mail.mime import parse_message
from app.integrations.mail.smtp import MailSendError
from app.services import intake_acknowledgements as ack
from test_database_naming import schema_database


@pytest.fixture
def workspace(schema_database, monkeypatch):
    Base.metadata.create_all(schema_database)
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    with sessions() as db:
        db.add_all([User(id='admin', name='Admin', email='admin@example.test', role=Role.admin),
            User(id='staff', name='Staff', email='staff@example.test', role=Role.staff)])
        db.flush()
        db.add_all([MailAccount(id='mailbox', address='jobs@example.test', provider='zoho'),
            Candidate(id='candidate', ref='CND-ACK', name='Synthetic applicant', phone='9000000001', source=Source.email),
            LoginSession(id='ack-admin', user_id='admin', expires_at=ack.utcnow() + timedelta(hours=1)),
            LoginSession(id='ack-staff', user_id='staff', expires_at=ack.utcnow() + timedelta(hours=1))])
        db.commit()
    monkeypatch.setattr(ack, 'account_config', lambda *_: MailConfig(host='imap.example.test', port=993,
        username='jobs@example.test', password='synthetic-only', smtp_host='smtp.example.test', smtp_port=465, smtp_use_ssl=True))
    return sessions


@pytest.fixture
def client(workspace):
    app = FastAPI()
    app.include_router(router)
    def database():
        with workspace() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as result:
        result.cookies.set(SESSION_COOKIE, 'ack-admin')
        yield result


def enable(db, *, at=None):
    row = IntakeAcknowledgementSettings(source_kind='email', channel_id='mailbox', enabled=True,
        enabled_at=at or ack.utcnow() - timedelta(hours=1), body=ack.DEFAULT_BODY, updated_by='admin')
    db.add(row)
    db.flush()
    return row


def source(db, id='receipt', *, status='completed', header_changes=None, received_at=None):
    headers = {'parsed': True, 'from': ['Applicant <applicant@example.test>'],
        'return-path': ['<applicant@example.test>'], 'auto-submitted': [], 'precedence': [],
        'list-id': [], 'list-unsubscribe': [], 'x-auto-response-suppress': []}
    headers.update(header_changes or {})
    row = MailMessage(id=id, account_id='mailbox', provider_message_id=id, from_address='applicant@example.test',
        subject='My resume', received_at=received_at or ack.utcnow(), candidate_id='candidate' if status == 'completed' else None,
        message_id_header=f'<{id}@example.test>', automation_headers=headers)
    db.add(row)
    db.flush()
    db.add(EmailIngestionJob(message_id=id, status=status, candidate_id=row.candidate_id))
    db.flush()
    return row


def test_settings_default_off_role_preview_and_new_receipts_only(workspace, client):
    payload = {'enabled': True, 'body': ack.DEFAULT_BODY}
    initial = client.get('/api/intake-acknowledgements/email/mailbox')
    assert initial.status_code == 200 and initial.json()['enabled'] is False
    assert client.patch('/api/intake-acknowledgements/email/mailbox', json=payload).status_code == 422
    client.cookies.set(SESSION_COOKIE, 'ack-staff')
    assert client.patch('/api/intake-acknowledgements/email/mailbox', json={**payload, 'previewedBody': ack.DEFAULT_BODY}).status_code == 403
    client.cookies.set(SESSION_COOKIE, 'ack-admin')
    with workspace() as db:
        old = source(db, received_at=ack.utcnow() - timedelta(days=7))
        assert ack.queue_intake_acknowledgement(db, 'email', old.id) is None
        db.commit()
    enabled = client.patch('/api/intake-acknowledgements/email/mailbox', json={**payload, 'previewedBody': ack.DEFAULT_BODY})
    assert enabled.status_code == 200 and enabled.json()['enabled'] is True
    with workspace() as db:
        assert ack.queue_intake_acknowledgement(db, 'email', 'receipt') is None
        fresh = source(db, id='fresh')
        row = ack.queue_intake_acknowledgement(db, 'email', fresh.id)
        assert row.status == 'pending'
        db.commit()


@pytest.mark.parametrize('status', ['pending', 'processing', 'retry', 'needs_review', 'failed', 'ignored'])
def test_unsaved_or_reviewing_intake_never_queues(workspace, status):
    with workspace() as db:
        enable(db)
        row = source(db, status=status)
        assert ack.queue_intake_acknowledgement(db, 'email', row.id) is None
        assert db.scalar(select(func.count()).select_from(IntakeAcknowledgement)) == 0


@pytest.mark.parametrize('headers', [
    {'parsed': False}, {'return-path': ['<>']}, {'return-path': []},
    {'historical_import': True},
    {'auto-submitted': ['auto-generated']}, {'auto-submitted': ['auto-replied']},
    {'precedence': ['bulk']}, {'list-id': ['Recruitment newsletter']},
    {'x-auto-response-suppress': ['All']}, {'from': ['One <one@example.test>, Two <two@example.test>']},
])
def test_automatic_bulk_legacy_and_ambiguous_mail_is_suppressed(workspace, headers):
    with workspace() as db:
        enable(db)
        message = source(db, header_changes=headers)
        row = ack.queue_intake_acknowledgement(db, 'email', message.id)
        assert row.status == 'suppressed' and row.last_error


def test_source_and_intent_rollback_together_and_duplicate_callback_keeps_one(workspace):
    with workspace() as db:
        enable(db); source(db)
        first = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        repeated = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        assert repeated.id == first.id
        db.rollback()
    with workspace() as db:
        assert db.get(MailMessage, 'receipt') is None
        assert db.scalar(select(func.count()).select_from(IntakeAcknowledgement)) == 0
        enable(db); source(db)
        db.commit()
    def queue():
        with workspace() as db:
            row = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
            db.commit()
            return row.id
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids = list(executor.map(lambda _: queue(), range(2)))
    assert len(set(ids)) == 1
    with workspace() as db:
        assert db.scalar(select(func.count()).select_from(IntakeAcknowledgement)) == 1


def test_reviewed_multi_person_receipt_gets_one_ack_only_after_every_group_saved(workspace):
    with workspace() as db:
        enable(db)
        message = source(db)
        message.candidate_id = None
        bundle = MultiPersonIntake(id='bundle', source_type='email', source_id=message.id, created_by='admin', status='review')
        db.add(bundle); db.flush()
        db.add_all([DocumentPersonGroup(id='person-a', bundle_id=bundle.id, ordinal=1, label='First', status='committed', candidate_id='candidate'),
            DocumentPersonGroup(id='person-b', bundle_id=bundle.id, ordinal=2, label='Second')])
        assert ack.queue_intake_acknowledgement(db, 'email', message.id) is None
        bundle.status = 'completed'
        assert ack.queue_intake_acknowledgement(db, 'email', message.id) is None
        db.get(DocumentPersonGroup, 'person-b').status = 'committed'
        db.get(DocumentPersonGroup, 'person-b').candidate_id = 'candidate'
        row = ack.queue_intake_acknowledgement(db, 'email', message.id)
        assert row.status == 'pending'
        assert ack.queue_intake_acknowledgement(db, 'email', message.id).id == row.id


def test_sender_observes_committed_intent_and_replayed_worker_never_sends_again(workspace):
    with workspace() as db:
        enable(db); source(db)
        row = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        db.commit(); row_id = row.id
    calls = []
    def deliver(config, outgoing, recipients, *, envelope_from):
        with workspace() as db:
            durable = db.get(IntakeAcknowledgement, row_id)
            assert durable.status == 'sending' and durable.attempts == 1
        assert outgoing['Auto-Submitted'] == 'auto-replied'
        assert outgoing['X-Auto-Response-Suppress'] == 'All'
        assert outgoing['In-Reply-To'] == '<receipt@example.test>'
        assert recipients == ['applicant@example.test'] and envelope_from == ''
        calls.append(outgoing['Message-ID'])
        return []
    for _ in range(2):
        ack.dispatch_email_acknowledgement(row_id, sessions=workspace, settings=SimpleNamespace(email_ingestion_enabled=True), sender=deliver)
    assert len(calls) == 1
    with workspace() as db:
        assert db.get(IntakeAcknowledgement, row_id).status == 'sent'


def test_unknown_send_is_not_retryable_and_admin_can_resolve_without_sending(workspace, client):
    with workspace() as db:
        enable(db); source(db)
        row = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        db.commit(); row_id = row.id
    calls = []
    def ambiguous(*args, **kwargs):
        calls.append(1)
        raise MailSendError('SMTP response lost after submission.', unknown=True)
    for _ in range(2):
        ack.dispatch_email_acknowledgement(row_id, sessions=workspace, settings=SimpleNamespace(email_ingestion_enabled=True), sender=ambiguous)
    assert len(calls) == 1
    assert client.post(f'/api/intake-acknowledgements/attempts/{row_id}/retry').status_code == 409
    assert client.post(f'/api/intake-acknowledgements/attempts/{row_id}/resolve', json={
        'outcome': 'sent', 'providerChecked': False, 'reason': 'Not checked yet'}).status_code == 409
    resolved = client.post(f'/api/intake-acknowledgements/attempts/{row_id}/resolve', json={
        'outcome': 'sent', 'providerChecked': True, 'reason': 'Matching provider reference found in Sent mail'})
    assert resolved.status_code == 200 and resolved.json()['status'] == 'sent'
    assert len(calls) == 1


def test_definite_failure_retry_and_disconnect_pause_preserve_identity(workspace, client):
    with workspace() as db:
        enable(db); source(db)
        row = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        db.commit(); row_id = row.id
    def refused(*args, **kwargs):
        raise MailSendError('Recipient rejected', unknown=False)
    ack.dispatch_email_acknowledgement(row_id, sessions=workspace, settings=SimpleNamespace(email_ingestion_enabled=True), sender=refused)
    for _ in range(2):
        result = client.post(f'/api/intake-acknowledgements/attempts/{row_id}/retry')
        assert result.status_code == 200 and result.json()['id'] == row_id
    with workspace() as db:
        db.get(MailAccount, 'mailbox').disconnected_at = ack.utcnow(); db.commit()
    ack.dispatch_email_acknowledgement(row_id, sessions=workspace, settings=SimpleNamespace(email_ingestion_enabled=True), sender=lambda *_a, **_k: pytest.fail('Disconnected mailbox must not send'))
    with workspace() as db:
        assert db.get(IntakeAcknowledgement, row_id).status == 'paused'
    assert client.post(f'/api/intake-acknowledgements/attempts/{row_id}/retry').status_code == 409


def test_disable_cancels_pending_and_does_not_reset_unknown(workspace, client):
    with workspace() as db:
        enable(db); source(db); source(db, id='other')
        queued = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        uncertain = ack.queue_intake_acknowledgement(db, 'email', 'other')
        uncertain.status = 'unknown'
        db.commit(); queued_id, uncertain_id = queued.id, uncertain.id
    response = client.patch('/api/intake-acknowledgements/email/mailbox', json={'enabled': False, 'body': ack.DEFAULT_BODY})
    assert response.status_code == 200
    with workspace() as db:
        assert db.get(IntakeAcknowledgement, queued_id).status == 'suppressed'
        assert db.get(IntakeAcknowledgement, uncertain_id).status == 'unknown'


def test_mime_retains_verified_raw_automation_headers_and_absence_is_distinct():
    raw = b'From: Applicant <applicant@example.test>\r\nReturn-Path: <>\r\nAuto-Submitted: auto-generated\r\nSubject: Receipt\r\n\r\nTest'
    message = parse_message(raw, provider_id='mime', uid=1, uid_validity='1', folder='INBOX', received_at=datetime.now(timezone.utc), unread=True)
    assert message.automation_headers['parsed'] is True
    assert message.automation_headers['return-path'] == ['<>']
    assert message.automation_headers['auto-submitted'] == ['auto-generated']
    assert message.automation_headers['precedence'] == []


def wa_source(db, *, received_at=None):
    db.add(WhatsAppChannel(phone_number_id='123456', display_number='Synthetic only'))
    db.add(IntakeAcknowledgementSettings(source_kind='whatsapp', channel_id='123456', enabled=True,
        enabled_at=ack.utcnow() - timedelta(days=3), body=ack.DEFAULT_BODY, updated_by='admin'))
    db.flush()
    message = WhatsAppMessage(id='wa-receipt', provider_message_id='wamid.synthetic-only', phone_number_id='123456',
        sender_number='919000000001', message_type='document', status='completed', candidate_id='candidate',
        received_at=received_at or ack.utcnow())
    db.add(message); db.flush()
    return message


def test_whatsapp_ack_uses_same_transaction_outbox_and_exposes_provider_status(workspace):
    with workspace() as db:
        message = wa_source(db)
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', message.id)
        assert row.status == 'pending' and row.outbound_id
        outgoing = db.get(WhatsAppSendAttempt, row.outbound_id)
        assert outgoing.sender_user_id is None and outgoing.source_message_id == message.id
        assert outgoing.request_key == row.request_key and outgoing.state == 'queued'
        assert ack.queue_intake_acknowledgement(db, 'whatsapp', message.id).id == row.id
        db.commit()
        outgoing.state, outgoing.provider_message_id = 'delivered', 'wamid.accepted'
        db.commit()
        snapshot = ack.attempt_out(db, row)
        assert snapshot['status'] == 'sent' and snapshot['providerMessageId'] == 'wamid.accepted'
        assert snapshot['canRetry'] is False


def test_whatsapp_expired_window_is_visible_failure_and_candidate_stays_saved(workspace):
    with workspace() as db:
        message = wa_source(db, received_at=ack.utcnow() - timedelta(days=2))
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', message.id)
        assert row.status == 'failed' and '24-hour' in row.last_error and row.outbound_id is None
        db.commit()
    with workspace() as db:
        assert db.get(WhatsAppMessage, message.id).candidate_id == 'candidate'
        assert db.get(Candidate, 'candidate') is not None
        assert db.scalar(select(func.count()).select_from(WhatsAppSendAttempt)) == 0


def test_disabling_whatsapp_ack_cancels_linked_queued_attempt(workspace, client):
    with workspace() as db:
        message = wa_source(db)
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', message.id)
        db.commit(); row_id, outgoing_id = row.id, row.outbound_id
    response = client.patch('/api/intake-acknowledgements/whatsapp/123456', json={'enabled': False, 'body': ack.DEFAULT_BODY})
    assert response.status_code == 200
    with workspace() as db:
        assert db.get(IntakeAcknowledgement, row_id).status == 'suppressed'
        assert db.get(WhatsAppSendAttempt, outgoing_id).state == 'cancelled'


def test_worker_recovers_stale_sending_to_unknown_without_resending(workspace, monkeypatch):
    with workspace() as db:
        enable(db); source(db)
        row = ack.queue_intake_acknowledgement(db, 'email', 'receipt')
        row.status, row.started_at, row.attempts = 'sending', ack.utcnow() - timedelta(minutes=4), 1
        db.commit(); row_id = row.id
    monkeypatch.setattr(ack, 'deliver', lambda *_a, **_k: pytest.fail('Unknown delivery must never be retried'))
    ack.dispatch_pending_email_acknowledgements(sessions=workspace, settings=SimpleNamespace(email_ingestion_enabled=True))
    with workspace() as db:
        row = db.get(IntakeAcknowledgement, row_id)
        assert row.status == 'unknown' and row.attempts == 1


def test_historical_marker_survives_provider_view_and_suppresses_backfill(workspace):
    from app.services.mailbox_sync import persist_message
    raw = b'From: Applicant <applicant@example.test>\r\nReturn-Path: <applicant@example.test>\r\nSubject: Historical resume\r\n\r\nTest'
    incoming = parse_message(raw, provider_id='receipt', uid=1, uid_validity='1', folder='INBOX', received_at=ack.utcnow(), unread=True)
    with workspace() as db:
        enable(db)
        message = source(db, header_changes={'historical_import': True})
        persisted = persist_message(db, None, 'mailbox', incoming)
        assert persisted.id == message.id and persisted.automation_headers['historical_import'] is True
        row = ack.queue_intake_acknowledgement(db, 'email', message.id)
        assert row.status == 'suppressed' and 'Historical' in row.last_error


def test_editing_enabled_template_preserves_activation_and_reenable_resets_cutoff(workspace, client):
    with workspace() as db:
        original = enable(db, at=ack.utcnow() - timedelta(days=2))
        before = original.enabled_at
        db.commit()
    changed = ack.DEFAULT_BODY + ' Thank you.'
    saved = client.patch('/api/intake-acknowledgements/email/mailbox', json={
        'enabled': True, 'body': changed, 'previewedBody': changed})
    assert saved.status_code == 200
    assert datetime.fromisoformat(saved.json()['enabledAt']) == before
    assert client.patch('/api/intake-acknowledgements/email/mailbox', json={'enabled': False, 'body': changed}).status_code == 200
    enabled = client.patch('/api/intake-acknowledgements/email/mailbox', json={
        'enabled': True, 'body': changed, 'previewedBody': changed})
    assert enabled.status_code == 200 and datetime.fromisoformat(enabled.json()['enabledAt']) > before


# ------------------------------------------------ approved template instead of plain text

ACK_URL = '/api/intake-acknowledgements/whatsapp/123456'
CHOICE = {'name': 'documents_received', 'language': 'en', 'parameters': ['first_name', 'agency_name']}


def add_template(db, *, status='APPROVED', body='Hello {{1}}, {{2}} received your documents.'):
    db.add(WhatsAppTemplate(phone_number_id='123456', name='documents_received', language='en', category='UTILITY',
        status=status, components=[{'type': 'BODY', 'text': body}]))
    db.flush()


def choose(db, choice=None):
    config = db.scalar(select(IntakeAcknowledgementSettings).where(IntakeAcknowledgementSettings.source_kind == 'whatsapp'))
    assert ack.write_template_choice(db, config, choice or CHOICE)


def test_template_is_validated_saved_kept_and_cleared_through_the_settings_api(workspace, client):
    with workspace() as db:
        wa_source(db); add_template(db)
        db.add(WhatsAppTemplate(phone_number_id='123456', name='old_one', language='en', category='UTILITY', status='REJECTED',
            components=[{'type': 'BODY', 'text': 'Hi'}]))
        db.commit()
    base = {'enabled': True, 'body': ack.DEFAULT_BODY, 'previewedBody': ack.DEFAULT_BODY}
    assert client.get(ACK_URL).json()['template'] is None and client.get(ACK_URL).json()['templateAvailable'] is True
    missing = client.patch(ACK_URL, json={**base, 'template': {**CHOICE, 'name': 'not_synced'}})
    assert missing.status_code == 422 and 'synced' in missing.json()['detail']
    rejected = client.patch(ACK_URL, json={**base, 'template': {'name': 'old_one', 'language': 'en', 'parameters': []}})
    assert rejected.status_code == 409 and 'not approved' in rejected.json()['detail']
    wrong_count = client.patch(ACK_URL, json={**base, 'template': {**CHOICE, 'parameters': ['first_name']}})
    assert wrong_count.status_code == 422 and '2 value' in wrong_count.json()['detail']
    assert client.patch(ACK_URL, json={**base, 'template': {**CHOICE, 'parameters': ['phone_number', 'x']}}).status_code == 422
    assert client.patch('/api/intake-acknowledgements/email/mailbox', json={**base, 'template': CHOICE}).status_code == 422
    saved = client.patch(ACK_URL, json={**base, 'template': CHOICE})
    assert saved.status_code == 200, saved.text
    assert saved.json()['template'] == CHOICE
    kept = client.patch(ACK_URL, json=base)  # template left out: unchanged
    assert kept.json()['template'] == CHOICE
    cleared = client.patch(ACK_URL, json={**base, 'template': None})
    assert cleared.json()['template'] is None
    with workspace() as db:
        audits = [row.meta for row in db.scalars(select(AuditEntry)
            .where(AuditEntry.entity_type == 'intake_ack_settings'))]
    assert [item['templateChanged'] for item in audits] == [True, False, True]
    client.cookies.set(SESSION_COOKIE, 'ack-staff')
    assert client.patch(ACK_URL, json={**base, 'template': CHOICE}).status_code == 403


def test_configured_template_is_sent_even_after_the_24_hour_window_and_filled_from_the_candidate(workspace):
    with workspace() as db:
        message = wa_source(db, received_at=ack.utcnow() - timedelta(days=2))
        add_template(db); choose(db)
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', message.id)
        assert row.status == 'pending' and row.outbound_id, row.last_error
        db.commit()
        outgoing = db.get(WhatsAppSendAttempt, row.outbound_id)
        assert outgoing.message_type == 'template' and outgoing.sender_user_id is None
        assert outgoing.template == {'name': 'documents_received', 'language': 'en', 'parameters': ['Synthetic', 'our agency']}
        assert outgoing.body == 'Hello Synthetic, our agency received your documents.' and row.body == outgoing.body


def test_template_that_stopped_being_approved_falls_back_to_plain_text_inside_the_window(workspace):
    with workspace() as db:
        message = wa_source(db)
        add_template(db, status='PAUSED'); choose(db)
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', message.id)
        assert row.status == 'pending'
        outgoing = db.get(WhatsAppSendAttempt, row.outbound_id)
        assert outgoing.message_type == 'text' and outgoing.body == ack.DEFAULT_BODY


def test_without_a_template_column_plain_text_still_works_and_choosing_one_is_refused(workspace, client, schema_database):
    from sqlalchemy import text
    with workspace() as db:
        wa_source(db); db.commit()
    with schema_database.begin() as connection:
        connection.execute(text('ALTER TABLE intake_acknowledgement_settings DROP COLUMN whatsapp_template'))
    snapshot = client.get(ACK_URL).json()
    assert snapshot['template'] is None and snapshot['templateAvailable'] is False
    base = {'enabled': True, 'body': ack.DEFAULT_BODY, 'previewedBody': ack.DEFAULT_BODY}
    refused = client.patch(ACK_URL, json={**base, 'template': CHOICE})
    assert refused.status_code == 409 and 'database update' in refused.json()['detail']
    assert client.patch(ACK_URL, json=base).status_code == 200
    with workspace() as db:
        db.add(WhatsAppMessage(id='second', provider_message_id='wamid.second', phone_number_id='123456', sender_number='919000000001',
            message_type='document', status='completed', candidate_id='candidate', received_at=ack.utcnow()))
        db.flush()
        row = ack.queue_intake_acknowledgement(db, 'whatsapp', 'second')
        assert row.status == 'pending' and db.get(WhatsAppSendAttempt, row.outbound_id).message_type == 'text'


def test_template_migration_is_additive_idempotent_and_reversible(schema_database):
    from sqlalchemy import inspect, text
    from test_database_naming import _migrate
    engine = schema_database
    _migrate(engine, "0017_agency_settings")
    def columns():
        return {column["name"] for column in inspect(engine).get_columns("intake_acknowledgement_settings")}
    before = columns()
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO intake_acknowledgement_settings (id,source_kind,channel_id,enabled,body) VALUES ('s','whatsapp','1',true,'kept text kept')"))
    _migrate(engine, "head")
    assert columns() - before == {"whatsapp_template"}
    with engine.begin() as connection:
        assert connection.execute(text("SELECT whatsapp_template, body FROM intake_acknowledgement_settings")).one() == (None, "kept text kept")
        connection.execute(text("""UPDATE intake_acknowledgement_settings SET whatsapp_template = '{"name":"x"}'"""))
        connection.execute(text("UPDATE alembic_version SET version_num = '0017_agency_settings'"))
    _migrate(engine, "head")  # revision lost after the column was made: harmless
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT whatsapp_template->>'name' FROM intake_acknowledgement_settings")) == "x"
    _migrate(engine, "0017_agency_settings", downgrade=True)
    assert columns() == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT body FROM intake_acknowledgement_settings")) == "kept text kept"
