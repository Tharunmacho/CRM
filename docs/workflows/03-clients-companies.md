# Clients and employer companies

This module creates the employer context required by every job order. Source: PRD page 3. Requirements: **CLIENT-01, CLIENT-02**. The current CRM uses a client contact above one or more employer companies; the PDF describes the employer itself as the client. This naming and hierarchy difference must remain visible during owner review.

## Roles and preparation

Admin, Manager and Staff can create and edit client/company records. Destructive deletion is restricted and must preserve linked business history. Obtain the employer's real contact information and an agreement where applicable. An order cannot replace its employer relationship with free text.

## User journey

1. Open **Clients**, create the contact and save their name, communication details, location, notes and active status.
2. Open the saved client and choose **Add employer**; the client contact is preselected. Alternatively open **Companies**, link the contact and enter the employer's company name, country, city, industry, contact person/email, phone, WhatsApp, address, notes and status.
3. Save the employer before attaching its agreement. Upload the agreement and confirm that it appears on the saved record.
4. In the client detail's **Employers & hiring** section, choose **View hiring orders** for an employer. The Jobs workspace retains that employer filter, and a new order preselects it. In Companies, opening a row or choosing **Hiring orders** does the same; the **Client contact** link opens the parent relationship separately.
5. Edit a contact or employer when details change. Phone, employer WhatsApp and agency/contact WhatsApp are separate fields and must not overwrite one another.
6. Replace an agreement when needed. Open the latest original and older versions to verify that the old document remains available.
7. Set an obsolete record inactive when it has history. Use deletion only when permitted and the server confirms that linked records/documents do not prevent it.

## Screen and backend contract

Forms need clear contact-versus-employer labels, required-field validation and active/inactive states. Save and upload progress are distinct: if the employer saved but the agreement upload failed, retry the upload against the existing ID. Do not create the employer twice.

Backend validation protects valid parent relationships, referenced records and historical links. Record activity identifies edits and uploads. New orders require eligible active parents. Deletion and changes to parent relationships must not race into orphaned orders or lost agreements.

## Recovery and handoffs

Keep entered fields after a validation error. If another user removes/deactivates the parent before save, show the conflict and reload the available parents. An agreement failure should leave the saved employer usable with an explicit retry. Once the employer is ready, proceed to [designations and orders](04-designations-orders.md).

## Acceptance checklist

- [ ] Create one contact with two employers; their orders and candidates remain separate.
- [ ] Save different phone and WhatsApp values and reopen both fields.
- [ ] Fail an agreement upload after employer save; retry without a duplicate employer.
- [ ] Replace an agreement and download both versions.
- [ ] Attempt to delete a linked employer; verify the history is preserved.
- [ ] Decide whether the retained contact → company hierarchy is acceptable or requires a separate product/model change.

## Current implementation and remaining acceptance

Forms, connected employer/order navigation, independent company WhatsApp, agreement versions and audit are implemented. Client/employer screens check current records before exposing actions; failed reads have an explicit retry. Search-empty states distinguish no matching rows from an empty database. New employers need an active client contact. Literal employer-as-Client alignment remains a deliberate product decision; this guide does not silently authorize a data-model migration.

Implementation: [Clients](../../frontend/src/screens/clients/clients.tsx), [client detail](../../frontend/src/screens/clients/client-detail.tsx), [Companies](../../frontend/src/screens/companies/companies.tsx), [master-data API](../../backend/app/api/routers/data.py). File rules: [Documents](06-documents-ocr.md).
