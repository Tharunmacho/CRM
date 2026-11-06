"""The Data Bank list carries every scalar candidate field the expandable row shows, and never the deferred JSONB columns.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL), with the fixtures of test_application_form.py.
"""
import json
from datetime import date

from sqlalchemy import event, select

from app.db.models import Candidate
from app.services.candidate_search import Search, search_candidates
from test_application_form import desk  # noqa: F401  (fixture)
from test_database_naming import schema_database  # noqa: F401

LIST_FIELDS = {
    "fatherName", "maritalStatus", "currentAddress", "experienceIndia", "nativePlace", "familyMemberNumber", "jobDurationPreference",
    "bestTimeToContact", "countryPreference", "serviceChargesStatus", "interestLevel", "nextFollowUpDate", "passportNo", "passportIssue",
    "passportExpiry", "passportPlace", "gulfExperience", "totalExperience", "expectedSalary", "languages", "nationality", "currentCity",
    "gender", "dob", "whatsapp", "email", "skills", "designations", "sourceDetail", "ownerId", "addedAt",
}
FULL_AADHAAR = "999988887777"


def test_list_rows_carry_every_scalar_field_and_no_deferred_column(desk):
    with desk.sessions() as db:
        c = db.get(Candidate, "candidate")
        c.father_name, c.marital_status, c.current_address, c.experience_india = "Test Father", "Married", "1 Test Street", 3
        c.native_place, c.family_member_number, c.job_duration_preference = "Madurai", "+910000000003", "long"
        c.best_time_to_contact, c.country_preference, c.interest_level = "evening", "Qatar", "interested"
        c.next_follow_up_date, c.passport_no, c.gender, c.dob = date(2026, 12, 1), "P0000001", "male", date(1990, 1, 5)
        c.education = [{"level": "SECRET-EDU"}]
        c.work_history = [{"company": "SECRET-WORK"}]
        c.ocr_data = {"aadhaar": {"aadhaar": {"aadhaar_number": FULL_AADHAAR}}}
        db.commit()
    statements: list[str] = []
    with desk.sessions() as db:
        event.listen(db.get_bind(), "before_cursor_execute", lambda conn, cursor, statement, *args: statements.append(statement))
        result = search_candidates(db, Search(), 0, 20)
    row = next(item for item in result["items"] if item["id"] == "candidate")
    assert LIST_FIELDS <= set(row), LIST_FIELDS - set(row)
    assert row["fatherName"] == "Test Father" and row["experienceIndia"] == 3 and row["nextFollowUpDate"] == "2026-12-01"
    # the deferred JSONB columns are not in the row, were not selected, and nothing of them or of the OCR output leaked
    assert "education" not in row and "workHistory" not in row and "ocrData" not in row and "fieldSources" not in row
    text = json.dumps(result)
    assert "SECRET-EDU" not in text and "SECRET-WORK" not in text and FULL_AADHAAR not in text
    listing = [s for s in statements if "FROM crm_candidates" in s and "count(*) OVER" in s]
    assert len(listing) == 1
    assert "crm_candidates.education" not in listing[0] and "crm_candidates.work_history" not in listing[0] and "crm_candidates.ocr_data" not in listing[0]
    assert not [s for s in statements if "crm_candidates.education" in s or "crm_candidates.ocr_data" in s]
