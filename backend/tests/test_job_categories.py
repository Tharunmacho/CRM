"""The managed job category list (migration 0019), against the guarded disposable PostgreSQL DB.

The seed is a plain overseas-recruitment list, writes are admin-only and audited, a rename also renames the category on
the job titles that used it (one transaction), a category that job titles use cannot be deleted, names are unique
ignoring case, and the job title API still accepts any category text. A database without the table keeps working.
"""
import json

import psycopg
import pytest
from sqlalchemy import func, inspect, select, text

from app.db.models import AuditEntry, Designation
from app.services import agency_settings as service, change_feed
from test_agency_settings import desk, keep_loggers_enabled  # noqa: F401  (fixtures)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

MESSAGE_IN_USE = "This category is used by job titles. Mark it inactive instead."


def names(env):
    return [item["name"] for item in env.client.get("/api/settings").json()["categories"]]


def by_name(env, name):
    return next(item for item in env.client.get("/api/settings").json()["categories"] if item["name"] == name)


def add(env, name, **extra):
    response = env.client.post("/api/settings/categories", json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


def change(env, category_id, **body):
    return env.client.put(f"/api/settings/categories/{category_id}", json=body)


def title(env, key, category):
    with env.sessions() as db:
        db.add(Designation(id=key, title=f"Title {key}", category=category))
        db.commit()


def category_of(env, key):
    with env.sessions() as db:
        return db.get(Designation, key).category


# ------------------------------------------------------------------ migration

def test_the_seed_is_a_plain_unique_list_and_everything_is_active(desk):
    listed = desk.client.get("/api/settings").json()
    categories = listed["categories"]
    assert listed["categoriesEditable"] is True
    assert 14 <= len(categories) <= 20
    assert len({item["name"].lower() for item in categories}) == len(categories)
    assert all(item["active"] and 2 <= len(item["name"]) <= 120 and item["name"] == item["name"].strip() for item in categories)
    assert [item["sortOrder"] for item in categories] == sorted(item["sortOrder"] for item in categories)
    for expected in ("Construction", "Welding and fabrication", "Healthcare and nursing", "Oil and gas", "Domestic and household"):
        assert expected in names(desk)
    assert "Test 1" not in names(desk)  # test data is not part of the seed


def test_migration_is_additive_idempotent_and_reversible(schema_database):
    engine = schema_database
    _migrate(engine, "0018_ack_whatsapp_template")
    before = set(inspect(engine).get_table_names())
    assert "job_categories" not in before
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO crm_job_designations (id, title, category) VALUES ('d1', 'Welder', 'Test 1')"))
    _migrate(engine, "head")
    assert set(inspect(engine).get_table_names()) - before == {"job_categories"}
    with engine.connect() as connection:
        seeded = connection.scalar(text("SELECT count(*) FROM job_categories"))
        assert connection.scalar(text("SELECT category FROM crm_job_designations WHERE id = 'd1'")) == "Test 1"  # untouched
    assert seeded >= 14
    with engine.begin() as connection:
        connection.execute(text("UPDATE job_categories SET is_active = false WHERE name = 'Security'"))
        connection.execute(text("UPDATE alembic_version SET version_num = '0018_ack_whatsapp_template'"))
    _migrate(engine, "head")  # the revision was lost after the table was made: running 0019 again must be harmless
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM job_categories")) == seeded
        assert connection.scalar(text("SELECT is_active FROM job_categories WHERE name = 'Security'")) is False  # an edit survives
    _migrate(engine, "0018_ack_whatsapp_template", downgrade=True)
    assert set(inspect(engine).get_table_names()) == before
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT category FROM crm_job_designations WHERE id = 'd1'")) == "Test 1"
    _migrate(engine, "head")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM job_categories")) == seeded


def test_the_database_enforces_unique_names_ignoring_case_and_the_length_rule(desk):
    from sqlalchemy.exc import IntegrityError
    for statement in (
        "INSERT INTO job_categories (id, name) VALUES ('x1', 'construction')",
        "INSERT INTO job_categories (id, name) VALUES ('x2', 'A')",
        "INSERT INTO job_categories (id, name) VALUES ('x3', '   ')",
    ):
        with pytest.raises(IntegrityError), desk.engine.begin() as connection:
            connection.execute(text(statement))


# ------------------------------------------------------------------ permissions and validation

def test_everyone_signed_in_reads_but_only_an_admin_writes(desk):
    first = desk.client.get("/api/settings").json()["categories"][0]
    for who in ("staff", "manager", "admin"):
        desk.sign_in(who)
        body = desk.client.get("/api/settings/categories")
        assert body.status_code == 200 and body.json()["categories"] and body.json()["editable"] is True
        assert desk.client.get("/api/settings").json()["categories"]
    for who in ("staff", "manager"):
        desk.sign_in(who)
        assert desk.client.post("/api/settings/categories", json={"name": "Aviation"}).status_code == 403
        assert change(desk, first["id"], name="Renamed").status_code == 403
        assert desk.client.delete(f"/api/settings/categories/{first['id']}").status_code == 403
    desk.sign_in("admin")
    assert "Aviation" not in names(desk) and first["name"] in names(desk)
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_type == "job_category")) == 0


@pytest.mark.parametrize("bad", ["", "   ", "A", "x" * 121])
def test_invalid_names_are_refused_with_a_message_for_the_name(desk, bad):
    response = desk.client.post("/api/settings/categories", json={"name": bad})
    assert response.status_code == 422
    errors = {item["field"]: item["message"] for item in response.json()["detail"]["errors"]}
    assert errors.get("name")
    assert change(desk, by_name(desk, "Construction")["id"], name=bad).status_code == 422
    assert "Construction" in names(desk)


def test_a_name_is_trimmed_and_unique_ignoring_case(desk):
    created = add(desk, "  Aviation   and  airports ")
    assert "Aviation and airports" in [item["name"] for item in created["categories"]]
    for duplicate in ("aviation and AIRPORTS", "CONSTRUCTION", " construction "):
        refused = desk.client.post("/api/settings/categories", json={"name": duplicate})
        assert refused.status_code == 409 and refused.json()["detail"] == "That category is already in the list."
    other = by_name(desk, "Security")
    assert change(desk, other["id"], name="construction").status_code == 409
    assert change(desk, other["id"], name="security").status_code == 200  # only the case changes: allowed
    assert "security" in names(desk) and "Security" not in names(desk)
    assert desk.client.put("/api/settings/categories/nope", json={"name": "Whatever"}).status_code == 404
    assert desk.client.delete("/api/settings/categories/nope").status_code == 404


# ------------------------------------------------------------------ add, change, delete

def test_categories_can_be_added_switched_off_and_deleted_with_an_audit_trail(desk):
    created = add(desk, "Aviation")
    row = next(item for item in created["categories"] if item["name"] == "Aviation")
    assert row["active"] is True and row["sortOrder"] == max(item["sortOrder"] for item in created["categories"])
    switched = change(desk, row["id"], name="Aviation", active=False)
    assert switched.status_code == 200
    assert next(item for item in switched.json()["categories"] if item["id"] == row["id"])["active"] is False
    assert change(desk, row["id"], name="Aviation", active=True).status_code == 200
    deleted = desk.client.delete(f"/api/settings/categories/{row['id']}")
    assert deleted.status_code == 200 and "Aviation" not in [item["name"] for item in deleted.json()["categories"]]
    with desk.sessions() as db:
        actions = [(entry.action, entry.target, entry.actor_id) for entry in db.scalars(
            select(AuditEntry).where(AuditEntry.entity_type == "job_category").order_by(AuditEntry.at, AuditEntry.id))]
    assert actions == [("Added job category", "Aviation", "admin"), ("Updated job category", "Aviation", "admin"),
                       ("Updated job category", "Aviation", "admin"), ("Deleted job category", "Aviation", "admin")]
    assert change(desk, row["id"], name="Aviation").status_code == 404


def test_saving_nothing_new_writes_no_audit_entry(desk):
    row = by_name(desk, "Security")
    assert change(desk, row["id"], name="Security").status_code == 200
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_type == "job_category")) == 0


def test_a_category_that_job_titles_use_cannot_be_deleted_only_switched_off(desk):
    title(desk, "t1", "test 1")  # the match ignores case
    add(desk, "Test 1")
    row = by_name(desk, "Test 1")
    refused = desk.client.delete(f"/api/settings/categories/{row['id']}")
    assert refused.status_code == 409 and refused.json()["detail"] == MESSAGE_IN_USE
    assert change(desk, row["id"], name="Test 1", active=False).status_code == 200
    assert category_of(desk, "t1") == "test 1"  # switching off never touches a job title
    with desk.sessions() as db:
        db.get(Designation, "t1").category = None
        db.commit()
    assert desk.client.delete(f"/api/settings/categories/{row['id']}").status_code == 200


def test_renaming_renames_the_category_on_the_job_titles_that_used_it(desk):
    add(desk, "Test 1")
    row = by_name(desk, "Test 1")
    title(desk, "a", "Test 1")
    title(desk, "b", "test 1")  # different case, same category
    title(desk, "c", "Something else")
    title(desk, "d", None)
    renamed = change(desk, row["id"], name="Aircraft maintenance")
    assert renamed.status_code == 200
    assert [category_of(desk, key) for key in "abcd"] == ["Aircraft maintenance", "Aircraft maintenance", "Something else", None]
    assert "Test 1" not in names(desk) and "Aircraft maintenance" in names(desk)
    with desk.sessions() as db:
        entry = db.scalars(select(AuditEntry).where(AuditEntry.entity_type == "job_category", AuditEntry.action == "Updated job category")).one()
        assert entry.meta["jobTitlesRenamed"] == 2 and entry.meta["changes"]["name"] == {"from": "Test 1", "to": "Aircraft maintenance"}


def test_a_refused_rename_changes_no_job_title(desk):
    add(desk, "Test 1")
    title(desk, "a", "Test 1")
    assert change(desk, by_name(desk, "Test 1")["id"], name="Construction").status_code == 409
    assert category_of(desk, "a") == "Test 1" and "Test 1" in names(desk)


# ------------------------------------------------------------------ job titles still accept any text

def designation_body(**extra):
    return {"title": "Welder", "requiredSkills": [], "minExperience": 0, **extra}


def test_job_titles_use_the_managed_spelling_but_still_accept_unknown_text(desk):
    created = desk.client.post("/api/designations", json=designation_body(category="  oil   AND gas "))
    assert created.status_code == 201 and created.json()["category"] == "Oil and gas"
    unknown = desk.client.post("/api/designations", json=designation_body(category="Test 1"))
    assert unknown.status_code == 201 and unknown.json()["category"] == "Test 1"
    for blank in (None, "", "   "):
        assert desk.client.post("/api/designations", json=designation_body(category=blank)).json()["category"] is None
    updated = desk.client.put(f"/api/designations/{unknown.json()['id']}", json=designation_body(category="SECURITY"))
    assert updated.status_code == 200 and updated.json()["category"] == "Security"
    assert desk.client.put(f"/api/designations/{unknown.json()['id']}", json=designation_body(category="x" * 121)).status_code == 422


# ------------------------------------------------------------------ the desk payload

def test_bootstrap_carries_the_same_category_list(desk):
    add(desk, "Aviation")
    boot = desk.client.get("/api/bootstrap").json()["settings"]
    direct = desk.client.get("/api/settings").json()
    assert boot["categories"] == direct["categories"] and boot["categoriesEditable"] is True
    assert set(boot["categories"][0]) == {"id", "name", "active", "sortOrder"}


def test_a_database_without_the_table_keeps_working(desk):
    with desk.engine.begin() as connection:
        connection.execute(text("DROP TABLE job_categories"))
    service.reset_cache()
    settings = desk.client.get("/api/settings").json()
    assert settings["categories"] == [] and settings["categoriesEditable"] is False
    assert settings["editable"] is True and settings["currencies"]  # the rest of the settings are unaffected
    boot = desk.client.get("/api/bootstrap")
    assert boot.status_code == 200 and boot.json()["settings"]["editable"] is True and boot.json()["settings"]["categoriesEditable"] is False
    assert desk.client.post("/api/settings/categories", json={"name": "Aviation"}).status_code == 503
    created = desk.client.post("/api/designations", json=designation_body(category="  Test 1 "))
    assert created.status_code == 201 and created.json()["category"] == "Test 1"


# ------------------------------------------------------------------ live updates

def test_the_category_table_feeds_the_settings_topic():
    assert change_feed.classify_statement("INSERT INTO job_categories (id, name) VALUES (%(i)s, %(n)s)") == ("INSERT", "job_categories")
    assert change_feed.classify_statement("DELETE FROM job_categories WHERE id = %(i)s") == ("DELETE", "job_categories")
    pending = change_feed.Pending()
    pending.note_table("UPDATE", "job_categories")
    body = json.loads(pending.payload(now_ms=1))
    assert body["t"] == ["settings"]
    for role in ("staff", "manager", "admin"):
        assert change_feed.visible_to(body, role, "someone")["topics"] == ["settings"]


def test_changing_a_category_announces_the_settings_topic(desk):
    change_feed.install()
    url = desk.engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as listener:
        listener.execute(f"LISTEN {change_feed.CHANNEL}")
        list(listener.notifies(timeout=0.3))
        add(desk, "Aviation")
        first = [json.loads(note.payload) for note in listener.notifies(timeout=0.8)]
        change(desk, by_name(desk, "Aviation")["id"], name="Aviation and airports")
        second = [json.loads(note.payload) for note in listener.notifies(timeout=0.8)]
    assert first and all(message["t"] == ["settings"] for message in first)
    # A rename also rewrites job titles, so the job title screens are told as well.
    assert second and all("settings" in message["t"] for message in second) and any("designations" in message["t"] for message in second)
