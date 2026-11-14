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
  optionsSource?: 'users' | 'currencies'
  placeholder?: string
  help?: string
  /** Render the value in mono — identifiers read character by character. */
  ident?: boolean
  width?: 'full' | 'half'
  /** Needed before the stage can be completed (shown with an asterisk). */
  required?: boolean
  showWhen?: { field: string; in: string[] }
}

export interface StageEvaluation {
  canComplete: boolean
  missing: string[]
  /** Records a medical attempt and creates a follow-up without advancing. */
  repeats?: boolean
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
  /** Every stage can close an application: through its own answers (stages 1, 2, 4, 5, 6) or the stopping exit below. */
  closer: boolean
  /** The stopping exit: instead of moving on, the application closes here. Same list as the server's STOP_EXITS. */
  stops?: { value: string; label: string }[]
  /** Stage 6 can repeat after an unfit result. */
  repeatable?: boolean
  /**
   * Stages 4, 5 and 6 are decisions: the recruiter must choose an outcome (it is not an optional detail) and only the
   * positive one moves the candidate on. `fields` are the names shown together as the choice, outside the optional details.
   */
  decision?: DecisionDef
  evaluate: (d: Record<string, any>) => StageEvaluation
}

export interface DecisionDef {
  field: string
  /** What is missing while nothing is chosen: the same label the server reports. */
  prompt: string
  /** The sentence shown while the choice has not been made. */
  message: string
  fields: string[]
  /** The choices after which the candidate goes to the next stage; every other choice stops them here. */
  advances: string[]
}

const YES_NO = [
  { value: 'yes', label: 'Yes' },
  { value: 'no', label: 'No' },
]

const need = (d: Record<string, any>, keys: [string, string][]) =>
  keys.filter(([k]) => d[k] == null || (typeof d[k] === 'string' ? !d[k].trim() : !d[k] && d[k] !== 0)).map(([, label]) => label)

const manual = (outcome = 'Completed manually'): StageEvaluation => ({ canComplete: true, missing: [], outcome })

/**
 * The stopping exit of every stage (mirrors STOP_EXITS in backend/app/domain/stages.py): choice -> closure reason, closure label
 * and the outcome written on the stage record. Rejected and Declined are offered at stages 1-10, each stage adds its own wording,
 * and stage 11 only has Not travelling (completing it is the one way to Deployed). An explicit stop wins over the stage's answers.
 */
type StopExit = { value: string; label: string; reason: ClosureReason; closure: string; outcome: string }
const GENERIC_STOPS: StopExit[] = [
  { value: 'rejected', label: 'Rejected', reason: 'stage_rejected', closure: 'Rejected', outcome: 'Rejected' },
  { value: 'declined', label: 'Declined', reason: 'stage_declined', closure: 'Candidate declined', outcome: 'Declined' },
]
const OWN_STOPS: Record<number, StopExit[]> = {
  1: [{ value: 'rejected', label: 'Rejected', reason: 'prescreen_failed', closure: 'Failed prescreening', outcome: 'Failed' }],
  3: [{ value: 'not_possible', label: 'Interview not possible', reason: 'interview_not_possible', closure: 'Interview not possible', outcome: 'Interview not possible' }],
  4: [{ value: 'rejected', label: 'Rejected', reason: 'interview_rejected', closure: 'Rejected at interview', outcome: 'Rejected' }],
  5: [{ value: 'declined', label: 'Declined', reason: 'offer_declined', closure: 'Offer declined', outcome: 'Declined' }],
  7: [{ value: 'not_available', label: 'Passport not available', reason: 'passport_not_available', closure: 'Passport not available', outcome: 'Passport not available' }],
  8: [{ value: 'not_paid', label: 'Not paid', reason: 'advance_not_paid', closure: 'Advance not paid', outcome: 'Not paid' }],
  9: [{ value: 'visa_rejected', label: 'Visa rejected', reason: 'visa_rejected', closure: 'Visa rejected', outcome: 'Visa rejected' }],
  10: [{ value: 'refused', label: 'Visa stamping refused', reason: 'visa_stamping_refused', closure: 'Visa stamping refused', outcome: 'Stamping refused' }],
  11: [{ value: 'not_travelling', label: 'Not travelling', reason: 'not_travelling', closure: 'Not travelling', outcome: 'Not travelling' }],
}
export const stopExits = (no: number): StopExit[] => {
  if (no === 11) return OWN_STOPS[11]
  const own = OWN_STOPS[no] ?? []
  return [...GENERIC_STOPS.map((generic) => own.find((item) => item.value === generic.value) ?? generic), ...own.filter((item) => !GENERIC_STOPS.some((generic) => generic.value === item.value))]
}

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
        required: true,
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
      if (d.result === 'failed')
        return {
          canComplete: true,
          missing: [],
          closes: { reason: 'prescreen_failed', label: 'Failed prescreening' },
          outcome: 'Failed',
        }
      return manual(d.result === 'passed' ? 'Passed' : undefined)
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
      { name: 'forwarded', required: true, label: 'Forwarded to client', type: 'radio', options: YES_NO },
      {
        name: 'dateForwarded',
        label: 'Date forwarded',
        type: 'date',
        width: 'half',
        showWhen: { field: 'forwarded', in: ['yes'] },
      },
      {
        name: 'reason',
        required: true,
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
      return manual(d.forwarded === 'yes' ? 'Forwarded' : undefined)
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
      { name: 'scheduled', required: true, label: 'Interview scheduled', type: 'radio', options: YES_NO },
      {
        name: 'date',
        required: true,
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
      return manual(d.scheduled === 'yes' ? 'Scheduled' : undefined)
    },
  },
  {
    no: 4,
    key: 'interview_result',
    title: 'Interview result',
    taskTitle: 'Record the interview result',
    summary: 'Record what the client decided, with their reasoning.',
    closer: true,
    decision: {
      field: 'result',
      prompt: 'Interview result',
      message: 'Choose the interview result, Selected or Rejected, before moving this candidate.',
      fields: ['result'],
      advances: ['selected'],
    },
    fields: [
      {
        name: 'result',
        required: true,
        label: 'Interview result',
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
      if (d.result === 'rejected')
        return {
          canComplete: true,
          missing: [],
          closes: { reason: 'interview_rejected', label: 'Rejected at interview' },
          outcome: 'Rejected',
        }
      if (d.result === 'selected') return manual('Selected')
      return { canComplete: false, missing: ['Interview result'], outcome: '' }
    },
  },
  {
    no: 5,
    key: 'offer_letter',
    title: 'Offer letter',
    taskTitle: 'Obtain the offer letter',
    summary: 'Record whether the offer came and whether the candidate accepted it, and file the letter.',
    closer: true,
    decision: {
      field: 'result',
      prompt: 'Offer letter result',
      message: 'Choose the offer letter result, Agreed, Declined or Not received, before moving this candidate.',
      fields: ['result'],
      advances: ['agreed'],
    },
    fields: [
      {
        name: 'result',
        required: true,
        label: 'Offer letter result',
        type: 'radio',
        options: [
          { value: 'agreed', label: 'Agreed' },
          { value: 'declined', label: 'Declined' },
          { value: 'not_received', label: 'Not received' },
        ],
      },
      { name: 'file', label: 'Offer letter', type: 'file' },
      // Written before the result existed. Never offered on a new record; shown only where an older record holds it.
      { name: 'obtained', label: 'Offer letter obtained (earlier record)', type: 'radio', options: YES_NO, showWhen: { field: 'obtained', in: ['yes', 'no'] } },
    ],
    evaluate: (d) => {
      if (d.result === 'declined')
        return { canComplete: true, missing: [], closes: { reason: 'offer_declined', label: 'Offer declined' }, outcome: 'Declined' }
      if (d.result === 'not_received')
        return { canComplete: true, missing: [], closes: { reason: 'offer_not_received', label: 'Offer not received' }, outcome: 'Not received' }
      if (d.result === 'agreed') return manual('Agreed')
      return { canComplete: false, missing: ['Offer letter result'], outcome: '' }
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
    decision: {
      field: 'result',
      prompt: 'Medical result',
      message: 'Choose the medical result, Fit or Unfit, before moving this candidate.',
      fields: ['result', 'reapplying', 'retestDate'],
      advances: ['fit'],
    },
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
        required: true,
        label: 'Medical result',
        type: 'radio',
        options: [
          { value: 'fit', label: 'Fit' },
          { value: 'unfit', label: 'Unfit' },
        ],
      },
      {
        name: 'reapplying',
        label: 'Plan another medical instead of stopping',
        type: 'radio',
        options: YES_NO,
        showWhen: { field: 'result', in: ['unfit'] },
        help: 'Leave this on No to stop here. Yes keeps the candidate on this stage and raises a follow-up task.',
      },
      {
        name: 'retestDate',
        required: true,
        label: 'New medical date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'reapplying', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      if (d.result === 'fit') return { canComplete: true, missing: [], outcome: 'Fit' }
      if (d.result !== 'unfit') return { canComplete: false, missing: ['Medical result'], outcome: '' }
      if (d.reapplying === 'yes') {
        const missing = need(d, [['retestDate', 'New medical date']])
        return {
          canComplete: missing.length === 0,
          missing,
          outcome: 'Unfit: reapplying',
          repeats: true,
        }
      }
      return {
        canComplete: true,
        missing: [],
        closes: { reason: 'medical_unfit', label: 'Medically unfit' },
        outcome: 'Unfit',
      }
    },
  },
  {
    no: 7,
    key: 'passport',
    title: 'Passport',
    taskTitle: 'Collect original passport',
    summary: 'Take custody of the original passport and record who is holding it.',
    closer: false,
    fields: [
      {
        name: 'copy',
        label: 'Passport copy',
        type: 'file',
        help: 'Pre-filled from the profile. Replace it if you have a clearer scan.',
      },
      { name: 'collected', required: true, label: 'Original passport collected', type: 'radio', options: YES_NO },
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
      return manual(d.collected === 'yes' ? 'Collected' : undefined)
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
      { name: 'collected', required: true, label: 'Advance collected', type: 'radio', options: YES_NO },
      {
        name: 'amount',
        required: true,
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
        optionsSource: 'currencies',
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
        required: true,
        label: 'Receipt',
        type: 'file',
        showWhen: { field: 'collected', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      return manual(d.collected === 'yes' ? 'Collected' : undefined)
    },
  },
  {
    no: 9,
    key: 'visa_arrival',
    title: 'Visa arrival + Google review',
    taskTitle: 'Follow up the visa',
    summary: 'Record the visa once it arrives, and ask the candidate for a Google review.',
    closer: false,
    fields: [
      { name: 'arrived', required: true, label: 'Visa arrived', type: 'radio', options: YES_NO },
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
      return manual(d.arrived === 'yes' ? 'Arrived' : undefined)
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
      { name: 'done', required: true, label: 'Stamping done', type: 'radio', options: YES_NO },
      {
        name: 'date',
        label: 'Date',
        type: 'date',
        width: 'half',
        showWhen: { field: 'done', in: ['yes'] },
      },
      {
        name: 'file',
        required: true,
        label: 'Stamped visa copy',
        type: 'file',
        showWhen: { field: 'done', in: ['yes'] },
      },
    ],
    evaluate: (d) => {
      return manual(d.done === 'yes' ? 'Stamped' : undefined)
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
      { name: 'file', required: true, label: 'Ticket copy', type: 'file' },
    ],
    evaluate: (d) => {
      const missing = need(d, [['file', 'Air ticket'], ['travelDate', 'Travel date']])
      return missing.length ? { canComplete: false, missing, outcome: '' } : manual('Booked')
    },
  },
]

/** Mirror the server's supplied-value checks before its authoritative validation. */
for (const definition of STAGES) {
  // Staff decide when to move. Documents and stage details are optional; the
  // feedback explains that decision and stays in the completed stage record.
  definition.fields = [
    { name: 'feedback', label: 'Stage feedback', type: 'textarea', required: true,
      placeholder: 'What happened at this stage, and why are you moving it?',
      help: 'Saved with your name and date in this stage\'s history.' },
    ...definition.fields.map((field) => ({ ...field, required: field.name === definition.decision?.field || ['reason', 'retestDate'].includes(field.name) })),
  ]
  const evaluate = definition.evaluate
  definition.evaluate = (data) => {
    const invalid: string[] = []
    if (typeof data.feedback !== 'string' || !data.feedback.trim()) invalid.push('Stage feedback')
    else if (data.feedback.trim().length > 10000) invalid.push('Stage feedback: use 10,000 characters or fewer')
    for (const field of definition.fields) {
      const value = data[field.name]
      if (value == null || value === '') continue
      if (field.options && !field.options.some((option) => option.value === value)) invalid.push(`${field.label}: choose a listed option`)
      if (field.type === 'date') {
        const date = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00Z`) : null
        if (!date || !Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) invalid.push(`${field.label}: enter a valid date`)
      }
      if (field.type === 'time' && (typeof value !== 'string' || !/^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$/.test(value))) invalid.push(`${field.label}: enter a valid time`)
      if (field.type === 'file' && (typeof value !== 'object' || typeof value.id !== 'string' || !value.id.trim())) invalid.push(`${field.label}: upload a document`)
      if (field.type === 'checkbox' && typeof value !== 'boolean') invalid.push(`${field.label}: choose yes or no`)
      if (field.type === 'number' && (typeof value === 'boolean' || !Number.isFinite(Number(value)) || Number(value) <= 0 || Number(value) >= 10_000_000_000 || Math.abs(Number(value) * 100 - Math.round(Number(value) * 100)) > 0.000001)) invalid.push(`${field.label}: enter a positive amount with at most two decimal places`)
    }
    if (definition.no === 6 && data.result === 'unfit' && data.reapplying === 'yes' && data.retestDate && data.appliedDate && data.retestDate < data.appliedDate) invalid.push('New medical date must be on or after the previous medical date')
    if (!invalid.length && data.stop != null && data.stop !== '') {
      const exit = stopExits(definition.no).find((item) => item.value === data.stop)
      return exit
        ? { canComplete: true, missing: [], closes: { reason: exit.reason, label: exit.closure }, outcome: exit.outcome }
        : { canComplete: false, missing: ['Stop reason: choose one of the options for this stage'], outcome: '' }
    }
    return invalid.length ? { canComplete: false, missing: invalid, outcome: '' } : evaluate(data)
  }
  definition.closer = true
  definition.stops = stopExits(definition.no).map(({ value, label }) => ({ value, label }))
}

export const stageByNo = (no: number) => STAGES[no - 1]
export const TOTAL_STAGES = STAGES.length

/** Route filters may be edited or linked from outside the application. */
export function parseStageNumber(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null
  const stage = Number(value)
  return Number.isInteger(stage) && stage >= 1 && stage <= TOTAL_STAGES ? stage : null
}

export const closureLabels: Record<ClosureReason, string> = {
  prescreen_failed: 'Failed prescreening',
  not_interested: 'Candidate not interested',
  resume_not_received: 'Resume not received',
  interview_rejected: 'Rejected at interview',
  medical_unfit: 'Medically unfit',
  unmapped: 'Removed from the order',
  offer_declined: 'Offer declined',
  offer_not_received: 'Offer not received',
  stage_rejected: 'Rejected',
  stage_declined: 'Candidate declined',
  interview_not_possible: 'Interview not possible',
  passport_not_available: 'Passport not available',
  advance_not_paid: 'Advance not paid',
  visa_rejected: 'Visa rejected',
  visa_stamping_refused: 'Visa stamping refused',
  not_travelling: 'Not travelling',
}

/** Whether a field should render given the values recorded so far. */
export function fieldVisible(f: StageField, d: Record<string, any>) {
  if (!f.showWhen) return true
  return f.showWhen.in.includes(d[f.showWhen.field])
}
