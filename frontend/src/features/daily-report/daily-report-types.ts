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
  from: string; to: string; days: number
  bucket?: 'day' | 'week'
  /** The period includes today (additive). */
  partial?: boolean
  /** Total: from the earliest record to today. */
  allTime?: boolean
}

export interface UnassignedLeads { total: number; neverContacted: number; newInPeriod: number }
export interface LeadSheetSummary {
  totalLeads: number; assigned: number; contacted: number; notContacted: number
  /** A count of leads nobody owns, or (additive) the same with how many were never contacted and how many are new in the period. */
  unassigned: number | UnassignedLeads
  /** Additive: leads never contacted, by the Lead sheet screen's own rule (its Not contacted card). */
  neverContacted?: number
  byStatus: { key: string; label: string; count: number }[]
  /** Additive: the columns of the lead matrix (the nine statuses, then the status templates that hold leads) and every recruiter's leads by column; the row 'unassigned' is the leads nobody owns. */
  statusColumns?: { key: string; label: string; template?: boolean }[]
  byStatusPerStaff?: { id: string; counts: Record<string, number> }[]
  /** Additive, period-aware: new leads, leads assigned and not yet contacted in the period and the backlog (assigned, never contacted). */
  newInPeriod?: number; assignedInPeriod?: number; notContactedInPeriod?: number; backlog?: number
}
export interface StageTotal { no: number; key: string; label: string; count: number }
/** One of the 13 stages over the period (additive `stageFlow`): stage records completed in it, and how many of those ended the placement. */
export interface StageFlow { no: number; key: string; label: string; completed: number; stopped: number }
/** Per credited person (the one who completed the stage record), stages 1 to 13 as string keys: completed in the period, and how many of those ended the placement. */
export interface StageFlowStaff { id: string; completed: Record<string, number>; stopped: Record<string, number> }
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
  /** Additive: per stage 1 to 13, the stage records completed in the period and how many of those ended the placement. */
  stageFlow?: StageFlow[]
  /** Additive: the same per credited person (the Task report's matrix in its Completed mode). */
  stageFlowStaff?: StageFlowStaff[]
  byClient?: ClientRow[]
  /** Additive: the management reading of the Task report and of the Lead sheet report (older servers send neither). */
  taskInsight?: TaskInsight
  leadInsight?: LeadInsight
}

/* ---------------------------------------------------------------- the management reading (additive keys of the report) */

export interface WaitingStage { no: number; label: string; count: number; stuck: number; avgDays: number | null }
export interface LongestWaiting { candidateId: string; careId: string | null; name: string; staffId: string | null; staffName: string; stage: number; stageLabel: string; since: string | null; days: number }
export interface ExitReason { label: string; count: number }
export interface Compared { value: number }
/** The Task report's management figures: how long candidates have waited at their stage now, how many are stuck, what was completed and lost in the period. */
export interface TaskInsight {
  stuckDays: number
  waiting: { total: number; stuck: number; byStage: WaitingStage[]; busiest: WaitingStage | null }
  stuckPerStaff: Record<string, number>
  longest: LongestWaiting[]
  completed: Compared
  stopped: Compared
  exitReasons: ExitReason[]
  exitOther: number
}
export interface AgingLead { leadId: string; name: string; staffId: string | null; staffName: string; since: string | null; days: number }
/** The Lead sheet report's management figures: leads nobody has contacted for more than `staleDays`. */
export interface LeadInsight {
  staleDays: number
  stale: { total: number; unassigned: number; perStaff: Record<string, number> }
  aging: AgingLead[]
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
