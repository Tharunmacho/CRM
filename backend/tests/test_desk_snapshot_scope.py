"""Real-PostgreSQL assertions for Staff detail masking in the shared desk snapshot."""
import pytest

from app.db.models import Application, ApplicationStage, Candidate, Client, Company, Designation, Order, Remark, Source, Task, TaskKind
from app.services.desk_snapshot import read_snapshot
from test_database_naming import schema_database
from test_workflow_scheduler import workspace


def seed_details(sessions):
    with sessions() as db:
        db.add(Client(id="contact", name="Synthetic contact"))
        db.add(Candidate(id="other-candidate", ref="CND-OTHER", name="Other candidate", phone="9000000002", source=Source.walk_in, owner_id="other"))
        db.flush()
        db.add(Company(id="employer", name="Synthetic employer", client_id="contact"))
        db.add(Designation(id="designation", title="Welder"))
        db.flush()
        db.add(Order(id="order", ref="ORD-PRIVATE", company_id="employer", designation_id="designation"))
        db.flush()
        for owner, candidate in [("owner", "candidate"), ("other", "other-candidate")]:
            db.add(Application(id="app-" + owner, candidate_id=candidate, order_id="order", owner_id=owner,
                               draft={"privateNote": owner + " draft"}))
        db.flush()
        for owner, candidate in [("owner", "candidate"), ("other", "other-candidate")]:
            db.add(ApplicationStage(id="stage-" + owner, application_id="app-" + owner, stage=1, attempt=1,
                                    data={"privateNote": owner + " stage"}, owner_id=owner, completed_by=owner))
            db.add(Task(id="task-" + owner, kind=TaskKind.stage, title=owner + " work", candidate_id=candidate,
                        application_id="app-" + owner, stage=1, owner_id=owner))
        db.flush()
        for owner in ("owner", "other"):
            db.add(Remark(id="remark-" + owner, task_id="task-" + owner, author_id=owner, body=owner + " remark"))
        db.commit()


@pytest.mark.parametrize("actor", ["owner", "other"])
def test_staff_share_candidate_and_placement_summaries_but_only_receive_owned_details(workspace, actor):
    seed_details(workspace)
    with workspace() as db:
        snapshot = read_snapshot(db, actor)
    assert {row["id"] for row in snapshot["candidates"]} == {"candidate", "other-candidate"}
    assert {row["id"] for row in snapshot["applications"]} == {"app-owner", "app-other"}
    drafts = {row["id"]: row["draft"] for row in snapshot["applications"]}
    assert drafts["app-" + actor] == {"privateNote": actor + " draft"}
    other = "other" if actor == "owner" else "owner"
    assert drafts["app-" + other] == {}
    assert [row["id"] for row in snapshot["stages"]] == ["stage-" + actor]
    assert [row["id"] for row in snapshot["tasks"]] == ["task-" + actor]
    assert [row["id"] for row in snapshot["remarks"]] == ["remark-" + actor]


@pytest.mark.parametrize("actor", ["admin", "manager"])
def test_supervisors_receive_both_recruiters_details(workspace, actor):
    seed_details(workspace)
    with workspace() as db:
        snapshot = read_snapshot(db, actor)
    assert {row["id"] for row in snapshot["stages"]} == {"stage-owner", "stage-other"}
    assert {row["id"] for row in snapshot["tasks"]} == {"task-owner", "task-other"}
    assert {row["id"] for row in snapshot["remarks"]} == {"remark-owner", "remark-other"}
    assert all(row["draft"].get("privateNote") for row in snapshot["applications"])
