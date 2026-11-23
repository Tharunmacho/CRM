"""One client per name AND country (or phone, or e-mail): a repeated create (double click, two people at once) is refused instead of leaving two rows;
two real clients that share a name tell themselves apart by country, phone or e-mail."""
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db.models import Client, Role
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_master_data import desk  # noqa: F401  (fixture)


def as_admin(desk):
    desk.actor.id, desk.actor.role = "admin", Role.admin


def test_a_client_with_the_same_name_and_country_is_not_created_twice(desk):
    as_admin(desk)
    first = desk.client.post("/api/clients", json={"name": "Al Dhow Engineering"})
    assert first.status_code == 201, first.text
    for spelling in ("Al Dhow Engineering", "  al dhow   ENGINEERING ", "AL DHOW ENGINEERING"):
        again = desk.client.post("/api/clients", json={"name": spelling})
        assert again.status_code == 409, spelling
        detail = again.json()["detail"]
        assert "already exists" in detail and "Al Dhow Engineering" in detail, detail   # it says WHICH client
        assert "country" in detail and "phone" in detail and "e-mail" in detail          # and how to tell the two apart
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Client).where(func.lower(Client.name).like("%al dhow%"))) == 1


def test_the_same_name_in_another_country_is_a_different_client_but_the_same_country_is_not(desk):
    as_admin(desk)
    assert desk.client.post("/api/clients", json={"name": "Mohammed Ali", "country": "Kuwait"}).status_code == 201
    assert desk.client.post("/api/clients", json={"name": "Mohammed Ali", "country": "Qatar"}).status_code == 201
    assert desk.client.post("/api/clients", json={"name": "mohammed  ali"}).status_code == 201          # no country is a country of its own
    same = desk.client.post("/api/clients", json={"name": "MOHAMMED ALI", "country": " kuwait "})
    assert same.status_code == 409 and "Kuwait" in same.json()["detail"], same.text
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Client).where(func.lower(Client.name).like("%mohammed%ali%"))) == 3


def test_the_same_phone_number_or_e_mail_is_the_same_client_whatever_the_name(desk):
    as_admin(desk)
    assert desk.client.post("/api/clients", json={"name": "Gulf Builders Contact", "phone": "+974 5555 1234", "email": "Hr@Gulf.Example"}).status_code == 201
    by_phone = desk.client.post("/api/clients", json={"name": "Someone Else", "phone": "974-5555-1234"})
    assert by_phone.status_code == 409 and "Gulf Builders Contact" in by_phone.json()["detail"], by_phone.text
    by_mail = desk.client.post("/api/clients", json={"name": "Another Person", "email": " hr@gulf.example "})
    assert by_mail.status_code == 409 and "Gulf Builders Contact" in by_mail.json()["detail"], by_mail.text
    assert desk.client.post("/api/clients", json={"name": "Someone Else", "phone": "+974 5555 9999", "email": "other@gulf.example"}).status_code == 201


def test_a_different_name_is_always_allowed(desk):
    as_admin(desk)
    assert desk.client.post("/api/clients", json={"name": "Finesco International"}).status_code == 201
    assert desk.client.post("/api/clients", json={"name": "Finesco International (Kuwait)"}).status_code == 201
    assert desk.client.post("/api/clients", json={"name": "Finesco Holdings"}).status_code == 201


def test_two_creates_at_the_same_moment_leave_one_client(desk):
    """A double click: both requests pass the duplicate check at the same instant unless the check takes a lock, so one must be refused."""
    as_admin(desk)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: desk.client.post("/api/clients", json={"name": "Race Trading", "country": "Oman"}).status_code, range(2)))
    assert sorted(answers) == [201, 409], answers
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Client).where(Client.name == "Race Trading")) == 1


def test_editing_a_client_keeps_working_with_its_own_name(desk):
    as_admin(desk)
    made = desk.client.post("/api/clients", json={"name": "Keep Name Ltd", "country": "UAE"}).json()
    saved = desk.client.put(f"/api/clients/{made['id']}", json={"name": "Keep Name Ltd", "country": "UAE", "notes": "edited"})
    assert saved.status_code == 200, saved.text
