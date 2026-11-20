import type { DbColumn } from '@/features/candidates/components/db-table'
import { EXTRA_PREFIX, LEAD_HEADINGS, type ExtraColumn, type Lead } from '../lead-model'
import { DEFAULT_COLUMN_FIELDS, FIELD_PREFIX, LEAD_FIELD_DEFS } from '../lead-fields'
import { workBlock, type LeadColumnContext } from './lead-actions'
import {
  CampaignCell, ContactCell, CreatedCell, UploadedCell, EducationCell, ExperienceCell, ExtraAnswerCell, FieldCell, LeadActions,
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
/** The three experience headings are shortened so every heading stays on one line; the full name is the header's tooltip. */
export const LEAD_SHORT_HEADINGS = { name: 'Name', experienceIndia: 'India exp.', experienceAbroad: 'Abroad exp.', experience: 'Total exp.' } as const

/**
 * The narrowest a place or job column may be: a single 15-letter word at the table's 13px bold type (about 8px per
 * capital letter) plus the cell's 8px side padding and the 1px column line, so 'Mayiladuthurai' is never broken mid-word. The width is
 * taken from the optional columns, which the fit hides first (see column-fit.ts) when the box is too narrow.
 */
export const LONGEST_WORD_LETTERS = 15
export const BOLD_LETTER_PX = 8
export const CELL_PADDING_PX = 16 + 1
export const WORD_COLUMN_MIN = LONGEST_WORD_LETTERS * BOLD_LETTER_PX + CELL_PADDING_PX

export function leadColumns(context: LeadColumnContext, extras: ExtraColumn[] = []): DbColumn<Lead>[] {
  const h = LEAD_HEADINGS
  /** Only an admin or MD ticks rows (bulk actions), so only their first column has room for the tick box. */
  const ticks = context.me ? context.me.role !== 'staff' : true
  const core: DbColumn<Lead>[] = [
    { key: 'actions', header: h.actions, width: ticks ? 108 : 72, align: 'center', sticky: 'start', render: (l) => <LeadActions lead={l} context={context} /> },
    { key: 'created', header: h.created, sortKey: 'created', width: 88, sticky: 'start', group: 'Workflow', render: (l) => <CreatedCell lead={l} /> },
    { key: 'uploaded', header: h.uploaded, sortKey: 'uploaded', width: 92, sticky: 'start', optional: 12, group: 'Workflow', render: (l) => <UploadedCell lead={l} /> },
    { key: 'name', header: LEAD_SHORT_HEADINGS.name, headerTitle: h.name, sortKey: 'name', width: 162, align: 'center', className: 'ld-col-name', group: 'Person', sticky: 'start', render: (l) => <NameCell lead={l} /> },
    { key: 'position', header: h.position, sortKey: 'position', width: WORD_COLUMN_MIN, align: 'center', group: 'Job answers', render: (l) => <PositionCountryCell lead={l} /> },
    { key: 'education', header: h.education, width: 88, optional: 10, align: 'center', group: 'Person', render: (l) => <EducationCell lead={l} /> },
    { key: 'experienceIndia', header: LEAD_SHORT_HEADINGS.experienceIndia, headerTitle: h.experienceIndia, width: 80, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="india" /> },
    { key: 'experienceAbroad', header: LEAD_SHORT_HEADINGS.experienceAbroad, headerTitle: h.experienceAbroad, width: 96, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="abroad" /> },
    { key: 'experience', header: LEAD_SHORT_HEADINGS.experience, headerTitle: h.experience, width: 86, optional: 8, group: 'Job answers', render: (l) => <ExperienceCell lead={l} kind="total" /> },
    { key: 'location', header: h.location, width: WORD_COLUMN_MIN, align: 'center', optional: 9, group: 'Contact', render: (l) => <LocationCell lead={l} /> },
    { key: 'campaign', header: h.campaign, sortKey: 'campaign', width: 140, align: 'center', optional: 11, group: 'Campaign & ad', render: (l) => <CampaignCell lead={l} /> },
    { key: 'status', header: h.status, sortKey: 'status', width: 112, align: 'center', group: 'Workflow', render: (l) => <StatusOwnerCell lead={l} userById={context.userById} onViewRemarks={context.onViewRemarks} /> },
    { key: 'resume', header: h.resume, width: 100, align: 'center', group: 'Workflow', render: (l) => <ResumeCell lead={l} block={workBlock(context, l)} onUpload={context.onUploadResume} onConvert={context.onConvert} /> },
    { key: 'contact', header: h.contact, width: 214, align: 'center', optional: 4, off: true, group: 'Contact', render: (l) => <ContactCell lead={l} /> },
    { key: 'state', header: h.state, width: 113, align: 'center', optional: 5, off: true, group: 'Contact', render: (l) => <StateCell lead={l} /> },
    { key: 'profile', header: 'Education / Marital status', width: 130, align: 'center', optional: 7, off: true, group: 'Person', render: (l) => <ProfileCell lead={l} /> },
  ]
  const fields: DbColumn<Lead>[] = LEAD_FIELD_DEFS.filter((field) => !DEFAULT_COLUMN_FIELDS.has(field.id)).map((field, index) => ({
    key: `${FIELD_PREFIX}${field.id}`, header: field.label, width: field.width, align: 'left', optional: 20 + index, off: true, group: field.group,
    render: (l: Lead) => <FieldCell lead={l} field={field} />,
  }))
  const questions: DbColumn<Lead>[] = extras.map((column, index) => ({
    key: `${EXTRA_PREFIX}${column.key}`, header: column.label, width: 140, align: 'left', optional: 60 + index, off: true, group: 'Job answers',
    render: (l) => <ExtraAnswerCell lead={l} answerKey={column.key} />,
  }))
  return [...core, ...fields, ...questions]
}
