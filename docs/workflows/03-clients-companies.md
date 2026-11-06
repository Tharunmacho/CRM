# Clients and employer companies

This module creates the employer context required by every job order. Source: PRD page 3. Requirements: **CLIENT-01, CLIENT-02**. The current CRM uses a client contact above one or more employer companies; the PDF describes the employer itself as the client. This naming and hierarchy difference must remain visible during owner review.

## Roles and preparation

Admin, Manager and Staff can create and edit client/company records. Destructive deletion is restricted and must preserve linked business history. Obtain the employer's real contact information and an agreement where applicable. An order cannot replace its employer relationship with free text.

## User journey

1. Open **Clients**, create the contact and save their name, communication details, location, notes and active status.
2. Open the saved client and choose **Add employer**; the client contact is preselected. Companies are managed only inside Clients (there is no separate Companies page; `/companies` redirects to Clients). The **New client** form also takes the first employer's company name, country, city, industry, contact person/email, phone, WhatsApp, address, notes and status.
3. Save the employer before attaching its agreement. Upload the agreement and confirm that it appears on the saved record.
4. In the client detail's **Employers & hiring** section, choose **View hiring orders** for an employer. The Jobs workspace retains that employer filter, and a new order preselects it.
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

Implementation: [Clients](../../frontend/src/screens/clients/clients.tsx), [client detail](../../frontend/src/screens/clients/client-detail.tsx), [master-data API](../../backend/app/api/routers/data.py). File rules: [Documents](06-documents-ocr.md).

**Update (client form).** *New client* now follows the PRD client fields in one form: company name, industry, country, city, address, status, agreement, contact person, email, phone, WhatsApp and notes. `POST /api/clients` accepts an optional `company` block and saves the contact and its first employer in one transaction (no migration; the contact → company model is unchanged). The agreement uploads to the new employer afterwards, and a failed upload is retried without creating a duplicate. The client detail page shows the contact, each employer (address, agreement, contacts), and every order with matched / selected / deployed progress and its candidates.

**Update (single client form).** Companies no longer have their own page or menu entry. *New client* is the one complete form: contact person (name, email, phone, WhatsApp, status), company (name, industry, country, city, address), an optional company contact (person, email, phone, WhatsApp; blank fields use the contact person's details), the agreement and notes. `POST /api/clients` `company` accepts those optional contact fields. More companies are added, edited, given an agreement, viewed in history or deleted (where permitted) from the client's own page.

**Update (designation and website).** *New client* and *Edit client* now also hold the contact person's **Designation** (job title, up to 120 characters) and the company's **Website**. The website is optional: a bare domain such as `www.gulfbuild.com` is saved as `https://www.gulfbuild.com`, and the API refuses addresses with spaces, `javascript:`/`data:`/`mailto:`/`ftp:` schemes, `user:password@` parts, a host without a dot, or more than 255 characters (`normalize_website` in `backend/app/api/routers/data.py`; the browser applies the same rule in `shared/lib/website.ts`). `POST/PUT /api/clients` accept `designation`; the `company` block of `POST /api/clients`, `POST /api/companies` and `PUT /api/companies/{id}` accept `website`; both come back in the same camelCase shape in the desk snapshot and bootstrap, and a change is recorded in the record history. The Clients table and cards show the designation under the contact name and the website (a link that opens in a new tab with `rel="noopener noreferrer"`) under the company name; the client page shows both, the search box also matches designation and website, and **Download CSV** gained *Website* and *Designation* columns. Migration `0025_client_fields` adds `crm_client_contacts.designation VARCHAR(120)` and `crm_employer_companies.website VARCHAR(255)`: nullable, `ADD COLUMN IF NOT EXISTS`, existing rows unchanged, downgrade drops both columns (and so discards the values typed since).
