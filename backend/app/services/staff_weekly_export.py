"""Excel and CSV files for the weekly staff report, built from the same report the screen shows."""
from __future__ import annotations

import csv
from datetime import datetime
from io import StringIO

from app.services.report_exports import xlsx_rows

STATUS_LABELS = {"worked": "Worked", "no_attendance": "No attendance", "pending": "Not in yet", "not_scheduled": "Not scheduled"}
STATE_LABELS = {"present": "Present", "absent": "Absent", "leave": "Leave", "off": "Day off", "pending": "Not in yet",
                "untracked": "Not tracked", "future": "Upcoming"}

SUMMARY_COLUMNS = [
    "Recruiter", "Attendance", "Days worked", "Days absent", "Hours worked", "Hours expected", "Active hours in CRM",
    "Daily tasks assigned", "Daily tasks completed", "Completion %", "Postponed", "Overdue", "Backlog (earlier days)",
    "Stage moves", "Forwarded to client", "Interviews scheduled", "Selected", "Visa received", "Visa stamped", "Deployed",
    "Candidates in progress", "Stuck candidates", "Score",
    "Leads assigned", "Assigned leads called", "Lead calls logged", "Leads worked", "Leads interested", "Resumes received",
    "Leads converted", "Lead conversion %", "Lead follow-ups overdue",
]
LEAD_COLUMNS = ["Recruiter", "Leads assigned", "Assigned leads called", "Assigned, not called", "Lead calls logged", "Leads worked",
                "Leads interested", "Resumes received", "Leads converted", "Lead conversion % (converted / worked)",
                "Lead follow-ups overdue (now)", "Open leads held (now)"]


def _lead_cells(p):
    lead = p.get("leads") or {}
    if not lead.get("available"):
        return [""] * 9
    return [lead["assigned"], lead["contacted"], lead["calls"], lead["worked"], lead["interested"], lead["resumesReceived"],
            lead["converted"], lead["conversionPercent"] if lead["conversionPercent"] is not None else "", lead["followUpsOverdue"]]


def _local(value, zone):
    if not value:
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    from zoneinfo import ZoneInfo
    return value.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M")


def summary_rows(data):
    rows = []
    for p in data["staff"]:
        a, t, s, o = p["attendance"], p["tasks"], p["stages"], p["outcomes"]
        rows.append([
            p["name"], STATUS_LABELS.get(p["status"], p["status"]), a["workedDays"], a["absentDays"], a["hoursWorked"],
            a["expectedHours"], a["activeHours"],
            t["assigned"] if t["available"] else "", t["completed"] if t["available"] else "",
            t["completionPercent"] if t["completionPercent"] is not None else "",
            t["postponed"] if t["available"] else "", t["overdue"] if t["available"] else "",
            t["backlog"] if t["available"] else "",
            s["moves"], o["forwarded"], o["interviewsScheduled"], o["selected"], o["visaReceived"], o["visaStamped"],
            o["deployed"], s["activeCandidates"], p["stuck"]["count"], p["score"] if p["score"] is not None else "",
            *_lead_cells(p),
        ])
    return rows


def sheets(data):
    zone, period = data["timezone"], data["range"]
    about = [["Report", "Weekly recruiter performance"], ["Period", f"{period['from']} to {period['to']}"],
             ["Generated at", _local(data["generatedAt"], zone)], ["Time zone", zone]]
    about += [["Score formula", line] for line in data["formula"]["lines"]]
    about += [["Note", note] for note in data["notes"]]
    staff = [SUMMARY_COLUMNS] + summary_rows(data)
    daily = [["Recruiter", "Date", "Day", "Attendance", "Check-in", "Check-out", "Hours worked", "Active hours in CRM",
              "Tasks assigned", "Tasks done", "Tasks missed", "Stage moves"]]
    for p in data["staff"]:
        for d in p["days"]:
            daily.append([p["name"], d["date"], STATE_LABELS.get(d["state"], d["state"]), _local(d["firstIn"], zone)[11:],
                          _local(d["lastOut"], zone)[11:] if d["lastOut"] else "", round(d["workedSeconds"] / 3600, 2),
                          round(d["activeSeconds"] / 3600, 2), d["tasks"]["assigned"], d["tasks"]["done"], d["tasks"]["missed"],
                          d["stageMoves"]])
    stages = [["Recruiter", "Stage", "Stage name", "In this stage now", "Moved in period", "Reached (all time)"]]
    for p in data["staff"]:
        for n in range(1, 12):
            stages.append([p["name"], n, data["funnel"][n - 1]["label"], p["stages"]["inStageNow"][n],
                           p["stages"]["movesByStage"][n], p["stages"]["reached"][n]])
    funnel = [["Stage", "Stage name", "Reached (all time)", "In this stage now", "Closed here", "Moved in period",
               "Reached vs previous stage (%)", "Lost before this stage"]]
    for row in data["funnel"]:
        funnel.append([row["stage"], row["label"], row["reached"], row["inStageNow"], row["closedHere"], row["movesInPeriod"],
                       row["conversionPercent"] if row["conversionPercent"] is not None else "",
                       row["lostFromPrevious"] if row["lostFromPrevious"] is not None else ""])
    leads = [LEAD_COLUMNS]
    for p in data["staff"]:
        lead = p.get("leads") or {}
        if lead.get("available"):
            leads.append([p["name"], lead["assigned"], lead["contacted"], lead["notContacted"], lead["calls"], lead["worked"],
                          lead["interested"], lead["resumesReceived"], lead["converted"],
                          lead["conversionPercent"] if lead["conversionPercent"] is not None else "",
                          lead["followUpsOverdue"], lead["open"]])
    if len(leads) == 1:
        leads.append(["The Lead sheet has no numbers for this period."])
    highlights = [["Recruiter", "Severity", "Issue", "Detail"]] + [[h["staffName"], h["severity"], h["title"], h["detail"]]
                                                                  for h in data["highlights"]]
    return [("About this report", about), ("Recruiter summary", staff), ("Day by day", daily), ("Stages by recruiter", stages),
            ("Leads by recruiter", leads), ("Team funnel", funnel), ("Questions to ask", highlights)]


def xlsx(data) -> bytes:
    return xlsx_rows(sheets(data))


def _safe(value):
    """Keep a spreadsheet from running text that starts like a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_bytes(data) -> bytes:
    """The staff summary table as UTF-8 CSV (with a byte-order mark so Excel reads names correctly)."""
    out = StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(SUMMARY_COLUMNS)
    for row in summary_rows(data):
        writer.writerow([_safe(v) for v in row])
    return ("﻿" + out.getvalue()).encode("utf-8")
