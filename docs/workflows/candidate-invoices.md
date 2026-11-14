# Candidate invoices after ticket upload

The candidate's invoice belongs to their placement. The admin enters the actual agreed service fee in INR, from 5,000 to 30,000 before GST. Advance-payment amounts do not supply this fee. CGST is 9% and SGST is 9%, as confirmed for this installation; for example, a 10,000 service fee produces 900 CGST, 900 SGST and an 11,800 total.

## Who can see invoices (admin only)

The service fee, CGST and SGST, the invoice (number and PDF), the Payments panel, the invoice settings and Reports > **Fee collections** belong to the **admin alone**. Recruiters (role `staff`) and managers never see or change them, and nothing replaces the admin's fee entry on the recruiter's screens. The server is the source of truth (`services/invoice_access.py`); the screens mirror it with the `view_invoices` capability.

- Every invoice endpoint answers `403 Only the admin can see or change invoices.` to anyone but the admin: `GET/PUT /api/candidates/{id}/invoice`, `POST .../invoice/reconcile`, `GET/PUT /api/invoice-settings`, `GET/POST .../invoice/payments`, `POST .../invoice/payments/{id}/void` and `GET /api/invoices/finance-summary`. The same sentence answers a download of the invoice PDF (`GET /api/files/{id}` for an `invoice` file), a manual upload to the `invoice` slot and a manual status for it.
- The application form payload carries no invoice for them: no `invoice` document slot (and no place in `documentOrder`), no `invoice_generated` tracking row, no Invoice in the deployment checklist or in the missing-document percentage. No fee, tax figure, invoice number or file link appears in the form, candidate detail (`GET /api/candidates/{id}`), the file listing (`GET /api/files`), the candidate record export and ZIP, the Travelled report and its export, or the bulk-import readiness notes.
- Only the visibility and entry are restricted. **Issuing the invoice is automatic and server-side**: uploading the air ticket (by the recruiter, a manager or the admin) still reconciles the placement's invoice once the admin has saved the fee and billing settings, and the reserved or issued invoice and recorded payments still prevent deleting the candidate or moving the lead back. A recruiter is never blocked on a fee, invoice or document they cannot see; document checklists never gate stage moves.
- The Advance payment stage (stage 8) is a recruitment stage and remains visible to the people who work it; its amount is not the service fee, and the admin confirms an eligible advance into the receipts ledger.
- Audit entries about fees, invoices and receipts are on the admin's Users audit only.

## Candidate workflow

1. The recruiter chooses the candidate's client and job to identify the placement.
2. The admin enters and saves the agreed service fee (Invoice card on the application form). If a ticket is already present, the save action clearly indicates that it generates the invoice.
3. The air ticket is uploaded by whoever works the candidate. When the saved fee and billing settings are available, this triggers invoice generation automatically.
4. The admin views, prints or downloads the resulting PDF from the candidate record. The invoice is also evidence for the admin's document checklist and is included in the admin's candidate file downloads.

Missing assignment, ticket, fee or billing settings is shown explicitly. Missing information never produces an invented charge, recipient or invoice number. Completing the final recruitment stage without a ticket does not issue an invoice. Recruitment stage movement remains a separate manual action requiring feedback.

Replacing a ticket, repeating an upload, retrying generation or refreshing the page must not create another invoice. An issued invoice preserves the original recipient, fee, tax calculation, number and PDF. Later changes to candidate details or company settings must not alter that issued record.

## Billing setup

Company details come from the supplied `GST Format - Copy.docx`, as authorized by the user. Preserve the GSTIN exactly as supplied; do not silently change a letter to a digit. The template's recipient, passport, date and 15,000 fee are examples and are not candidate defaults.

Invoice 073 is already used for another customer. The new CRM series begins at `CI/26-27/001`, then `CI/26-27/002`, separating it from those existing bills. Allocate numbers centrally and uniquely when issuing, within the 16-character limit. Administrative settings control issuer details and subsequent numbering; they do not supply a candidate's agreed fee. [CBIC Rule 46](https://taxinformation.cbic.gov.in/content-page/explore-rules/1000136/1000001) permits multiple consecutive invoice series with numbers unique within a financial year.

## Payments and collections

An invoice is not evidence that a candidate paid. The admin records actual receipts against the candidate and placement, including partial payments and advances before ticket upload. A saved agreed fee is needed to calculate the expected total; issuing a PDF is not required to record an earlier payment.

Show service fee, GST, agreed total, issued invoice total, received amount and outstanding balance separately. Keep invoice status distinct from unpaid, partly paid and paid status. The collections view (Reports > Fee collections, admin only) supports candidate-level inspection and aggregate totals across every candidate. Its tab is not shown to anyone else and `?tab=collections` opens Pipeline for them.

Existing stage-eight entries are not automatically new cash: imported stages may lack amounts and reopening may copy old values. Eligible recorded advances require explicit confirmation before inclusion in the payment ledger, with source and placement deduplication. Repeated requests must not duplicate receipts. Corrections are made by the admin, retain the original history and a reason; recording a correction does not perform a bank refund.

## Validation and rollout

Validate decimal calculations and boundaries, admin-only permissions (`test_invoice_access.py`: 403 for a recruiter and a manager on every endpoint, omission from every other payload), placement isolation, concurrent issue attempts, upload and generation retries, immutable PDFs, and checklist/export integration using isolated test data. Loading an invoice or settings is read-only; invoice creation follows an authorized write.

The local and hosted CRM share their runtime database. Their schema checks require the exact migration revision. Prepare and test compatible application releases before a coordinated schema rollout; migrating the shared database alone can make the older application unhealthy.

OCR review is separate from invoice generation. The invoice uses saved candidate information; unresolved OCR flags are not evidence that the candidate's identity details have been verified.
