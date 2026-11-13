import type { DbColumn } from '@/features/candidates/components/db-table'
import { EXTRA_PREFIX, LEAD_HEADINGS, type ExtraColumn, type Lead } from '../lead-model'
import { DEFAULT_COLUMN_FIELDS, FIELD_PREFIX, LEAD_FIELD_DEFS } from '../lead-fields'
import { workBlock, type LeadColumnContext } from './lead-actions'
import {
  CampaignCell, ContactCell, CreatedCell, EducationCell, ExperienceCell, ExtraAnswerCell, FieldCell, LeadActions,
  LocationCell, NameCell, PositionCountryCell, ProfileCell, ResumeCell, StateCell, StatusOwnerCell,
} from './lead-columns'

/**
 * The Lead sheet table: one table that always fits its box (DbTable `fit`). Widths are the least a column needs to show its
 * content in full; the table shares what is left over. Actions and Created keep their original left positions. Name, age and contact share one cell, followed by the job,
 * education, India/abroad/total experience and location. Workflow fields follow those facts.
 * When the minimums do not add up the table first tightens its spacing, then hides the columns with the highest `optional`
 * number; the Columns menu, the expandable row, the drawer and the cards still show every field. The rest of the sheet's
 * fields (see lead-fields.ts) and its custom questions (`extras`) are columns that start switched off.
 */
export function leadColumns(context: LeadColumnContext, extras: ExtraColumn[] = []): DbColumn<Lead>[] {
  const h = LEAD_HEADINGS
  const core: DbColumn<Lead>[] = [
    { key: 'actions', header: h.actions, width: 104, sticky: 'start', render: (l) => <LeadActions lead={l} context={context} /> },
    { key: 'created', header: h.created, sortKey: 'created', width: 92, sticky: 'start', group: 'Workflow', render: (l) => <CreatedCell lead={l} /> },
    { key: 'name', header: h.name, sortKey: 'name', width: 192, align: 'left', group: 'Person', sticky: 'start', render: (l) => <NameCell lead={l} /> },
    { key: 'position', header: h.position, sortKey: 'position', width: 140, align: 'left', group: 'Job answers', render: (l) => <PositionCountryCell lead={l} /> },
    { key: 'education', header: h.education, width: 130, align: 'left', group: 'Person', render: (l) => <EducationCell lead={l} /> },
    { key: 'experienceIndia', header: h.experienceIndia, width: 92, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="india" /> },
    { key: 'experienceAbroad', header: h.experienceAbroad, width: 92, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="abroad" /> },
    { key: 'experience', header: h.experience, width: 120, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="total" /> },
    { key: 'location', header: h.location, width: 116, align: 'left', group: 'Contact', render: (l) => <LocationCell lead={l} /> },
    { key: 'campaign', header: h.campaign, sortKey: 'campaign', width: 164, optional: 6, group: 'Campaign & ad', render: (l) => <CampaignCell lead={l} /> },
    { key: 'status', header: h.status, sortKey: 'status', width: 108, group: 'Workflow', render: (l) => <StatusOwnerCell lead={l} userById={context.userById} /> },
    { key: 'resume', header: h.resume, width: 92, group: 'Workflow', render: (l) => <ResumeCell lead={l} block={workBlock(context, l)} onUpload={context.onUploadResume} onConvert={context.onConvert} /> },
    { key: 'contact', header: h.contact, width: 214, optional: 4, off: true, group: 'Contact', render: (l) => <ContactCell lead={l} /> },
    { key: 'state', header: h.state, width: 95, align: 'left', optional: 5, off: true, group: 'Contact', render: (l) => <StateCell lead={l} /> },
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
