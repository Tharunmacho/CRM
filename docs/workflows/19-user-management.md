# User accounts access and assignment availability

User Management lets Admin control who can enter the CRM, what role they hold and whether Staff receive new candidates. Account changes must preserve recruitment work and a usable administrator account. Source: PRD page 6. Requirements: **USER-01, USER-02, USER-03**.

## Roles and preparation

Only an active Admin can open User Management and use its administration APIs. Manager supervision in Staff Directory does not grant account creation, password resets or deactivation. Prepare the person's name, unique email, phone, role and initial password, and decide whether an active Staff account should receive automatic candidates.

Before deactivating or changing a Staff account to another role, inspect their owned candidates and open tasks and identify a replacement. The replacement must be another active Staff member who is not on manual or approved attendance leave. Paused automatic allocation does not prevent a deliberate manual transfer to that otherwise eligible person.

## Add and edit an account

1. Open **User Management**. Inspect Team members, Active accounts, In rotation and On leave. Search the directory by name, email or phone. These counts describe accounts and backend availability; they are not attendance check-in counts.
2. Click **Add user**. Enter name, email, phone and role, choose Active as appropriate, and enter the same initial password twice. Passwords must be 12–128 characters. Share the initial password through your organization's normal private method; the CRM does not send an invitation email in this flow.
3. For a Staff account, choose whether they receive auto-assigned candidates and whether the manual leave flag applies. Admin and Manager do not receive automatic candidates; the backend clears Staff-only assignment flags for those roles.
4. Save, then verify the new row and allocation status. An inactive account cannot sign in or join automatic rotation. A successful account save followed by a failed directory reload reports that it saved; reload before adding the account again.
5. Open the row's actions and choose **Edit account** to change details, role or active state. A duplicate email, invalid fields, protected Admin change or missing replacement is rejected with a visible error and the form remains available.

## Availability and recruitment handoff

The directory exposes **Receives auto-assigned candidates** and **Manual leave** for appropriate Staff accounts. Automatic assignment uses active Staff with auto-assignment enabled who are absent from both manual and approved attendance leave. The availability panel shows the agency date/timezone and supports **Refresh availability**.

Turning off automatic allocation stops new round-robin assignments; it does not unassign existing candidates. Setting manual leave also excludes new allocation and manual reassignment targets, but does not silently redistribute existing work. Approved attendance leave is separate: clearing Manual leave does not cancel an approved leave record. Use [Staff Directory](11-staff-assignment-directory.md) and the attendance/cover workflow to inspect and redistribute work when needed.

## Deactivate or remove a Staff role

1. Open **Deactivate account**, or edit the Staff role/active state. The form displays the candidates and open tasks that need transfer.
2. Choose **Reassign work to** from the current backend availability list. The current account cannot be its own replacement. Availability loading/failure prevents confirmation; **Refresh availability** makes the choice reviewable again.
3. If no eligible replacement exists, keep the original account unchanged and resolve staffing/availability first. When the account owns no candidates and no open tasks, no replacement is required.
4. Confirm the account change. Deactivation, required candidate/open-task transfer, session revocation and the account audit entry commit together. A failure must not leave the user inactive while their work is stranded halfway through a transfer.
5. Verify the inactive account, replacement owner and Staff Directory counts. The replacement and active Admin/Manager accounts receive candidate-linked assignment notices in the same transaction. Open a transferred candidate and task: completed stages/tasks, documents, remarks and original SLA due times remain intact. Completed and cancelled tasks do not become new open work or acquire fabricated completion dates.
6. Review account audit history and candidate assignment history for the actor, old/new ownership and transfer totals. Account transfers preserve historical assignment events for reporting and missing-update checks.

Admin cannot deactivate their own account or remove their own Admin role. The last active administrator must remain active and retain Admin. Changing role, email or activation revokes affected login sessions so old access cannot continue under stale account details. Reactivating an account is an edit; review the Staff assignment toggle explicitly rather than assuming earlier automatic allocation settings were restored.

## Password reset session sign-out and audit history

Use **Reset password** to set and confirm a new password. The server hashes it and revokes existing sessions; the account retains its current active/inactive state. Resetting an inactive account's password does not reactivate it.

Use **Sign out all sessions** when access must stop on current devices without changing the password or ownership. The person can sign in again if active and still holds valid credentials. This is not account deactivation or a candidate transfer. Presence sessions are closed through the activity service; do not treat revoking a login session as an attendance checkout or payroll decision.

Use **View audit history** to inspect actual actions performed by that user and account-administration changes targeting them. Older rows load by page. The history shows time, action, target and available change/transfer details. Passwords are excluded from responses and audit metadata. Loading, no-history and failure/retry states must not be mistaken for missing user activity; an invalid history cursor can be resolved by reopening/reloading the history.

## Screen and backend contract

The backend rechecks that the acting Admin is active while locking the account directory, using the same assignment coordination used by intake. This prevents a concurrent new candidate from being allocated to a person midway through deactivation. It validates unique normalized email, roles and required fields, and protects self/last-Admin access independently of hidden UI controls.

Candidate and open-task transfer uses locked records, preserves task deadlines and records old/new owners. Approved/manual leave is checked again on the server, so a replacement that became unavailable after the dialog opened can be rejected safely. A stale browser list is never authority to override that check.

The screen confirms current accounts and work on entry and offers **Refresh directory**. Loading/failed reads hide outdated account controls and provide retry. It distinguishes no search matches from no eligible allocation staff. Availability totals show an unavailable/loading state instead of a guessed zero when the endpoint has not returned. Forms retain failures, show busy state and allow Cancel before submission. Account security changes are explicit dialog actions; viewing a row does not trigger them.

## Recovery and handoffs

If a save fails, correct the visible validation or retry after restoring connectivity. If the response is uncertain, reload the account and inspect history before repeating deactivation, password reset or session revocation. A saved-but-refresh-failed message means the account operation already committed.

After deactivation, verify that the old login is refused, existing sessions can no longer read protected data, and the replacement sees the transferred candidate/task queue. Use [Tasks](16-tasks-sla-daily-updates.md) to review preserved deadlines and [Reports](17-reports.md) for historical ownership/event attribution. Use the [attendance guide](../attendance.md) for attendance policy and browser-presence meaning.

## Acceptance checklist

- [ ] Create and edit Admin, Manager and Staff accounts; validate required fields and duplicate-email rejection.
- [ ] Confirm only active eligible Staff enter automatic rotation; verify paused, manual-leave and approved-leave cases.
- [ ] Confirm clearing manual leave does not erase approved attendance leave.
- [ ] Deactivate a recruiter with candidates, open tasks and completed work; verify atomic replacement transfer and unchanged deadlines/history.
- [ ] Reject a missing, inactive, on-leave, non-Staff or self replacement; allow an active manually chosen Staff member whose automatic allocation is paused.
- [ ] Simulate a transaction failure and verify neither deactivation, partial transfer nor its notifications commit; repeating an unchanged account save does not duplicate assignment notices.
- [ ] Block self-deactivation, removing one's own Admin role and removing the last active Admin.
- [ ] Reset a password and force logout; verify session revocation, no credential disclosure and unchanged ownership.
- [ ] Verify role/email changes revoke old sessions and an inactive account cannot sign in.
- [ ] Load real audit rows and older pages, including a recoverable history-loading failure.
- [ ] Attempt these APIs as Manager and Staff and verify access is denied.

## Current implementation and remaining acceptance

Account create/edit/deactivation, assignment availability, atomic transfer, credential/session actions and paginated audit history are implemented with isolated role, rollback and preservation tests. Production review should use designated test accounts to confirm real session revocation and recipient workflow visibility. Automatic invitation emails, password-recovery email and permanent staff-account deletion are not part of this Admin workflow.

Implementation: [User Management screen](../../frontend/src/screens/users/users.tsx), [user API](../../backend/app/api/routers/users.py), [account transactions](../../backend/app/services/user_admin.py), [staff availability](../../backend/app/services/attendance_availability.py).
