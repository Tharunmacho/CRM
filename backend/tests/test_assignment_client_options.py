"""Client selection keeps empty clients and every company's jobs and status visible."""
from sqlalchemy import update

from app.db.models import Application, ApplicationStatus, Client, Company, Order, OrderStatus, Role
from test_application_form import as_user, desk, form  # noqa: F401
from test_application_workflow import mapped
from test_database_naming import schema_database  # noqa: F401


def test_active_clients_without_companies_or_jobs_are_visible_with_scope_preserved(desk):
    with desk.sessions() as db:
        db.add_all([Client(id="empty", name="Active without company"),
                    Client(id="no-jobs", name="Active without open jobs"),
                    Client(id="inactive", name="Inactive client", status="inactive")])
        db.flush()
        db.add_all([Company(id="no-jobs-co", client_id="no-jobs", name="No open jobs company"),
                    Company(id="inactive-co", client_id="inactive", name="Inactive company")])
        db.flush()
        db.add(Order(id="inactive-open", ref="INACTIVE", company_id="inactive-co", designation_id="designation", openings=1))
        db.commit()

    for actor, role in (("staff", Role.staff), ("admin", Role.admin), ("manager", Role.manager)):
        as_user(desk, actor, role)
        clients = {row["id"]: row for row in form(desk)["assignment"]["options"]["clients"]}
        assert set(clients) == {"client", "empty", "no-jobs"}
        assert clients["empty"] == {"id": "empty", "name": "Active without company", "companyId": None, "companyName": None, "jobs": []}
        assert clients["no-jobs"]["jobs"] == []
        assert clients["no-jobs"]["companyId"] == "no-jobs-co"
        assert len(clients["client"]["jobs"]) == 2

    as_user(desk, "other", Role.staff)
    block = form(desk)["assignment"]
    assert block["can"]["job"] is False and block["options"]["clients"] == []


def test_one_client_option_combines_every_company_and_retains_unavailable_job_statuses(desk):
    with desk.sessions() as db:
        db.add(Company(id="second-company", client_id="client", name="Second employer"))
        db.flush()
        db.add_all([Order(id="second-open", ref="SECOND", company_id="second-company", designation_id="designation", openings=2),
                    Order(id="second-closed", ref="CLOSED", company_id="second-company", designation_id="designation", openings=2, status=OrderStatus.closed),
                    Order(id="second-filled", ref="FILLED", company_id="second-company", designation_id="designation", openings=3, status=OrderStatus.filled),
                    Order(id="second-held", ref="HELD", company_id="second-company", designation_id="designation", openings=2, status=OrderStatus.on_hold)])
        db.commit()

    (client,) = form(desk)["assignment"]["options"]["clients"]
    assert client["id"] == "client" and client["companyId"] is None and client["companyName"] is None
    assert {job["orderId"]: job["status"] for job in client["jobs"]} == {
        "order": "open", "other-order": "open", "second-open": "open", "second-closed": "closed",
        "second-filled": "filled", "second-held": "on_hold"}


def test_deployed_placement_of_inactive_client_is_retained_with_empty_active_clients(desk):
    application_id = mapped(desk)
    with desk.sessions() as db:
        db.execute(update(Application).where(Application.id == application_id).values(status=ApplicationStatus.deployed, current_stage=11))
        db.execute(update(Order).values(status=OrderStatus.filled))
        db.execute(update(Client).where(Client.id == "client").values(status="inactive"))
        db.add(Client(id="empty", name="Empty active client"))
        db.commit()

    block = form(desk)["assignment"]
    clients = {row["id"]: row for row in block["options"]["clients"]}
    assert set(clients) == {"client", "empty"}
    assert [(job["orderId"], job["status"]) for job in clients["client"]["jobs"]] == [("order", "filled")]
    assert block["job"]["applicationId"] == application_id and block["client"]["companyId"] == "company"
