"""Reading and normalising lead sheets (Meta Lead Ads exports, Google lead forms, any CSV / TSV / XLSX).

Pure functions: no database. `lead_import.py` stores what this module produces. The rules (encoding, delimiter, header
aliases, phone/e-mail/id keys, dates, experience, prettified values) are documented in
docs/workflows/lead-sheet-contract.md and exercised by tests/test_lead_sheet_parsing.py.

The reader never evaluates formulas, never resolves XML entities and refuses archives that unpack to more than
MAX_UNPACKED bytes or hold more than MAX_ROWS rows.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

MAX_UPLOAD = 25 * 1024 * 1024
MAX_UNPACKED = 50 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 300
MAX_ZIP_ENTRIES = 300

csv.field_size_limit(10_000_000)


class LeadFileError(Exception):
    """A file the importer cannot read. `code` and `status` map straight onto the API error."""

    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ------------------------------------------------------------------ bytes -> table

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def decode_bytes(data: bytes) -> tuple[str, str]:
    """(text, encoding name): BOM UTF-16 LE/BE, UTF-8 with BOM, UTF-8, a BOM-less UTF-16 guess, else Windows-1252."""
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", errors="replace"), "utf-16-le"
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", errors="replace"), "utf-16-be"
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    sample = data[:4000]
    if len(sample) >= 4 and sample[1::2].count(0) > len(sample) // 4 and sample[0::2].count(0) == 0:
        return data.decode("utf-16-le", errors="replace"), "utf-16-le"
    if len(sample) >= 4 and sample[0::2].count(0) > len(sample) // 4 and sample[1::2].count(0) == 0:
        return data.decode("utf-16-be", errors="replace"), "utf-16-be"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), "cp1252"


def clean_cell(value) -> str:
    """Text of one cell: NFKC-free (names keep their characters), no control characters, no NUL, trimmed."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "")
    text = "".join(ch for ch in text if ch in "\t\n\r" or unicodedata.category(ch)[0] != "C")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def detect_delimiter(text: str) -> str:
    """Tab, comma, semicolon or pipe: whichever occurs most often (outside quotes) in the header record. Tab wins ties."""
    counts = {"\t": 0, ",": 0, ";": 0, "|": 0}
    quoted = False
    for ch in text[:20000]:
        if ch == '"':
            quoted = not quoted
        elif not quoted:
            if ch in "\r\n":
                break
            if ch in counts:
                counts[ch] += 1
    best = max(counts.values())
    if best == 0:
        return ","
    for delimiter in ("\t", ",", ";", "|"):
        if counts[delimiter] == best:
            return delimiter
    return ","


def read_delimited(text: str, delimiter: str | None = None) -> tuple[list[list[str]], str]:
    delimiter = delimiter or detect_delimiter(text)
    rows: list[list[str]] = []
    try:
        for record in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, quotechar='"'):
            rows.append([clean_cell(cell) for cell in record])
            if len(rows) > MAX_ROWS + 1:
                raise LeadFileError("too_many_rows", f"The file has more than {MAX_ROWS:,} rows. Split it and upload the parts one by one.")
    except csv.Error as exc:
        raise LeadFileError("unsupported_file", f"The file could not be read as a spreadsheet ({exc}).") from None
    return rows, delimiter


# ------------------------------------------------------------------ xlsx (zip + xml, no third-party library)

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _read_member(archive: zipfile.ZipFile, name: str, budget: list[int]) -> bytes:
    """One archive member, never more than the remaining unpacked budget (a lying size header cannot bypass it)."""
    with archive.open(name) as handle:
        data = handle.read(budget[0] + 1)
    budget[0] -= len(data)
    if budget[0] < 0:
        raise LeadFileError("file_too_large", "The workbook unpacks to more than 50 MB. Save it again as CSV.", 413)
    if b"<!DOCTYPE" in data[:2000].upper() or b"<!ENTITY" in data[:20000].upper():
        raise LeadFileError("unsupported_file", "The workbook contains unsupported XML declarations.")
    return data


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Za-z]+", ref or "")
    if not letters:
        return -1
    number = 0
    for ch in letters.group(0).upper():
        number = number * 26 + (ord(ch) - 64)
    return number - 1


def _shared_strings(data: bytes) -> list[str]:
    strings: list[str] = []
    for _, element in ET.iterparse(io.BytesIO(data), events=("end",)):
        if element.tag == _NS + "si":
            parts = []
            for child in element:  # plain <t> or rich-text <r><t>; phonetic hints (<rPh>) are skipped
                if child.tag == _NS + "t":
                    parts.append(child.text or "")
                elif child.tag == _NS + "r":
                    parts.extend(t.text or "" for t in child.findall(_NS + "t"))
            strings.append("".join(parts))
            element.clear()
    return strings


def _number_text(raw: str) -> str:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    if value == value.to_integral_value():
        return str(int(value))
    text = format(value.normalize(), "f")
    return text


def workbook_sheets(data: bytes) -> list[str]:
    """Worksheet labels for a bounded import preview; no workbook values are evaluated."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > MAX_ZIP_ENTRIES or sum(i.file_size for i in archive.infolist()) > MAX_UNPACKED:
                raise LeadFileError("file_too_large", "The workbook is too large. Save the needed sheet as CSV.", 413)
            root = ET.fromstring(_read_member(archive, "xl/workbook.xml", [MAX_UNPACKED]))
            return [sheet.get("name", "") for sheet in root.findall(f"{_NS}sheets/{_NS}sheet")]
    except (zipfile.BadZipFile, KeyError, ET.ParseError, RuntimeError):
        raise LeadFileError("unsupported_file", "The workbook could not be read. Save it as .xlsx or .csv.") from None


def read_xlsx(data: bytes, *, sheet_name: str | None = None, max_rows: int | None = None,
              max_columns: int | None = None, preserve_row_numbers: bool = False) -> list[list[str]]:
    max_rows = MAX_ROWS if max_rows is None else max_rows
    max_columns = MAX_COLUMNS if max_columns is None else max_columns
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise LeadFileError("unsupported_file", "This is not a valid .xlsx workbook. Save it as .xlsx or .csv and try again.") from None
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise LeadFileError("unsupported_file", "The workbook has too many parts.")
        names = {info.filename for info in infos}
        if sum(info.file_size for info in infos) > MAX_UNPACKED:
            raise LeadFileError("file_too_large", "The workbook unpacks to more than 50 MB. Save it again as CSV.", 413)
        if any("vbaproject" in name.lower() for name in names) or "xl/workbook.xml" not in names:
            if "xl/workbook.xml" in names or any("vbaproject" in name.lower() for name in names):
                raise LeadFileError("unsupported_file", "Macro-enabled workbooks are not accepted. Save the sheet as .xlsx or .csv.")
            raise LeadFileError("unsupported_file", "This is not an Excel workbook. Save it as .xlsx or .csv.")
        budget = [MAX_UNPACKED]
        content_types = _read_member(archive, "[Content_Types].xml", budget) if "[Content_Types].xml" in names else b""
        if b"macroEnabled" in content_types:
            raise LeadFileError("unsupported_file", "Macro-enabled workbooks are not accepted. Save the sheet as .xlsx or .csv.")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared = _shared_strings(_read_member(archive, "xl/sharedStrings.xml", budget))
        sheet_path = _first_sheet(archive, names, budget, sheet_name)
        raw = _read_member(archive, sheet_path, budget)
    rows: list[list[str]] = []
    row: dict[int, str] = {}
    current = -1
    cell_type = ""
    cell_ref = ""
    cell_text: str | None = None
    inline_parts: list[str] = []
    in_inline = False
    for event, element in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
        tag = element.tag
        if event == "start":
            if tag == _NS + "c":
                cell_type, cell_ref, cell_text, inline_parts, in_inline = element.get("t", "n"), element.get("r", ""), None, [], False
            elif tag == _NS + "is":
                in_inline = True
            elif tag == _NS + "row":
                row = {}
                if preserve_row_numbers:
                    try:
                        number = int(element.get("r", str(len(rows) + 1)))
                    except ValueError:
                        raise LeadFileError("unsupported_file", "The workbook has an invalid row number.") from None
                    if number > max_rows + 1 or number < len(rows) + 1:
                        raise LeadFileError("too_many_rows", f"The sheet must use at most {max_rows:,} rows in order.")
                    rows.extend([[] for _ in range(number - len(rows) - 1)])
            continue
        if tag == _NS + "v":
            cell_text = element.text or ""
        elif tag == _NS + "t" and in_inline:
            inline_parts.append(element.text or "")
        elif tag == _NS + "is":
            in_inline = False
        elif tag == _NS + "c":
            value = ""
            if cell_type == "s":
                try:
                    value = shared[int(cell_text or "")]
                except (ValueError, IndexError):
                    value = ""
            elif cell_type == "inlineStr":
                value = "".join(inline_parts)
            elif cell_type in ("str", "e"):
                value = "" if cell_type == "e" else (cell_text or "")
            elif cell_type == "b":
                value = "TRUE" if (cell_text or "") == "1" else "FALSE"
            elif cell_text is not None:
                value = _number_text(cell_text) if cell_type in ("n", "") else cell_text
            index = _col_index(cell_ref)
            if index < 0:
                index = (max(row) + 1) if row else 0
            if index >= max_columns and value != "" and max_columns != MAX_COLUMNS:
                raise LeadFileError("too_many_columns", f"The sheet must have at most {max_columns} columns.")
            if index < max_columns and value != "":
                row[index] = clean_cell(value)
            element.clear()
        elif tag == _NS + "row":
            current += 1
            if row:
                width = max(row) + 1
                rows.append([row.get(i, "") for i in range(width)])
            else:
                rows.append([])
            if len(rows) > max_rows + 1:
                raise LeadFileError("too_many_rows", f"The sheet has more than {max_rows:,} rows. Split it and upload the parts one by one.")
            element.clear()
    return rows


def _first_sheet(archive: zipfile.ZipFile, names: set[str], budget: list[int], selected: str | None = None) -> str:
    try:
        workbook = ET.fromstring(_read_member(archive, "xl/workbook.xml", budget))
        sheets = workbook.findall(f"{_NS}sheets/{_NS}sheet")
        first = next((sheet for sheet in sheets if selected is None or sheet.get("name") == selected), None)
        if selected is not None and first is None:
            raise LeadFileError("unsupported_file", "Choose one of the workbook's worksheets.")
        rid = first.get(_OFFICE_REL + "id") if first is not None else None
        if rid and "xl/_rels/workbook.xml.rels" in names:
            rels = ET.fromstring(_read_member(archive, "xl/_rels/workbook.xml.rels", budget))
            for rel in rels.iter(_REL + "Relationship"):
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    path = target if target.startswith("xl/") else "xl/" + target
                    if path in names:
                        return path
    except ET.ParseError:
        raise LeadFileError("unsupported_file", "The workbook could not be read. Save it again as .xlsx or .csv.") from None
    if selected is not None:
        raise LeadFileError("unsupported_file", "The selected worksheet could not be read.")
    for candidate in ("xl/worksheets/sheet1.xml",):
        if candidate in names:
            return candidate
    sheets = sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml"))
    if not sheets:
        raise LeadFileError("unsupported_file", "The workbook has no worksheet.")
    return sheets[0]


@dataclass
class ParsedTable:
    format: str            # csv | tsv | xlsx
    encoding: str | None
    delimiter: str | None
    headers: list[str]
    rows: list[tuple[int, list[str]]]  # (spreadsheet row number, cells) for each non-blank data row


def read_table(data: bytes, filename: str = "") -> ParsedTable:
    if not data:
        raise LeadFileError("unsupported_file", "The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise LeadFileError("file_too_large", "Lead sheets must be under 25 MB. Split the file and upload the parts.", 413)
    if data.startswith(OLE_MAGIC):
        raise LeadFileError("unsupported_file", "Old .xls workbooks are not supported. Save as .xlsx or .csv and upload again.")
    if data.startswith(b"%PDF") or data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8\xff"):
        raise LeadFileError("unsupported_file", "This is not a spreadsheet. Upload the .csv or .xlsx export.")
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        table, encoding, delimiter, fmt = read_xlsx(data), None, None, "xlsx"
    else:
        text, encoding = decode_bytes(data)
        table, delimiter = read_delimited(text)
        fmt = "tsv" if delimiter == "\t" else "csv"
    numbered = list(enumerate(table, start=1))
    header_at = next((i for i, (_, cells) in enumerate(numbered) if any(cells)), None)
    if header_at is None:
        raise LeadFileError("unsupported_file", "The file has no header row.")
    header_cells = numbered[header_at][1]
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, cell in enumerate(header_cells):
        label = cell or f"column_{index + 1}"
        seen[label] = seen.get(label, 0) + 1
        headers.append(label if seen[label] == 1 else f"{label} ({seen[label]})")
    while headers and not header_cells[len(headers) - 1] and len(headers) > 1:  # trailing empty header cells
        headers.pop()
    if len(headers) > MAX_COLUMNS:
        raise LeadFileError("unsupported_file", f"The sheet has more than {MAX_COLUMNS} columns.")
    rows = []
    for number, cells in numbered[header_at + 1:]:
        if any(cells):
            rows.append((number, (cells + [""] * len(headers))[:len(headers)] if len(cells) <= len(headers) else cells[:len(headers)]))
    return ParsedTable(fmt, encoding, delimiter, headers, rows)


# ------------------------------------------------------------------ header mapping

# Field keys the importer understands (what `mapping` values may be).
FIELDS = (
    "externalId", "createdTime", "name", "firstName", "lastName", "phone", "whatsapp", "email", "city", "state",
    "currentLocation", "positionApplied", "countryInterest", "experienceRestaurant", "experienceTotal", "gender",
    "maritalStatus", "dob", "educationLevel", "militaryStatus", "platform", "isOrganic", "leadStatusSource",
    "campaignId", "campaignName", "adsetId", "adsetName", "adId", "adName", "formId", "formName",
)

_ALIASES: dict[str, tuple[str, ...]] = {
    "externalId": ("id", "leadid", "leadgenid", "metaleadid", "responseid", "submissionid", "entryid"),
    "createdTime": ("createdtime", "created", "createdat", "datecreated", "submittedat", "submissiondate", "timestamp", "leaddate", "date", "submittedon", "createdon"),
    "name": ("fullname", "name", "candidatename", "leadname", "customername", "applicantname", "yourname", "contactname", "personname"),
    "firstName": ("firstname", "givenname", "fname"),
    "lastName": ("lastname", "surname", "familyname", "lname"),
    "phone": ("phonenumber", "phone", "mobile", "mobilenumber", "mobileno", "contact", "contactnumber", "contactno", "phoneno",
              "cell", "cellphone", "telephone", "tel", "mobilephone", "primaryphone", "yourphonenumber", "yourmobilenumber"),
    "whatsapp": ("whatsapp", "whatsappnumber", "whatsappno", "whatsappphone", "whatsappphonenumber"),
    "email": ("email", "emailaddress", "emailid", "mail", "mailid", "workemail", "youremail", "eaddress"),
    "city": ("city", "town", "cityname", "district"),
    "state": ("state", "province", "region", "statename"),
    "currentLocation": ("currentlocation", "location", "currentstate", "presentlocation", "currentplace"),
    "positionApplied": ("whichpositionareyouapplyingfor", "position", "positionapplied", "job", "jobtitle", "jobrole", "jobposition",
                        "designation", "role", "appliedfor", "post", "postapplied", "appliedposition", "jobapplied", "trade", "skill"),
    "countryInterest": ("countryinterest", "country", "preferredcountry", "countrypreference", "interestedcountry", "destination", "countryofinterest"),
    "experienceRestaurant": ("howmanyyearsofexperiencedoyouhaveintherestaurantindustry", "restaurantexperience", "restaurantindustryexperience",
                             "experienceinrestaurant", "hotelexperience", "kitchenexperience", "relevantexperience"),
    "experienceTotal": ("whatisyourtotalyearsofworkexperience", "totalexperience", "experience", "exp", "yearsofexperience", "workexperience",
                        "totalyearsofexperience", "experienceyears", "totalworkexperience", "overallexperience"),
    "gender": ("gender", "sex"),
    "maritalStatus": ("maritalstatus", "marital", "married"),
    "dob": ("dateofbirth", "dob", "birthdate", "birthday", "dateofbirthddmmyyyy", "birthdateofcandidate"),
    "educationLevel": ("educationlevel", "education", "qualification", "highestqualification", "educationalqualification"),
    "militaryStatus": ("militarystatus", "military"),
    "platform": ("platform", "channel", "adplatform"),
    "isOrganic": ("isorganic", "organic"),
    "leadStatusSource": ("leadstatus", "status", "leadstate"),
    "campaignId": ("campaignid",),
    "campaignName": ("campaignname", "campaign"),
    "adsetId": ("adsetid", "adgroupid"),
    "adsetName": ("adsetname", "adgroupname", "adset", "adgroup"),
    "adId": ("adid",),
    "adName": ("adname", "ad"),
    "formId": ("formid",),
    "formName": ("formname", "form", "leadformname"),
}
ALIAS_LOOKUP: dict[str, str] = {}
for _field, _names in _ALIASES.items():
    for _alias in _names:
        ALIAS_LOOKUP.setdefault(_alias, _field)

# Companion headings emitted by the CRM export contain question text, not answers.
EXPERIENCE_QUESTION_COMPANIONS = {
    "experienceindia": "indiaexperiencequestion", "experienceabroad": "abroadexperiencequestion",
    "experiencetotal": "totalexperiencequestion", "experiencerestaurant": "restaurantexperiencequestion",
}


def header_key(header: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", unicodedata.normalize("NFKC", header).lower())


def geographic_experience_scope(header: str) -> str | None:
    """Recognise explicitly labelled experience, never cuisine or residence.

    Mixed India/abroad questions stay ambiguous. Keeping this independent of
    field mapping also lets older imports expose their retained source answers.
    """
    label = unicodedata.normalize("NFKC", clean_cell(header))
    label = re.sub(r"([a-z])([A-Z])", r"\1 \2", label)
    words = re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()
    compact = header_key(header)
    if compact in EXPERIENCE_QUESTION_COMPANIONS.values():
        return None
    short = {
        "indiaexperience": "india", "indianexperience": "india", "experienceindia": "india",
        "indiaexp": "india", "expindia": "india", "indianexp": "india", "expindian": "india",
        "domesticexperience": "unspecified", "domesticexp": "unspecified", "experiencedomestic": "unspecified", "expdomestic": "unspecified",
        "localexperience": "unspecified", "localexp": "unspecified", "experiencelocal": "unspecified", "explocal": "unspecified",
        "abroadexperience": "abroad", "experienceabroad": "abroad", "abroadexp": "abroad", "expabroad": "abroad",
        "overseasexperience": "abroad", "experienceoverseas": "abroad", "overseasexp": "abroad", "expoverseas": "abroad",
        "gulfexperience": "abroad", "experiencegulf": "abroad", "gulfexp": "abroad", "expgulf": "abroad",
        "foreignexperience": "abroad", "experienceforeign": "abroad", "foreignexp": "abroad", "expforeign": "abroad",
    }
    if compact in short:
        return short[compact]
    if not re.search(r"\b(?:experience|exp|worked|working|employed)\b", words):
        return None
    geographic_words = bool(re.search(r"\b(?:india|indian|abroad|overseas|gulf|foreign|domestic|local)\b", words))
    if geographic_words and re.search(
        r"\b(?:customers?|clients?|consumers?|vendors?|suppliers?|citizens?|citizenship|nationality|nationals?|passport|"
        r"birth|born|residence|residents?|residing|lived|living)\b", words
    ):
        # A customer's location or a candidate's nationality/residence is not
        # evidence of work location. Keep even compound questions unassigned.
        return "unspecified"
    # A geographical adjective alone can describe software, employers or food.
    # Require a regional experience label or an explicit work-location phrase.
    bridge = r"(?:(?:do you have|have you had|have you gained|you have|gained|acquired|obtained) )?"
    experience = rf"\b(?:experience|exp) {bridge}(?:working |work )?"
    india = bool(re.search(r"\b(?:india|indian) (?:work )?(?:experience|exp)\b", words)
                 or re.search(experience + r"(?:in|within) india\b", words)
                 or re.search(r"\b(?:worked|working|employed) (?:in|within) india\b", words))
    abroad_location = r"(?:abroad|overseas|in (?:the )?gulf|in foreign countries|outside(?: of)? india)\b"
    abroad = bool(re.search(r"\b(?:abroad|overseas|gulf|foreign) (?:work )?(?:experience|exp)\b", words)
                  or re.search(experience + abroad_location, words)
                  or re.search(r"\b(?:worked|working|employed) " + abroad_location, words))
    combined = re.search(r"\b(?:india|indian) (?:and|or) (?:abroad|overseas|gulf|foreign|outside(?: of)? india)\b|"
                         r"\b(?:abroad|overseas|gulf|foreign) (?:and|or) (?:india|indian)\b", words)
    if combined:
        return "mixed"
    if india and abroad:
        return "mixed"
    if india or abroad:
        return "india" if india else "abroad"
    # Retain ambiguous geographic questions without inferring a location or
    # incorrectly treating a narrower question as total work experience.
    return "unspecified" if geographic_words else None


def _fuzzy_field(key: str) -> str | None:
    if "whatsapp" in key:
        return "whatsapp"
    if "phone" in key or "mobile" in key:
        return "phone"
    if "email" in key:
        return "email"
    if "restaurant" in key and "experience" in key:
        return "experienceRestaurant"
    if "experience" in key:
        return "experienceTotal"
    if "position" in key or "jobtitle" in key or "applyingfor" in key:
        return "positionApplied"
    if "dateofbirth" in key or "birth" in key:
        return "dob"
    if "fullname" in key:
        return "name"
    return None


@dataclass
class ColumnMap:
    columns: list[dict] = field(default_factory=list)   # [{header, field, confidence}] in file order
    by_field: dict[str, int] = field(default_factory=dict)  # field -> column index

    @property
    def unmapped(self) -> list[str]:
        return [c["header"] for c in self.columns if not c["field"]]

    def as_map(self) -> dict[str, str | None]:
        return {c["header"]: c["field"] for c in self.columns}


def map_headers(headers: list[str], override: dict | None = None) -> ColumnMap:
    """Header -> field. The best claim on a field wins (exact, then alias, then fuzzy; ties go to the first column)."""
    override = {header_key(k): v for k, v in (override or {}).items()}
    rank = {"override": 0, "exact": 1, "alias": 2, "fuzzy": 3}
    claims: dict[str, tuple[int, int]] = {}  # field -> (rank, index)
    proposals: list[tuple[str | None, str | None]] = []
    for index, header in enumerate(headers):
        key = header_key(header)
        field_name, how = None, None
        if key in override:
            field_name, how = (override[key] if override[key] in FIELDS else None), "override"
            if field_name is None:
                proposals.append((None, None))
                continue
        elif key in ALIAS_LOOKUP:
            field_name = ALIAS_LOOKUP[key]
            how = "exact" if key == header_key(field_name) else "alias"
        elif key in EXPERIENCE_QUESTION_COMPANIONS.values():
            field_name, how = None, None
        elif geographic_experience_scope(header):
            # There are no geographic experience columns in the lead table.
            # Retain these in raw/extra; the read API projects their answers.
            # They must not steal the total/restaurant field through fuzzy matching.
            field_name, how = None, None
        else:
            field_name = _fuzzy_field(key)
            how = "fuzzy" if field_name else None
        proposals.append((field_name, how))
        if field_name:
            best = claims.get(field_name)
            if best is None or (rank[how], index) < best:
                claims[field_name] = (rank[how], index)
    result = ColumnMap()
    for index, header in enumerate(headers):
        field_name, how = proposals[index]
        if field_name and claims[field_name][1] == index:
            result.columns.append({"header": header, "field": field_name, "confidence": how})
            result.by_field[field_name] = index
        else:
            result.columns.append({"header": header, "field": None, "confidence": None})
    return result


# ------------------------------------------------------------------ value normalisation

LIMITS = {
    "external_id": 64, "phone_key": 20, "email_key": 255, "name": 200, "phone": 40, "phone_country_code": 6, "email": 255,
    "whatsapp": 40, "city": 120, "state": 120, "current_location": 120, "position_applied": 200, "country_interest": 120,
    "experience_restaurant_text": 200, "experience_total_text": 200, "gender": 20, "marital_status": 120,
    "education_level": 200, "military_status": 200, "platform": 30, "lead_status_source": 60, "campaign_id": 64,
    "campaign_name": 255, "adset_id": 64, "adset_name": 255, "ad_id": 64, "ad_name": 255, "form_id": 64, "form_name": 255,
}

STATE_NAMES = {
    "tamilnadu": "Tamil Nadu", "tn": "Tamil Nadu", "kerala": "Kerala", "kl": "Kerala", "karnataka": "Karnataka", "ka": "Karnataka",
    "andhrapradesh": "Andhra Pradesh", "ap": "Andhra Pradesh", "telangana": "Telangana", "ts": "Telangana",
    "maharashtra": "Maharashtra", "mh": "Maharashtra", "westbengal": "West Bengal", "wb": "West Bengal",
    "uttarpradesh": "Uttar Pradesh", "up": "Uttar Pradesh", "madhyapradesh": "Madhya Pradesh", "mp": "Madhya Pradesh",
    "gujarat": "Gujarat", "rajasthan": "Rajasthan", "odisha": "Odisha", "orissa": "Odisha", "bihar": "Bihar", "assam": "Assam",
    "punjab": "Punjab", "haryana": "Haryana", "jharkhand": "Jharkhand", "chhattisgarh": "Chhattisgarh", "goa": "Goa",
    "himachalpradesh": "Himachal Pradesh", "uttarakhand": "Uttarakhand", "delhi": "Delhi", "newdelhi": "Delhi",
    "jammuandkashmir": "Jammu and Kashmir", "jammukashmir": "Jammu and Kashmir", "puducherry": "Puducherry", "pondicherry": "Puducherry",
    "sikkim": "Sikkim", "tripura": "Tripura", "manipur": "Manipur", "meghalaya": "Meghalaya", "nagaland": "Nagaland",
    "mizoram": "Mizoram", "arunachalpradesh": "Arunachal Pradesh", "chandigarh": "Chandigarh", "ladakh": "Ladakh",
}

COUNTRIES = {
    "dubai": "Dubai", "uae": "UAE", "abudhabi": "Abu Dhabi", "sharjah": "Sharjah", "qatar": "Qatar", "doha": "Qatar",
    "kuwait": "Kuwait", "saudi": "Saudi Arabia", "ksa": "Saudi Arabia", "riyadh": "Saudi Arabia", "jeddah": "Saudi Arabia",
    "oman": "Oman", "muscat": "Oman", "bahrain": "Bahrain", "malaysia": "Malaysia", "singapore": "Singapore",
    "canada": "Canada", "germany": "Germany", "uk": "UK", "australia": "Australia", "newzealand": "New Zealand",
    "maldives": "Maldives", "europe": "Europe", "usa": "USA", "japan": "Japan", "poland": "Poland", "romania": "Romania",
    "serbia": "Serbia", "croatia": "Croatia", "israel": "Israel", "russia": "Russia", "cyprus": "Cyprus",
    "mauritius": "Mauritius", "brunei": "Brunei", "hongkong": "Hong Kong", "taiwan": "Taiwan", "malta": "Malta",
}

_SMALL_UPPER = re.compile(r"^[A-Za-z]{2,5}$")


def prettify(value: str | None) -> str | None:
    """`south_indian_chef_(cdp)` -> `South Indian Chef (CDP)`; text that already has mixed case is left alone."""
    text = " ".join(clean_cell(value).split())
    if not text:
        return None
    snake = "_" in text and " " not in text
    if snake:
        text = text.replace("_", " ")
        text = " ".join(text.split())
    if snake or text == text.lower() or text == text.upper():
        words = []
        for word in text.split(" "):
            inner = re.fullmatch(r"\(([A-Za-z]{2,5})\)", word)
            if inner:
                words.append(f"({inner.group(1).upper()})")
            elif word in ("&", "-", "/"):
                words.append(word)
            else:
                words.append(word[:1].upper() + word[1:].lower() if word else word)
        text = " ".join(words)
    return text


def prettify_state(value: str | None) -> str | None:
    text = prettify(value)
    if not text:
        return None
    base = re.sub(r",?\s*india$", "", text, flags=re.I).strip()
    key = re.sub(r"[^a-z]", "", base.lower())
    return STATE_NAMES.get(key) or base or None


def digits_of(value) -> str:
    return re.sub(r"\D", "", clean_cell(value))


CALLING_CODES = {"91", "971", "966", "974", "965", "968", "973", "977", "94", "880", "92", "63", "62", "60", "65", "44", "1", "61", "49", "33"}


def parse_phone(value) -> dict:
    """{phone, country_code, key, valid, given}. See the contract for the rule."""
    raw = re.sub(r"^\s*p\s*:", "", clean_cell(value), flags=re.I).strip()
    given = bool(raw)
    digits = digits_of(raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if not 7 <= len(digits) <= 15:
        return {"phone": None, "country_code": None, "key": None, "valid": False, "given": given}
    code = ""
    if len(digits) > 10:
        code = next((digits[:n] for n in (3, 2, 1) if digits[:n] in CALLING_CODES and 7 <= len(digits) - n <= 11), digits[:-10])
    national = digits[len(code):]
    return {"phone": f"+{code}{national}" if code else national, "country_code": code or None,
            "key": digits[-10:], "valid": True, "given": given}


_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def parse_email(value) -> str | None:
    text = clean_cell(value).lower()
    return text if text and _EMAIL.match(text) else None


_ID_PREFIX = re.compile(r"^[A-Za-z]{1,2}:")


def parse_id(value) -> str | None:
    text = _ID_PREFIX.sub("", clean_cell(value), count=1).strip()
    return text or None


def parse_gender(value) -> str | None:
    key = re.sub(r"[^a-z]", "", clean_cell(value).lower())
    if key in ("male", "m", "man", "men", "boy", "gent", "mr"):
        return "Male"
    if key in ("female", "f", "woman", "women", "girl", "lady", "ms", "mrs", "miss"):
        return "Female"
    if key in ("other", "others", "nonbinary", "transgender", "nonbinarygenderqueer", "prefernottosay", "custom"):
        return "Other"
    return None


def parse_bool(value) -> bool | None:
    key = clean_cell(value).lower()
    if key in ("true", "yes", "y", "1"):
        return True
    if key in ("false", "no", "n", "0"):
        return False
    return None


def parse_platform(value) -> str | None:
    key = clean_cell(value).lower()
    if not key:
        return None
    return {"instagram": "ig", "facebook": "fb", "messenger": "msg", "audience network": "an"}.get(key, key)[:30]


def parse_experience(value) -> tuple[float | None, str | None]:
    """(years, original text). `10+ years` -> 10, `1-3 years` -> 1 (the minimum), `6 months` -> 0.5, nonsense -> None."""
    text = clean_cell(value)
    if not text:
        return None, None
    lowered = text.lower()
    years: float | None = None
    if re.search(r"\b(fresher|no experience|nil|none|zero)\b", lowered) or lowered in ("no", "0", "n/a"):
        years = 0.0
    elif lowered.startswith("less than"):
        years = 0.0
    else:
        found = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:[-–—]|to)\s*(\d+(?:[.,]\d+)?)", lowered)
        single = re.search(r"(\d+(?:[.,]\d+)?)", lowered)
        pick = found.group(1) if found else (single.group(1) if single else None)
        if pick is not None:
            number = float(pick.replace(",", "."))
            tail = lowered[(found or single).end():]
            if re.match(r"\s*(?:months?|mos?\b|m\b)", tail) or (found and re.match(r"\s*(?:months?|mos?\b)", lowered[found.end():])):
                number = round(number / 12, 1)
            years = number
    if years is not None and (years < 0 or years > 60):
        years = None
    return (round(years, 1) if years is not None else None), text[:200]


_EXCEL_EPOCH = date(1899, 12, 30)


def _excel_serial(value: str) -> date | None:
    if re.fullmatch(r"\d{4,6}(?:\.\d+)?", value):
        number = float(value)
        if 1 <= number <= 80000:
            return _EXCEL_EPOCH + timedelta(days=int(number))
    return None


def parse_dob(value, today: date | None = None) -> date | None:
    text = clean_cell(value)
    if not text:
        return None
    today = today or date.today()
    found: date | None = None
    serial = _excel_serial(text)
    if serial:
        found = serial
    else:
        iso = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T ].*)?$", text)
        parts = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2}|\d{4})(?:[T ].*)?$", text)
        try:
            if iso:
                found = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            elif parts:
                first, second, year = int(parts.group(1)), int(parts.group(2)), int(parts.group(3))
                if len(parts.group(3)) == 2:
                    year += 1900 if year > today.year % 100 else 2000
                if first > 12 and second <= 12:
                    day, month = first, second          # unambiguous DD/MM
                elif second > 12 and first <= 12:
                    month, day = first, second          # unambiguous MM/DD
                elif "-" in text or "." in text:
                    day, month = first, second          # dashes and dots are written day first
                else:
                    month, day = first, second          # Meta exports MM/DD/YYYY
                found = date(year, month, day)
            else:
                for pattern in ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%B-%Y"):
                    try:
                        found = datetime.strptime(text, pattern).date()
                        break
                    except ValueError:
                        continue
        except ValueError:
            found = None
    if found and (found.year < 1920 or found > today):
        return None
    return found


def parse_created_time(value, tz: str = "Asia/Kolkata") -> datetime | None:
    text = clean_cell(value)
    if not text:
        return None
    serial = _excel_serial(text) if re.fullmatch(r"\d{5}(?:\.\d+)?", text) else None
    if serial:
        base = datetime.combine(serial, datetime.min.time())
        fraction = float(text) % 1
        return (base + timedelta(seconds=round(fraction * 86400))).replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
    candidate = text.replace("Z", "+00:00").replace("z", "+00:00")
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for pattern in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
                        "%Y/%m/%d %H:%M:%S", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    if parsed.year < 2000 or parsed.year > 2100:
        return None
    return parsed


def guess_country(*names: str | None) -> str | None:
    """First known destination word in the campaign / ad set / ad / form name (`Dubai-Chef-03-11` -> `Dubai`)."""
    for name in names:
        words = re.findall(r"[A-Za-z]+", name or "")
        for index, word in enumerate(words):
            joined = (word + (words[index + 1] if index + 1 < len(words) else "")).lower()
            for key in (joined, word.lower()):
                if key in COUNTRIES:
                    return COUNTRIES[key]
    return None


def smart_name(value) -> str | None:
    text = " ".join(clean_cell(value).split())
    if not text:
        return None
    if text == text.lower() or text == text.upper():
        text = " ".join(word[:1].upper() + word[1:].lower() for word in text.split(" "))
    return text


def clip(key: str, value):
    if isinstance(value, str):
        limit = LIMITS.get(key)
        if limit and len(value) > limit:
            return value[:limit]
    return value


# ------------------------------------------------------------------ one row

def normalise_row(headers: list[str], cells: list[str], columns: ColumnMap, *, campaign_label: str | None = None,
                  tz: str = "Asia/Kolkata", today: date | None = None) -> dict:
    """The lead's column values for one file row plus `_invalid` ({code, message}) when it cannot be used.

    `extra` holds the non-empty cells of unmapped columns; `raw` every non-empty cell, both header -> text.
    """
    by_field = columns.by_field

    def cell(field_name: str) -> str:
        index = by_field.get(field_name)
        return cells[index] if index is not None and index < len(cells) else ""

    raw = {headers[i]: cells[i] for i in range(min(len(headers), len(cells))) if cells[i]}
    mapped_indexes = set(by_field.values())
    extra = {headers[i]: cells[i] for i in range(min(len(headers), len(cells))) if i not in mapped_indexes and cells[i]}

    phone = parse_phone(cell("phone"))
    whatsapp = parse_phone(cell("whatsapp"))
    email = parse_email(cell("email"))
    name = smart_name(cell("name")) or smart_name(" ".join(p for p in (cell("firstName"), cell("lastName")) if p))
    exp_restaurant = parse_experience(cell("experienceRestaurant"))
    exp_total = parse_experience(cell("experienceTotal"))
    campaign_name = clean_cell(cell("campaignName")) or campaign_label or None
    adset_name, ad_name, form_name = (clean_cell(cell(k)) or None for k in ("adsetName", "adName", "formName"))
    position = prettify(cell("positionApplied"))
    state = prettify_state(cell("state"))
    location = prettify_state(cell("currentLocation")) or None
    values = {
        "external_id": parse_id(cell("externalId")),
        "phone_key": phone["key"],
        "email_key": email,
        "created_time": parse_created_time(cell("createdTime"), tz),
        "name": name,
        "phone": phone["phone"],
        "phone_country_code": phone["country_code"],
        "email": email,
        "whatsapp": whatsapp["phone"],
        "whatsapp_same": (whatsapp["key"] == phone["key"]) if whatsapp["valid"] and phone["valid"] else None,
        "city": prettify(cell("city")),
        "state": state,
        "current_location": location,
        "position_applied": position,
        "country_interest": prettify(cell("countryInterest")) or guess_country(campaign_name, adset_name, ad_name, form_name),
        "experience_restaurant_years": exp_restaurant[0],
        "experience_restaurant_text": exp_restaurant[1],
        "experience_total_years": exp_total[0],
        "experience_total_text": exp_total[1],
        "gender": parse_gender(cell("gender")),
        "marital_status": prettify(cell("maritalStatus")),
        "dob": parse_dob(cell("dob"), today),
        "education_level": clean_cell(cell("educationLevel")) or None,
        "military_status": prettify(cell("militaryStatus")),
        "platform": parse_platform(cell("platform")),
        "is_organic": parse_bool(cell("isOrganic")),
        "lead_status_source": clean_cell(cell("leadStatusSource")) or None,
        "campaign_id": parse_id(cell("campaignId")),
        "campaign_name": campaign_name,
        "adset_id": parse_id(cell("adsetId")),
        "adset_name": adset_name,
        "ad_id": parse_id(cell("adId")),
        "ad_name": ad_name,
        "form_id": parse_id(cell("formId")),
        "form_name": form_name,
        "extra": extra,
        "raw": raw,
    }
    for key in list(values):
        values[key] = clip(key, values[key])
    invalid = None
    if not phone["valid"] and not email:
        if phone["given"]:
            invalid = {"code": "bad_phone", "message": "The phone number is not valid (7 to 15 digits) and there is no e-mail"}
        else:
            invalid = {"code": "no_contact", "message": "No phone number and no e-mail"}
    values["_invalid"] = invalid
    return values
