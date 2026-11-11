import type { DbColumn } from '@/features/candidates/components/db-table'
import { EXTRA_PREFIX, LEAD_HEADINGS, type ExtraColumn, type Lead } from '../lead-model'
import { DEFAULT_COLUMN_FIELDS, FIELD_PREFIX, LEAD_FIELD_DEFS } from '../lead-fields'
import { workBlock, type LeadColumnContext } from './lead-actions'
import {
  CampaignCell, ContactCell, CreatedCell, ExperienceCell, ExtraAnswerCell, FieldCell, LeadActions,
  LocationCell, NameCell, PositionCountryCell, ProfileCell, ResumeCell, StatusOwnerCell,
} from './lead-columns'

/**
 * The Lead sheet table: one table that always fits its box (DbTable `fit`). Widths are the least a column needs to show its
 * content in full; the table shares what is left over. Several sheet fields share one cell (name over date of birth and gender,
 * phone with WhatsApp over e-mail, city over state, restaurant over total years, campaign over form, status over assignee).
 * When the minimums do not add up the table first tightens its spacing, then hides the columns with the highest `optional`
 * number; the Columns menu, the expandable row, the drawer and the cards still show every field. The rest of the sheet's
 * fields (see lead-fields.ts) and its custom questions (`extras`) are columns that start switched off.
 */
export function leadColumns(context: LeadColumnContext, extras: ExtraColumn[] = []): DbColumn<Lead>[] {
  const h = LEAD_HEADINGS
  const core: DbColumn<Lead>[] = [
    { key: 'actions', header: h.actions, width: 104, sticky: 'start', render: (l) => <LeadActions lead={l} context={context} /> },
    { key: 'created', header: h.created, sortKey: 'created', width: 92, optional: 3, group: 'Workflow', sticky: 'start', render: (l) => <CreatedCell lead={l} /> },
    { key: 'name', header: h.name, sortKey: 'name', width: 164, group: 'Person', sticky: 'start', render: (l) => <NameCell lead={l} /> },
    { key: 'contact', header: h.contact, width: 214, group: 'Contact', render: (l) => <ContactCell lead={l} /> },
    { key: 'position', header: h.position, sortKey: 'position', width: 140, group: 'Job answers', render: (l) => <PositionCountryCell lead={l} /> },
    { key: 'experience', header: h.experience, width: 90, optional: 2, group: 'Job answers', render: (l) => <ExperienceCell lead={l} /> },
    { key: 'location', header: h.location, width: 116, optional: 1, group: 'Contact', render: (l) => <LocationCell lead={l} /> },
    { key: 'campaign', header: h.campaign, sortKey: 'campaign', width: 164, optional: 4, group: 'Campaign & ad', render: (l) => <CampaignCell lead={l} /> },
    { key: 'status', header: h.status, sortKey: 'status', width: 110, group: 'Workflow', render: (l) => <StatusOwnerCell lead={l} userById={context.userById} /> },
    { key: 'resume', header: h.resume, width: 92, group: 'Workflow', render: (l) => <ResumeCell lead={l} block={workBlock(context, l)} onUpload={context.onUploadResume} onConvert={context.onConvert} /> },
    { key: 'profile', header: 'Education / Marital status', width: 130, optional: 7, off: true, group: 'Person', render: (l) => <ProfileCell lead={l} /> },
  ]
  const fields: DbColumn<Lead>[] = LEAD_FIELD_DEFS.filter((field) => !DEFAULT_COLUMN_FIELDS.has(field.id)).map((field, index) => ({
    key: `${FIELD_PREFIX}${field.id}`, header: field.label, width: field.width, optional: 20 + index, off: true, group: field.group,
    render: (l: Lead) => <FieldCell lead={l} field={field} />,
  }))
  const questions: DbColumn<Lead>[] = extras.map((column, index) => ({
    key: `${EXTRA_PREFIX}${column.key}`, header: column.label, width: 140, optional: 60 + index, off: true, group: 'Job answers',
    render: (l) => <ExtraAnswerCell lead={l} answerKey={column.key} />,
  }))
  return [...core, ...fields, ...questions]
}
