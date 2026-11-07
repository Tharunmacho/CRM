# Clients and reference agencies: contract

Shared by the backend and the two frontend screens. Keep this file in step with the code.

## What the client wants

- **Clients page**: simple and crisp like the Dashboard. No KPI cards, no All/Active/Inactive tabs, no agreement/status badges.
  Two buttons: **New client** and **Add reference agency**. Each record is a card.
- **Card fields** (client and reference agency alike): Name (contact person), Name of the agency, Contact no, Email, Address, State.
- **Staff details**: an optional list of people on the client's / agency's own team (name, designation, phone, email), added in the same form.
- **Allocate staff** (clients only): a dropdown of the CRM staff created in User Management (multi-select). The chosen staff look after
  that client end to end: every candidate who comes in (direct, WhatsApp, email, manual) for that client's jobs/requirements, and every
  candidate matched to one of the client's orders, is routed to those staff.

## Data model (migration `0026_client_agency`, additive and idempotent; never edit an applied migration)

| Table / column | Notes |
|---|---|
| `crm_employer_companies.state` | `String(100)`, nullable. The agency's state. (Company already holds name, address, phone, email.) |
| `crm_client_contacts.staff_contacts` | `JSONB NOT NULL DEFAULT '[]'`: `[{name, designation?, phone?, email?}]`, max 50 entries. |
| `crm_client_staff` | `(client_id FK crm_client_contacts ON DELETE CASCADE, user_id FK auth_users ON DELETE CASCADE)`, primary key on both. |
| `crm_reference_agencies` | `id`, `name` (contact person, required), `agency_name` (required), `phone`, `email`, `address` (Text), `state`, `staff_contacts` JSONB default `[]`, `notes`, timestamps. Unique index on `lower(agency_name)` is NOT wanted (two branches may share a name). |

## API (camelCase JSON)

- Workspace `clients[]` (ClientOut) gains `staffContacts: StaffContact[]` and `staffIds: string[]`; workspace `companies[]` gains `state`.
- `POST /api/clients` / `PUT /api/clients/{id}` accept `staffContacts` and `staffIds` (every id must be an active CRM user with role staff or manager; otherwise 422).
  `PUT` replaces the whole list. On create the nested `company` object accepts `state` and `address`; `PUT /api/companies/{id}` accepts `state`.
- `GET /api/reference-agencies` (any signed-in user) -> `{items: ReferenceAgency[]}` newest first.
  `POST /api/reference-agencies`, `PUT /api/reference-agencies/{id}`, `DELETE /api/reference-agencies/{id}`: same roles that may create clients.
  Change-feed topic `reference_agencies` (add the table to `change_feed.py`).
- Types: see `frontend/src/shared/model/types.ts` (`Client`, `Company`, `StaffContact`, `ReferenceAgency`).

## Routing rule (backend)

1. **Mapping**: when a candidate is matched to an order (application created), if the order's client has staff and the candidate's current
   owner is not one of them, the candidate is reassigned to the client staff member who owns the fewest candidates (ties: name, then id).
   The reassignment is written to the assignment history and notifies the new owner exactly like an admin reassignment does. A client with no
   staff changes nothing.
2. **Intake** (email, WhatsApp, direct/manual entry): a new candidate whose designation matches (case-insensitive) the designation of an
   open order of a client with staff goes to the least-loaded of that client's staff. Several matching clients: the least-loaded staff
   member across all of them. No match: today's round-robin, unchanged.
3. An admin's manual reassignment always wins and is never undone by 1 or 2 later (rule 1 runs only when the application is created).
4. Ownership rules from before still hold: only the owner works a candidate; the uploader/creator becomes owner when no rule above applies.

### Backend behaviour the screens can rely on (clarifications; no names or shapes changed)

- `PUT /api/clients/{id}`: `staffIds` / `staffContacts` that are **sent** replace the saved list (`[]` clears it); one that is **left out** keeps it.
- `staffIds` (response and desk snapshot) lists only people who can still work the client's candidates (active, role staff or manager), ordered by
  name. A deactivated or demoted user drops out of the list; a deleted user's link is removed by the database. A 422 for a bad `staffIds` has
  `detail: {message, errors: [{field: "staffIds", message}]}`.
- `staffContacts` entries are trimmed, blank optional boxes become `null`, emails are lower-cased, `name` is required, at most 50 (422 otherwise).
- `DELETE /api/reference-agencies/{id}` answers 204. Any signed-in user may add, change or delete (same as creating a client). Audit entity type is
  `reference_agency` (`GET /api/master-data/reference_agency/{id}/audit` works).
- Live topic `reference_agencies` is in the server's client topic list; a change to a client's allocated staff announces topic `clients`.
- Routing is silent in the API: after mapping a candidate or adding one by hand, the returned/next-read `ownerId` may be someone other than the caller.

## Frontend

- `/clients`: header, a two-way switch **Clients | Reference agencies**, a search box, then a card grid. Buttons **Add reference agency** and
  **New client**. A card shows the six fields, the allocated staff (client) as small avatars with names, and Open / Edit.
- `/clients/:id`: the same simple style: header with the agency name, a details card (the six fields + allocated staff + staff details),
  then the orders and the candidates matched to them. No KPI cards, no agreement/status badges.
- Dialogs: `ClientDialog` (keep its props) and a new `AgencyDialog`. Fields in a clean two-column grid; "Staff details" is a repeatable
  row group; "Allocate staff" is a multi-select dropdown of CRM users.

## Reference agency: specialisation and preferred country (migration `0033_agency_focus`)

Two multi-select button groups in the agency dialog, after the details and before Staff details. Nothing else was added.
- **Specialisation**: Oil and gas, Hospitality, Construction, General worker, Medical, All sectors. **All sectors** means every sector, so it stands alone:
  choosing it clears the single sectors and choosing a single sector clears it (the form does this; the server also reduces any list containing it to `["All sectors"]`).
- **Preferred country**: Singapore, Malaysia, Europe, Gulf, Others.
- Wire: `specialisations` and `preferredCountries`, lists of those exact strings (anything else is a 422), distinct and in the order above; default `[]`.
  A PUT that does not mention a list keeps it; an explicit `[]` clears it.
- Storage: `crm_reference_agencies.specialisations` and `.preferred_countries`, JSONB NOT NULL DEFAULT `'[]'`. Additive and idempotent; an older server sharing the
  database ignores the columns and its INSERTs (which do not name them) still work. Older rows read as empty lists. Downgrade drops both columns.
- The agency list shows the choices as compact chips (table column and card rows) and the search box matches them.
