"""Synthetic lead-sheet files shaped like the real exports (never real people: every value is generated)."""
import io
import zipfile
from xml.sax.saxutils import escape

META_HEADERS = [
    "id", "created_time", "ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name", "form_id", "form_name",
    "is_organic", "platform", "current_location", "which_position_are_you_applying_for?",
    "how_many_years_of_experience_do_you_have_in_the_restaurant_industry?", "what_is_your_total_years_of_work_experience?",
    "email", "full_name", "phone_number", "state", "marital_status", "gender", "city", "date_of_birth", "education_level",
    "military_status", "lead_status",
]
POSITIONS = ["south_indian_chef_(cdp)", "kitchen_helper", "dosa_&_parotta_master"]
STATES = ["Kerala", "Tamilnadu", "kerala", "tamil nadu", "Tamil Nadu, India"]
EXPERIENCE = ["20", "10+ years", "5 years", "1-3 years", "22", "6 months"]
MARITAL = ["Married", "married", "Yes", "single"]
GENDERS = ["male", "Male", "female", "Men"]
DOBS = ["03/27/1971", "1990-09-25", "25/12/1985", "07/04/1992"]
EDUCATION = ["High school / GED", "Bachelor's degree", "Some high school"]


def phone_digits(n: int) -> str:
    return str(9000000000 + n)  # ten synthetic digits


def lead_row(n: int, **over) -> list[str]:
    values = {
        "id": f"l:{1795000000000000 + n}", "created_time": f"2026-11-04T19:{n % 60:02d}:51+05:30", "ad_id": "ag:120249141517430605",
        "ad_name": "Dubai-Chef-03-11", "adset_id": "as:120249141517440605", "adset_name": "New Leads ad set",
        "campaign_id": "c:120249141517420605", "campaign_name": "Dubai-Chef-03-11", "form_id": "f:1479234614067817",
        "form_name": "Dubai-Chef-03-11-co", "is_organic": "false", "platform": ["ig", "fb"][n % 2],
        "current_location": ["tamil_nadu", "kerala"][n % 2], "which_position_are_you_applying_for?": POSITIONS[n % 3],
        "how_many_years_of_experience_do_you_have_in_the_restaurant_industry?": EXPERIENCE[n % 6],
        "what_is_your_total_years_of_work_experience?": EXPERIENCE[(n + 1) % 6], "email": f"lead{n}@example.test",
        "full_name": f"Test Lead {n:05d}", "phone_number": f"p:+91{phone_digits(n)}", "state": STATES[n % 5],
        "marital_status": MARITAL[n % 4], "gender": GENDERS[n % 4], "city": f"Testville {n % 7}", "date_of_birth": DOBS[n % 4],
        "education_level": EDUCATION[n % 3], "military_status": ["No", "nil", "Yes"][n % 3], "lead_status": "complete",
    }
    values.update(over)
    return [values[h] for h in META_HEADERS]


def meta_rows(start: int, stop: int) -> list[list[str]]:
    return [lead_row(n) for n in range(start, stop)]


def meta_export(rows, headers=META_HEADERS) -> bytes:
    """UTF-16 LE with BOM, TAB separated, LF line ends: exactly what Meta Lead Ads downloads as .csv."""
    text = "\n".join("\t".join(r) for r in [list(headers), *rows]) + "\n"
    return b"\xff\xfe" + text.encode("utf-16-le")


def csv_bytes(rows, headers=META_HEADERS, delimiter=",", encoding="utf-8", bom=False, newline="\n") -> bytes:
    def quote(cell):
        if any(c in cell for c in (delimiter, '"', "\n")):
            return '"' + cell.replace('"', '""') + '"'
        return cell
    text = newline.join(delimiter.join(quote(c) for c in r) for r in [list(headers), *rows]) + newline
    data = text.encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def _col(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx_bytes(rows, *, shared=True, extra_parts: dict | None = None, content_types_extra: str = "") -> bytes:
    """A small but real .xlsx (zip + XML). Cells that are int/float are written as numbers, strings as shared or inline strings."""
    strings: list[str] = []

    def sst(value: str) -> int:
        if value not in strings:
            strings.append(value)
        return strings.index(value)

    body = []
    for r, row in enumerate(rows, 1):
        cells = []
        for c, value in enumerate(row):
            ref = f"{_col(c)}{r}"
            if value is None or value == "":
                continue
            if isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            elif shared:
                cells.append(f'<c r="{ref}" t="s"><v>{sst(str(value))}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>')
        body.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>' + content_types_extra + '</Types>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Leads" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>{"".join(body)}</sheetData></worksheet>')
        if shared:
            z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
        for name, data in (extra_parts or {}).items():
            z.writestr(name, data)
    return out.getvalue()
