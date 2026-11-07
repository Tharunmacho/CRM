import { useMemo } from 'react'
import { BriefcaseBusiness, GraduationCap, IdCard, UserRound, type LucideIcon } from 'lucide-react'
import { completenessOf, emptyEducationRow, emptyExperienceRow, moveRow, removeRow } from '../form-model'
import type { DocumentKind } from '../types'
import type { FormContext } from '../components/sections'
import { FillFieldView, useFillEnv } from './fill-controls'
import { FillCardShell } from './fill-card'
import { FillRows } from './fill-rows'
import { PhotoTile } from './fill-photo'
import { PassportDetailsFields, PassportStatusChip } from './passport-details'
import { PassportUpload } from './passport-upload'
import {
  EDUCATION_COLUMNS_FILL,
  EXPERIENCE_COLUMNS_FILL,
  cardCounts,
  cardMissing,
  cardsFor,
  changeEducationRow,
  changeExperienceRow,
  educationView,
  rowsSourceChip,
  type FillCard,
} from './fill-fields'
import '@/styles/application-form-fill.css'

const ICONS: Record<FillCard['kind'], LucideIcon> = { fields: UserRound, passport: IdCard, education: GraduationCap, experience: BriefcaseBusiness }

/**
 * The "Fill details" view: the four sections of the paper form a person fills, with its own numbers and titles (1 Personal
 * details, 2 Passport details, 3 Educational details, 4 Previous experience), as flat sections that the page puts inside
 * one white card, in the Add candidate look. Typing writes the draft at once (`ctx.setDraft`), so the paper view, the
 * completeness and the "N to add" counts follow every keystroke. Sections 5 to 7 (Documents, Client and recruitment,
 * Recruitment process tracking) are the shell's; the passport document itself is also a row of the Documents list.
 */
export function FillView({ ctx, canWork, upload, busyKinds }: {
  ctx: FormContext
  canWork: boolean
  upload: (kind: DocumentKind, file: File) => Promise<void>
  busyKinds: DocumentKind[]
}): JSX.Element {
  const env = useFillEnv(ctx)
  const { form, draft, setDraft, errors } = ctx
  const cards = useMemo(() => cardsFor(), [])
  // Worked out from the draft with the form's own rule, so the counts move as the person types and uploads.
  const missing = useMemo(() => completenessOf(draft, form.documents).missing, [draft, form.documents])

  const passportAside = (
    <>
      <PassportStatusChip expiryDate={draft.passport.expiryDate} />
      <PassportUpload slot={form.documents.passport} canWork={canWork} busy={busyKinds.includes('passport')} upload={upload} />
    </>
  )

  const education = (
    <FillRows
      kind="education" dataField="education" rows={draft.education} columns={EDUCATION_COLUMNS_FILL} valueOf={(row, key) => (educationView(row) as unknown as Record<string, string>)[key]}
      errors={errors} errorPrefix="education" canWork={canWork} chip={rowsSourceChip(form, env.saved, draft, 'education')} icon={GraduationCap}
      copy={{ noun: 'Qualification', add: 'Add qualification', empty: 'No qualifications yet. Upload the resume or add one.' }}
      onChange={(rowId, key, value) => setDraft((current) => ({ ...current, education: current.education.map((row) => (row.rowId === rowId ? changeEducationRow(row, key, value) : row)) }))}
      onAdd={() => setDraft((current) => ({ ...current, education: [...current.education, emptyEducationRow()] }))}
      onMove={(rowId, direction) => setDraft((current) => ({ ...current, education: moveRow(current.education, rowId, direction) }))}
      onRemove={(rowId) => setDraft((current) => ({ ...current, education: removeRow(current.education, rowId) }))}
    />
  )

  const history = draft.experience.history
  const setHistory = (update: (rows: typeof history) => typeof history) => setDraft((current) => ({ ...current, experience: { ...current.experience, history: update(current.experience.history) } }))
  const experienceRows = (
    <FillRows
      kind="experience" dataField="experience.history" rows={history} columns={EXPERIENCE_COLUMNS_FILL} valueOf={(row, key) => (row as unknown as Record<string, string>)[key]}
      errors={errors} errorPrefix="experience.history" canWork={canWork} chip={rowsSourceChip(form, env.saved, draft, 'experience.history')} icon={BriefcaseBusiness}
      copy={{ noun: 'Employer', add: 'Add employer', empty: 'No previous employers yet. Upload the resume or add one.' }}
      onChange={(rowId, key, value) => setHistory((rows) => rows.map((row) => (row.rowId === rowId ? changeExperienceRow(row, key, value) : row)))}
      onAdd={() => setHistory((rows) => [...rows, emptyExperienceRow()])}
      onMove={(rowId, direction) => setHistory((rows) => moveRow(rows, rowId, direction))}
      onRemove={(rowId) => setHistory((rows) => removeRow(rows, rowId))}
    />
  )

  const body = (card: FillCard) => {
    const grid = card.fields.length > 0 && (
      <div className="aff-grid">
        {card.no === 1 && <PhotoTile photo={form.documents.photo?.current ?? null} canWork={canWork} busy={busyKinds.includes('photo')} upload={upload} />}
        {card.fields.map((item) => <FillFieldView key={item.key} env={env} field={item} canWork={canWork} />)}
      </div>
    )
    if (card.kind === 'passport') return <PassportDetailsFields ctx={ctx} canWork={canWork} />
    if (card.kind === 'education') return education
    if (card.kind === 'experience') return <>{grid}{experienceRows}</>
    return grid
  }

  return (
    <div className="aff" id="af-fill">
      <div className="aff-stack">
        {cards.map((card) => (
          <FillCardShell key={card.no} no={card.no} icon={ICONS[card.kind]} title={card.title} hint={card.hint} missing={cardMissing(card, missing).length} counts={cardCounts(card)} aside={card.kind === 'passport' ? passportAside : undefined}>
            {body(card)}
          </FillCardShell>
        ))}
      </div>
    </div>
  )
}
