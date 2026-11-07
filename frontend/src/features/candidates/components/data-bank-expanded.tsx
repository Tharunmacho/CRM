import type { ReactNode } from 'react'
import type { Candidate } from '@/shared/model/types'
import { bankFieldRows, EMPTY_VALUE, type BankContext } from '@/features/candidates/data-bank-fields'
import { CandidateActions } from '@/features/candidates/components/data-bank-columns'
import type { useStore } from '@/shared/state/crm-store'
import '@/styles/data-bank-fields.css'

type Store = ReturnType<typeof useStore>

interface Props {
  candidate: Candidate
  store: Store
  context: BankContext
  onOpen: (candidate: Candidate) => void
  onReassign: (candidate: Candidate) => void
  onEdit: (candidate: Candidate) => void
}

/**
 * The panel under an expanded Data Bank row: every field of the candidate (preferred country, passport, job, documents on file ...)
 * as a label and its value, two pairs per line with the labels aligned, then the row's actions (Open, Application form, Assign,
 * Edit). Fields with no answer stay listed with a dash so two candidates can be compared line by line. Education and work history
 * are long and not loaded in the list: they point to the profile.
 */
export function DataBankExpanded({ candidate, store, context, onOpen, onReassign, onEdit }: Props): ReactNode {
  const rows = bankFieldRows(candidate, context)
  return (
    <div className="dbx-panel" onClick={(event) => event.stopPropagation()}>
      <dl className="dbx-grid">
        {rows.map((row) => (
          <div key={row.key} className="dbx-row">
            <dt title={row.label}>{row.label}</dt>
            <dd title={row.value || undefined}>
              {row.large
                ? <button type="button" className="dbx-link" onClick={() => onOpen(candidate)}>Open the profile to read</button>
                : row.value || <span className="db-muted">{EMPTY_VALUE}</span>}
            </dd>
          </div>
        ))}
      </dl>
      <div className="dbx-acts"><CandidateActions candidate={candidate} store={store} onOpen={onOpen} onReassign={onReassign} onEdit={onEdit} /></div>
    </div>
  )
}
