"""A bulk-imported candidate is named by their documents; photo pages and degree certificates never raise a reading warning."""
from types import SimpleNamespace

from app.integrations.ocr.classification import classify_page, education_certificate
from app.integrations.ocr.inspection import _real_words
from app.services import form_extraction as fx

DEGREE = """Centre Code: 73 Reg.No. CB19S 192715
BHARATHIDASAN UNIVERSITY
(Accredited with A+ Grade by NAAC in the Third Cycle)
FACULTY OF SCIENCE
The Syndicate of the BHARATHIDASAN UNIVERSITY hereby makes known that GUNAVELAN R has been
admitted to the Degree of BACHELOR OF SCIENCE, having been certified by duly appointed Examiners
"""
MARKSHEET = "Tamil Nadu Board of Higher Secondary Examination\nStatement of Marks\nName of the candidate: A KUMAR\nTotal 512\n"
TEXTURE = "~~\n+\n~~\n�he\na's\n��\nv=\n�7\no\nan)\naf\nre\nPee rr\n"


def candidate(**kw):
    values = dict(name="Gunavelan-alnoor", field_sources={"_provisionalName": True}, dob=None, gender=None, father_name=None,
                  nationality=None, passport_no=None, passport_place=None, current_address=None, phone=None, email=None,
                  marital_status=None, native_place=None, current_city=None, whatsapp=None, passport_issue=None, passport_expiry=None,
                  education=[], work_history=[], skills=[], languages=[], designations=[], total_experience=None, experience_india=0,
                  gulf_experience=None, family_member_number=None, job_duration_preference=None, country_preference=None,
                  best_time_to_contact=None, service_charges_status="not_discussed", interest_level=None, next_follow_up_date=None)
    values.update(kw)
    return SimpleNamespace(**values)


def test_degree_certificate_and_marksheet_are_resume_content_not_unknown():
    assert education_certificate(DEGREE) and education_certificate(MARKSHEET)
    for text in (DEGREE, MARKSHEET):
        page = classify_page(text)
        assert page.document_type == "resume" and not page.inherited


def test_ordinary_text_is_not_an_education_certificate():
    assert not education_certificate("Dear Mr. Kumar, we are pleased to offer you employment as a steel fixer.")
    assert not education_certificate("Experience certificate: this is to certify that Mr. Kumar worked with us as a welder.")


def test_texture_of_a_photo_has_no_real_words_and_a_document_has_many():
    assert _real_words(TEXTURE) < 8
    assert _real_words(DEGREE) >= 20


def test_the_folder_name_is_replaced_by_the_name_on_a_document():
    c = candidate()
    result = fx.apply_values(c, "resume", {"name": "Gunavelan R"})
    assert c.name == "Gunavelan R" and "personal.candidateName" in result.filled
    assert "_provisionalName" not in c.field_sources and c.field_sources["personal.candidateName"] == "extracted"


def test_a_different_looking_document_name_still_replaces_a_provisional_name():
    c = candidate(name="Sivakumar folder")
    fx.apply_values(c, "resume", {"name": "Kandasamy Sivakumar"})
    assert c.name == "Kandasamy Sivakumar"


def test_a_name_a_person_typed_is_never_replaced():
    c = candidate(name="Typed Name", field_sources={"_provisionalName": True})
    fx.mark_manual(c, ["name"])
    fx.apply_values(c, "resume", {"name": "Someone Else"})
    assert c.name == "Typed Name"


def test_a_normal_candidate_is_not_provisional():
    c = candidate(name="Alex Example", field_sources={})
    assert not fx.provisional_name(c)


def test_one_candidates_folder_is_never_split_by_document_words_or_a_typo():
    from app.services.bulk_candidate_import import split_people
    files = ["EVisa-123456789.pdf", "Itinerary AB1CD2 (1).pdf", "RASHIDKHAN-SIGNED OFFER.jpeg", "RASHIDKHAND-WhatsApp Image.jpeg",
             "Rashid Khan Nawazkhan-picker.pdf", "Rashid khan - PP.pdf", "Rashiid khan-visa,medical,pcc s.pdf", "rashidkhan (3 files merged) 10.pdf"]
    assert split_people(files) is None


def test_two_real_people_in_one_folder_are_still_split():
    from app.services.bulk_candidate_import import split_people
    split = split_people(["Sivakumar-offer.pdf", "Sivakumar-pp.pdf", "Poomalai-offer.pdf", "EVisa-1.pdf", "31MAY-3-PAX-CHENNAI-KUWAIT.pdf"])
    assert split and sorted(p["name"] for p in split["people"]) == ["Poomalai", "Sivakumar"]
    assert "31MAY-3-PAX-CHENNAI-KUWAIT.pdf" in split["shared"] and "EVisa-1.pdf" in split["left_out"]


def _folder(*names):
    from app.api.routers.candidate_import import FileIn, FolderIn
    return FolderIn(folder="mixed", files=[FileIn(path=n) for n in names])


MIXED = ["Sivakumar-offer.pdf", "Sivakumar-pp.pdf", "Poomalai-offer.pdf", "Poomalai-pp.pdf"]


def test_a_person_can_make_a_folder_one_candidate():
    from app.api.routers.candidate_import import _units
    assert len(_units([_folder(*MIXED)])) == 2
    one = _units([_folder(*MIXED)], "one")
    assert len(one) == 1 and len(one[0].files) == 4
    assert len(_units([_folder(*MIXED)], "auto", {"mixed": "one"})) == 1       # per-folder choice
    assert len(_units([_folder(*MIXED)], "one", {"mixed": "auto"})) == 2       # beats the default


def test_a_person_can_move_a_file_to_another_candidate_or_leave_it_out():
    from app.api.routers.candidate_import import _units, assign_key
    units = _units([_folder(*MIXED)])
    siva, poo = units[0].folder, units[1].folder
    moved = _units([_folder(*MIXED)], assign={assign_key("mixed", "Poomalai-pp.pdf"): siva, assign_key("mixed", "Sivakumar-offer.pdf"): ""})
    by = {u.folder: sorted(f.path for f in u.files) for u in moved}
    assert by[siva] == ["Poomalai-pp.pdf", "Sivakumar-pp.pdf"] and by[poo] == ["Poomalai-offer.pdf"]


def test_other_certificates_that_name_a_degree_are_not_taken_for_education():
    for text in (
        "This is to certify that Mr Kumar, graduate of Anna University, Bachelor of Engineering, worked with our company as a site engineer for five years.",
        "Medical certificate issued by Dr Rao, Madras Medical College University, MBBS Bachelor of Medicine. The candidate is physically fit for overseas work.",
        "Police verification report. The applicant holds a Bachelor of Engineering from Anna University and has no criminal record in this district.",
    ):
        assert not education_certificate(text), text
    assert education_certificate(DEGREE) and education_certificate(MARKSHEET)


def test_three_people_in_one_folder_never_share_a_file():
    from app.services.bulk_candidate_import import split_people
    files = ["Mohamed Ali-passport.pdf", "Mohamed Ali-offer.pdf", "Mohamed Asif-passport.pdf", "Mohamed Asif-offer.pdf",
             "Mohamed-visa.pdf", "Sivakumar-passport.pdf", "Sivakumar-offer.pdf", "scan1.pdf", "31MAY-3-PAX-CHENNAI-KUWAIT.pdf"]
    split = split_people(files)
    by = {p["name"]: p["paths"] for p in split["people"]}
    assert sorted(by) == ["Mohamed Ali", "Mohamed Asif", "Sivakumar"]
    placed = [path for paths in by.values() for path in paths if path not in split["shared"]]
    assert len(placed) == len(set(placed))                                            # no file on two people
    assert set(split["left_out"]) == {"Mohamed-visa.pdf", "scan1.pdf"}               # never guessed
    assert all("31MAY-3-PAX-CHENNAI-KUWAIT.pdf" in paths for paths in by.values())   # a group ticket covers everyone


def test_left_out_files_can_be_placed_by_hand_and_same_name_people_stay_apart():
    from app.api.routers.candidate_import import _units, assign_key
    names = ["Mohamed Ali-passport.pdf", "Mohamed Asif-passport.pdf", "Mohamed-visa.pdf", "scan1.pdf"]
    units = _units([_folder(*names)])
    assert [u.name for u in units] == ["Mohamed Ali", "Mohamed Asif"]
    assert all(u.left_out == ["Mohamed-visa.pdf", "scan1.pdf"] for u in units)        # shown, never guessed
    placed = _units([_folder(*names)], assign={assign_key("mixed", "Mohamed-visa.pdf"): units[1].folder})
    assert [f.path for f in placed[1].files] == ["Mohamed Asif-passport.pdf", "Mohamed-visa.pdf"]
    assert all(u.left_out == ["scan1.pdf"] for u in placed)
    twins = _units([_folder("Rahul Kumar-passport.pdf", "Rahul Kumar-offer.pdf", "Sunil-offer.pdf", "Sunil-passport.pdf")])
    assert len({u.folder for u in twins}) == len(twins)


def test_imported_documents_are_named_by_candidate_and_document():
    from app.services.bulk_candidate_import import import_file_name
    taken: set[str] = set()
    assert import_file_name("Gunavelan R", ["passport"], "Gunavelan-PP.pdf", taken) == "Gunavelan_R-Passport.pdf"
    assert import_file_name("Gunavelan R", ["passport"], "other.PDF", taken) == "Gunavelan_R-Passport-2.pdf"
    assert import_file_name("Gunavelan R", ["signed_offer", "passport", "aadhaar"], "x.pdf", taken) == "Gunavelan_R-Signed_offer_Passport_Aadhaar_card.pdf"
    assert import_file_name("Gunavelan R", [], "scan.jpeg", taken) == "Gunavelan_R-Document.jpeg"
