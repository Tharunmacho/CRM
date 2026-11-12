"""Synthetic agency directory imports; only the isolated crm_schema_test database."""
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.api.routers import reference_agencies
from app.core.deps import current_user
from app.db.models import AuditEntry, ReferenceAgency, Role, User
from app.db.session import get_db
from app.services import reference_agency_import as imports
from test_database_naming import _migrate, schema_database  # noqa: F401


@pytest.fixture
def env(schema_database):
    _migrate(schema_database, "head")
    with Session(schema_database, expire_on_commit=False) as db:
        db.add(User(id="agency-staff", name="Synthetic staff", email="agency@example.test", role=Role.staff))
        db.commit()
        app = FastAPI()
        app.include_router(reference_agencies.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[current_user] = lambda: db.get(User, "agency-staff")
        with TestClient(app) as client:
            yield SimpleNamespace(db=db, app=app, client=client, engine=schema_database)


def upload(env, content, *, filename="agencies.csv", confirm=None, **options):
    data = {key: json.dumps(value) if key == "mapping" else str(value) for key, value in options.items()}
    if confirm:
        data["previewHash"] = confirm["previewHash"]
        for key in ("headerRow", "sheetName", "mapping"):
            data.setdefault(key, json.dumps(confirm[key]) if key == "mapping" else str(confirm[key]))
    result = env.client.post("/api/reference-agencies/import/" + ("confirm" if confirm else "preview"),
                             data=data, files={"file": (filename, content)})
    assert result.status_code == 200, result.text
    return result.json()


def create(env, **payload):
    result = env.client.post("/api/reference-agencies", json=payload)
    assert result.status_code == 201, result.text
    return result.json()


def xlsx(*tabs):
    """Minimal real workbook; numeric tuple (row, column, value) preserves sparse cells."""
    output = io.BytesIO()
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                         + "".join(f'<sheet name="{escape(label)}" sheetId="{i}" r:id="r{i}"/>' for i, (label, _) in enumerate(tabs, 1)) + '</sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                         + "".join(f'<Relationship Id="r{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(tabs) + 1)) + '</Relationships>')
        for i, (_, rows) in enumerate(tabs, 1):
            xml = []
            for row_number, cells in rows:
                content = []
                for column, value in enumerate(cells):
                    reference = chr(65 + column) + str(row_number)
                    if isinstance(value, (int, float)):
                        content.append(f'<c r="{reference}" t="n"><v>{value}</v></c>')
                    else:
                        content.append(f'<c r="{reference}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
                xml.append(f'<row r="{row_number}">' + ''.join(content) + '</row>')
            archive.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="{ns}"><sheetData>' + ''.join(xml) + '</sheetData></worksheet>')
    return output.getvalue()


@pytest.mark.parametrize("payload,expected", [
    ({"name": "  Arun   Kumar "}, ("Arun Kumar", "Arun Kumar Agency")),
    ({"agencyName": " Coast Recruiters ", "name": ""}, ("Coast Recruiters", "Coast Recruiters")),
    ({"name": "Arun", "agencyName": "  "}, ("Arun", "Arun Agency")),
])
def test_either_name_has_the_requested_display_fallback(env, payload, expected):
    saved = create(env, **payload)
    assert (saved["name"], saved["agencyName"]) == expected
    assert saved["phone"] is None and saved["address"] is None and saved["place"] is None


@pytest.mark.parametrize("payload", [{}, {"name": " ", "agencyName": " "}, {"name": "N" * 200},
                                       {"name": "Alex", "place": "x" * 201}])
def test_invalid_names_and_place_do_not_create_placeholder_data(env, payload):
    assert env.client.post("/api/reference-agencies", json=payload).status_code == 422
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0


@pytest.mark.parametrize("phone", ["fred", "1234", "1234567890123456", "9000000001/9000000002"])
def test_import_and_crud_reject_invalid_phone_without_truncating_or_making_one_up(env, phone):
    assert env.client.post("/api/reference-agencies", json={"name": "Alex", "phone": phone}).status_code == 422
    preview = upload(env, ("Name,Phone\nAlex," + phone + "\n").encode())
    assert preview["counts"]["invalid"] == 1 and not preview["canConfirm"]
    assert preview["rows"][0]["values"]["phone"] == phone


def test_place_is_separate_from_address_and_older_put_keeps_it(env):
    saved = create(env, name="Alex", agencyName="North Agency", place="Trichy", address="42 First Street")
    path = "/api/reference-agencies/" + saved["id"]
    reply = env.client.put(path, json={"name": "Alex", "agencyName": "North Agency", "address": "42 First Street"})
    assert reply.status_code == 200 and reply.json()["place"] == "Trichy"
    assert reply.json()["address"] == "42 First Street"
    assert env.client.put(path, json={"name": "Alex", "agencyName": "North Agency", "place": ""}).json()["place"] is None


def test_preview_is_readonly_and_section_place_never_becomes_address(env):
    data = b'Name,Agency Name,\nAlex,North Agency,9000000001\nTRICHY,,\nBlair,East Agency,9000000002\nMADURAI,,\nCasey,,9000000003\nOther Places,,\nDevon,South Agency,9000000004\n'
    preview = upload(env, data)
    assert preview["mapping"] == {"name": 0, "agencyName": 1, "phone": 2}
    assert preview["columns"][2]["label"] == "Column 3 (blank)"
    assert preview["counts"] == {"create": 4, "add_contact": 0, "duplicate": 0, "conflict": 0, "invalid": 0, "section": 3}
    values = [row["values"] for row in preview["rows"] if row["status"] == "create"]
    assert [row["place"] for row in values] == [None, "Trichy", "Madurai", None]
    assert values[2]["agencyName"] == "Casey Agency"
    assert all(row["address"] is None for row in values)
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0
    assert env.db.scalar(select(func.count()).select_from(AuditEntry)) == 0
    result = upload(env, data, confirm=preview)
    assert result["created"] == 4
    assert {item["place"] for item in result["items"]} == {None, "Trichy", "Madurai"}


def test_xlsx_selected_sheet_sparse_row_numbers_and_numeric_phone(env):
    data = xlsx(("Ignore", [(1, ["Agency"]), (2, ["Not selected"])]),
                ("Agencies", [(3, ["Contact Person", "Agency", "Mobile No", "City"]),
                              (5, ["Alex", "Synthetic Agency", 9000000001.0, "Trichy"])]))
    preview = upload(env, data, filename="agencies.xlsx", sheetName="Agencies")
    assert preview["sheets"] == ["Ignore", "Agencies"] and preview["sheetName"] == "Agencies"
    assert preview["headerRow"] == 3
    assert preview["rows"][0]["rowNumber"] == 5
    assert preview["rows"][0]["values"]["phone"] == "9000000001"
    assert upload(env, data, filename="agencies.xlsx", confirm=preview)["created"] == 1


def test_explicit_column_mapping_no_header_and_scientific_csv_phone(env):
    data = b'Alex,Synthetic Agency,9.000000001E9,Trichy\n'
    preview = upload(env, data, headerRow=0, mapping={"name": 0, "agencyName": 1, "phone": 2, "place": 3})
    assert preview["rows"][0]["rowNumber"] == 1
    assert preview["rows"][0]["values"]["phone"] == "9000000001"
    assert upload(env, data, confirm=preview)["created"] == 1


def test_repeated_file_and_repeated_contacts_are_safe_and_keep_primary_details(env):
    data = b'Agency,Name,Phone,Place\nNorth Agency,Alex,9000000001,Trichy\nNorth Agency,Blair,9000000002,Trichy\nnorth  agency,blair,90000 00002,trichy\n'
    preview = upload(env, data)
    assert [row["status"] for row in preview["rows"]] == ["create", "add_contact", "duplicate"]
    result = upload(env, data, confirm=preview)
    assert result["created"] == 1 and result["contactsAdded"] == 1 and result["skipped"] == 1
    item = result["items"][0]
    assert item["name"] == "Alex" and item["phone"] == "9000000001"
    assert item["staffContacts"] == [{"name": "Blair", "phone": "9000000002", "email": None, "designation": None}]
    repeated = upload(env, data, confirm=preview)
    assert repeated["created"] == repeated["contactsAdded"] == 0 and repeated["skipped"] == 3
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 1
    assert env.db.scalar(select(func.count()).select_from(AuditEntry)) == 2


def test_conflicting_contact_details_and_ambiguous_agencies_are_never_overwritten(env):
    original = create(env, name="Alex", agencyName="North Agency", phone="9000000001", place="Trichy", address="First Street")
    create(env, name="Casey", agencyName="Ambiguous", place="Trichy")
    create(env, name="Devon", agencyName="Ambiguous", place="Trichy")
    data = b'Agency,Name,Phone,Place,Address\nNorth Agency,Alex,9000000002,Trichy,\nNorth Agency,Blair,9000000001,Trichy,\nNorth Agency,Blair,9000000003,Trichy,Other Street\nAmbiguous,Someone,9000000004,Trichy,\nNorth Agency,Blair,9000000003,Trichy,First Street\n'
    preview = upload(env, data)
    assert [row["status"] for row in preview["rows"]] == ["conflict"] * 4 + ["add_contact"]
    result = upload(env, data, confirm=preview)
    assert result["contactsAdded"] == 1 and result["conflicts"] == 4
    env.db.expire_all()
    saved = env.db.get(ReferenceAgency, original["id"])
    assert saved.phone == "9000000001" and saved.address == "First Street" and saved.name == "Alex"


def test_invalid_rows_are_visible_and_skipped_without_losing_valid_rows(env):
    data = b'Name,Agency,Email\nAlex,North Agency,alex@example.test\n,,\n,,bad-email\n'
    preview = upload(env, data)
    assert preview["counts"]["create"] == 1 and preview["counts"]["invalid"] == 1
    result = upload(env, data, confirm=preview)
    assert result["created"] == 1 and result["invalid"] == 1


def test_changed_file_or_mapping_requires_new_preview(env):
    data = b'Name,Agency\nAlex,North Agency\n'
    preview = upload(env, data)
    response = env.client.post("/api/reference-agencies/import/confirm", data={"previewHash": preview["previewHash"]},
                               files={"file": ("agencies.csv", data.replace(b"Alex", b"Blair"))})
    assert response.status_code == 409
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0


def test_confirm_rechecks_new_duplicate_since_preview(env):
    data = b'Name,Agency\nAlex,North Agency\n'
    preview = upload(env, data)
    create(env, name="Alex", agencyName="North Agency")
    result = upload(env, data, confirm=preview)
    assert result["created"] == 0 and result["skipped"] == 1


def test_different_contacts_with_unknown_and_known_place_keep_separate_branches(env):
    data = b'Agency,Name,Phone,Place\nNorth Agency,Alex,9000000001,\nNorth Agency,Blair,9000000002,Trichy\n'
    preview = upload(env, data)
    assert [row["status"] for row in preview["rows"]] == ["create", "create"]
    result = upload(env, data, confirm=preview)
    assert result["created"] == 2 and result["contactsAdded"] == 0
    assert {(item["name"], item["place"]) for item in result["items"]} == {("Alex", None), ("Blair", "Trichy")}
    repeated = upload(env, data, confirm=preview)
    assert repeated["created"] == 0 and repeated["skipped"] == 2
    addition = upload(env, b'Agency,Name,Phone,Place\nNorth Agency,Alex,9000000001,Madurai\n')
    assert addition["counts"]["conflict"] == 1 and addition["counts"]["create"] == 0


def test_imported_notes_survive_ordinary_edit_that_has_no_notes_field(env):
    data = b'Agency,Name,Notes\nNorth Agency,Alex,Call after noon\n'
    preview = upload(env, data)
    row = upload(env, data, confirm=preview)["items"][0]
    reply = env.client.put("/api/reference-agencies/" + row["id"], json={"name": "Alex", "agencyName": "North Agency", "phone": "9000000001"})
    assert reply.status_code == 200 and reply.json()["notes"] == "Call after noon"


def test_home_phone_prefix_matches_but_other_international_prefixes_do_not(env, monkeypatch):
    monkeypatch.setattr(imports.agency_settings, "home_digits", lambda db=None: "91")
    create(env, name="Alex", agencyName="North Agency", phone="9000000001")
    data = b'Name,Agency,Phone\nAlex,North Agency,+91 90000 00001\nAlex,North Agency,+971 90000 00001\n'
    preview = upload(env, data)
    assert [row["status"] for row in preview["rows"]] == ["duplicate", "conflict"]


def test_control_characters_are_cleaned_and_maximum_generated_name_is_checked(env):
    saved = create(env, name="Al\u200bex\x00   Example", place="Tri\x00chy")
    assert saved["name"] == "Alex Example" and saved["place"] == "Trichy"
    exact = create(env, name="A" * 193)
    assert len(exact["agencyName"]) == 200
    assert env.client.post("/api/reference-agencies", json={"name": "B" * 194}).status_code == 422


def test_changed_mapping_requires_new_preview(env):
    data = b'Name,Agency\nAlex,North Agency\n'
    preview = upload(env, data)
    response = env.client.post("/api/reference-agencies/import/confirm", data={
        "previewHash": preview["previewHash"], "mapping": json.dumps({"name": 1, "agencyName": 0}), "headerRow": "1"},
        files={"file": ("agencies.csv", data)})
    assert response.status_code == 409
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0


def test_missing_worksheet_is_explicit_and_switching_redetects_narrow_columns(env):
    data = xlsx(("Wide", [(1, ["Notes", "City", "Agency", "Name"]), (2, ["", "Trichy", "North", "Alex"])]),
                ("Narrow", [(1, ["Agency"]), (2, ["Only Agency"])]))
    preview = upload(env, data, filename="agencies.xlsx", sheetName="Narrow")
    assert preview["mapping"] == {"agencyName": 0}
    assert preview["rows"][0]["values"]["name"] == "Only Agency"
    response = env.client.post("/api/reference-agencies/import/preview", data={"sheetName": "Missing"},
                               files={"file": ("agencies.xlsx", data)})
    assert response.status_code == 422


def test_busy_directory_lock_rolls_back_without_partial_rows(env):
    data = b'Name,Agency\nAlex,North Agency\n'
    parsed = imports.parse_upload(UploadFile(io.BytesIO(data), filename="agencies.csv"), mapping=None, header_row=None, sheet_name=None)
    from fastapi import HTTPException
    env.db.commit()
    with env.engine.begin() as held:
        held.execute(text("LOCK TABLE crm_reference_agencies IN SHARE ROW EXCLUSIVE MODE"))
        with pytest.raises(HTTPException) as failure:
            imports.confirm(env.db, SimpleNamespace(id="agency-staff"), parsed)
        assert failure.value.status_code == 409
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0


def test_concurrent_confirmations_do_not_duplicate_agency_or_team(env):
    data = b'Name,Agency,Phone\nAlex,North Agency,9000000001\nBlair,North Agency,9000000002\n'
    parsed = imports.parse_upload(UploadFile(io.BytesIO(data), filename="agencies.csv"), mapping=None, header_row=None, sheet_name=None)
    barrier = Barrier(2)
    # End the fixture's read transaction before the independent write sessions.
    env.db.commit()
    def run():
        with Session(env.engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            return imports.confirm(db, SimpleNamespace(id="agency-staff"), parsed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sum(item["created"] for item in results) == 1
    assert sum(item["contactsAdded"] for item in results) == 1
    row = env.db.scalar(select(ReferenceAgency))
    assert len(row.staff_contacts) == 1
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 1


def test_transaction_failure_rolls_back_entire_import(env):
    data = b'Name,Agency\nAlex,North Agency\nBlair,East Agency\n'
    parsed = imports.parse_upload(UploadFile(io.BytesIO(data), filename="agencies.csv"), mapping=None, header_row=None, sheet_name=None)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        imports.confirm(env.db, SimpleNamespace(id="missing-actor"), parsed)
    assert failure.value.status_code == 409
    assert env.db.scalar(select(func.count()).select_from(ReferenceAgency)) == 0


@pytest.mark.parametrize("options", [{"mapping": '{"name":99}'}, {"mapping": '{"name":0,"agencyName":0}'},
                                     {"mapping": '{"name":true}'}, {"headerRow": "21"}])
def test_invalid_mapping_is_rejected(env, options):
    response = env.client.post("/api/reference-agencies/import/preview", data=options,
                               files={"file": ("agencies.csv", b'Name,Agency\nAlex,North\n')})
    assert response.status_code == 422


def test_upload_limits_wrong_format_and_unauthenticated_access(env):
    for filename, content, status in (("old.xls", b"not xlsx", 422), ("big.csv", b"x" * (imports.MAX_BYTES + 1), 413),
                                       ("rows.csv", b"Name\n" + b"Alex\n" * (imports.MAX_ROWS + 1), 422)):
        response = env.client.post("/api/reference-agencies/import/preview", files={"file": (filename, content)})
        assert response.status_code == status
    env.app.dependency_overrides.pop(current_user)
    assert env.client.post("/api/reference-agencies/import/preview", files={"file": ("agencies.csv", b"Name\nAlex\n")}).status_code == 401
    assert env.client.post("/api/reference-agencies/import/confirm", data={"previewHash": "x"}, files={"file": ("agencies.csv", b"Name\nAlex\n")}).status_code == 401


def test_migration_is_additive_repeatable_and_keeps_existing_values(schema_database):
    _migrate(schema_database, "0035_invoice_payments")
    with schema_database.begin() as conn:
        conn.execute(text("INSERT INTO crm_reference_agencies (id,name,agency_name,address) VALUES ('old','Alex','North Agency','First Street')"))
    _migrate(schema_database, "head")
    with schema_database.begin() as conn:
        assert conn.execute(text("SELECT name,address,place FROM crm_reference_agencies WHERE id='old'")).one() == ("Alex", "First Street", None)
        conn.execute(text("UPDATE crm_reference_agencies SET place='Trichy' WHERE id='old'"))
        conn.execute(text("UPDATE alembic_version SET version_num='0035_invoice_payments'"))
    _migrate(schema_database, "head")
    with schema_database.connect() as conn:
        assert conn.scalar(text("SELECT place FROM crm_reference_agencies WHERE id='old'")) == "Trichy"
    _migrate(schema_database, "0035_invoice_payments", downgrade=True)
    assert "place" not in {column["name"] for column in inspect(schema_database).get_columns("crm_reference_agencies")}
