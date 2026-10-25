"""Word and Excel mail attachments become something the browser can show."""
import io
import zipfile
from unittest.mock import patch

import pytest

from app.db.models import MailAttachment
from app.services import candidate_search  # noqa: F401  (must load before test_mail_workflow patches get_settings at import)
from app.integrations.ocr import inspection
from app.services import attachment_preview as preview
from test_mail_workflow import mailbox, schema_database  # noqa: F401

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def make_docx(extra="") -> bytes:
    body = (f'<w:document xmlns:w="{W}"><w:body>'
            '<w:p><w:r><w:t>Alex Example</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>&lt;script&gt;alert(1)&lt;/script&gt; Welder</w:t><w:tab/><w:t>5 years</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Skill</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>TIG</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
            f'{extra}</w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", body)
    return out.getvalue()


def make_xlsx() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{S}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                         '<sheets><sheet name="Candidates" sheetId="1" r:id="rId7"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         '<Relationship Id="rId7" Target="worksheets/sheet9.xml"/></Relationships>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{S}"><si><t>Name</t></si><si><r><t>Al</t></r><r><t>ex &amp; Co</t></r></si></sst>')
        archive.writestr("xl/worksheets/sheet9.xml", f'<worksheet xmlns="{S}"><sheetData>'
                         '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="inlineStr"><is><t>&lt;b&gt;Age&lt;/b&gt;</t></is></c></row>'
                         '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>42</v></c><c r="C2" t="b"><v>1</v></c></row>'
                         '</sheetData></worksheet>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{S}"><sheetData><row r="1"><c r="A1"><v>WRONG</v></c></row></sheetData></worksheet>')
    return out.getvalue()


def attach(env, tmp_path, name, data, identifier="doc"):
    (tmp_path / identifier).write_bytes(data)
    with env.sessions() as db:
        db.add(MailAttachment(id=identifier, message_id="message-0", file_name=name, object_key=identifier))
        db.commit()
    return f"/api/inbox/attachments/{identifier}/preview"


def test_docx_html_escapes_text_and_keeps_tables():
    html = preview.docx_html(make_docx())
    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt; Welder" in html
    assert "<td><p>Skill</p></td><td><p>TIG</p></td>" in html and "Alex Example" in html


def test_xlsx_html_reads_first_sheet_in_workbook_order_with_shared_runs_and_escaping():
    html = preview.xlsx_html(make_xlsx())
    assert "WRONG" not in html and "<h3>Candidates</h3>" in html
    assert "<td>Name</td><td></td><td>&lt;b&gt;Age&lt;/b&gt;</td>" in html
    assert "<td>Alex &amp; Co</td><td>42</td><td>TRUE</td>" in html


def test_documents_with_entity_declarations_or_huge_contents_are_refused():
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("word/document.xml", '<!DOCTYPE d [<!ENTITY a "aaaa">]><w:document xmlns:w="%s"><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>' % W)
    with pytest.raises(preview.PreviewUnavailable) as caught:
        preview.docx_html(out.getvalue())
    assert caught.value.status == 422
    with patch.object(preview, "MAX_UNZIPPED_BYTES", 10), pytest.raises(preview.PreviewUnavailable) as caught:
        preview.docx_html(make_docx())
    assert caught.value.status == 413


def test_long_documents_are_cut_with_a_note():
    paragraphs = "".join('<w:p><w:r><w:t>line</w:t></w:r></w:p>' for _ in range(50))
    with patch.object(preview, "MAX_PARAGRAPHS", 10):
        html = preview.docx_html(make_docx(paragraphs))
    assert html.count("<p>line</p>") < 50 and "start of a long document" in html


def test_endpoint_serves_sandboxed_html_without_libreoffice(mailbox, tmp_path):
    with patch.object(inspection, "_find_libreoffice", return_value=None):
        response = mailbox.client.get(attach(mailbox, tmp_path, "CV.DOCX", make_docx()))
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/html")
        policy = response.headers["content-security-policy"]
        assert policy.startswith("sandbox") and "default-src 'none'" in policy
        assert response.headers["x-content-type-options"] == "nosniff" and "Alex Example" in response.text
        sheet = mailbox.client.get(attach(mailbox, tmp_path, "list.xlsx", make_xlsx(), "sheet"))
        assert sheet.status_code == 200 and "Alex &amp; Co" in sheet.text


def test_endpoint_converts_to_pdf_once_and_caches_by_content(mailbox, tmp_path):
    calls = []

    def fake(data, suffix, deadline):
        calls.append(suffix)
        return b"%PDF-1.7 converted"

    with patch.object(inspection, "_find_libreoffice", return_value="soffice"), patch.object(inspection, "_word_pdf", fake):
        url = attach(mailbox, tmp_path, "letter.doc", OLE + b" old word file")
        first = mailbox.client.get(url)
        second = mailbox.client.get(url)
    assert first.status_code == second.status_code == 200
    assert first.headers["content-type"] == "application/pdf" and first.content == b"%PDF-1.7 converted"
    assert calls == ["doc"]
    assert list((tmp_path / "mail-previews").glob("*.pdf"))


def test_failed_conversion_falls_back_for_docx_and_explains_for_doc(mailbox, tmp_path):
    def broken(data, suffix, deadline):
        raise inspection.InspectionError("converter failed")

    with patch.object(inspection, "_find_libreoffice", return_value="soffice"), patch.object(inspection, "_word_pdf", broken):
        ok = mailbox.client.get(attach(mailbox, tmp_path, "cv.docx", make_docx()))
        assert ok.status_code == 200 and ok.headers["content-type"].startswith("text/html")
        bad = mailbox.client.get(attach(mailbox, tmp_path, "old.doc", OLE + b" x", "old"))
        assert bad.status_code == 422 and "Download" in bad.json()["detail"]


def test_unsupported_missing_tool_and_fake_files_get_plain_messages(mailbox, tmp_path):
    with patch.object(inspection, "_find_libreoffice", return_value=None):
        text = mailbox.client.get(attach(mailbox, tmp_path, "notes.txt", b"hello", "txt"))
        assert text.status_code == 415 and "cannot show" in text.json()["detail"]
        old = mailbox.client.get(attach(mailbox, tmp_path, "old.xls", OLE + b" x", "xls"))
        assert old.status_code == 415 and "not set up" in old.json()["detail"]
        fake = mailbox.client.get(attach(mailbox, tmp_path, "fake.docx", b"just text", "fake"))
        assert fake.status_code == 422 and "does not look like" in fake.json()["detail"]
        assert mailbox.client.get("/api/inbox/attachments/unknown/preview").status_code == 404
        big = attach(mailbox, tmp_path, "big.docx", b"PK" + b"0" * 100, "big")
        with patch.object(preview, "MAX_SOURCE_BYTES", 10):
            assert mailbox.client.get(big).status_code == 413
