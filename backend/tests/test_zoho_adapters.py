"""Zoho IMAP/SMTP adapter contracts. No test opens a network connection."""
from dataclasses import replace
from email import policy
from email.parser import BytesParser
import smtplib
from types import SimpleNamespace

import pytest
from app.integrations.mail import smtp
from app.integrations.mail.imap import MailboxCursor, _mailbox_wire_name
from app.integrations.mail.mailbox import WritableMailbox, MailMutationError, decode_folder
from app.integrations.mail.base import MailAdapterError
from app.services.zoho_mail import make_config, validate_config


def config(**kwargs):return make_config('jobs@example.test','synthetic-app-password',**kwargs)


class FakeIMAP:
    def __init__(self,caps=(b'IMAP4REV1',b'UIDPLUS',b'MOVE')):
        self.capabilities=caps;self.calls=[];self.fail=None;self.rows=[b'4 8 2 8'];self.literal=None
    def select(self,folder,readonly=True):self.calls.append(('SELECT',folder,readonly));return 'OK',[b'3']
    def response(self,key):
        return key,{'UIDVALIDITY':[b'123'],'UIDNEXT':[b'9'],'COPYUID':[b'777 8 22'],'APPENDUID':[b'456 31']}[key]
    def uid(self,command,*args):
        self.calls.append((command,*args))
        if self.fail==command:raise OSError('sensitive-provider-details')
        return 'OK',self.rows if command=='SEARCH' else [b'ok']
    def list(self):
        return 'OK',[b'(\\HasNoChildren \\Sent) "/" "Sent Items"',b'(\\Noselect) "/" "Parent"',
            b'(\\HasNoChildren) "/" "R&-D"',b'(\\HasNoChildren) "/" "&ZeVnLIqe-"']
    def status(self,folder,query):return 'OK',[b'"folder" (MESSAGES 12 UNSEEN 3)']
    def append(self,*args):self.calls.append(('APPEND',*args));return 'OK',[b'saved']
    def create(self,*args):self.calls.append(('CREATE',*args));return 'OK',[b'created']


def box(connection=None):
    result=WritableMailbox(config());result._connection=connection or FakeIMAP();return result


@pytest.mark.parametrize('name',['INBOX','Sent Items','R&D','日本語','العربية','Quote " here'])
def test_modified_utf7_folder_roundtrip(name):
    wire=_mailbox_wire_name(name)[1:-1].replace('\\"','"').replace('\\\\','\\')
    assert decode_folder(wire)==name


def test_folder_list_parses_roles_names_counts_and_excludes_nonselectable():
    rows=box().folders()
    assert [(row['name'],row['role']) for row in rows]==[('Sent Items','sent'),('R&D','custom'),('日本語','custom')]
    assert all(row['total']==12 and row['unread']==3 for row in rows)


def test_search_is_literal_and_page_uses_deduplicated_descending_uids(monkeypatch):
    adapter=box();monkeypatch.setattr(adapter,'fetch',lambda uid,uid_validity:uid)
    cursor,total,rows=adapter.page('INBOX',search='தமிழ் "quoted"',offset=1,limit=1)
    assert cursor.uid_validity=='123' and total==3 and rows==[4]
    assert adapter._connection.literal=='தமிழ் "quoted"'.encode()
    assert ('SEARCH','CHARSET','UTF-8','UNDELETED','TEXT') in adapter._connection.calls
    with pytest.raises(MailAdapterError):adapter.page('INBOX',search='hello\r\nUID STORE')


def test_flag_changes_use_only_one_uid_and_retrieve_provider_state(monkeypatch):
    adapter=box();monkeypatch.setattr(adapter,'fetch',lambda uid,uid_validity:SimpleNamespace(uid=uid,unread=False,starred=True))
    result=adapter.set_flags('INBOX',8,'123',unread=False,starred=True)
    assert result.uid==8 and result.starred
    calls=adapter._connection.calls
    assert ('STORE','8','+FLAGS.SILENT','(\\Seen)') in calls
    assert ('STORE','8','+FLAGS.SILENT','(\\Flagged)') in calls


@pytest.mark.parametrize('unread,flag', [(True, 'UNSEEN'), (False, 'SEEN')])
def test_read_filter_is_applied_in_provider_search_before_paging(monkeypatch, unread, flag):
    adapter = box()
    monkeypatch.setattr(adapter, 'fetch', lambda uid, uid_validity: uid)
    _, total, rows = adapter.page('INBOX', unread=unread, offset=1, limit=1)
    assert ('SEARCH', None, 'UNDELETED', flag) in adapter._connection.calls
    assert total == 3 and rows == [4]
    adapter.page('INBOX', search='candidate', unread=unread, limit=1)
    assert ('SEARCH', 'CHARSET', 'UTF-8', 'UNDELETED', flag, 'TEXT') in adapter._connection.calls


@pytest.mark.parametrize('uid',['1:*',0,-1,True])
def test_mutations_reject_multi_message_or_invalid_uid_before_command(uid):
    adapter=box()
    for operation in [lambda:adapter.set_flags('INBOX',uid,'123',unread=False),lambda:adapter.move('INBOX',uid,'123','Trash'),lambda:adapter.remove_draft('Drafts',uid,'123')]:
        with pytest.raises(MailMutationError):operation()
    assert adapter._connection.calls==[]


def test_uidvalidity_guard_stops_remote_mutations():
    adapter=box()
    with pytest.raises(MailAdapterError,match='reset by the mail provider') as failure:
        adapter.move('INBOX',8,'old-validity','Trash')
    assert 'Contact your administrator' in str(failure.value)
    assert not any(call[0] in {'MOVE','COPY','STORE','EXPUNGE'} for call in adapter._connection.calls)


def test_native_move_returns_destination_uid_without_copying_twice():
    adapter=box();assert adapter.move('INBOX',8,'123','Archive')==('777',22)
    assert ('MOVE','8','"Archive"') in adapter._connection.calls
    assert not any(call[0] in {'COPY','STORE','EXPUNGE'} for call in adapter._connection.calls)


def test_fallback_move_only_expunges_the_copied_uid():
    adapter=box(FakeIMAP((b'IMAP4REV1',b'UIDPLUS')))
    assert adapter.move('INBOX',8,'123','Trash')==('777',22)
    assert ('COPY','8','"Trash"') in adapter._connection.calls
    assert ('STORE','8','+FLAGS.SILENT','(\\Deleted)') in adapter._connection.calls
    assert ('EXPUNGE','8') in adapter._connection.calls


def test_fallback_move_without_safe_capability_does_not_copy():
    adapter=box(FakeIMAP((b'IMAP4REV1',)))
    with pytest.raises(MailMutationError) as error:adapter.move('INBOX',8,'123','Trash')
    assert not error.value.unknown
    assert not any(call[0]=='COPY' for call in adapter._connection.calls)


def test_disconnected_move_and_partial_flag_mutations_are_unknown_and_sanitized():
    adapter=box(FakeIMAP((b'UIDPLUS',)));adapter._connection.fail='EXPUNGE'
    with pytest.raises(MailMutationError) as error:adapter.move('INBOX',8,'123','Trash')
    assert error.value.unknown and 'sensitive' not in str(error.value)
    adapter=box();adapter._connection.fail='STORE'
    with pytest.raises(MailMutationError) as error:adapter.set_flags('INBOX',8,'123',starred=True)
    assert error.value.unknown


def test_draft_append_and_uid_specific_removal():
    adapter=box();assert adapter.append_draft('Drafts',b'synthetic mime')==('456',31)
    assert adapter._connection.calls[-1][0]=='APPEND'
    assert adapter._connection.calls[-1][2]=='(\\Draft \\Seen)'
    adapter.remove_draft('Drafts',8,'123')
    assert ('EXPUNGE','8') in adapter._connection.calls


def test_compose_preserves_attachment_and_thread_without_exposing_bcc():
    message,recipients=smtp.compose(config(),message_id='<stable@crm.test>',to=['one@example.test'],cc=['two@example.test'],bcc=['hidden@example.test','one@example.test'],subject='Résumé',body='Message body',attachments=[{'name':'resume.pdf','mime':'application/pdf','data':b'%PDF synthetic'}],in_reply_to='<original@example.test>',references='<older@example.test>')
    parsed=BytesParser(policy=policy.default).parsebytes(message.as_bytes())
    assert recipients==['one@example.test','two@example.test','hidden@example.test']
    assert parsed['Bcc'] is None and b'hidden@example.test' not in message.as_bytes()
    assert parsed['In-Reply-To']=='<original@example.test>'
    assert parsed['References']=='<older@example.test> <original@example.test>'
    attachment=list(parsed.iter_attachments())[0]
    assert attachment.get_filename()=='resume.pdf' and attachment.get_payload(decode=True)==b'%PDF synthetic'
    with pytest.raises(smtp.MailSendError):smtp.compose(config(),message_id='<stable@crm.test>',to=['one@example.test'],cc=[],bcc=[],subject='Bad\nBcc: hidden@example.test',body='body')


def test_smtp_starttls_partial_recipient_results_and_unknown_outcomes(monkeypatch):
    class Server:
        def __init__(self,*args,**kwargs):self.calls=[]
        def ehlo(self):self.calls.append('ehlo')
        def starttls(self,context):self.calls.append('tls')
        def login(self,*args):self.calls.append('login')
        def send_message(self,message,**kwargs):self.calls.append('send');return {'bad@example.test':(550,b'rejected')}
        def close(self):self.calls.append('close')
    server=Server();monkeypatch.setattr(smtp.smtplib,'SMTP',lambda *a,**k:server)
    message,recipients=smtp.compose(config(),message_id='<stable@crm.test>',to=['good@example.test','bad@example.test'],cc=[],bcc=[],subject='Test',body='Synthetic')
    assert smtp.deliver(config(smtp_port=587),message,recipients)==['bad@example.test']
    assert server.calls==['ehlo','tls','ehlo','login','send','close']
    def disconnected(*args,**kwargs):raise smtplib.SMTPServerDisconnected('secret response')
    server.send_message=disconnected
    with pytest.raises(smtp.MailSendError) as error:smtp.deliver(config(smtp_port=587),message,recipients)
    assert error.value.unknown and 'secret' not in str(error.value)


@pytest.mark.parametrize('changes',[{'host':'127.0.0.1'},{'host':'imappro.zoho.com.evil.test'},{'smtp_host':'smtppro.zoho.in'},{'smtp_host':'smtp.zoho.com'},{'port':143},{'use_ssl':False},{'smtp_port':25}])
def test_zoho_policy_rejects_other_hosts_regions_account_types_and_cleartext(changes):
    with pytest.raises(MailAdapterError):validate_config(replace(config(),**changes))


@pytest.mark.parametrize('region',['com','in','eu','com.au','jp','ca','com.cn','sa'])
def test_zoho_policy_supports_configured_data_center(region):
    value=config(region=region)
    assert value.host=='imappro.zoho.'+region
    assert value.smtp_host=='smtppro.zoho.'+region and value.smtp_use_ssl
