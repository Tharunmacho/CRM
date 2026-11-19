/**
 * The Daily CRM Work Report contract (GET /api/reports/daily-work and its drill-down, submit, mine and export).
 * Types only: the server builds these numbers, the screen never works a figure out for itself.
 * Keys marked optional are additive: a server that does not send them yet is shown with dashes, not an error.
 */

/** The eight figures, in the order the server sends them. */
export type MetricKey = 'leadsContacted' | 'prescreened' | 'forwarded' | 'shortlisted' | 'interviews' | 'offers' | 'visas' | 'travelled'

export interface SeriesPoint { date: string; value: number; bucket?: 'week' }

export interface DailyMetric {
  key: MetricKey
  label: string
  /** The plain sentence that says how this number is counted (the small i on a column heading). */
  definition: string
  value: number
  /** The previous period's value; missing for Total (nothing to compare with). */
  previous: number | null
  /** Percent change against the previous period; null when the previous period had nothing to compare with. */
  changePct: number | null
  series: SeriesPoint[]
}

export interface OutcomeCount { key: string; label: string; count: number }

export interface LeadFeedback {
  contacted: number
  byOutcome: OutcomeCount[]
  resumesReceived: number
  interested: number
  callbacksDue: number
}

export interface FunnelStep { key: string; label: string; value: number }

export interface StaffLeadFeedback { contacted: number; interested: number; notInterested: number; noAnswer: number; wrongNumber: number; callback: number; other: number }
/** Lead sheet figures of one person (additive). `assigned` is what they hold now; `contacted` is in the period. */
export interface StaffLeads { assigned: number; contacted: number; interested: number; notInterested: number; noAnswer: number; wrongNumber: number; callback: number; notContacted: number; notReachable?: number
  /** Additive, period-aware: leads given to the person in the period, and those of the period not yet contacted. `assigned` stays the workload now, `notContacted` the backlog now. */
  assignedInPeriod?: number; notContactedInPeriod?: number
  /** Additive: the person's never-contacted leads, by the Lead sheet screen's own rule. */
  neverContacted?: number }
export interface TaskCounts { done: number; pending: number; overdue: number }

export interface StaffRow {
  id: string
  name: string
  role: string
  initials: string
  metrics: Partial<Record<MetricKey, number>>
  total: number
  interviewsOnline?: number
  leadFeedback: StaffLeadFeedback
  leads?: StaffLeads
  tasks?: TaskCounts
  /** null when the range is not a single day (nothing to submit). */
  submitted: boolean | null
  submittedAt: string | null
  note: string | null
  idle: boolean
  /** The figures this person submitted (additive), and whether the numbers moved after they submitted. */
  submittedFigures?: Figures | null
  changedSinceSubmit?: boolean
}

/** One client with activity in the period (additive `byClient`). */
export interface ClientRow { id: string; name: string; metrics: Partial<Record<MetricKey, number>>; interviewsOnline?: number; total: number }

/** One submitted day-close report in the history list. */
export interface HistoryRow { date: string; staffId: string; staffName: string; submittedAt: string; note: string | null; figures: Figures }
export interface HistoryPage { total: number; page: number; pageSize: number; rows: HistoryRow[] }

export interface Compliance { expected: number; submitted: number; missing: { id: string; name: string }[] }

export interface DailyPeriod {
  from: string; to: string; days: number; previousFrom: string; previousTo: string
  bucket?: 'day' | 'week'
  /** The period includes today: it is compared with the same time of the previous period (additive). */
  partial?: boolean
  previousUntil?: string
}

export interface UnassignedLeads { total: number; neverContacted: number; newInPeriod: number }
export interface LeadSheetSummary {
  totalLeads: number; assigned: number; contacted: number; notContacted: number
  /** A count of leads nobody owns, or (additive) the same with how many were never contacted and how many are new in the period. */
  unassigned: number | UnassignedLeads
  /** Additive: leads never contacted, by the Lead sheet screen's own rule (its Not contacted card). */
  neverContacted?: number
  byStatus: { key: string; label: string; count: number }[]
  /** Additive, period-aware: new leads, leads assigned and not yet contacted in the period, the backlog (assigned, never contacted), and the same figures for the previous period. */
  newInPeriod?: number; assignedInPeriod?: number; notContactedInPeriod?: number; backlog?: number
  previous?: { assignedInPeriod: number; contacted: number; newInPeriod: number }
}
export interface StageTotal { no: number; key: string; label: string; count: number }
export interface StageBreakdown { totals: StageTotal[]; perStaff: { id: string; counts: Record<string, number>; total: number }[] }

export interface DailyReport {
  period: DailyPeriod
  generatedAt: string
  /** 'team' for admin and MD (all staff, the staff filter); 'self' for a recruiter (their own figures only). */
  scope: 'team' | 'self'
  metrics: DailyMetric[]
  extra: { interviewsOnline: number; leadsAssigned: number; leadsContactedPct: number | null }
  leadFeedback: LeadFeedback
  funnel: FunnelStep[]
  staff: StaffRow[]
  compliance: Compliance | null
  clients: { id: string; name: string }[]
  leadSheet?: LeadSheetSummary
  tasks?: { total: number; done: number; pending: number; overdue: number }
  stages?: StageBreakdown
  byClient?: ClientRow[]
}

export interface DrillRow {
  at: string
  staffName: string
  kind: 'candidate' | 'lead'
  candidateId: string | null
  leadId: string | null
  careId: string | null
  name: string
  client: string | null
  job: string | null
  detail: string
}

export interface DrillPage { metric: string; label: string; total: number; page: number; pageSize: number; rows: DrillRow[] }

export type Figures = Partial<Record<MetricKey | 'interviewsOnline', number>>

/** What submitting (or reading) a day's report returns. `submitted` is only sent by GET /mine; a submit always means submitted. */
export interface MyDay { date: string; submittedAt: string | null; figures: Figures; note: string | null; submitted?: boolean }

/** `all`: Total, asked as period=all (the dates are then ignored by the server). */
export interface DailyQuery { from: string; to: string; staff: string; client: string; all?: boolean }
