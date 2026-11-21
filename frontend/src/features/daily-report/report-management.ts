/**
 * The management reading of the two period reports (Task report, Lead sheet report): the summary-card figures with their comparison against the previous
 * period, the conversion rate, days in words, the stuck and aging marks. Pure (types only), so node can test it as it is (tests/report-management.test.mjs).
 * The server counts; this file only turns its numbers into the sentences and shares a manager reads.
 */
import type { Compared, DailyReport, ExitReason, WaitingStage } from './daily-report-types'

export type TrendTone = 'good' | 'bad' | 'plain'
export interface Trend {
  kind: 'up' | 'down' | 'flat'
  /** "+12", "−3", "0". */
  delta: string
  /** What it is compared with: "last week", "yesterday", "the previous 3 days". */
  against: string
  /** good = the change is what a manager wants (more completed, fewer lost), bad = the opposite, plain = no judgement. */
  tone: TrendTone
}

/** What the previous period is called on the cards: a day is compared with the day before, a week with the last week, any other length by its days. */
export function compareName(period: { from: string; to: string; days: number }, today: string): string {
  if (period.days <= 1) return period.to === today ? 'yesterday' : 'the day before'
  if (period.days === 7) return 'last week'
  return `the previous ${period.days} days`
}

/** The arrow and number of a card ("+12 vs last week"); null when there is nothing to compare with (a missing figure, or the period Total). `better` says which direction is good. */
export function trendOf(value: number | null | undefined, previous: number | null | undefined, against: string, better: 'up' | 'down' | 'none' = 'up'): Trend | null {
  if (value == null || previous == null || !Number.isFinite(value) || !Number.isFinite(previous)) return null
  const diff = Math.round(value - previous)
  const kind = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat'
  const tone: TrendTone = kind === 'flat' || better === 'none' ? 'plain' : (kind === 'up') === (better === 'up') ? 'good' : 'bad'
  return { kind, delta: diff > 0 ? `+${diff.toLocaleString('en-IN')}` : diff < 0 ? `−${Math.abs(diff).toLocaleString('en-IN')}` : '0', against, tone }
}
/** The same as a sentence for a screen reader and a tooltip: "12 more than last week", "3 fewer than yesterday", "same as last week". */
export function trendSentence(trend: Trend | null): string {
  if (!trend) return ''
  const n = trend.delta.replace(/^[+−]/, '')
  return trend.kind === 'flat' ? `same as ${trend.against}` : `${n} ${trend.kind === 'up' ? 'more' : 'fewer'} than ${trend.against}`
}

/** A share as a whole percent (never above 100 for a part of a whole); null without a whole to divide by. */
export function percentOf(part: number | null | undefined, whole: number | null | undefined): number | null {
  if (part == null || !whole || whole <= 0) return null
  return Math.round((part / whole) * 100)
}
/** Conversion lead to interested: of the leads contacted in the period, those whose last answer was interested. */
export const conversion = (interested: number | null | undefined, contacted: number | null | undefined) => percentOf(interested, contacted)
export const percentText = (value: number | null | undefined) => (value == null ? '–' : `${value}%`)

/** "1 day", "18.5 days", "38 days"; a dash when nothing waits. */
export function daysText(days: number | null | undefined): string {
  if (days == null || !Number.isFinite(days)) return '–'
  const shown = Number.isInteger(days) ? String(days) : days.toFixed(1)
  return `${shown} ${days === 1 ? 'day' : 'days'}`
}
/** "38d" on the cards of a narrow table. */
export const daysShort = (days: number | null | undefined) => (days == null || !Number.isFinite(days) ? '–' : `${Number.isInteger(days) ? days : days.toFixed(1)}d`)

/** The reason most candidates were lost for (ties: the first listed). */
export const biggestReason = (reasons: readonly ExitReason[]): ExitReason | null => reasons.reduce<ExitReason | null>((best, item) => (item.count > (best?.count ?? 0) ? item : best), null)

/* ---------------------------------------------------------------- Task report */

export interface TaskSummary {
  stuckDays: number
  waiting: number
  completed: Compared
  stopped: Compared
  reason: ExitReason | null
  stuck: number
  stuckPct: number | null
  busiest: WaitingStage | null
  /** The comparison label, "last week". */
  against: string
}

/** The five cards of the Task report; null when the server has not sent the management reading yet. */
export function taskSummary(report: Pick<DailyReport, 'taskInsight' | 'period'>, today: string): TaskSummary | null {
  const insight = report.taskInsight
  if (!insight) return null
  return {
    stuckDays: insight.stuckDays, waiting: insight.waiting.total, completed: insight.completed, stopped: insight.stopped, reason: biggestReason(insight.exitReasons),
    stuck: insight.waiting.stuck, stuckPct: percentOf(insight.waiting.stuck, insight.waiting.total), busiest: insight.waiting.busiest,
    against: compareName(report.period, today),
  }
}

/** The waiting-days heading of a stage row: the average and the stuck count, by stage number (1 to 13). */
export function waitingByStage(report: Pick<DailyReport, 'taskInsight'>): Map<number, WaitingStage> {
  return new Map((report.taskInsight?.waiting.byStage ?? []).map(item => [item.no, item]))
}

/** The "Stuck" figure of one person (null when the server sends no reading, so the table shows a dash rather than a made-up zero). */
export function stuckOf(report: Pick<DailyReport, 'taskInsight'>, id: string): number | null {
  const per = report.taskInsight?.stuckPerStaff
  return per ? per[id] ?? 0 : null
}

/** How serious a wait is: past the stuck line is "late", past twice the line "long". */
export function waitTone(days: number, stuckDays: number): 'ok' | 'late' | 'long' {
  return days > stuckDays * 2 ? 'long' : days > stuckDays ? 'late' : 'ok'
}

/* ---------------------------------------------------------------- Lead sheet report */

export interface LeadSummary {
  newLeads: Compared
  contacted: Compared
  interested: Compared
  notInterested: Compared
  followUp: number
  conversion: { value: number | null; previous: number | null }
  stale: number
  staleDays: number
  against: string
}

/** The seven cards of the Lead sheet report; null when the server has not sent the period-aware lead figures. */
export function leadSummary(report: Pick<DailyReport, 'leadSheet' | 'leadFeedback' | 'leadInsight' | 'period'>, today: string): LeadSummary | null {
  const sheet = report.leadSheet
  if (!sheet || sheet.newInPeriod === undefined) return null
  const insight = report.leadInsight
  const was = insight?.previous ?? null
  const back = sheet.previous ?? null
  const contacted = report.leadFeedback.contacted
  const interested = report.leadFeedback.interested
  const lost = report.leadFeedback.byOutcome.find(item => item.key === 'notInterested')?.count ?? 0
  return {
    newLeads: { value: sheet.newInPeriod, previous: back?.newInPeriod ?? null },
    contacted: { value: contacted, previous: back?.contacted ?? null },
    interested: { value: interested, previous: was?.interested ?? null },
    notInterested: { value: lost, previous: was?.notInterested ?? null },
    followUp: report.leadFeedback.callbacksDue,
    conversion: { value: conversion(interested, contacted), previous: was && back ? conversion(was.interested, back.contacted) : null },
    stale: insight?.stale.total ?? 0,
    staleDays: insight?.staleDays ?? 3,
    against: compareName(report.period, today),
  }
}
/** Stage records completed and stopped in the period, by stage number, for the people shown (the Administrator account left out); null when the server sends no per-person flow. */
export function flowByStage(report: Pick<DailyReport, 'stageFlowStaff'>, shown: ReadonlySet<string>): Map<number, { completed: number; stopped: number }> | null {
  if (!report.stageFlowStaff) return null
  const out = new Map<number, { completed: number; stopped: number }>()
  for (const person of report.stageFlowStaff) {
    if (!shown.has(person.id)) continue
    for (const [key, n] of Object.entries(person.completed)) {
      const no = Number(key)
      const entry = out.get(no) ?? { completed: 0, stopped: 0 }
      entry.completed += n
      entry.stopped += person.stopped[key] ?? 0
      out.set(no, entry)
    }
  }
  return out
}
