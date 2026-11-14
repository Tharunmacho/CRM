"""Pure completeness wording checks that do not require the disposable database."""

from copy import deepcopy

from app.services.application_form import completeness_payload


def complete_form_with_missing_passport_scan() -> dict:
    return {
        "personal": {
            "candidateName": "Example Candidate",
            "fatherName": "Example Parent",
            "dateOfBirth": "1990-01-01",
            "maritalStatus": "married",
            "gender": "male",
            "nationality": "Indian",
            "languagesKnown": ["English"],
            "mobileNumber": "9000000000",
            "currentAddress": "Example address",
        },
        "passport": {
            "passportNumber": "K1234567",
            "placeOfIssue": "Kochi",
            "issueDate": "2020-01-01",
            "expiryDate": "2030-01-01",
        },
        "education": [{"level": "Diploma"}],
        "experience": {"totalExperience": 1, "history": [{"company": "Example Co."}]},
        "fieldSources": {},
        "documents": {
            "passport": {"slot": "passport", "label": "Passport", "required": True, "optional": False, "uploaded": False},
        },
        "screening": {
            "nativePlace": None,
            "familyMemberNumber": None,
            "jobPreference": None,
            "countryPreference": None,
            "jobDurationPreference": None,
            "bestTimeToContact": None,
            "interestLevel": "not_interested",
            "serviceChargesStatus": "not_discussed",
        },
    }


def test_passport_scan_wording_distinguishes_saved_details_without_counting_it_uploaded():
    with_details = completeness_payload(complete_form_with_missing_passport_scan())
    document_missing = {item["key"]: item for item in with_details["missing"]}["documents.passport"]

    assert with_details["total"] == 17
    assert with_details["filled"] == 16
    assert document_missing["message"] == "Passport scan not uploaded (passport details are saved)"

    without_details_form = deepcopy(complete_form_with_missing_passport_scan())
    without_details_form["passport"] = {"passportNumber": None, "placeOfIssue": None, "issueDate": None, "expiryDate": None}
    without_details = completeness_payload(without_details_form)
    document_missing = {item["key"]: item for item in without_details["missing"]}["documents.passport"]

    assert document_missing["message"] == "Passport not uploaded"
