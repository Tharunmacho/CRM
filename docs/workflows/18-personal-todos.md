# Personal reminders and to do items

The To-do List lets Admin and Manager keep private follow-ups such as calling a client, arranging a meeting or checking a document. It is separate from recruitment tasks: completing a personal item does not complete a candidate stage or stop a recruitment SLA. Source: PRD page 5. Requirements: **TODO-01, TODO-02**.

## Roles and preparation

Admin and Manager each have their own list. Staff do not have this menu or permission to create and update these items. One administrator cannot use this screen to browse another person's private reminders. Choose a due date/time and, if useful, identify an existing candidate, client or order to link.

The form uses the browser's local date/time and sends a timestamp with its timezone to the server. Check the displayed local time when working from a device in a different timezone. Reminder delivery requires the workflow worker; the visible bell also depends on the signed-in browser fetching notifications.

## User journey

1. Open **To-do List** or the personal **My to-dos** card on the Dashboard. Read the Today, Upcoming, Overdue and Completed totals, then choose the corresponding tab.
2. Click **Add item**. Enter a meaningful title, due date/time, priority and optional notes. Choose **No link**, or select a candidate, client or order. For example: “Call client about Friday interview slot,” due tomorrow at 10:00, linked to that order.
3. Click **Add item** in the dialog. The title and due time are required. After a successful save, the item appears in your own due-time-sorted list and appropriate dashboard view. Cancel before saving to leave the list unchanged.
4. Open a linked record when it is time to act. The link helps reach context; it does not grant permission beyond your normal role or assign the personal item to another user.
5. Use the edit control to change title, notes, date/time, priority or record link, then **Save changes**. Changing the due time resets the reminder's delivery marker so the revised schedule can generate a reminder.
6. Tick the item when finished. It moves to **Done** and contributes to the completion display. Untick a completed item to reopen it. This manual completion applies only to the personal list; recruitment tasks close through their actual call/stage actions.
7. Use the delete control only when the item is no longer needed. Review the item in the confirmation dialog; **Keep item** cancels without changes and **Delete item** removes it. Linked recruitment records remain available. There is no recycle-bin or undo flow.
8. When an open item becomes due, inspect the bell reminder. Follow it to the linked record, or to the To-do List if there is no link. Marking that notification read does not tick the item as done.

## Day buckets and reminder timing

**Today** contains unfinished items due later today. **Upcoming** contains unfinished items on later dates. An unfinished item whose due time has passed moves to **Overdue**, including an item due earlier today. **Done** contains completed items regardless of their former due date. Overdue timestamps are highlighted so they can be distinguished from priority badges.

The worker checks due personal items independently every **30 seconds**. It stores a notification and its delivery marker together. The visible browser bell polls every **30 seconds** while the application is visible, so the reminder is not guaranteed to appear at the exact due second. Network delay, a sleeping/hidden browser or a stopped worker can add delay. The list checks its deadline buckets every 30 seconds while visible and immediately on returning to the tab; a due-time transition does not depend on editing an item.

An already-delivered item does not receive repeated alerts merely because it remains overdue. Reopening it without changing its due time does not clear the existing reminder marker. If a new scheduled reminder is needed, edit the due time. A completed item is excluded from due-item scanning.

## Screen and backend contract

The server takes ownership from the signed-in account; the browser cannot choose a different owner. It validates a nonblank title of at most 300 characters, a timezone-bearing due timestamp, a supported priority and a CRM-local link. Private items are scoped in loaded workspace data, and update requests check the same owner again. The selected UI links come from existing candidate/client/order records.

Creates, edits, completion/reopen and deletion have actor/time audit entries. Updates lock the item. Concurrent reminder workers skip rows another worker is handling; a failed reminder transaction leaves both the notification and delivery marker uncommitted so a later scan can retry.

The screen confirms current workspace data on entry and offers **Refresh list**. Loading and failed reads are separate from a genuinely empty list. Empty tabs explain that no items match and offer Add item where appropriate. Save and row-action errors are visible and do not intentionally discard the form. Busy controls reduce repeated clicks. If a write saved but refreshing the list failed, the screen reports that the save succeeded and asks for a refresh; the old list is not evidence that the write was rejected.

## Recovery and handoffs

After an uncertain create response, refresh the personal list before submitting the same title again. The create endpoint has no general duplicate-title/idempotency guarantee. Completion/reopen now sends the intended done state, so retrying the same state does not reverse the previous successful request or duplicate its audit event. Refresh after a connection problem to confirm the saved result.

If no reminder arrives, check the due time, whether the item was completed or previously reminded, worker health and the browser bell refresh. Do not create extra copies to compensate for a display delay. This module does not send email, WhatsApp or operating-system reminders.

Linked candidate/client/order pages retain their normal workflow. Recruitment follow-ups that must obey task ownership, daily remarks and 48-hour escalation belong in [Tasks](16-tasks-sla-daily-updates.md), not in a substitute private checkbox.

## Acceptance checklist

- [ ] Create an item with a title, notes, due time, priority and candidate/client/order link; reopen it and verify persistence.
- [ ] Test Today, Upcoming, earlier-today Overdue and Done with known times.
- [ ] Sign in as another Admin/Manager and verify the item does not appear; reject another owner's update request.
- [ ] Confirm Staff cannot open the module or create/update an item through the API.
- [ ] Complete and reopen an item; verify no candidate/task/stage status changes.
- [ ] Let an open item become due and allow the worker scan plus bell-poll delay; verify one private notification.
- [ ] Change the due time after a reminder and verify the new schedule can notify; reopening alone should not duplicate the original alert.
- [ ] Fail a save or list refresh, retain entered details and reconcile saved state before retrying a create; replay explicit completion and confirm it stays completed.
- [ ] Cancel deletion and verify the item remains, then delete it and confirm removal from the personal list/dashboard without deleting its linked record.

## Current implementation and remaining acceptance

Private create/edit/complete/reopen/delete, optional record links, dashboard items and stored bell reminders are implemented. Isolated tests cover due-item cadence, repeat safety and transaction rollback. Production acceptance requires the worker and real signed-in browser polling to operate together. Instant push, repeating reminders, shared/team personal lists and deletion undo are not implemented by this workflow.

Implementation: [To-do screen](../../frontend/src/screens/todos/todo.tsx), [private-item API](../../backend/app/api/routers/data.py), [reminder delivery](../../backend/app/services/workflow_scheduler.py), [notification polling](../../frontend/src/shared/state/crm-store.tsx).
