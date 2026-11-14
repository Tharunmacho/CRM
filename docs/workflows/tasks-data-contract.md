# Tasks data contract (Today's candidates, Follow-up, goal settings and report inputs)

## The three buckets (current rule)

A recruiter's candidates move between three buckets on their own, computed from the live records on every read (no table is written, nothing is allocated):

| Bucket | Screen | Who is in it |
| --- | --- | --- |
| **Today's candidates** | Tasks (`GET /api/daily-work`) | Candidates the recruiter owns, status `new`, `in_process` or `needs_review`, with NO active application yet or an active application at **stage 1 (Prescreening)**. Finishing prescreening completes the task. |
| **Follow-up** | Tasks > Follow-up (`GET /api/follow-up`) | Candidates the recruiter owns whose application is `status = active` at `current_stage` **2 to 11** (selected and moving). |
| **Travelled** | `/travelled` (`GET /api/travelled`, unchanged) | Applications with `status = deployed` (stage 11 completed). |

A stage 1 completion moves the placement to stage 2: the candidate leaves *To work* (it is ticked under *Done today* until the day ends) and appears in Follow-up. A negative exit (`closure_reason` such as `interview_rejected`, `offer_declined`, `offer_not_received`, `medical_unfit`, `not_interested`, `resume_not_received`, `unmapped`, or any later one) sets the application to `closed`, so it drops out of Follow-up; the stage rules (`app/domain/stages.py`) decide what closes an application and this feature reads only `status` and `current_stage`, never an outcome. Completing stage 11 sets `deployed`: the candidate leaves Follow-up and shows on Travelled. A candidate with several active placements is in Follow-up once per placement and not in Today's candidates while one of them is past stage 1. The screens follow the live topics `tasks`, `candidates` and `applications` (and Travelled follows `reports`), which every stage completion and application close publishes, so a candidate changes bucket without a reload.

### Follow-up

`GET /api/follow-up[?staff=<id>|all]`, scoped like `/api/daily-work` (a recruiter sees only their own; admin/manager see everyone's or one recruiter's, unknown id 404, a recruiter asking for another's 403). Rows are one per active placement, **stalest first**: `lastUpdateAt` is the latest of the last stage completed, the last remark written on any of the placement's tasks, the last draft saved and the placement's creation; `id` breaks ties. `summary.total` and the supervisors' `staff[].summary.total` are exact counts (rows are capped at 1500: `truncated`). Row fields: `id` and `applicationId` (the application), `taskId` (the open stage task a remark goes on, else null), `candidateId`, `ownerId`, `stage` (2-11), `lastUpdateAt`, `client`, `clientContact`, `country`, `countrySource`, `locationHint`, `position`, `positionSource`, `candidate`, `candidateRef`, `candidateCareId`, `orderRef`, and `remark` (the newest remark with `body`, `at`, `by`, `count`, or null). The Follow-up screen's columns: S.No, Client, Country, Candidate (Care India ID first, CND muted), Job, Recruiter (supervisors only), Stage, Last update / remarks, Actions (Open = the application form, menu = Open stage / Open candidate profile).

### Today's candidates

Tasks opens on **Today's candidates**, ONE flat list of the signed-in recruiter's own candidates. There is no daily quota, no cap, no morning/evening sessions, no hourly slots, no deadlines, and nothing is "allocated", "postponed", "overdue" or "carried over". The list is computed from the live records on every read (`GET /api/daily-work`, a read only: no table is written), so an assignment, reassignment or status change shows at once. The code is `backend/app/services/daily_work.py`.

**Who is on a list.** A candidate with `owner_id` = the recruiter and `status` in `new`, `in_process` or `needs_review` (the `WORKABLE` tuple; `needs_review` is assigned but still flagged for the recruiter's check) that has no active application at stage 2 or later (or finished prescreening today: see Done today). Deployed, not-interested and ownerless candidates are never listed, and a candidate owned by somebody else is never on your list. Only users with the recruiter role (`Role.staff`) have a list; admin and manager read everybody's.

**One row per candidate.** A candidate with an active placement shows that order's client, country and job and opens the stage form (`applicationId` set, `kind: "stage"`); one without shows its own application-form values (preferred country, preferred job, else first skill; client has no fallback), has no `applicationId` and opens the application form (`kind: "call"`). With several active placements the one with an open stage task wins, else the newest. Remarks live on the placement's open stage task, else the candidate's open initial-call task (`taskId`, null when neither exists).

**Order (stable, the same on every read).**

1. Candidates still to work: those with a stage waiting first (an open `stage` or `remedical` task on an active application, the one waiting longest first by `workflow_tasks.created_at`), then the rest, newest candidate first (`crm_candidates.created_at`; the assignment time is not stored separately), `id` breaks ties.
2. Then the **Done today** group, most recently finished first.

**Done today.** The agency-timezone day (`AGENCY_TIMEZONE`, default `Asia/Kolkata`) bounds it. A candidate is done today when its recruiter finished **prescreening** for it today (a `crm_application_stages` row with `stage = 1`, `completed_at` in the day, `reopened_reason` null, outcome not `Unfit: reapplying`, credited to the owner: `COALESCE(owner_id, completed_by)` = the candidate's owner) or its initial call got a real outcome today (`call_called` true, `call_interested` yes or no, `call_recorded_at` in the day; an unanswered call stays to be worked). `completedAt` is the latest of those. A candidate finished through stage 1 is already in Follow-up while it is still shown under Done today; the next day only Follow-up has it. A candidate finished by its call only (no application yet) is back among the work the next day. A candidate reassigned after finishing is gone from the old recruiter's lists and, if past prescreening, in the new recruiter's Follow-up.

**Response** of `GET /api/daily-work[?staff=<id>|all]` (recruiters may only pass their own id or `all`; admin/manager may pass any recruiter id, unknown ones answer 404):

```json
{ "agencyDate": "2026-11-03", "serverTime": "...", "staffCount": 1,
  "summary": { "total": 15, "toWork": 12, "doneToday": 3 },
  "truncated": false,
  "items": [ { "id": "<candidate id>", "kind": "stage|call", "taskId": null, "applicationId": null, "candidateId": "...", "ownerId": "...",
               "stage": 1, "waiting": true, "status": "open|done", "completedAt": null,
               "client": "...", "clientContact": "...", "country": "...", "countrySource": "order|preference|null", "countryIsPreferred": false,
               "locationHint": null, "position": "...", "positionSource": "order|preference|skill|null", "positionIsPreferred": false,
               "candidate": "...", "candidateRef": "CND-01092", "candidateCareId": "CI-26-27-001", "orderRef": null,
               "referral": { "source": "...", "label": "...", "detail": null }, "remark": null } ],
  "staff": [ { "userId": "...", "name": "...", "active": true, "summary": { "total": 4, "toWork": 3, "doneToday": 1 } } ] }
```

`staff` (admin and manager only) lists every recruiter (inactive ones only while they still own candidates) with their own counts, whatever `staff=` filtered the rows to; `summary` covers the recruiters shown. Rows are capped at `MAX_ROWS` (1500) per response (`truncated`); the counts are exact. There are no `dailyQuota`, `morningQuota`, `eveningQuota`, `goals`, `teamGoals`, `checkpoints`, `shortfall`, `offToday`, `session`, `slotNo`, `dueAt`, `workDate`, `postponed`, `daysLate` or `overdueAt` fields any more.

**What stays in the database (no migration).** The three tables below are untouched. Nothing creates `workflow_daily_work_items` rows any more; the workflow worker's daily pass (`scan_daily_work`, every minute) only *retires* rows the old quota left `open` (sets `status = 'released'`, `release_reason = 'quota_removed'`, `released_at`) so they are not reported as backlog. Finished (`completed`) rows are history and stay as they are. The goal settings (`/api/daily-goals`) are no longer used by the Daily work screen; only the weekly recruiter report still reads the **stage-move** target. The overdue and carried-over bell notices (`kind = 'daily_work'`) are no longer sent.

**Tasks navigation badge.** A recruiter's badge is the number of candidates still to work today (`summary.toWork`); admin and manager keep the count of overdue 48-hour tasks. The "Complete your tasks first" banner and every deadline wording are gone.

**Consumers that were checked.** Weekly recruiter report (`staff_weekly.py`): reads `workflow_daily_work_items` and `workflow_daily_goals` unchanged, so periods before the change keep their task numbers and periods after it have no rows: the daily-task part shows "No data yet" and is left out of the score (hours and stage moves count for more), see `17-reports.md`. Lead sheet: `dailyWorkGate` is always unlocked (as before). Dashboard, recruiter directory, attendance, the Tasks navigation badge (overdue 48-hour tasks) and the 48-hour Task rows, SLA and escalations do not read daily work and are unaffected.

---

# History: the daily quota (retired)

This is the stable read contract for reports built on the former Tasks daily-quota feature (migrations `0020_daily_work_quota`, plus `0023_daily_work_call_items` which added `kind` and made `task_id` nullable). Table and column names below will not change without a new migration and an update to this file. Times are stored in UTC; "day" columns are the **agency-timezone** calendar day (`AGENCY_TIMEZONE`, default `Asia/Kolkata`). Only read these tables from reports. **Retired:** no new rows are written; the descriptions of "allocated", "slot", "deadline", "postponed" and "overdue" below describe the rows already stored from the days the quota was in use (5 candidates a day, 3 morning and 2 evening hourly slots).

## `workflow_daily_work_items` (one row per candidate handed to a recruiter for a day; history only)

| Column | Meaning |
| --- | --- |
| `id` | Item id. |
| `owner_id` | The recruiter (`auth_users.id`) the candidate was allocated to. |
| `work_date` | Agency day it was allocated. An unfinished item is **postponed** on every later day (see below). |
| `kind` | `stage`: a matched candidate's open application-stage task. `call`: a candidate the recruiter owns who is not matched to an order yet, worked through the initial call (then matching). |
| `task_id`, `application_id`, `candidate_id` | For `stage`: the stage task (`workflow_tasks`) and placement (`crm_applications`; client/country/position come from its order). For `call`: both are null (the row falls back on the candidate's `designations[0]` (else first skill) for position and `country_preference` for country, flagged `positionIsPreferred` / `countryIsPreferred`; client has no fallback). `candidate_id` is always set. |
| `stage` | Stage the candidate was at when allocated (1 to 11); null for `call`. |
| `slot_no`, `session` | Place in the day's quota (1-based) and `morning` or `evening`. Default quota 5: slots 1-3 morning, 4-5 evening. |
| `due_at` | The hourly deadline for this slot. |
| `status` | `open` (to do), `completed`, `released` (cancelled, reassigned, deployed or not interested before it was done; ignore for performance). `completed` means: `stage`: the stage task was completed by the owner; `call`: the initial call was recorded with a real outcome (called, interest yes or no) **after** the allocation, or a stage was completed for the candidate after allocation. |
| `allocated_at` | When the system allocated it. |
| `started_at` | First draft save or owner remark; equals `completed_at` when done in one step. Null until work began. |
| `completed_at`, `completed_by` | When and by whom the stage was completed (`status = completed`). |
| `overdue_at` | Equals `due_at` when the slot was missed (still open after `due_at`, or completed after it); null when finished on time. |
| `postponed_at` | Start of the day after `work_date` when the item was still unfinished then; null if finished within `work_date`. |
| `released_at`, `release_reason` | `task_cancelled` (unmapped, closed or reopened) or `reassigned` (the task, or for `call` the candidate, moved to someone else; a candidate who became deployed or not interested without a recorded call is also released and stored as `reassigned`). |
| `overdue_notified_at`, `postponed_notified_at` | Alert bookkeeping; not performance data. |

**Who was in the pool (the retired allocation rule).** Every working day each active recruiter who is not on leave, a holiday or a weekly off gets up to their goal (default 5) in this priority order: (1) their open application-stage tasks, oldest first; then (2) candidates they **own** with status `new`, `in_process` or `needs_review` (assigned but still flagged for the recruiter's check: no phone read yet, document warnings) and not matched to any active order, oldest first, those never called before those already called and interested (a called-and-interested candidate stays on the daily list until matched to an order). Deployed, not-interested and ownerless candidates are never allocated, nobody is allocated twice on the same day, and a candidate with an unfinished (`open`) item is never allocated twice (one open item per candidate), so unfinished work carries over instead. If a recruiter has fewer candidates than the goal they simply get fewer; the Tasks screen and the admin recruiter board show why ("No candidates assigned to this recruiter. Assign candidates from the Data Bank.").

Useful derived facts for a week `[from, to]` and a recruiter (always exclude `status = 'released'`):

* Allocated: rows with `work_date` in range. Completed on the day: `completed_at` local date = `work_date`. Completed on time: `completed_at <= due_at`. Late: `completed_at > due_at` (`overdue_at` not null).
* Carried over: `postponed_at` not null. Days late: local date of `completed_at` (or today, if still open) minus `work_date`.
* Open at the end of the period: `status = 'open'`.
* Time to complete: `completed_at - COALESCE(started_at, allocated_at)`.

## `workflow_daily_goals` (targets, edited by the admin)

`user_id` null = the agency default; a row with a user overrides it for that person. Effective goal = the person's row, else the agency row. Columns: `metric`, `target`, `morning_target` (candidates only; evening share = `target - morning_target`), `updated_by`, `updated_at`.

`metric` values: `candidates` (default 5, morning 3), `forwarded_to_client` (default 2) and `stage_moves` (default 5). A `target` of 0 means no goal for that metric.

## `workflow_daily_goal_progress` (target vs achieved, per day)

Unique on (`user_id`, `goal_date`, `metric`). `target` and `morning_target` are the values in force on that day (a later change to the goals does not rewrite history once the day is over); `actual` and `morning_actual` are the achieved counts. It is **no longer refreshed** (the Tasks screen and the worker stopped writing it); rows from the days the quota was in use remain.

* `candidates`: `actual` = items completed that day (any `work_date`), `morning_actual` = how many of them were morning slots.
* `forwarded_to_client`: stage 2 completions with outcome `Forwarded` recorded that day for the recruiter (`crm_application_stages.owner_id`).
* `stage_moves`: every stage completion recorded that day for the recruiter (not counting reopened attempts).

## Live updates

The Daily work screen refetches on the `tasks`, `candidates` and `applications` live topics (a stage finished or a candidate assigned changes the list). `workflow_daily_work_items` and `workflow_daily_goals` still map to the `tasks` topic; the progress snapshot is ignored (and no longer rewritten by reads).
