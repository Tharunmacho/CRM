# Reference agencies and spreadsheet import

An agency has a contact name, an agency name, optional phone/email, optional **Place/City**, a separate full address, state, notes, team contacts, specialisations and preferred countries. Place never supplies or fabricates an address.

At least one of contact name or agency name must be supplied. A contact-only entry `Alex` saves as contact `Alex`, agency `Alex Agency`. An agency-only entry uses the agency name as its internal contact display fallback; the screen suppresses the repeated label. Nothing invents a phone, email, location or address. Names and place allow 200 characters; a generated agency name must fit within that limit. Existing clients retain their own validation rules.

Migration `0036_reference_agency_place` adds nullable `crm_reference_agencies.place`. It leaves existing records untouched and is repeatable. Agency PUT requests that omit place or notes preserve those fields; an explicitly empty value clears it. This protects imported notes when a form has no notes box.

## Preview

`POST /api/reference-agencies/import/preview` accepts multipart `file`, optional `sheetName`, optional `headerRow` (1–20, or 0 for no header), and optional JSON `mapping` from field names to zero-based column indices. Supported fields are `name`, `agencyName`, `phone`, `email`, `place`, `address`, `state`, `notes`. A column maps to at most one field. Limits: CSV/XLSX, 5 MB upload, 2,000 data rows and 50 columns. Existing bounded ZIP/XML parsing handles XLSX without evaluating formulas or loading macros. CSV uses the existing encoding decoder.

The response contains `fileName`, `sheetName`, `sheets`, `headerRow`, `columns` (`index`, `label`, short `sample`), `mapping`, `previewHash`, `rows`, `counts` and `canConfirm`. Each row has its source `rowNumber`, normalised `values`, `status`, explanation `message`, and an existing `agencyId` where applicable. Status is `create`, `add_contact`, `duplicate`, `conflict`, `invalid`, or `section`. Preview performs no writes.

Recognised headings populate an editable mapping. A single unnamed column whose sample values all look like phone numbers may be suggested as Phone; its position and values remain visible for confirmation. Explicit mapping handles headerless sheets. Switching worksheets without a mapping detects that worksheet's headers again.

Single-cell TRICHY and MADURAI section rows supply place for following rows that do not specify it themselves. An explicit `Place: value` or `City: value` section works similarly. `Other Places` is only a category: it clears inherited place. Rows before any section retain blank place; a city is never guessed from an agency or contact name. Numeric Excel phones and scientific-number CSV phones retain whole digits; spacing is cleaned without inventing a number or country code. Phones require 5–15 digits with the punctuation supported by the form. A cell containing multiple full phone numbers is flagged for splitting into separate team contacts, never truncated.

## Confirmation and matching

`POST /api/reference-agencies/import/confirm` sends the same file/options plus `previewHash`. A changed file, worksheet, header or mapping receives 409 and requires a fresh preview. The server reparses the file and recomputes the hash. Confirmation acquires a short directory lock, rechecks stored records, applies accepted rows together, and audits each creation/contact addition with source file and row. Any database failure rolls the whole import back. No physical documents are uploaded or changed.

- Matching uses normalised agency name and place. Different named branches remain separate agencies. A different contact with a named place is also kept separate from an unknown-location agency; the same contact gaining a previously missing location is flagged for review. Ambiguous existing matches are conflicts.
- An already stored matching contact or a repeated sheet row is skipped. Name matching ignores case and repeated spaces. Phone matching uses the existing configured home-country normaliser, preserving other international prefixes rather than comparing only the last ten digits. Multiple-phone text retains its separators.
- A genuinely distinct contact under the same agency/place is appended to its team. Existing primary contact, address, phone and other details are retained. The existing team limit (50) and team-name limit (160) still apply.
- Different or additional details for an existing contact, conflicting address/state/notes, and ambiguous matches are flagged for review and skipped. Import never overwrites existing values, fills uncertain blanks, or merges agencies solely by phone.
- Empty/invalid rows are explained; valid rows may still be confirmed. A second confirmation rechecks and skips records already saved, including concurrent imports. Newly arising conflicts are reported as skipped.

Confirmation returns `created`, `contactsAdded`, `skipped`, `conflicts`, `invalid`, and changed agency `items`. Section rows are informational and are excluded from skipped-data counts. Any signed-in user may import, consistent with existing agency CRUD; unauthenticated requests are rejected. The existing agency live-change topic and audit history update the directory.
