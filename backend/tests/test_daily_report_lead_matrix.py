"""Lead sheet report: every recruiter's leads by status (`leadSheet.byStatusPerStaff` + `statusColumns`), on the daily report's seed.

Seed (test_daily_work_report): asha holds L1 interested, L2 new, L3 wrong_number, L4 not_interested, L5 invalid, L6 contacted;
ravi holds L7 new, L8 interested, L9 new, L11 converted; L10 (new) is nobody's.
"""
from sqlalchemy import text

from test_daily_work_report import env, person, report, world  # noqa: F401  (fixtures and helpers of the daily report's seed)

DAY = {"from": "2026-11-05", "to": "2026-11-05"}
BUILTIN = ["new", "contacted", "interested", "resume_requested", "resume_received", "converted", "not_interested", "invalid", "wrong_number"]


def counts_of(sheet, uid):
    return next(r["counts"] for r in sheet["byStatusPerStaff"] if r["id"] == uid)


def test_every_recruiter_by_every_status_ties_to_the_sheet(env):
    body = report(env, **DAY)
    sheet = body["leadSheet"]
    assert [c["key"] for c in sheet["statusColumns"]] == BUILTIN
    assert [c["label"] for c in sheet["statusColumns"]][:3] == ["Not contacted", "Contacted", "Interested"]
    assert [r["id"] for r in sheet["byStatusPerStaff"]][-1] == "unassigned"
    asha, ravi, nobody = counts_of(sheet, "asha"), counts_of(sheet, "ravi"), counts_of(sheet, "unassigned")
    assert asha == {"new": 1, "contacted": 1, "interested": 1, "resume_requested": 0, "resume_received": 0, "converted": 0,
                    "not_interested": 1, "invalid": 1, "wrong_number": 1}
    assert (ravi["new"], ravi["interested"], ravi["converted"]) == (2, 1, 1) and sum(ravi.values()) == 4
    assert nobody["new"] == 1 and sum(nobody.values()) == 1
    for row in body["staff"]:   # a row adds up to the person's Assigned
        assert sum(counts_of(sheet, row["id"]).values()) == row["leads"]["assigned"]
    for column in sheet["byStatus"]:   # the columns add up to the existing byStatus (no template on the seed)
        assert sum(r["counts"][column["key"]] for r in sheet["byStatusPerStaff"]) == column["count"]
    assert sum(sum(r["counts"].values()) for r in sheet["byStatusPerStaff"]) == sheet["totalLeads"] == 11


def test_one_recruiter_has_only_their_row_and_no_unassigned_row(env):
    sheet = report(env, staff="asha", **DAY)["leadSheet"]
    assert [r["id"] for r in sheet["byStatusPerStaff"]] == ["asha"] and sum(sheet["byStatusPerStaff"][0]["counts"].values()) == 6
    mine = report(env, "ravi", **DAY)["leadSheet"]
    assert [r["id"] for r in mine["byStatusPerStaff"]] == ["ravi"]


def test_status_templates_get_their_own_column_after_the_built_in_ones(env):
    with env.world.sessions() as db:
        db.execute(text("UPDATE crm_leads SET status = 'contacted', status_label = 'Ringing' WHERE id IN ('L2', 'L9')"))
        db.execute(text("UPDATE crm_leads SET status = 'not_interested', status_label = 'Not eligible' WHERE id = 'L10'"))
        db.execute(text("UPDATE crm_leads SET status = 'contacted', status_label = 'Visa pending' WHERE id = 'L6'"))
        db.commit()
    try:
        sheet = report(env, **DAY)["leadSheet"]
        keys = [c["key"] for c in sheet["statusColumns"]]
        assert keys == BUILTIN + ["t:ringing", "t:not eligible", "t:visa pending"]
        assert [c["label"] for c in sheet["statusColumns"]][-3:] == ["Ringing", "Not eligible", "Visa pending"]
        assert counts_of(sheet, "asha")["t:ringing"] == 1 and counts_of(sheet, "asha")["t:visa pending"] == 1
        assert counts_of(sheet, "asha")["new"] == 0 and counts_of(sheet, "asha")["contacted"] == 0   # a templated lead counts once, under its name
        assert counts_of(sheet, "ravi")["t:ringing"] == 1 and counts_of(sheet, "unassigned")["t:not eligible"] == 1
        total = sum(sum(r["counts"].values()) for r in sheet["byStatusPerStaff"])
        assert total == sheet["totalLeads"] == 11
        for uid in ("asha", "ravi"):
            assert sum(counts_of(sheet, uid).values()) == person(report(env, **DAY), uid)["leads"]["assigned"]
    finally:
        with env.world.sessions() as db:
            db.execute(text("UPDATE crm_leads SET status_label = NULL WHERE status_label IS NOT NULL"))
            db.execute(text("UPDATE crm_leads SET status = 'new' WHERE id IN ('L2', 'L9', 'L10')"))
            db.execute(text("UPDATE crm_leads SET status = 'contacted' WHERE id = 'L6'"))
            db.commit()
