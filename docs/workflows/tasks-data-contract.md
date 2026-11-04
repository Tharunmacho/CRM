# Tasks data contract (daily quota, goals and report inputs)

This is the stable read contract for reports built on the Tasks daily-quota feature (migrations `0020_daily_work_quota`, plus `0023_daily_work_call_items` which added `kind` and made `task_id` nullable). Table and column names below will not change without a new migration and an update to this file. Times are stored in UTC; "day" columns are the **agency-timezone** calendar day (`AGENCY_TIMEZONE`, default `Asia/Kolkata`). Only read these tables from reports; the application writes them.

## `workflow_daily_work_items` (one row per candidate handed to a recruiter for a day)

| Column | Meaning |
| --- | --- |
| `id` | Item id. |
| `owner_id` | The recruiter (`auth_users.id`) the candidate was allocated to. |
| `work_date` | Agency day it was allocated. An unfinished item is **postponed** on every later day (see below). |
| `kind` | `stage`: a matched candidate's open application-stage task. `call`: a candidate the recruiter owns who is not matched to an order yet, worked through the initial call (then matching). |
| `task_id`, `application_id`, `candidate_id` | For `stage`: the stage task (`workflow_tasks`) and placement (`crm_applications`; client/country/position come from its order). For `call`: both are null (the candidate's position/country preference is on `crm_candidates`). `candidate_id` is always set. |
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

**Who is in the pool (the allocation rule).** Every working day each active Staff recruiter who is not on leave, a holiday or a weekly off gets up to their goal (default 5) in this priority order: (1) their open application-stage tasks, oldest first; then (2) candidates they **own** with status `new` or `in_process` and not matched to any active order, oldest first, those never called before those already called and interested (a called-and-interested candidate stays on the daily list until matched to an order). Deployed, not-interested and ownerless candidates are never allocated, nobody is allocated twice on the same day, and a candidate with an unfinished (`open`) item is never allocated twice (one open item per candidate), so unfinished work carries over instead. If a recruiter has fewer candidates than the goal they simply get fewer; the Tasks screen and the admin staff board show why ("No candidates assigned to this staff member. Assign candidates from the Data Bank.").

Useful derived facts for a week `[from, to]` and a recruiter (always exclude `status = 'released'`):

* Allocated: rows with `work_date` in range. Completed on the day: `completed_at` local date = `work_date`. Completed on time: `completed_at <= due_at`. Late: `completed_at > due_at` (`overdue_at` not null).
* Carried over: `postponed_at` not null. Days late: local date of `completed_at` (or today, if still open) minus `work_date`.
* Open at the end of the period: `status = 'open'`.
* Time to complete: `completed_at - COALESCE(started_at, allocated_at)`.

## `workflow_daily_goals` (targets, edited by the admin)

`user_id` null = the agency default; a row with a user overrides it for that person. Effective goal = the person's row, else the agency row. Columns: `metric`, `target`, `morning_target` (candidates only; evening share = `target - morning_target`), `updated_by`, `updated_at`.

`metric` values: `candidates` (default 5, morning 3), `forwarded_to_client` (default 2) and `stage_moves` (default 5). A `target` of 0 means no goal for that metric.

## `workflow_daily_goal_progress` (target vs achieved, per day)

Unique on (`user_id`, `goal_date`, `metric`). `target` and `morning_target` are the values in force on that day (a later change to the goals does not rewrite history once the day is over); `actual` and `morning_actual` are the achieved counts. It is refreshed whenever the Tasks screen loads and by the one-minute workflow scan, and the previous day is finalised on the first scan of the next day. Weekly reports should prefer this table for goal attainment.

* `candidates`: `actual` = items completed that day (any `work_date`), `morning_actual` = how many of them were morning slots.
* `forwarded_to_client`: stage 2 completions with outcome `Forwarded` recorded that day for the recruiter (`crm_application_stages.owner_id`).
* `stage_moves`: every stage completion recorded that day for the recruiter (not counting reopened attempts).

## Live updates

All three tables feed the `tasks` live topic except the progress snapshot (ignored: it is rewritten by reads). Screens refetch on `tasks` changes and tick timers locally.
