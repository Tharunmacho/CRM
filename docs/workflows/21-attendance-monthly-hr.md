# Monthly attendance leave approvals and duty planning

This module applies the reference attendance rules explicitly approved by the user and connects approved leave to recruitment availability and work cover. Requirements: **ATT-03, ATT-04, ATT-05, ATT-06**. These are approved extensions; the original recruitment PDF did not define this HR policy. The reference salary/payroll/payslip module is not implemented.

## Roles and preparation

Staff sees their own month, daily record, requests and schedule. Manager sees Staff plus their own record; they can decide eligible Staff requests and manage Staff scheduling/regularisation, but cannot approve or correct themselves. Admin supervises both employee roles, fills the reference financial-approval role, and alone can grant/replace direct paid/unpaid calendar leave, override a status or correct raw sessions. No employee approves their own request.

Employee accounts and agency timezone must be configured. Confirm the effective shift and calendar before reviewing totals. The complete runtime must be running for scheduled leave-cover transfers; opening Attendance or Staff Directory does not perform a transfer.

## Approved calculation rules

| Rule | Current calculation |
| --- | --- |
| Default shift | 10:00 to 19:00 with a 60-minute break, giving 480 payable minutes. Effective-dated timings and one-date duty plans can replace the default. |
| Flexible coverage | A 60-minute window on each side of the shift. Coverage follows first check-in to last checkout, less the shift break; approved early check-in can permit earlier coverage. Browser activity does not set payable time. |
| Monthly grace | 60 minutes, allocated in date order. The screen warns after 45 minutes used. Missing days and unpaid leave do not consume this allowance. |
| Paid permissions | At most two qualifying approved permission occasions per month. Admin can explicitly allow another late/early-exit occasion with a reason; it still uses the remaining 60-minute allowance. |
| Official duty and WFH | An approved official-duty or work-from-home day receives full required-day coverage without needing office punches. WFH approval must precede shift start. |
| Missing punches | Provisional until shift end plus two days; an unresolved missing punch then makes the full duty unpaid. |
| Paid leave | One approved paid day per calendar month. A further paid-leave approval becomes unpaid leave, and the conversion is visible. |
| Weekly off | Sunday by default. An employee may choose Friday for this week or the next three weeks until Thursday 23:59:59 in the agency timezone. A separately approved rotational off locks that week's choice. |
| Extra overtime | Only explicitly approved extra OT offsets eligible partial-day late/short time in that month. It does not cancel full-day absence or unpaid leave. Unused OT is shown separately and does not create cash payment. |
| Historical coverage | Tracking begins with the earliest recorded attendance, request, calendar entry or adjustment. Future days and days before tracking began are not charged. Inactive employees retain history; accrual stops at the recorded deactivation date or the available legacy account update date. |

An overnight shift belongs to its check-in date for monthly calculation. The daily activity screen instead clips a session to each day's overlap. Daily unpaid rows show time before the monthly OT offset; the month shows the offset and final unpaid balance separately.

Late/early minutes remain visible even when flexible coverage completes the required day. For example, a default 10:30–19:30 shift supplies eight covered hours after the break, so the late arrival does not itself create a half-hour unpaid deduction. An approved permission only covers the actual shortfall; extra time does not automatically become approved OT.

## User journey

### Review the month

1. Open **Attendance**, choose the month and employee scope. Manager can choose **Team** or **My attendance**; Staff stays in their own scope.
2. Review **Team overview** or **My month**. Supervisor cards show active staff, pending permissions, pending extra OT and unpaid time. Employee cards show present days, worked time, unpaid time and approved overtime.
3. Read the policy strip before interpreting the totals. Open an employee's **View record**, or their request count, to inspect the underlying days or requests.
4. In **Employee record** / **Daily records**, inspect result, first/last clock times, required shift, late/early minutes, allowance used and unpaid minutes. A provisional day includes its correction deadline.
5. Open **Overtime and unpaid time breakdown** to compare unpaid time before OT, OT applied, final unpaid balance and unused OT.
6. Use **View sessions & activity** on a day to inspect its actual sessions and browser estimates without changing the monthly calculation.

Attendance percentage uses credited days out of scheduled days, not a browser-online percentage. Weekly offs/holidays are excluded from scheduled days; paid leave and qualifying attendance/permission statuses count as credited. A provisional day with check-in is credited, and a month with no scheduled days has no percentage. Absence badges include unpaid leave; late/incomplete badges follow the displayed day classification.

## Request and approval journey

1. Staff or Manager selects **New request**, then chooses date and request type: late arrival, early exit, early check-in, official duty, work from home, paid leave, unpaid leave, rotational weekly off or extra overtime.
2. Enter requested minutes for timed permissions/OT and a reason. For leave, choose an eligible Staff colleague when one is available. Submit once; the form keeps a stable request key during retries.
3. Review the saved request under **My requests**. A leave request with a nominated colleague starts as **Awaiting cover**; other eligible requests enter the approval queue.
4. The nominated colleague selects **Accept cover** or **Decline cover** and can enter a note. Acceptance moves the request to approvers. After decline, the requester selects **Choose cover** to nominate another eligible colleague.
5. Manager/Admin opens **Approvals**, inspects the date, reason, requested minutes, cover response and outstanding stages, then selects **Approve** or **Reject** with a decision reason.
6. Ordinary permissions need managerial authority. Leave and extra OT require managerial and financial stages; Admin fills the financial role and may satisfy both. Manager requests therefore require Admin. The request remains pending until every required stage is approved.
7. After the final approval, review the calendar/month again. A paid-leave allowance already consumed converts the new leave to unpaid leave; the request explicitly displays that conversion. A rejection remains visible with its actor and reason.

Late arrival and early check-in must be requested before shift start. Early exit must be requested before leaving. Work from home must be both requested and approved before shift start. Timed requests require positive minutes. The server checks these rules, current role/employee eligibility and conflicting leave requests; changing the browser's clock cannot bypass server time.

## Weekly off and roster journey

1. In the employee's own view, open **Weekly off**. Choose Sunday or Friday for an editable week. The screen shows the deadline or locked/approved-rotation state; it disables choices after the deadline.
2. For an exceptional rotational off, submit a **Rotational weekly off** request for the intended date and follow approval. Approval fixes that week's selected day.
3. An authorized supervisor opens **Duty roster** and selects **Plan duty** for an employee/date. Enter shift start/end, break and reason. A date-specific plan overrides a normal weekly off/holiday, but not approved leave.
4. Use **Set timings** for a recurring schedule from an effective date. Earlier records retain the shift applicable to their date.
5. Use **Set calendar day** for holiday/weekly-off entries or, as Admin, paid/unpaid leave. Manager cannot replace an existing approved leave entry with a different calendar status.
6. To remove a duty plan, inspect its employee/date, select **Remove**, and confirm the dialog. That date resumes normal shift/weekly-off rules. Cancel leaves it unchanged.

Reference-company holidays and individual employee exceptions were not copied. Record Care India's calendar and timings explicitly.

## Corrections journey

1. Open the employee's daily record and choose **Correct record** if the role permits it. Select the employee/date and correction type.
2. **Add a missing punch** supplies the check-in/out action and a past time with timezone. The punch must belong to the selected day or its allowed overnight shift.
3. **Recover unpaid minutes** records positive recovery minutes and a reason while retaining original raw clock evidence.
4. Admin may **Override attendance status** with a reason. This changes the classification only; it does not by itself erase unpaid minutes. Use the appropriate permission/calendar/recovery action to alter the calculation.
5. Save, then inspect the recomputed day and **More → Correction history**. Actor, reason and detail remain visible. Editing the raw clock session itself follows the separate [clock correction workflow](20-attendance-clock-activity.md).

## Recruitment leave cover journey

1. Approved paid/unpaid calendar leave makes that Staff member ineligible for new allocation and manual reassignment on the leave date. A pending request does not make them unavailable.
2. On the leave date, the supervised worker checks that approval, accepted cover and leave calendar are still valid and that cover remains eligible.
3. It transfers the absent employee's eligible uncontacted candidates and their open tasks to the accepted Staff colleague. Original deadlines remain unchanged; candidate/task history records the handover.
4. On the following day, the worker returns unfinished, unchanged work only when the original owner remains active Staff and is not manually on leave. Completed work and later human reassignment remain intact.
5. Consecutive leave days settle the previous handover before starting the next. A missed leave window is marked settled without retroactively transferring work. A durable ledger prevents repeated sweeps from transferring the same work twice.

Manager leave can be recorded, but does not invent Manager-owned recruitment work. Verify the actual handover state and ownership in [Staff Directory](11-staff-assignment-directory.md), the candidate profile and task history rather than assuming that approval alone has already moved candidates.

## Screen and backend contract

Monthly records, requests, settings and corrections come from separate scoped attendance endpoints and load together for the selected month/employee. Loading and error states are explicit. Decision buttons depend on backend-calculated permitted actions; hidden buttons do not replace API authorization. The request shows each required approval stage, actor, time, reason and cover state.

Writes lock the assignment cursor, relevant employees and request/work records consistently with recruitment assignment. A transaction commits a decision together with calendar effects, audit and notifications; a partial decision must not leave the calendar approved independently. Paid-leave quota is checked against existing calendar entries. Cover handover and return use durable records and ownership checks, not a page-load side effect.

## Recovery and cancellation

Cancel an unsaved form/decision to leave records unchanged. During save, repeat submission and closure are disabled; a server failure preserves the form for correction/retry. Reusing a saved request key with changed content produces a conflict and directs the user to review history before creating another request. After an uncertain save, refresh history before opening a fresh form that would use a new key.

An already-decided request, unavailable cover, missed cutoff or unauthorized self-action returns an actionable error. Refresh before another decision because another approver may have changed the state. The current UI has no general requester cancellation/withdrawal action after submission; do not promise one. An authorized approver can reject a pending request, while already-effective calendar mistakes require the appropriate authorized correction.

## Acceptance checklist

- [ ] Default full shift, flex-window coverage, late arrival, early exit and overnight work calculate from punches, independently of browser activity.
- [ ] Monthly grace is allocated chronologically, warns at 45 minutes and never exceeds 60; the two-occasion limit and reasoned Admin exception behave as described.
- [ ] Missing punches remain provisional until the deadline, then become unpaid unless corrected; future/untracked days do not create invented absences.
- [ ] Staff permission, Manager request and attempted self-approval follow the correct authority and stage rules.
- [ ] WFH/early/late timing restrictions are enforced by server time.
- [ ] First paid leave remains paid; a second approved paid-leave request visibly converts to unpaid.
- [ ] Cover acceptance, decline and renomination work; unavailable colleagues are rejected.
- [ ] Friday/Sunday choice locks at the Thursday cutoff; approved rotation stays fixed.
- [ ] Duty plan overrides weekly off/holiday while preserving approved leave; effective-dated timing preserves earlier schedules.
- [ ] Approved OT offsets partial-day short time only; full absence/unpaid leave and unused OT remain separate.
- [ ] Added punch, time recovery and label override retain actor/reason and have their distinct effects.
- [ ] Worker handover and return preserve SLA/history, skip completed/later-reassigned work, and remain safe on restart/retry.
- [ ] Desktop/mobile totals and employee/day drill-downs use the same scoped records.

## Current implementation and remaining acceptance

The approved monthly rules, requests/stages, leave quota, scheduling, regularisation and recruitment cover are implemented and covered by isolated PostgreSQL tests. Local browser checks cover the connected attendance screens; production acceptance must still exercise real schedules, elapsed cutoffs, colleague approval and supervised worker handover/return. Salary, payroll, payslips, cash OT payment and general request withdrawal are not implemented.

Implementation: [attendance workspace](../../frontend/src/features/attendance/components/attendance-workspace.tsx), [forms](../../frontend/src/features/attendance/components/attendance-forms.tsx), [HR service](../../backend/app/services/attendance_hr.py), [policy calculation](../../backend/app/services/attendance_policy.py). Full policy and role adaptation: [attendance guide](../attendance.md).
