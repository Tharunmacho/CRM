"""Lead sheet file reading and normalisation (no database): encodings, delimiters, xlsx, header aliases and value rules."""
from datetime import date, datetime, timezone
import io
import zipfile

import pytest

from app.services import lead_parsing as lp
from lead_sheet_fixtures import META_HEADERS, csv_bytes, lead_row, meta_export, meta_rows, xlsx_bytes


def normalised(headers, cells, mapping=None, **kw):
    table_columns = lp.map_headers(headers, mapping)
    return lp.normalise_row(headers, cells, table_columns, today=date(2026, 11, 6), **kw)


# ------------------------------------------------------------------ encoding / delimiter / format

def test_meta_utf16_tsv_is_read_with_all_27_columns():
    table = lp.read_table(meta_export(meta_rows(0, 5)), "export.csv")
    assert (table.format, table.encoding, table.delimiter) == ("tsv", "utf-16-le", "\t")
    assert table.headers == META_HEADERS and len(table.rows) == 5
    assert table.rows[0][0] == 2, "row numbers follow the spreadsheet: header is row 1"


@pytest.mark.parametrize("delimiter,encoding,bom,fmt,enc_name", [
    (",", "utf-8", False, "csv", "utf-8"), (",", "utf-8", True, "csv", "utf-8-sig"), (";", "utf-8", False, "csv", "utf-8"),
    ("\t", "utf-8", False, "tsv", "utf-8"), (",", "cp1252", False, "csv", "cp1252"),
])
def test_csv_variants(delimiter, encoding, bom, fmt, enc_name):
    rows = [lead_row(1, city="Café Town")]
    table = lp.read_table(csv_bytes(rows, delimiter=delimiter, encoding=encoding, bom=bom), "x.csv")
    assert (table.format, table.encoding, table.delimiter) == (fmt, enc_name, delimiter)
    assert table.rows[0][1][META_HEADERS.index("city")] == "Café Town"


def test_utf16_big_endian_and_bomless_utf16_are_detected():
    text = "\t".join(META_HEADERS) + "\n" + "\t".join(lead_row(1)) + "\n"
    assert lp.read_table(b"\xfe\xff" + text.encode("utf-16-be"), "a.csv").encoding == "utf-16-be"
    assert lp.read_table(text.encode("utf-16-le"), "a.csv").encoding == "utf-16-le"


def test_quoted_fields_crlf_and_embedded_newlines():
    rows = [lead_row(1, full_name='Test, "Quoted" Lead', city="Line one\nLine two")]
    table = lp.read_table(csv_bytes(rows, newline="\r\n"), "x.csv")
    cells = table.rows[0][1]
    assert cells[META_HEADERS.index("full_name")] == 'Test, "Quoted" Lead'
    assert cells[META_HEADERS.index("city")] == "Line one\nLine two"
    assert len(table.rows) == 1


def test_blank_rows_are_skipped_but_numbering_is_kept():
    data = csv_bytes([lead_row(1), [""] * 27, lead_row(2)])
    table = lp.read_table(data, "x.csv")
    assert [n for n, _ in table.rows] == [2, 4]


def test_rejections():
    for payload, code in ((b"", "unsupported_file"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "unsupported_file"),
                          (b"%PDF-1.7 not a sheet", "unsupported_file"), (b"\n\n\n", "unsupported_file")):
        with pytest.raises(lp.LeadFileError) as caught:
            lp.read_table(payload, "x.xls")
        assert caught.value.code == code
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 100, "old.xls")
    assert "Save as .xlsx or .csv" in caught.value.message
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(b"a" * (lp.MAX_UPLOAD + 1), "big.csv")
    assert (caught.value.code, caught.value.status) == ("file_too_large", 413)


def test_row_cap(monkeypatch):
    monkeypatch.setattr(lp, "MAX_ROWS", 3)
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(csv_bytes(meta_rows(0, 10)), "x.csv")
    assert caught.value.code == "too_many_rows"
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(xlsx_bytes([META_HEADERS, *meta_rows(0, 10)]), "x.xlsx")
    assert caught.value.code == "too_many_rows"


# ------------------------------------------------------------------ xlsx

def test_xlsx_shared_inline_numbers_and_gaps():
    rows = [["Full Name", "Mobile", "Email", "Notes"], ["Test Person", 9876543210, "t@example.test", None],
            [None, None, None, None], ["Second Person", 9123456789.0, None, "hello"]]
    for shared in (True, False):
        table = lp.read_table(xlsx_bytes(rows, shared=shared), "leads.xlsx")
        assert table.format == "xlsx" and table.headers == ["Full Name", "Mobile", "Email", "Notes"]
        assert table.rows[0] == (2, ["Test Person", "9876543210", "t@example.test", ""])
        assert table.rows[1] == (4, ["Second Person", "9123456789", "", "hello"])


def test_xlsx_excel_serial_dob_only_in_date_columns():
    # 33000 = 1990-05-07 in the 1900 date system; the same number in the phone column stays a number
    row = normalised(["Name", "Phone", "DOB"], ["Test Person", "9876543210", "33000"])
    assert row["dob"] == date(1990, 5, 7) and row["phone_key"] == "9876543210"
    assert normalised(["Name", "Phone", "Age"], ["Test Person", "9876543210", "33000"])["dob"] is None


def test_xlsx_safety():
    base = [["Name", "Phone"], ["A", "9000000001"]]
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(xlsx_bytes(base, extra_parts={"xl/vbaProject.bin": b"macro"}), "x.xlsm")
    assert "Macro" in caught.value.message
    with pytest.raises(lp.LeadFileError):
        lp.read_table(xlsx_bytes(base, content_types_extra='<Override PartName="/xl/workbook.xml" ContentType="application/vnd.ms-excel.sheet.macroEnabled.main+xml"/>'), "x.xlsx")
    # zip bomb: 60 MB of zeros compress to a few KB but unpack beyond the 50 MB cap
    with pytest.raises(lp.LeadFileError) as caught:
        lp.read_table(xlsx_bytes(base, extra_parts={"xl/media/big.bin": b"\0" * (60 * 1024 * 1024)}), "x.xlsx")
    assert caught.value.code == "file_too_large"
    # entity declarations are never expanded
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>&a;</t></si></sst>'
    with pytest.raises(lp.LeadFileError):
        lp.read_table(xlsx_bytes(base, extra_parts={"xl/sharedStrings.xml": evil}), "x.xlsx")
    notxlsx = io.BytesIO()
    with zipfile.ZipFile(notxlsx, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(lp.LeadFileError):
        lp.read_table(notxlsx.getvalue(), "x.xlsx")


# ------------------------------------------------------------------ header mapping

def test_meta_headers_map_to_the_expected_fields():
    columns = lp.map_headers(META_HEADERS)
    mapped = {c["header"]: c["field"] for c in columns.columns}
    assert mapped["id"] == "externalId" and mapped["full_name"] == "name" and mapped["phone_number"] == "phone"
    assert mapped["which_position_are_you_applying_for?"] == "positionApplied"
    assert mapped["how_many_years_of_experience_do_you_have_in_the_restaurant_industry?"] == "experienceRestaurant"
    assert mapped["what_is_your_total_years_of_work_experience?"] == "experienceTotal"
    assert mapped["date_of_birth"] == "dob" and mapped["lead_status"] == "leadStatusSource"
    assert columns.unmapped == []
    assert {c["confidence"] for c in columns.columns} <= {"exact", "alias"}


@pytest.mark.parametrize("header,field", [
    ("Full Name", "name"), ("Candidate Name", "name"), ("Name", "name"), ("Phone", "phone"), ("Mobile", "phone"), ("Contact", "phone"),
    ("Mobile No.", "phone"), ("WhatsApp", "whatsapp"), ("E-mail", "email"), ("Email Address", "email"), ("City", "city"), ("State", "state"),
    ("DOB", "dob"), ("Date of Birth", "dob"), ("Position", "positionApplied"), ("Job", "positionApplied"), ("Experience", "experienceTotal"),
    ("Created Time", "createdTime"), ("Campaign Name", "campaignName"), ("Form Name", "formName"), ("Lead ID", "externalId"),
])
def test_header_aliases(header, field):
    assert lp.map_headers([header]).by_field == {field: 0}


def test_unknown_columns_are_kept_in_extra_and_raw():
    headers = ["Full Name", "Mobile", "Do you have a passport?", "Preferred shift"]
    row = normalised(headers, ["Test Person", "9000000001", "Yes", "Night"])
    assert row["extra"] == {"Do you have a passport?": "Yes", "Preferred shift": "Night"}
    assert row["raw"]["Full Name"] == "Test Person" and row["raw"]["Preferred shift"] == "Night"


def test_mapping_override_and_first_last_name():
    headers = ["col_a", "col_b", "First Name", "Last Name"]
    row = normalised(headers, ["Test Person", "9000000001", "Asha", "Tester"], mapping={"col_a": "city", "col_b": "phone"})
    assert row["city"] == "Test Person" and row["phone_key"] == "9000000001" and row["name"] == "Asha Tester"
    assert lp.map_headers(headers, {"col_a": "city"}).columns[0]["confidence"] == "override"
    assert lp.map_headers(["Name"], {"Name": None}).by_field == {}


# ------------------------------------------------------------------ values

@pytest.mark.parametrize("raw,phone,code,key", [
    ("p:+919876543210", "+919876543210", "91", "9876543210"), ("+91 98765 43210", "+919876543210", "91", "9876543210"),
    ("919876543210", "+919876543210", "91", "9876543210"), ("09876543210", "9876543210", None, "9876543210"),
    ("0091 98765-43210", "+919876543210", "91", "9876543210"), ("p:9876543210", "9876543210", None, "9876543210"),
    ("p:+971501234567", "+971501234567", "971", "1501234567"), ("  ( 987 ) 654 3210 ", "9876543210", None, "9876543210"),
    ("p:+1 (202) 555-0100", "+12025550100", "1", "2025550100"), ("2345678", "2345678", None, "2345678"),
])
def test_phone_rules(raw, phone, code, key):
    parsed = lp.parse_phone(raw)
    assert parsed["valid"] and (parsed["phone"], parsed["country_code"], parsed["key"]) == (phone, code, key)


def test_phone_match_ignores_formatting_and_country_code():
    keys = {lp.parse_phone(v)["key"] for v in ("p:+919876543210", "9876543210", "+91 98765 43210", "09876543210", "0091-9876543210")}
    assert keys == {"9876543210"}


@pytest.mark.parametrize("raw", ["", "12345", "p:", "abc", "1" * 16, "+1 (2)"])
def test_bad_phones(raw):
    assert not lp.parse_phone(raw)["valid"]


def test_email_and_ids():
    assert lp.parse_email("  Test.Lead@Example.TEST ") == "test.lead@example.test"
    assert lp.parse_email("not an email") is None and lp.parse_email("a@b") is None
    assert lp.parse_id("l:1795000000000001") == "1795000000000001" and lp.parse_id("ag:123") == "123" and lp.parse_id("") is None
    assert lp.parse_id("1795") == "1795"


@pytest.mark.parametrize("raw,expected", [
    ("03/27/1971", date(1971, 3, 27)), ("1990-09-25", date(1990, 9, 25)), ("25/12/1985", date(1985, 12, 25)),
    ("07/04/1992", date(1992, 7, 4)),  # ambiguous: MM/DD like Meta
    ("07-04-1992", date(1992, 4, 7)),  # dashes are day first
    ("12/31/90", date(1990, 12, 31)), ("1 Jan 1990", date(1990, 1, 1)), ("1990-09-25T00:00:00", date(1990, 9, 25)),
    ("", None), ("not a date", None), ("13/13/1990", None), ("1990-02-30", None), ("01/01/1850", None), ("01/01/2999", None),
])
def test_dob_formats(raw, expected):
    assert lp.parse_dob(raw, date(2026, 11, 6)) == expected


@pytest.mark.parametrize("raw,years", [
    ("10+ years", 10), ("1-3 years", 1), ("22", 22), ("5 years", 5), ("6 months", 0.5), ("2.5 years", 2.5), ("fresher", 0),
    ("Less than 1 year", 0), ("9999999999", None), ("some text with no number", None), ("", None), ("3-5", 3), ("over 7 yrs", 7),
])
def test_experience(raw, years):
    value, original = lp.parse_experience(raw)
    assert value == years and original == (raw or None)


def test_prettified_values():
    assert lp.prettify("south_indian_chef_(cdp)") == "South Indian Chef (CDP)"
    assert lp.prettify("dosa_&_parotta_master") == "Dosa & Parotta Master"
    assert lp.prettify("kitchen_helper") == "Kitchen Helper"
    assert lp.prettify("MARRIED") == "Married" and lp.prettify("Already Fine") == "Already Fine" and lp.prettify("  ") is None
    for raw in ("Tamilnadu", "tamil_nadu", "tamil nadu", "Tamil Nadu, India", "TN", "tamilnadu"):
        assert lp.prettify_state(raw) == "Tamil Nadu"
    for raw in ("kerala", "Kerala", "KL"):
        assert lp.prettify_state(raw) == "Kerala"
    assert lp.prettify_state("Some Town") == "Some Town"
    assert [lp.parse_gender(v) for v in ("male", "Male", "Men", "female", "other", "Yes")] == ["Male", "Male", "Male", "Female", "Other", None]
    assert lp.parse_platform("ig") == "ig" and lp.parse_platform("Instagram") == "ig" and lp.parse_platform("fb") == "fb"


def test_created_time_keeps_offset_and_naive_uses_agency_zone():
    parsed = lp.parse_created_time("2026-11-04T19:53:51+05:30")
    assert parsed.utcoffset().total_seconds() == 19800 and parsed.astimezone(timezone.utc) == datetime(2026, 11, 4, 14, 23, 51, tzinfo=timezone.utc)
    assert lp.parse_created_time("2026-11-04 19:53:51").astimezone(timezone.utc) == datetime(2026, 11, 4, 14, 23, 51, tzinfo=timezone.utc)
    assert lp.parse_created_time("garbage") is None


def test_country_guess():
    assert lp.guess_country("Dubai-Chef-03-11") == "Dubai"
    assert lp.guess_country(None, "Saudi cooks Nov") == "Saudi Arabia"
    assert lp.guess_country("Plain campaign") is None


def test_full_meta_row_normalises():
    row = normalised(META_HEADERS, lead_row(7))
    assert row["external_id"] == "1795000000000007" and row["phone"] == "+919000000007" and row["phone_key"] == "9000000007"
    assert row["email_key"] == "lead7@example.test" and row["name"] == "Test Lead 00007"
    assert row["position_applied"] in ("South Indian Chef (CDP)", "Kitchen Helper", "Dosa & Parotta Master")
    assert row["country_interest"] == "Dubai" and row["platform"] == "fb" and row["is_organic"] is False
    assert row["campaign_id"] == "120249141517420605" and row["ad_id"] == "120249141517430605"
    assert row["_invalid"] is None


def test_invalid_rows():
    headers = ["Name", "Phone", "Email"]
    assert normalised(headers, ["Only Name", "", ""])["_invalid"]["code"] == "no_contact"
    assert normalised(headers, ["Name", "12345", ""])["_invalid"]["code"] == "bad_phone"
    assert normalised(headers, ["Name", "12345", "ok@example.test"])["_invalid"] is None  # an e-mail is enough
    assert normalised(headers, ["", "9000000001", ""])["_invalid"] is None  # a phone is enough
