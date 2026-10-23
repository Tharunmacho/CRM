# Recruitment stages six to eleven and deployment

These stages handle medical clearance, passport custody, advance payment, visa, stamping and travel. Source: PRD pages 9–10. Requirements: **STAGE-06, STAGE-07, STAGE-08, DEPLOY-01**, with shared completion/reopen rules from STAGE-01 through STAGE-04.

## Preparation and common behavior

Stage 5 must complete before medical becomes current. Use the same owner/supervisor permissions, draft Save, immutable file versions and version-conflict recovery as [stages one to five](14-stages-one-to-five.md). Staff must continue daily remarks while waiting; the SLA does not pause for a clinic, employer or embassy.

## Stage 6 Medical and repeated examinations

1. Record whether medical was applied for and the application date, then the result **Fit/Unfit** when received.
2. A latest **Fit** result completes medical and creates the passport task. Do not combine a Fit result with a contradictory Applied No state.
3. For **Unfit**, choose whether the candidate is reapplying.
4. If Yes, enter the new medical date. Choose **Record attempt and schedule re-medical**. The failed attempt remains in history; a new follow-up task starts with its own 48-hour deadline while the application remains at stage 6.
5. Enter the next result. Repeat as needed; a retest date cannot precede the prior medical date when both are supplied.
6. Unfit with **Reapplying: No** explicitly closes the application as medically unfit. The candidate remains in Data Bank.

**Completion:** the latest valid Fit result. Applied No or an absent result waits; Unfit requires the reapply decision. This is the only repeating stage and the last negative-exit stage. Admin reopening medical preserves every previous attempt and invalidates later effective completions; it must not erase the retest history.

## Stage 7 Passport

1. Review the passport copy prefilled from the candidate profile. Replace it through the versioned upload if necessary.
2. Record **Original passport collected: Yes/No**, collection date and the staff custodian.
3. Yes completes the stage and creates the advance task. No saves a waiting state.

**Completion:** Original collected Yes. The PDF does not make every supporting passport field an extra advancement blocker. Supplied copy, custodian and date are validated, including the actual file/user reference. No does not close the application or reset the task. Admin reopen retains old copy versions/custody evidence and invalidates subsequent progression.

## Stage 8 Advance payment

1. Record **Advance collected: Yes/No**.
2. For a collection, enter the candidate-specific positive amount and its currency. Record date, payment mode, receipt number and upload the receipt.
3. Yes, positive amount and the genuine saved receipt complete the stage and create the visa follow-up task.
4. If unpaid, Save No and record the follow-up in daily remarks.

**Completion:** Yes + positive amount + receipt file. Payment values are validated as money; zero, negative or malformed values cannot complete. Currency is an explicit supported addition needed to avoid combining different currencies in reports. No waits. Admin reopen retains the old receipt/history; a superseded advance is excluded from effective collection reports rather than counted again.

## Stage 9 Visa arrival and Google review

1. Record **Visa arrived: Yes/No**, arrival date and visa copy when available.
2. Record whether a Google review was obtained.
3. Visa arrived Yes completes the stage and creates the stamping task.
4. Visa not arrived remains at stage 9. Save the waiting state and continue daily remarks.

**Completion:** Visa arrived Yes. The Google review checkbox is recorded but is not a blocker. The visa copy/date are validated when supplied; they are not additional hidden progression requirements. There is no negative exit here. Admin reopen retains the visa evidence and invalidates later stamping/ticket completion as effective current work.

## Stage 10 Visa stamping

1. Record **Stamping done: Yes/No** and date.
2. Upload the stamped visa copy to this stage.
3. Yes plus the genuine uploaded copy completes stage 10 and creates the air-ticket task.
4. No remains waiting at stage 10 with its task and deadline unchanged.

**Completion:** Yes + stamped visa file. A visa-arrival file from another field cannot be passed off as this stage's required evidence without the permitted association. Admin reopening stamping preserves versions and invalidates the old travel completion.

## Stage 11 Air ticket and deployment

1. Record **Booked by: Employer/Agency**, travel date, **Airline and flight number** and PNR. The UI stores airline/flight details in one combined field.
2. Upload the ticket copy and inspect it before final completion.
3. Complete the stage. The application becomes **Deployed**, the ticket task closes and no next recruitment task is created.
4. Verify the order's deployed count. When deployed equals openings, the order becomes **Filled**.

**Completion:** a genuine ticket file. Booking/travel details are validated when supplied; the PDF's required ticket copy controls completion. There is no No/negative branch at this final stage. Before committing, the backend locks/rechecks remaining order capacity so two candidates cannot claim the final opening simultaneously.

Admin can reopen a completed stage with a reason. Reopening deployment removes that completion from the effective deployment count, recalculates the order status and recreates current work. The former ticket/version remains historical. A newer active/deployed placement prevents reopening an older application over it.

## Recovery and handoffs

An upload failure leaves the current stage editable and does not advance. A saved receipt/ticket survives a later completion error. A stale version or changed order capacity requires refresh and review, not blind repeated completion. Closed candidates return to [Data Bank](07-data-bank-skills.md) for a new suitable order; deployed results feed [Reports](17-reports.md).

## Acceptance checklist

- [ ] Test Fit, Unfit/no reapply and at least two repeated medical attempts, including fresh follow-up tasks.
- [ ] Save waiting values in stages 7–10 and confirm no closure or SLA reset.
- [ ] Verify passport prefill/replacement and real custodian/file validation.
- [ ] Reject missing/zero advance or foreign receipt; report the valid collection by currency.
- [ ] Complete visa arrival without Google review and confirm the intended nonblocking behavior.
- [ ] Reject stamping without its file and ticket completion without a ticket.
- [ ] Complete the last opening concurrently and verify exactly the allowed deployments.
- [ ] Reopen medical, advance and final ticket examples; attempts/files remain while effective reports/counts change correctly.

## Current implementation and remaining acceptance

All six stages, repeated medical, real attachment checks, atomic deployment and reasoned reopening are implemented with rule and PostgreSQL workflow tests. Review actual agency passport/payment/travel examples and full stage UI behavior before production sign-off. The reference's shorter submission workflow does not replace this eleven-stage chain.

These stages use the same current-step overview, visible completion requirements, actual task deadline and saved-change recovery described in [stages one to five](14-stages-one-to-five.md). The next-action message distinguishes another medical attempt, an application closure and final deployment. Uploads, active-owner requirements and unsaved-edit protection remain enforced throughout the later stages.

Implementation: [backend rules](../../backend/app/domain/stages.py), [application API](../../backend/app/api/routers/applications.py), [stage fields](../../frontend/src/shared/workflow/stages.ts), [stage modal](../../frontend/src/features/workflow/components/stage-modal.tsx).
