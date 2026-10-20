import type { ClosureReason } from '@/shared/model/types'

/**
 * The eleven stages are configuration, not code. The modal renders from this file,
 * completion is validated against it, and the task engine reads the next task title
 * from it. Adding a twelfth stage is an entry here, not a refactor.
 */

export type FieldType =
  | 'radio'
  | 'select'
  | 'text'
  | 'textarea'
  | 'number'
  | 'date'
  | 'time'
  | 'checkbox'
  | 'file'

/**
 * A person recorded on a stage. Both halves are kept on purpose: the id links back to
 * the account, the name is the permanent record if that account is ever removed.
 * Legacy rows may hold a bare name string, so always read through `userRefName`.
 */
export interface UserRef {
  id: string
  name: string
}

export const userRefName = (raw: unknown): string =>
  typeof raw === 'string' ? raw : ((raw as UserRef | null)?.name ?? '')

export const userRefId = (raw: unknown): string =>
  typeof raw === 'string' ? '' : ((raw as UserRef | null)?.id ?? '')

export interface StageField {
  name: string
  label: string
  type: FieldType
  options?: { value: string; label: string }[]
  /** Options resolved from the store at render time rather than listed here. */
  optionsSource?: 'users'
  placeholder?: string
  help?: string
  /** Render the value in mono — identifiers read character by character. */
  ident?: boolean
  width?: 'full' | 'half'
  showWhen?: { field: string; in: string[] }
}

export interface StageEvaluation {
  canComplete: boolean
  missing: string[]
  /** Set when the recorded outcome ends the application here. */
  closes?: { reason: ClosureReason; label: string }
  /** Human-readable outcome stored on the completed stage record. */
  outcome: string
}

export interface StageDef {
  no: number
  key: string
  title: string
  /** Title of the task this stage generates for the candidate's owner. */
  taskTitle: string
  /** One line telling staff what this stage is for. */
  summary: string
  fields: StageField[]
  /** Only stages 1, 2, 4 and 6 can close an application. */
  closer: boolean
  /** Stage 6 can repeat after an unfit result. */
  repeatable?: boolean
  evaluate: (d: Record<string, any>) => StageEvaluation
}

const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
]

const need = (d: Record<string, any>, keys: [string, string][]) =>
  keys.filter(([k]) => !d[k] && d[k] !== 0).map(([, label]) => label)

export const STAGES: StageDef[] = [
  {
    no: 1,
    key: 'prescreening',
    title: 'Prescreening',
    taskTitle: 'Prescreen the candidate',
    summary: 'Check the candidate against the order before anything is sent to the client.',
    closer: true,
    fields: [
      {
        name: 'result',
        label: 'Result',
        type: 'radio',
        options: [
          { value: 'passed', label: 'Passed' },
          { value: 'failed', label: 'Failed' },
        ],
      },
      {
        name: 'remarks',
        label: 'Remarks',
        type: 'textarea',
        placeholder: 'What you checked and found',
      },
    ],
    evaluate: (d) => {
      const missing = need(d, [['result', 'Result']])
      if (d.result === 'failed')
        return {
          canComplete: true,
          missing: [],
          closes: { reason: 'prescreen_failed', label: 'Failed prescreening' },
          outcome: 'Failed',
        }
      return { canComplete: missing.length === 0, missing, outcome: 'Passed' }
    },
  },
  {
    no: 2,
    key: 'forwarded',
    title: 'Forwarded to client',
    taskTitle: 'Forward CV to client',
    summary: 'Send the CV to the employer and record the date it went out.',
    closer: true,
    fields: [
      { name: 'forwarded', label: 'Forwarded to client', type: 'radio', options: YES_NO },
      {
        name: 'dateForwarded',
        label: 'Date forwarded',
        type: 'date',
        width: 'half',
        showWhen: { field: 'forwarded', in: ['yes'] },
      },
      {
        name: 'reason',
        label: 'Reason not forwarded',
        type: 'select',
        showWhen: { field: 'forwarded', in: ['no'] },
        options: [
          { value: 'not_interested', label: 'Candidate not interested' },
          { value: 'resume_not_received', label: 'Resume not received' },
        ],
      },
    ],
    evaluate: (d) => {
      if (d.forwarded === 'no') {
        const missing = need(d, [['reason', 'Reason not forwarded']])
        if (missing.length) return { canComplete: false, missing, outcome: '' }
        const reason: ClosureReason =
          d.reason === 'resume_not_received' ? 'resume_not_received' : 'not_interested'
        return {
          canComplete: true,
          missing: [],
          closes: {
            reason,
            label:
              reason === 'resume_not_received'
                ? 'Resume not received'
                : 'Candidate not interested',
          },
          outcome: 'Not forwarded',
        }
      }
      const missing = need(d, [
        ['forwarded', 'Forwarded to client'],
        ['dateForwarded', 'Date forwarded'],
      ])
      return { canComplete: missing.length === 0, missing, outcome: 'Forwarded' }
    },
  },
  {
    no: 3,
    key: 'interview_scheduling',
    title: 'Interview scheduling',
    taskTitle: 'Schedule the interview',
    summary: 'Fix a date with the client and tell the candidate where to be.',
    closer: false,
    fields: [
      { name: 'scheduled', label: 'Interview scheduled', type: 'radio', options: YES_NO },
      {
        name: 'date',
        label: 'Date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'scheduled', in: ['yes'] },
      },
      {
        name: 'time',
        label: 'Time',
        type: 'time',
        width: 'half',
        showWhen: { field: 'scheduled', in: ['yes'] },
      },
      {
        name: 'mode',
        label: 'Mode',
        type: 'radio',
        showWhen: { field: 'scheduled', in: ['yes'] },
        options: [
          { value: 'in_person', label: 'In person' },
          { value: 'online', label: 'Online' },
        ],
      },
      {
        name: 'venue',
        label: 'Venue or meeting link',
        type: 'text',
        showWhen: { field: 'scheduled', in: ['yes'] },
        placeholder: 'Client office, Chennai — or a meeting link',
      },
    ],
    evaluate: (d) => {
      if (d.scheduled !== 'yes')
        return { canComplete: false, missing: ['Interview scheduled'], outcome: '' }
      const missing = need(d, [
        ['date', 'Date'],
        ['mode', 'Mode'],
      ])
      return { canComplete: missing.length === 0, missing, outcome: 'Scheduled' }
    },
  },
  {
    no: 4,
    key: 'interview_result',
    title: 'Interview result',
    taskTitle: 'Record the interview result',
    summary: 'Record what the client decided, with their reasoning.',
    closer: true,
    fields: [
      {
        name: 'result',
        label: 'Result',
        type: 'radio',
        options: [
          { value: 'selected', label: 'Selected' },
          { value: 'rejected', label: 'Rejected' },
        ],
      },
      {
        name: 'remarks',
        label: 'Interview remarks',
        type: 'textarea',
        placeholder: 'What the client said',
      },
    ],
    evaluate: (d) => {
      const missing = need(d, [
        ['result', 'Result'],
        ['remarks', 'Interview remarks'],
      ])
      if (missing.length) return { canComplete: false, missing, outcome: '' }
      if (d.result === 'rejected')
        return {
          canComplete: true,
          missing: [],
          closes: { reason: 'interview_rejected', label: 'Rejected at interview' },
          outcome: 'Rejected',
        }
      return { canComplete: true, missing: [], outcome: 'Selected' }
    },
  },
  {
    no: 5,
    key: 'offer_letter',
    title: 'Offer letter',
    taskTitle: 'Obtain the offer letter',
    summary: 'Collect the signed offer from the employer and file it.',
    closer: false,
    fields: [
      { name: 'obtained', label: 'Offer letter obtained', type: 'radio', options: YES_NO },
      {
        name: 'file',
        label: 'Offer letter',
        type: 'file',
        showWhen: { field: 'obtained', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.obtained !== 'yes')
        return { canComplete: false, missing: ['Offer letter obtained'], outcome: '' }
      const missing = need(d, [['file', 'Offer letter']])
      return { canComplete: missing.length === 0, missing, outcome: 'Obtained' }
    },
  },
  {
    no: 6,
    key: 'medical',
    title: 'Medical',
    taskTitle: 'Complete the medical',
    summary:
      'Send the candidate for the medical and record the result. A retest repeats this stage.',
    closer: true,
    repeatable: true,
    fields: [
      { name: 'applied', label: 'Applied for medical', type: 'radio', options: YES_NO },
      {
        name: 'appliedDate',
        label: 'Date applied',
        type: 'date',
        width: 'half',
        showWhen: { field: 'applied', in: ['yes'] },
      },
      {
        name: 'result',
        label: 'Result',
        type: 'radio',
        showWhen: { field: 'applied', in: ['yes'] },
        options: [
          { value: 'fit', label: 'Fit' },
          { value: 'unfit', label: 'Unfit' },
        ],
      },
      {
        name: 'reapplying',
        label: 'Reapplying',
        type: 'radio',
        options: YES_NO,
        showWhen: { field: 'result', in: ['unfit'] },
        help: 'A retest keeps the candidate on this stage and raises a follow-up task.',
      },
      {
        name: 'retestDate',
        label: 'New medical date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'reapplying', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.applied !== 'yes')
        return { canComplete: false, missing: ['Applied for medical'], outcome: '' }
      if (!d.result) return { canComplete: false, missing: ['Result'], outcome: '' }
      if (d.result === 'fit') return { canComplete: true, missing: [], outcome: 'Fit' }
      if (d.reapplying === 'yes') {
        const missing = need(d, [['retestDate', 'New medical date']])
        return {
          canComplete: false,
          missing: missing.length ? missing : ['A fit result is needed to move on'],
          outcome: '',
        }
      }
      if (d.reapplying === 'no')
        return {
          canComplete: true,
          missing: [],
          closes: { reason: 'medical_unfit', label: 'Medically unfit, not reapplying' },
          outcome: 'Unfit',
        }
      return { canComplete: false, missing: ['Reapplying'], outcome: '' }
    },
  },
  {
    no: 7,
    key: 'passport',
    title: 'Passport',
    taskTitle: 'Collect the original passport',
    summary: 'Take custody of the original passport and record who is holding it.',
    closer: false,
    fields: [
      {
        name: 'copy',
        label: 'Passport copy',
        type: 'file',
        help: 'Pre-filled from the profile. Replace it if you have a clearer scan.',
      },
      { name: 'collected', label: 'Original passport collected', type: 'radio', options: YES_NO },
      {
        name: 'collectedDate',
        label: 'Date collected',
        type: 'date',
        width: 'half',
        showWhen: { field: 'collected', in: ['yes'] },
      },
      {
        name: 'heldBy',
        label: 'Held by',
        type: 'select',
        optionsSource: 'users',
        width: 'half',
        showWhen: { field: 'collected', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.collected !== 'yes')
        return { canComplete: false, missing: ['Original passport collected'], outcome: '' }
      const missing = need(d, [
        ['collectedDate', 'Date collected'],
        ['heldBy', 'Held by'],
      ])
      return { canComplete: missing.length === 0, missing, outcome: 'Collected' }
    },
  },
  {
    no: 8,
    key: 'advance',
    title: 'Advance payment',
    taskTitle: 'Collect the advance',
    summary: 'Collect the agreed advance and file the receipt.',
    closer: false,
    fields: [
      { name: 'collected', label: 'Advance collected', type: 'radio', options: YES_NO },
      {
        name: 'amount',
        label: 'Amount',
        type: 'number',
        width: 'half',
        placeholder: '0',
        showWhen: { field: 'collected', in: ['yes'] },
      },
      {
        // The advance is collected from the candidate in India while the salary is
        // paid abroad, so it carries its own currency rather than the order's.
        name: 'amountCurrency',
        label: 'Currency',
        type: 'select',
        width: 'half',
        showWhen: { field: 'collected', in: ['yes'] },
        options: [
          { value: 'INR', label: 'INR — Indian Rupee' },
          { value: 'USD', label: 'USD — US Dollar' },
          { value: 'EUR', label: 'EUR — Euro' },
          { value: 'SGD', label: 'SGD — Singapore Dollar' },
          { value: 'AED', label: 'AED — UAE Dirham' },
          { value: 'SAR', label: 'SAR — Saudi Riyal' },
          { value: 'QAR', label: 'QAR — Qatari Riyal' },
          { value: 'OMR', label: 'OMR — Omani Rial' },
          { value: 'KWD', label: 'KWD — Kuwaiti Dinar' },
          { value: 'BHD', label: 'BHD — Bahraini Dinar' },
        ],
      },
      {
        name: 'date',
        label: 'Date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'collected', in: ['yes'] },
      },
      {
        name: 'mode',
        label: 'Payment mode',
        type: 'select',
        width: 'half',
        showWhen: { field: 'collected', in: ['yes'] },
        options: [
          { value: 'cash', label: 'Cash' },
          { value: 'bank', label: 'Bank transfer' },
          { value: 'upi', label: 'UPI' },
          { value: 'cheque', label: 'Cheque' },
        ],
      },
      {
        name: 'receiptNo',
        label: 'Receipt number',
        type: 'text',
        width: 'half',
        ident: true,
        showWhen: { field: 'collected', in: ['yes'] },
      },
      {
        name: 'receipt',
        label: 'Receipt',
        type: 'file',
        showWhen: { field: 'collected', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.collected !== 'yes')
        return { canComplete: false, missing: ['Advance collected'], outcome: '' }
      const missing = need(d, [
        ['amount', 'Amount'],
        ['amountCurrency', 'Currency'],
        ['receipt', 'Receipt'],
      ])
      return { canComplete: missing.length === 0, missing, outcome: 'Collected' }
    },
  },
  {
    no: 9,
    key: 'visa_arrival',
    title: 'Visa arrival',
    taskTitle: 'Follow up the visa',
    summary: 'Record the visa once it arrives, and ask the candidate for a Google review.',
    closer: false,
    fields: [
      { name: 'arrived', label: 'Visa arrived', type: 'radio', options: YES_NO },
      {
        name: 'date',
        label: 'Date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'arrived', in: ['yes'] },
      },
      {
        name: 'file',
        label: 'Visa copy',
        type: 'file',
        showWhen: { field: 'arrived', in: ['yes'] },
      },
      { name: 'googleReview', label: 'Google review obtained', type: 'checkbox' },
    ],
    evaluate: (d) => {
      if (d.arrived !== 'yes')
        return { canComplete: false, missing: ['Visa arrived'], outcome: '' }
      return { canComplete: true, missing: [], outcome: 'Arrived' }
    },
  },
  {
    no: 10,
    key: 'visa_stamping',
    title: 'Visa stamping',
    taskTitle: 'Get the visa stamped',
    summary: 'Get the visa stamped into the passport and file the stamped copy.',
    closer: false,
    fields: [
      { name: 'done', label: 'Stamping done', type: 'radio', options: YES_NO },
      {
        name: 'date',
        label: 'Date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'done', in: ['yes'] },
      },
      {
        name: 'file',
        label: 'Stamped visa copy',
        type: 'file',
        showWhen: { field: 'done', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.done !== 'yes') return { canComplete: false, missing: ['Stamping done'], outcome: '' }
      const missing = need(d, [['file', 'Stamped visa copy']])
      return { canComplete: missing.length === 0, missing, outcome: 'Stamped' }
    },
  },
  {
    no: 11,
    key: 'air_ticket',
    title: 'Air ticket',
    taskTitle: 'Book the air ticket',
    summary: 'Confirm travel and attach the ticket. This deploys the candidate.',
    closer: false,
    fields: [
      {
        name: 'bookedBy',
        label: 'Booked by',
        type: 'radio',
        options: [
          { value: 'employer', label: 'Employer' },
          { value: 'agency', label: 'Agency' },
        ],
      },
      { name: 'travelDate', label: 'Travel date', type: 'date', width: 'half' },
      {
        name: 'airline',
        label: 'Airline and flight number',
        type: 'text',
        width: 'half',
        ident: true,
        placeholder: 'EK 545',
      },
      { name: 'pnr', label: 'PNR', type: 'text', width: 'half', ident: true, placeholder: 'A1B2C3' },
      { name: 'file', label: 'Ticket copy', type: 'file' },
    ],
    evaluate: (d) => {
      const missing = need(d, [
        ['bookedBy', 'Booked by'],
        ['travelDate', 'Travel date'],
        ['file', 'Ticket copy'],
      ])
      return { canComplete: missing.length === 0, missing, outcome: 'Booked' }
    },
  },
]

export const stageByNo = (no: number) => STAGES[no - 1]
export const TOTAL_STAGES = STAGES.length

export const closureLabels: Record<ClosureReason, string> = {
  prescreen_failed: 'Failed prescreening',
  not_interested: 'Candidate not interested',
  resume_not_received: 'Resume not received',
  interview_rejected: 'Rejected at interview',
  medical_unfit: 'Medically unfit',
  unmapped: 'Unmapped',
}

/** Whether a field should render given the values recorded so far. */
export function fieldVisible(f: StageField, d: Record<string, any>) {
  if (!f.showWhen) return true
  return f.showWhen.in.includes(d[f.showWhen.field])
}
