# Lead sheet

The agency posts ads on social media. People who are interested in jobs abroad answer a form and the platform gives the
agency a sheet of those answers. The Lead sheet stores that file in the CRM exactly once per person, lets the team call every
lead, collect an updated resume and turn the good ones into Data Bank candidates. Technical details:
[lead sheet contract](lead-sheet-contract.md). (This guide is number 23; 18 is the personal to-do guide.)

## Roles and preparation

* **Admin and Manager** upload sheets, assign leads (one by one, in bulk or automatically), export, and can work any lead.
* **Staff** see the whole sheet but can only call, edit and convert the leads **assigned to them**, and only **after finishing today's
  daily tasks** (the Tasks > Daily work quota plus anything postponed). Until then the screen shows how many tasks are left and
  the server refuses the work. Reading is never blocked. A staff member with nothing allocated (day off, leave, all done) is free to work.

## Upload a sheet

1. Open **Lead sheet** > **Upload**. Choose the file exactly as the platform gave it: Meta's `.csv` (it is really a tab separated,
   UTF-16 file; that is fine), a normal CSV (comma or semicolon) or an Excel `.xlsx`. Old `.xls` files must be saved as `.xlsx` or `.csv` first.
2. The page first shows a **preview** (nothing is saved yet): the detected format, how each column of the file was understood
   (the columns it did not understand are kept anyway), how many people are new, how many are duplicates and how many rows are unusable,
   and the first rows as they will be stored. Fix a wrong column by choosing the right field for it. If the very same file was uploaded before, a warning says when.
3. Optionally choose who gets the new leads (a person, or **automatic** to spread them evenly over the staff who are available) and a campaign name for sheets that have none.
4. Confirm. The leads are stored in one step. The result lists the totals, and the duplicate and unusable rows with the reason.

## Duplicates: nobody is stored twice

You can upload as often as you like. If the first sheet had 1,000 people and the next sheet has the same 1,000 plus 500 new people, only the 500 are added.
A row is treated as the same person when, in this order: the platform lead id is the same, or the phone number is the same (formatting, `+91`, spaces and a leading `0` do not matter: the last 10 digits are compared),
or the e-mail is the same. It does not matter whether the earlier row came from the same file or from an old upload. A duplicate is ignored and listed (with the row and the lead it matched); it never changes
what the team has already recorded (status, owner, notes, calls). Optionally the upload can fill details the stored lead is still missing (never phone, e-mail or any CRM data).
A lead whose phone or e-mail is already a Data Bank candidate is stored but flagged, so the team does not chase an existing candidate by mistake.
A wrong upload can be undone by an admin from the upload history, as long as nobody has started working those leads.

## Work the leads

1. Open **Lead sheet**. The table shows name, phone, job, state, campaign, status, owner, last call and follow-up. Type in the search box (comma-separated words must all match, e.g. `kerala, chef, asha`) or use the filters
   (status, campaign, job, country, owner, date, has resume, already a candidate, follow-up due).
2. Open a lead and call. Record the outcome: answered, interested, not interested, no answer or wrong number, add remarks and optionally the next follow-up. The status, call count and history update.
3. Ask an interested lead for an updated resume. Set **Resume requested**. When the person sends it, upload it to the lead; the status becomes **Resume received**.
4. In **Convert to candidate**, choose the updated resume or use the one already saved, then press **Check resume**. Wait for a verified match before converting. A wrong person's file, several people's resumes, incomplete reading or uncertain identity blocks conversion and creates no candidate. Changing the selected file or candidate name requires another check.
   **Convert to candidate** then creates the Data Bank candidate and attaches the checked resume together, reusing its saved reading to fill permitted fields. It does not run the paid extraction again. Conversion without a resume remains available when none is selected or saved.
   If the phone or e-mail already belongs to a candidate, the CRM shows that candidate instead of creating a second one; **Link** connects the lead to it.
5. Leads that do not go anywhere are set to **Not interested**, **Invalid** or **Wrong number**; nothing is deleted.

## Statuses

New, Contacted, Interested, Resume requested, Resume received, Converted, Not interested, Invalid, Wrong number.

## Failure recovery

* A file that cannot be read (old `.xls`, macro workbook, password protected, too big, more than 100,000 rows) is refused with a message; nothing is stored.
* Two people uploading at the same time cannot create duplicates.
* An upload that stopped half way stores nothing (it is one transaction); upload it again.
* "Finish today's tasks first": complete or release the open daily items and the gate lifts at once (the screen refreshes by itself).
* A failed or uncertain resume check leaves the lead intact. Choose the correct, clearer file and check again; there is no bypass for an unchecked attached resume.

## Correct a mistaken conversion

An **admin** can choose **Move back to Lead** from a converted lead's menu or drawer. The confirmation identifies the candidate that will be removed and the lead status that will be restored; a reason is required and kept in the history. The original lead, its files and its history remain.

This is available only for an unused candidate created by that lead. Existing/shared candidates, any job application (including closed history), invoices or payments, ticket/invoice documents, other inbox/source links, recruiter notes or work already started block reversal. Only the untouched automatic initial-call task may be removed. The server checks again when saving; retrying the same saved reversal does not remove another candidate.

## The table

* One table that always fits its box: no sideways scroll from a 1100px window up (cards below). Several sheet fields share a cell (name over date of
  birth and gender, phone with WhatsApp and copy over the e-mail, city over state, restaurant over total years, campaign over form, position over
  country, status over assignee and follow-up).
* Column widths are the least a column needs to show its content in full. When they do not add up the table first tightens its spacing, then hides
  the least important columns; the **Columns** menu (grouped, searchable, Show all / Default / Fit to screen, spacing, opt-in Wide table) lists every
  field of the sheet plus the custom questions of the uploaded forms, remembered per screen. The arrow at the start of a row opens every field with
  its own heading; the toolbar expands or collapses all.
* Headings come from one config (`LEAD_HEADINGS`, `LEAD_FIELD_DEFS`); the pager has 10 / 25 / 50 / 100 / 250 / All (default from the screen height,
  remembered), numbered pages, Go to page; the count line and the hint under the table are worked out from the role, filters and data.
* A render error in the table or the drawer shows an inline message with Try again and leaves the rest of the screen working.
* Downloads: **Download import report (Excel)** (CSV as the alternative) and the lead **Export** (Excel/CSV with the custom question columns).

## Checks

* [ ] Upload the sample Meta file; preview shows the format and all columns mapped; confirm stores every person once.
* [ ] Upload the same file again: 0 new, every row listed as a duplicate.
* [ ] Upload a longer file: only the new people are added.
* [ ] As staff with open daily tasks: lead calls and edits are refused with the daily tasks message; as admin they work.
* [ ] Staff cannot upload, assign or export; staff cannot work another person's lead.
* [ ] Check the correct resume, then convert: one candidate and its checked file appear together, using the saved reading.
* [ ] A wrong-person, changed or unverified resume cannot create a candidate; no-resume conversion still works.
* [ ] As admin, reverse an unused conversion with a reason; the lead and source resume remain. A candidate with recruitment or financial history cannot be reversed.

## Campaigns tab, follow-up reminders, "In Data Bank" found later, WhatsApp messages

* **Campaigns tab** (next to Leads and Uploads): one row per ad campaign with its leads, new today and in 7 days, how many are assigned and contacted, interested, resumes in,
  converted with a small bar for the conversion rate, the last upload, the Facebook / Instagram split, the top jobs and the countries people want. The last row totals them.
  Click a heading to sort; click a row to see that campaign's leads on the Leads tab; the arrow shows the campaign's forms. On a narrow screen the rows become cards. Managers and admins see
  everything, staff see only the leads assigned to them.
* **Follow-up reminders**: when you set a follow-up date on a lead (in the lead, or when logging a call), the owner gets a to-do "Follow up lead: <name>" at 09:00 that day (a reminder in the notification bell when it is due; the **To-do** page itself is open to admins and managers),
  linking back to the lead. Moving or clearing the date moves or removes it; converting the lead, marking it not interested, invalid or wrong number, deleting it, or reassigning it
  (the to-do goes to the new owner) is followed too. The **Follow-ups due** card counts your own leads whose follow-up is today or overdue (click it to list them); overdue dates are red in the table.
* **Already in the Data Bank, found later**: if a candidate with the same phone or e-mail is added after the lead was uploaded, the lead gets the "In Data Bank" flag at once, and the
  candidate's profile shows "From lead sheet: <campaign>" linking back to the lead.
* **WhatsApp message**: the green WhatsApp button opens a small box with three messages (First contact, Request resume, Follow-up) already filled with the person's name, job and the agency name.
  Change the wording for this message if you like, then **Open WhatsApp**; the chosen message is noted in the lead's history ("WhatsApp message opened"). An administrator changes the three
  standard messages under **Users > Agency settings > Lead sheet WhatsApp templates**.
* **Loading**: while the leads are being fetched (first visit, or after a filter change that takes a moment) grey placeholder rows and "Loading leads…" are shown, the count says "Loading…", and
  "No leads yet" appears only after the server has answered with nothing. A failed load shows the reason with **Try again**.
* Undo of a status change made from a row (for example Not interested) is the "Undo" toast of the bulk actions.

### Checks (13B)

* [ ] Campaigns tab: totals match the Leads tab; sorting by conversion reorders the rows; clicking a campaign filters the Leads tab; as staff only your own counts appear.
* [ ] Set a follow-up date on a lead as its owner: a to-do "Follow up lead: <name>" appears at 09:00 on that day; clear the date: it disappears; reassign the lead: it moves to the new owner.
* [ ] Add a candidate whose phone matches an uploaded lead: the lead shows "In Data Bank"; the candidate's profile links back to the lead.
* [ ] WhatsApp button: pick a template, edit the text, Open WhatsApp: the lead's history has "WhatsApp message opened". Admin edits a template in Agency settings; the next popover uses it.
* [ ] Reload the Lead sheet on a slow connection: placeholder rows and "Loading leads…", never "No leads yet" before the answer.

## Bulk actions, status change and delete

* **Select leads**: tick rows (the ticks stay while you change pages) or the header box for the page. A bar sticks to the top of the table: "10 selected", **Select all 3,000 matching leads** (every page of the
  current search and filters, not only the ticked ones), and the actions **Assign to...** (a person, or spread evenly over available staff), **Change status...**, **Mark not interested**, **Export selected** (Excel),
  **Delete...** (admin only) and **Clear selection**. Changing the search, a filter or the page size clears the selection.
* Each action opens a dialog that says the **exact number of leads and what will happen** before anything is changed ("All 3,000 leads that match the current search and filters ... will be assigned to ...").
  The result appears as "2,998 leads updated, 2 skipped (2 converted or linked to a candidate)". At most 10,000 leads per action; with more matches the bar asks you to narrow the filters.
  Converted leads are never moved, changed or deleted by a bulk action. Only admin and manager use the bar (staff cannot assign, export or delete; a recruiter's own status changes follow the daily-task rule).
* **Change one lead's status**: the row's "..." menu has **Change status** and **Not interested**; the drawer has the Status box. A toast "Marked not interested. **Undo**" stays for 10 seconds and puts the previous status back.
* **Delete a lead** (admin only): "Delete lead" in the row menu or the drawer, after a confirmation. A lead that was converted to a candidate cannot be deleted (the message says which candidate). Deleting removes the
  call history and resume. **If the same person is in a later upload they are added again** (nothing remembers deleted leads). Deleting more than 20 leads at once needs the word DELETE typed. Every deletion is in the audit log.

### Checks (13A)

* [ ] Tick a page, **Select all N matching**: the bar says "All N matching leads are selected"; **Assign to...** shows N in the dialog and, after Assign, the toast says "N leads assigned". Evenly: each recruiter gets the same number (+-1).
* [ ] With a search typed (for example `Testville 3`), Select all matching, Mark not interested: exactly the leads the list showed change; the others stay.
* [ ] Row menu > Change status > Contacted shows "Status changed to Contacted. Undo"; Undo puts New back (the lead's history lists both changes). Same for Not interested.
* [ ] As admin delete one lead (the confirmation mentions the later upload); as a manager there is no delete. A converted lead cannot be deleted (menu entry disabled, the server refuses with 409).
* [ ] Delete 21 or more leads through the bar: the button stays disabled until DELETE is typed.
* [ ] Export selected downloads an Excel file with only the selected leads.
* [ ] As staff with open daily tasks: no ticks, no bar, "Change status" is disabled with the daily-task reason.
