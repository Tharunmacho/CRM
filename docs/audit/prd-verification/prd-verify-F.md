# PRD verification F: page 12 (daily updates) + whole-product cross-cutting checks

Scope: PRD lines 532-554 (page 12) and the role/permission table (lines 33-104), sidebar order, audit log, notifications, end-to-end candidate lifecycle.
Method: live UI against the shared mock (Vite 5198 / fake API 8766, header `x-mock-as: u1|u2|u3` on every request) plus a read of the real FastAPI code (file:line given). Nothing under M:\crm was edited.
Important limit: the fake API does NOT enforce roles and does NOT run the scheduler. "Server enforces" below comes from reading backend code only (nothing run against a real backend). Where the mock lacks a behaviour the status is COVERED-CODE-ONLY.

Evidence files (all in `M:\Temp\claude\M--crm\817cc2e2-a8ec-4230-b51f-65012d270b1a\scratchpad\F\`): dump.json, dump-manager.json (route access per role), caps.json (per-role control inventory), life.log / life.json (lifecycle walk), extra.log / extra.json (second walk, stage-2, notifications, Ctrl+K), audit-scan.txt (AST scan of mutating handlers), screenshots life-*.png and extra-*.png. Scripts in `...\scratchpad\pw\F-*.mjs`.

---

## 1. Role / permission matrix (PRD pages 1-2)

Role columns: A = admin (u1), M = manager (u2), S = staff (u3).

| # | Capability (PRD: A / M / S) | UI shows (observed live) | Server enforces (file:line) | Status |
|---|---|---|---|---|
| 1 | Connect / remove email and WhatsApp sources (Yes / No / No) | Inbox: admin sees "Mailboxes", "Connect mailbox", per-account "Auto-import" toggles, "WhatsApp" button, Reconnect; manager and staff see none of these (caps.json inbox diff; inbox.tsx:49,312,313,332; account-strip.tsx:94). WhatsApp page `/whatsapp` loads for all roles but connect, disconnect and auto-import controls are admin-only (whatsapp.tsx:35,94; wa-settings.tsx:43). | Admin-only: inbox.py:38,278 (connect), 344 (auto-import), mail_actions.py:24,28 (disconnect), 41,300,317,331; mail_accounts.py:31,99,126,145; zoho_mailbox.py:25,56; whatsapp.py:27,129,142,154. | COVERED |
| 2 | Create clients, designations, orders (Yes / Yes / Yes) | "New client", "New job title", "New order" visible for all three roles (caps.json clients, jobs). Delete buttons hidden for staff (jobs, client detail), shown to admin and manager. | No role check, so all roles: data.py:308 (client), 345 (company), 358 (designation), 415 (order) and edits at 328,350,363,423. Delete is admin or manager only: data.py:368,510,521,546 (beyond PRD, which is silent on delete). | COVERED |
| 3 | Map / unmap candidates to orders (Yes / Yes / Yes) | "Match to an order" and "Remove from this order" on candidate profile for all three; order page "Match candidates" (caps.json profile-other: staff has "Remove from this order"). Gate `map_candidates` = all roles (crm-store.tsx:96). | No role check: applications.py:272-273 (map), 305-307 (unmap). | COVERED |
| 4 | Data Bank shows All candidates (All / All / All) | Staff sees the same "All candidates 30" as admin and manager (dump.json /data-bank). | Candidate list unfiltered by role: candidates.py:70-72, candidate_search.py (no role filter; only optional staff filter :88); desk_snapshot.py:100 returns all candidates. Note desk_snapshot.py:49-50 only gives staff the applications of their own candidates; placement summaries stay shared. | COVERED |
| 5 | Work the stage modal (Any / Any / Own) | Staff opening another recruiter's candidate: note "Arjun Krishnan looks after this candidate. You can read the stages, but you cannot fill them in.", Save draft and Complete stage disabled (extra.log step C). Admin and manager get enabled controls (caps.json stage-other-s1). Staff on own candidate: enabled. Gates: stage-modal.tsx:148, task-parts.tsx:98, candidate-profile.tsx:298, data-bank-columns.tsx:128. | deps.py:107-125 `ensure_can_work` used at applications.py:326 (read draft), 334 (save), 350 (complete), files.py:148-158 (stage/candidate uploads); remarks data.py:584; initial call data.py:614. Reopen is separate (row 9). | COVERED |
| 6 | Reassign candidates and tasks (Yes / Yes / No) | Data Bank: checkboxes + "Reassign to a recruiter" / "Assign" and "Assign waiting candidates" only for admin and manager (staff has 0 checkboxes; caps.json databank-selected). Profile "Reassign" and Tasks "Reassign" button/Escalated tab only admin and manager. | data.py:737-738 `require_role(admin, manager)`; restart-clock admin only data.py:744; assign-waiting candidates.py:112-113 admin or manager. | COVERED |
| 7 | Receive auto-assigned candidates (No / No / Yes) | User dialog: auto-assign options only for Staff; text "Admins / Managers never do" (user-dialog.tsx:145). Mock always assigns u3, so live run cannot prove round-robin. | Ring contains only `Role.staff`: candidates.py:342-352 (`next_owner`); eligibility needs active, `receives_auto_assign`, not on leave. | COVERED-CODE-ONLY |
| 8 | Receive 48-hour escalations and missed-update alerts (Yes / Yes / No) | Bell: admin and manager lists contain "Task escalated", "Daily updates missing" (extra.log E, D2); staff list has none of those (extra.log E for u3). Seeded by the mock, not generated. | Recipients are active admin + manager only: workflow_scheduler.py:67 (supervisors), 92-95 (escalation/reminder), 133-134 (missed update). Tests: backend/tests/test_workflow_scheduler.py:39-100. | COVERED-CODE-ONLY (delivery proven by tests, bell display proven live) |
| 9 | Reopen a completed stage (Yes / No / No) | After expanding the done sections, admin has "Reopen stage 1-5"; manager and staff have 0 Reopen buttons (F-reopen.mjs output). stage-modal.tsx:449 returns null unless `reopen_stage` (admin only, crm-store.tsx:99). | applications.py:413-415 `require_role(Role.admin)`. | COVERED |
| 10 | Reports (All staff / All staff / Own numbers) | "Staff" filter select present for admin and manager, absent for staff; staff "Staff performance" shows only a single-person view (no "Rank staff by"). Export Excel/PDF shown to all three. | reporting.py:55-62 (`authorize`: 403 if staff asks for another id, forces `staff_id = user.id`), reports.py:28-36 and export 39-47 use the same authorize; option lists limited for staff reporting.py:394-396; dashboard reports.py:53-56. | COVERED |
| 11 | User management (Yes / No / No) | Sidebar item admin only. Direct URL `/users` as manager and staff renders "You do not have access to this page. You are signed in as Priya Nair (staff). Ask an admin if you need access." (routes.tsx:113-123; dump.json). | users.py:20 `admin = require_role(admin)` on list/create/patch/password/force-logout (users.py:80-109); agency settings settings.py:23,93+. | COVERED |
| 12 | Personal To-do List (Yes / Yes / No) | Sidebar item admin and manager only. `/todo` as staff shows the same plain denial message. Dashboard "My to-dos" card hidden for staff (dashboard.tsx:301). | data.py:673-674 and 696-697 `require_role(admin, manager)`; list is owner-only desk_snapshot.py:113; delete owner-only data.py:717-721. | COVERED |

Extra capability gating seen (not in PRD table, FYI): candidate/dashboard "Export Excel" is admin only (crm-store.tsx:98 `export: ['admin']`, candidates.py:76-77); the Reports page export is available to every role (reports.py:39). Not a gap, but the two export rules differ.

### 1b. Direct-URL access by role (dump.json, dump-manager.json)

| Route | Admin | Manager | Staff |
|---|---|---|---|
| `/`, `/inbox`, `/clients`, `/jobs`, `/add-candidate`, `/data-bank`, `/tasks`, `/reports` | open | open | open |
| `/todo` | open | open | plain denial message |
| `/users` | open | plain denial message | plain denial message |
| `/staff-directory` | open | open | "Only admins and managers can see the staff directory" |
| `/whatsapp`, `/companies`, `/mapped`, `/attendance` (extra pages) | open | open | open (attendance shows "My attendance") |

### 1c. Sidebar (PRD: Dashboard, Inbox, Clients, Jobs / Positions, Add Candidate, Data Bank, Tasks, To-do List (admin+manager), Reports, User Management (admin))

| Role | Sidebar observed | Status |
|---|---|---|
| Admin | Dashboard, Inbox, Clients, Jobs / Positions, Add Candidate, Data Bank, Tasks, To-do List, Reports, User Management (10, same order) | COVERED |
| Manager | same without User Management (9) | COVERED |
| Staff | same without To-do List and User Management (8) | COVERED |

Source: app-shell.tsx:63-77 (NAV, `extension: true` items are filtered out of the rail at :183), :182 (capability filter). Extra pages are in Ctrl+K only (extra.log F lists Dashboard, Inbox, WhatsApp, Clients, Companies, ...).

---

## 2. Audit log ("every create, edit, stage change, reassignment and file upload logged with user and timestamp")

Table `system_audit_log` (models.py:827-846): actor_id, action, target, entity, meta, `at` (timestamp default now()).

Mutating handlers that DO write an AuditEntry (file:line of the write):
- Candidates: create candidates.py:470; merge :646; correct :689; delete :796; assign-waiting candidate_review.py:127,135; reassign data.py:775,812 (restart clock :803); record call data.py:641; stage draft/complete/reopen/map/unmap applications.py:171-172 (`_audit`, called at 300,319,340,407,453).
- Master data (client, company, designation, order create/edit): data.py:282-298 `_master_save` (with field-level from/to), deletes data.py:372,515,532,559.
- Tasks: remark data.py:590; reset SLA workflow.py:57; workflow settings workflow.py:38.
- To-do: data.py:686,712,721.
- Files: files.py:182 (every upload, with attachment id/slot/version); intake document job create document_jobs.py:64; attach manual_extraction.py:206; multi-person intake document_person_split.py:179,309,353,575,596.
- Users: user_admin.py:53 (`_audit` on create, update, deactivate, password reset, force logout), :113 (reassign on account change); agency settings/currencies settings.py:67,145,178,222,239.
- Mail/WhatsApp sources: inbox.py:75 (connect, auto-import, ingest), mail_actions.py (disconnect, reply, review, history), mail_accounts.py:38, zoho_mailbox.py:28, whatsapp.py (connect/disconnect/retry/resolve), whatsapp_chat.py services (open, link, send), mail_compose.py:337 (send).
- Attendance: attendance_hr.py:65 `_audit` for every HR write; staff_activity.py:186,200,290.
- Automatic intake: email_intake.py:338, whatsapp_intake.py:154 (but actor_id is NULL, see below).

Mutating handlers that do NOT write an audit row (found by AST scan, audit-scan.txt, then checked by hand):

| Handler | File:line | In PRD's five categories? |
|---|---|---|
| Intake document draft save | document_jobs.py:142 `PUT /groups/{group_id}/draft` | edit of unsaved intake data; arguable |
| Intake document dismiss (removes an uploaded file from the intake) | document_jobs.py:207 | yes, effectively a delete of an upload |
| Intake document retry | document_jobs.py:187 | no (re-run) |
| Outgoing mail draft create / save / sync / attachment upload / attachment delete / discard / reconcile | mail_compose.py:100,194,208,215,241,271,352 | attachment upload to a draft is a file upload; the SEND is audited (:337) |
| Mail housekeeping: mark read, sync now, pause history, retry job, Zoho flags/reconcile | mail_actions.py:62,41,331; inbox.py:394; zoho_mailbox.py:181,281 | no |
| Presence heartbeat, login / logout, notification read, OCR scan (`/ocr/{kind}` reads the file and stores nothing) | attendance.py:110,118; auth.py:91,139; workflow.py:79,86; ocr.py:80 | no |
| Automatic intake rows have `actor_id` NULL (system) | email_intake.py:338, whatsapp_intake.py:154, candidate_review.py:45 | timestamp yes, user no (system actor) |

Status: **PARTIAL** (minor). All CRM-record creates/edits, stage changes, reassignments and stored-file uploads by users are logged with user and time. Missing: document_jobs.py:207 (dismiss) and :142 (draft edit) should write an AuditEntry; mail_compose.py:215/241 (draft attachment upload/delete) likewise if "file upload" is read literally. Automatic intake logs the system with no user.

---

## 3. Notification requirements

| Requirement (PRD) | Where generated | Bell/UI | Status |
|---|---|---|---|
| New assignment: owner and admin notified (PRD ~l.333) | candidates.py:461-466 (owner + all active admin and manager), document_person_split.py:572, assignment_notifications.py:34 (reassign), candidate_review.py:85, user_admin.py reassign | Bell row links to `/candidates/{id}` or `/tasks?task=` (assignment_notifications.py:36). Mock `POST /api/candidates` does not create a task or notification (mock-api-full.mjs:1018-1022), so lifecycle step 1e/1g could not be seen live. | COVERED-CODE-ONLY |
| 48-hour escalation to admin + managers with candidate, stage, owner, hours overdue; repeat every 24h (admin setting) | workflow_scheduler.py:20-24 (`overdue_detail`), 70-95, 79-87 (repeat), settings workflow.py:34-45 | Bell row (red dot for escalation) link `/tasks?task=<id>`; admin opens it and sees Reassign (extra.log D3) | COVERED-CODE-ONLY |
| Reassign an escalated task from the notification | n/a | notification -> `/tasks?task=` -> "Reassign" button (admin and manager; extra.log D3, caps tasks) | COVERED |
| Staff receive no escalation / missed-update alerts | workflow_scheduler.py:67 | staff bell has none (extra.log E u3) | COVERED |
| Personal to-do due reminder in bell | workflow_scheduler.py:50-58 (`deliver_due_todos`), runs every 30s workflow_worker.py:15-33 | bell row "To-do due" links `/todo` | COVERED-CODE-ONLY |
| End-of-day missed-update alert (page 12) | workflow_scheduler.py:101-134 | see section 5 | COVERED-CODE-ONLY |
| Bell mechanics (unread count, mark read, mark all, click navigates) | workflow.py:72-92 | app-shell.tsx:340-415 | COVERED |

---

## 4. End-to-end lifecycle walk (live, mock)

Candidate "Lifecycle Tester 24235" (cand31). Staff u3 added it; mock assigned it to Priya Nair. Screenshots: life-1-added.png ... life-7-dashboard.png, extra-A-after-complete.png, extra-B-tasks.png.

| Step | Works? | Evidence |
|---|---|---|
| 1 Add Candidate: fill name + phone, "Save and assign" | Yes | Toast "Assigned to Priya Nair." and app moves to the candidate profile `/candidates/cand31` (add-candidate.tsx:307,327). Candidate owner is a Staff user. |
| 1b Auto-assigned owner shown with "Initial call due within 48 hours" | Yes | add-candidate.tsx:307,391-396,439-446 |
| 1c Initial-call task created (48h) | Not provable on mock; COVERED-CODE-ONLY | Mock returns only `{id, ownerName}` (mock-api-full.mjs:1020-1021). Real code creates Task kind `initial_call` with `due_at = +48h` at candidates.py:448-458. |
| 1d Owner and admin notified | Not provable on mock; COVERED-CODE-ONLY | candidates.py:461-466 |
| 2 Tasks page lists the initial-call task; click goes to the candidate | Code path COVERED-CODE-ONLY | Task has no application, so `openTask` navigates to `/candidates/{id}` (tasks.tsx:221). Profile also has "Open the initial call task" button (candidate-profile.tsx:480). Row absent in the mock because the mock created no task. |
| 3 Record the call (profile "Record" -> Called Yes / Interested Yes / remarks -> "Save result") | Yes | Candidate `callOutcome` saved (life.log step 3). Real server closes the task only for called + yes/no (data.py:632-640). |
| 4 Match to an order ("Match to an order" -> pick order -> "Match and start stage 1") | Yes | Application `a55` created at stage 1 with a stage-1 task "Prescreen the candidate" due +48h (life.log step 4). No auto-open of the stage modal; the profile shows "Open stage 1" (not a dead end). |
| 5 Stage modal opens; "Daily updates" box; "Not updated today" badge; add remark | Yes | Badge shown before the remark, cleared after; history row "Priya Nair - Today, 17:41" (life.log 5, 5b, 5c). |
| 6 Complete stage 1 | Yes | Required "Result" radio (Passed / Failed); after choosing Passed the application is at stage 2 and the stage-1 task is done, stage-2 task open (extra.log A: `stage=2 tasks=s1:done,s2:open`). |
| 7 Tasks page | Yes | New row "Forward CV to client ... 47h left ... Not updated today" (extra.log B). |
| 8 Dashboard counts | Yes | `/dashboard` pipeline stage 1 went 3 -> 4 after mapping (life.log step 7); staff dashboard shows "Not updated today" tile/filter and tag on the "My tasks" rows (extra.log B2, B3). |

Links checked against PRD "opens X" statements:

| PRD statement | UI link | Status |
|---|---|---|
| Dashboard pipeline stage count "clickable to a filtered list" (PRD l.95) | Click a stage -> dialog listing candidates in that stage; each row opens the stage modal (dashboard.tsx:284-333). It is a dialog, not a page filtered list. | COVERED (form differs slightly) |
| Data Bank row click opens profile; each application opens its stage modal (l.206) | data-bank.tsx:335 hint; stage modal at :340 | COVERED |
| Task click opens the candidate's stage modal at that stage (l.217) | tasks.tsx:221 and "Open stage for X" buttons | COVERED |
| Stage modal opens from order page, Data Bank, or a task (l.340) | order-detail.tsx:388, data-bank.tsx:340, tasks.tsx, plus profile, client detail, dashboard | COVERED |
| Reassign an escalated task from the notification (l.521) | notification -> `/tasks?task=` -> Reassign | COVERED |
| Notification link for a missed-update alert | real backend link `/tasks?staff=<id>&date=<day>` opens the history view "Tasks with no daily update on <date>" (tasks.tsx:112-128; verified live with `?staff=u3&date=2026-10-24`) | COVERED |

---

## 5. Page 12 requirements (PRD lines 532-554)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| P12-1 | Every open task gets at least one remark from its owner each day until completed | COVERED | Rule enforced by the daily scan, which counts only a remark whose author equals the task owner: workflow_scheduler.py:109-126 (a supervisor's remark does not count; test_workflow_scheduler.py:79-98). Remark endpoint data.py:579-593 (staff only on own task; closed task refused 409). |
| P12-2 | "Add remark" box (text required) in the Tasks list and in the stage modal | COVERED | Tasks: row button "Add remark for X, N so far" and the expanded box (task-parts.tsx:91-160, aria-label "Add remark", button disabled while empty :157). Stage modal: "Daily updates" card (stage-modal.tsx:704-740, live step 5). Server: `RemarkIn` min_length=1 (data.py:575). |
| P12-3 | Remarks timestamped, cannot be edited or deleted, shown as a history under the task | COVERED (API level) | Only a POST route exists for remarks (data.py:579; no PUT/PATCH/DELETE anywhere); history list with author and time task-parts.tsx:162-186, stage-modal; copy "cannot be changed or deleted". Advisory: no database trigger enforces immutability (models.py:522; no trigger in alembic/versions), so it relies on the API having no such route. |
| P12-4 | "Not updated today" badge in the owner's Tasks and Dashboard | COVERED | Tasks: tag tasks.tsx:391, tile task-cockpit.tsx:36, filter tasks.tsx:232. Dashboard: tag stacked.tsx:47, filter dashboard.tsx:39. Stage modal: stage-modal.tsx:708. Client rule `updatedToday` = remark by the task owner on the agency day (crm-store.tsx:585); server dashboard computes the same (reporting.py:441-456). Seen live for staff in both pages (extra.log B, B2). |
| P12-5 | At end of day, list every open task with no remark that day and notify admin and managers in the CRM, grouped by staff | COVERED-CODE-ONLY | workflow_scheduler.py:101-134: for each unprocessed agency day up to yesterday it records one `missed_update` event per task (unique key, repeat-safe) and sends ONE notification per staff owner to all active admins and managers ("<name>: daily updates missing - N open tasks had no owner update on <day>") linking to `/tasks?staff=<id>&date=<day>`; list endpoint `/tasks/missed-updates` workflow.py:62-70 (admin or manager); history view tasks.tsx:112-128 (live OK). Worker: workflow_worker.py:12-33 (15-minute scan), started by runtime.py:196. Timing note: a day is only processed after agency midnight (loop `while day <= yesterday`, :101-104), so the alert arrives at about 00:00-00:15 the next day, not at a configurable office-closing time. Not exercised live (mock has no scheduler). |
| P12-6 | Completing the stage closes the task; no remark needed on the completion day | COVERED-CODE-ONLY | Scan skips tasks completed or cancelled during that day: workflow_scheduler.py:104-108 (`completed_at >= end` only counts tasks still open at day end); test test_workflow_scheduler.py:79-98 (completed and cancelled tasks create no event). UI shows the badge only for open tasks (task-parts.tsx:99, tasks.tsx:143). Live: completing stage 1 closed the task (extra.log A). |
| P12-7 | Missed daily updates counted per staff in the staff performance report | COVERED | Count of `missed_update` events per owner: reporting.py:326-336; UI column and tile report-staff.tsx:19,59,88,108; Excel/PDF columns report_exports.py:12,25. Visible live: admin and manager "Rank staff by ... Missed updates", staff sees own tile (caps.json reports-staff). |

---

## 6. Counts

| Status | Count | Items |
|---|---|---|
| COVERED | 21 | role rows 1,2,3,4,5,6,9,10,11,12; sidebar x3 roles; reassign-from-notification; staff-gets-no-alerts; bell mechanics; P12-1,2,3,4,7 |
| COVERED-CODE-ONLY | 8 | role rows 7, 8; notifications: assignment, escalation, to-do reminder; lifecycle 1c/1d/2 (mock gap); P12-5, P12-6 |
| PARTIAL | 2 | Audit log (section 2); Map-before-initial-call (below) |
| NOT COVERED | 0 | |
| DIFFERS-BY-DECISION (already agreed) | 0 new | |

(Counts are of the rows in this file; role row 7/8 and the notification rows overlap.)

## 7. Every PARTIAL / dead end / missing link, with the file to change

1. PARTIAL, audit log gaps (section 2).
   - `backend/app/api/routers/document_jobs.py:207` (`dismiss`): removing an uploaded intake file writes no AuditEntry. Add one with actor and filename.
   - `backend/app/api/routers/document_jobs.py:142` (`save_draft`) and `:187` (`retry`): no AuditEntry (lower priority).
   - `backend/app/api/routers/mail_compose.py:100,194,215,241,271`: mail draft create/edit/attachment upload/delete/discard are not logged (only the send at :337 is).
   - `backend/app/services/email_intake.py:338`, `whatsapp_intake.py:154`: automatic intake rows carry no actor (system); acceptable if "system" counts as the user, otherwise add a system user id.
2. PARTIAL, lifecycle edge: mapping a candidate before the initial call is recorded silently cancels the initial-call task and the call result can then never be recorded. `backend/app/api/routers/applications.py:290-297` sets `task.cancelled_at` and returns `closed_initial_call`; the front end never reads that flag (grep: no use in `frontend/src`), `frontend/src/features/candidates/components/map-dialog.tsx` shows no warning; after mapping the profile hides "Record" (`frontend/src/screens/candidates/candidate-profile.tsx` Initial call panel, condition `canWork && !active && !deployed`) and the server answers 409 "Record the result in the candidate's stages on the order" (`backend/app/api/routers/data.py:617`). The PRD (p.4/6) says the outcome is recorded on the candidate profile and does not say matching must wait, so decide: warn in map-dialog.tsx when an open initial-call task exists, or allow `record_call` after mapping.
3. Test-harness gap (not a product gap): `scratchpad/mock-api-full.mjs:1018-1022` `POST /api/candidates` creates no initial-call task and no notification, so the Tasks/bell parts of lifecycle step 1 can only be confirmed from backend code (candidates.py:448-466).
4. Observation only: manager and staff have no sidebar or button route to the extra WhatsApp page (Inbox "WhatsApp" button is admin only, inbox.tsx:313; dashboard source health admin only); they reach it by Ctrl+K or a candidate's "Open WhatsApp chat". WhatsApp page is outside the PRD's 10 items, so not a requirement gap.
5. Observation only: the end-of-day missed-update scan has no configurable time and runs after agency midnight (workflow_scheduler.py:101-104); a note for the client if "end of day" was meant as office close.

No dead ends found in the main chain (Add candidate -> profile -> Record call -> Match -> stage modal -> Tasks -> Dashboard); each step links to the next.
