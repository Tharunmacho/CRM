"""Demand-loaded candidate profiles retain the shared directory's wire contract."""
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routers import candidates, data
from app.core.deps import current_user
from app.db.models import Attachment, Candidate, User
from app.db.session import get_db
from test_attendance_read_performance import measured
from test_database_naming import schema_database
from test_workflow_scheduler import workspace


@pytest.mark.parametrize("actor_id", ["admin", "manager", "other"])
def test_full_detail_matches_shared_bootstrap_without_private_internal_fields(workspace, actor_id):
    with workspace() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.resume_text = "Synthetic searchable resume with TIG welding."
        candidate.ocr_data = {"raw": "excluded from public profile"}
        candidate.call_called = True
        candidate.call_interested = "yes"
        candidate.call_remarks = "Call recorded"
        candidate.call_recorded_at = datetime(2026, 10, 22, 4, tzinfo=timezone.utc)
        for version in (2, 1):
            db.add(Attachment(id=f"resume-{version}", entity_type="candidate", entity_id=candidate.id,
                slot="resume", version=version, file_name=f"resume-{version}.pdf", size_kb=12,
                uploaded_by="owner", object_key="private-test-file-" + str(version)))
        db.commit()
        actor = db.get(User, actor_id)
        app = FastAPI(); app.include_router(candidates.router); app.include_router(data.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[current_user] = lambda: actor
        client = TestClient(app)
        baseline = client.get("/api/bootstrap").json()["candidates"][0]
        with measured(db) as measurement:
            response = client.get("/api/candidates/candidate")
        assert response.status_code == 200
        assert response.json() == baseline
        assert measurement["queries"] == 1
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["resumeText"] == candidate.resume_text
        assert [row["version"] for row in response.json()["documents"][0]["versions"]] == [1, 2]
        body = json.dumps(response.json())
        for forbidden in ("ocr_data", "ocrData", "object_key", "objectKey", "resume_tsv", "password", "stages", "draft"):
            assert forbidden not in body
        assert client.get("/api/candidates/missing").status_code == 404


@pytest.mark.parametrize("actor_id", ["admin", "manager", "owner"])
def test_candidate_ocr_returns_the_whole_stored_payload_uncached(workspace, actor_id):
    stored = {
        "resume": {"request_id": "r", "pages": [{"page_number": 1, "text": "t", "lines": [{"text": "l"}]}]},
        "aadhaar": {"request_id": "a", "aadhaar": {"aadhaar_number": "000000000000", "masked_aadhaar_number": "XXXX XXXX 0000",
                                                    "vid": "0000000000000000", "address": "1 Example Road"}},
        "_sources": {"resume": {"source_sha256": "a", "selected_pages": [2], "local_inspection": {"page_count": 3}},
                     "aadhaar": {"source_sha256": "a", "selected_pages": [3]}},
        "additional_documents": [{"passport": {"request_id": "p2", "raw_mrz": "P<IND", "fields": [{"label": "x"}]},
                                  "_source": {"source_sha256": "b", "selected_pages": [1]}}],
    }
    with workspace() as db:
        db.get(Candidate, "candidate").ocr_data = stored
        db.commit()
        app = FastAPI(); app.include_router(candidates.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[current_user] = lambda: db.get(User, actor_id)
        client = TestClient(app)
        response = client.get("/api/candidates/candidate/ocr")
        assert response.status_code == 200 and response.json() == stored
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get("/api/candidates/missing/ocr").status_code == 404


def test_candidate_detail_requires_authentication():
    app = FastAPI(); app.include_router(candidates.router)
    assert TestClient(app).get("/api/candidates/missing").status_code == 401


def test_candidate_ocr_masks_aadhaar_identifiers_for_other_staff_only(workspace):
    stored = {
        "resume": {"request_id": "r", "pages": [{"page_number": 1, "text": "Mobile 9000000001 aadhaar 0000 0000 0000"}]},
        "aadhaar": {
            "request_id": "a",
            "aadhaar": {"name": "ALEX", "aadhaar_number": "000000000000", "masked_aadhaar_number": "XXXX XXXX 0000",
                        "vid": "0000000000000000", "enrollment_id": "0000/00000/00000", "address": "1 Example Road"},
            "pages": [{"page_number": 1, "text": "Aadhaar 1234 5678 9012 VID 1234 5678 9012 3456", "html": "<p>1234 5678 9012</p>",
                       "lines": [{"text": "1234 5678 9012", "words": [{"text": "1234"}, {"text": "5678"}, {"text": "9012"}]}]}],
        },
        "additional_documents": [{"aadhaar": {"request_id": "a2", "aadhaar": {"aadhaar_number": "111122223333"}}, "_source": {"x": 1}}],
    }
    with workspace() as db:
        db.get(Candidate, "candidate").ocr_data = stored
        db.commit()
        app = FastAPI(); app.include_router(candidates.router)
        app.dependency_overrides[get_db] = lambda: db
        results = {}
        for actor_id in ("admin", "manager", "owner", "other"):
            app.dependency_overrides[current_user] = lambda actor_id=actor_id: db.get(User, actor_id)
            response = TestClient(app).get("/api/candidates/candidate/ocr")
            assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
            results[actor_id] = response.json()
        assert results["admin"] == results["manager"] == results["owner"] == stored
        masked = results["other"]
        card = masked["aadhaar"]["aadhaar"]
        assert card["aadhaar_number"] == "XXXXXXXX0000"
        assert card["vid"].endswith("0000") and set(card["vid"][:-4]) == {"X"}
        assert card["enrollment_id"] == "XXXX/XXXXX/X0000"
        assert card["masked_aadhaar_number"] == "XXXX XXXX 0000" and card["address"] == "1 Example Road" and card["name"] == "ALEX"
        page = masked["aadhaar"]["pages"][0]
        assert "1234" not in page["text"].replace("3456", "") and "9012" in page["text"] and "3456" in page["text"]
        assert page["lines"][0]["text"] == "XXXX XXXX 9012"
        assert [word["text"] for word in page["lines"][0]["words"]] == ["XXXX", "XXXX", "9012"]
        assert masked["additional_documents"][0]["aadhaar"]["aadhaar"]["aadhaar_number"] == "XXXXXXXX3333"
        # Everything that is not an Aadhaar identifier is untouched, and the stored row is unchanged.
        assert masked["resume"] == stored["resume"]
        assert db.get(Candidate, "candidate").ocr_data == stored
