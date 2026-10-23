# Access navigation and audit

This module establishes who can see a record, who can change it and how a change is traced. Review it before approving any business workflow. Source: original PRD pages 1–2. Requirements: **NAV-01, NAV-02, AUD-01**.

## Roles and access

| Action | Admin | Manager | Staff |
| --- | --- | --- | --- |
| Read Data Bank and candidate summaries | All | All | All |
| Create clients, designations and orders; map or unmap | Yes | Yes | Yes |
| Correct candidates, record calls, work stages | Any candidate | Any candidate | Own assigned candidates |
| Receive automatic candidate allocation | No | No | Eligible Staff only |
| Reassign candidates and open tasks | Yes | Yes | No |
| Connect sources; manage users; reopen stages; reset SLA | Yes | No | No |
| Reports | All staff | All staff | Own numbers |
| Private personal to-dos | Own | Own | No access |
| Staff Directory | Team | Team | No access |
| Attendance | Employee oversight | Own and Staff oversight | Own |

Staff Directory and attendance are later approved additions. Private CRM mail drafts remain private to their author even from an Admin; shared provider folders follow the connected mailbox model. Candidate discovery does not confer permission to edit another recruiter's work.

## User journey

1. Open the CRM and sign in with an active account. A failed login shows an error and allows correction.
2. Land on Dashboard. The sidebar presents only permitted sections; direct links must enforce the same permissions.
3. Open Data Bank and inspect a candidate owned by another Staff member. Shared profile information remains readable, while restricted correction and stage actions are unavailable.
4. Make an authorized edit. The saved action records the actor, time, target and relevant change details.
5. Log out. Reopening a protected route requires authentication. Admin force logout or account deactivation also invalidates the old session.

The original sidebar specified Dashboard, Inbox, Clients, Jobs / Positions, Add Candidate, Data Bank, Tasks, To-do List, Reports and User Management. Current navigation also includes Companies, WhatsApp, Mapped Candidates, Attendance and Staff Directory. These extensions are documented rather than counted as literal ten-item parity.

## Screen and backend contract

Login needs a pending state, field/error feedback and a safe return to the requested route. Restricted routes need an understandable denied state. Buttons cannot be the only protection: APIs independently verify current role, active session and candidate ownership. Account changes that revoke sessions must affect an already-open browser.

Every create, edit, stage transition, reassignment and upload records audit information. Audit entries must not contain passwords or mailbox secrets. Source intake identifies system activity rather than inventing a human actor. A failed transaction must not leave an audit entry claiming a successful change.

## Recovery and handoffs

On session expiry, sign in again before retrying. A saved action followed by a failed refresh should offer reload, not blindly repeat the write. A permissions conflict after reassignment should preserve unsaved text where possible and explain that access changed. Role settings feed [User Management](19-user-management.md); source permissions feed [Zoho](08-zoho-inbox.md) and [WhatsApp](09-whatsapp.md).

## Acceptance checklist

- [ ] Test the same protected URL with all three roles, including a direct API request.
- [ ] Staff can discover another recruiter's candidate but cannot save its stage or correction.
- [ ] Admin and Manager private to-dos remain separate; Staff cannot access either list.
- [ ] A successful edit and a failed edit produce the correct audit outcome.
- [ ] Logout, force logout and deactivation invalidate existing sessions.
- [ ] Confirm the additional navigation and contact/company model as deliberate product choices.

## Current implementation and remaining acceptance

Workspace startup authenticates and returns the compact desk in one request. Anonymous visits return no CRM collections, and revoked sessions are rechecked against the database. Route-entry reads share a recent snapshot for up to five seconds; explicit refreshes and successful writes force a new read. Slow startup has a visible retry action. Full extracted resume text is requested only on the candidate profile.

Role gates, persistent sessions, owner checks and audit paths are implemented with targeted tests. Production cookie/domain behavior and account acceptance remain deployment checks. The extra navigation is a documented difference from the PDF, not an unfinished hidden screen.

Implementation: [routes](../../frontend/src/app/routes.tsx), [application shell](../../frontend/src/app/layout/app-shell.tsx), [authentication](../../backend/app/api/routers/auth.py), [server permissions](../../backend/app/core/deps.py). Evidence boundaries: [integration audit](../integration-audit.md).
