/**
 * The management reading of the two period reports (Task report, Lead sheet report): the summary-card figures, the conversion rate, days in words, the stuck and aging marks. Pure (types only), so node can test it as it is (tests/report-management.test.mjs).
 * The server counts; this file only turns its numbers into the sentences and shares a manager reads.
 */
import type { DailyReport, WaitingStage } from './daily-report-types'

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

/* ---------------------------------------------------------------- Task report */

export interface TaskSummary {
  stuckDays: number
  waiting: number
  completed: number
  stopped: number
  stuck: number
  stuckPct: number | null
  busiest: WaitingStage | null
}

/** The cards of the Task report; null when the server has not sent the management reading yet. */
export function taskSummary(report: Pick<DailyReport, 'taskInsight'>): TaskSummary | null {
  const insight = report.taskInsight
  if (!insight) return null
  return {
    stuckDays: insight.stuckDays, waiting: insight.waiting.total, completed: insight.completed.value, stopped: insight.stopped.value,
    stuck: insight.waiting.stuck, stuckPct: percentOf(insight.waiting.stuck, insight.waiting.total), busiest: insight.waiting.busiest,
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
  newLeads: number
  contacted: number
  interested: number
  notInterested: number
  followUp: number
  conversion: number | null
  stale: number
  staleDays: number
}

/** The cards of the Lead sheet report; null when the server has not sent the period-aware lead figures. */
export function leadSummary(report: Pick<DailyReport, 'leadSheet' | 'leadFeedback' | 'leadInsight'>): LeadSummary | null {
  const sheet = report.leadSheet
  if (!sheet || sheet.newInPeriod === undefined) return null
  const insight = report.leadInsight
  const contacted = report.leadFeedback.contacted
  const interested = report.leadFeedback.interested
  return {
    newLeads: sheet.newInPeriod,
    contacted,
    interested,
    notInterested: report.leadFeedback.byOutcome.find(item => item.key === 'notInterested')?.count ?? 0,
    followUp: report.leadFeedback.callbacksDue,
    conversion: conversion(interested, contacted),
    stale: insight?.stale.total ?? 0,
    staleDays: insight?.staleDays ?? 3,
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
