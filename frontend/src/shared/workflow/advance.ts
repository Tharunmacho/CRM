/**
 * What stage 9 (the advance) says, in words. Mirrors backend/app/services/advance_text.py.
 *
 * The stage record keeps `advanceStatus` ('collected' | 'pending'), `amount` (the advance collected, or the advance expected
 * while pending), `amountCurrency` and `serviceChargeTotal` (optional). Records saved before the status existed have none:
 * they read as Collected when they hold an amount.
 */

export type AdvanceStatus = 'collected' | 'pending'

const positive = (value: unknown): number | null => {
  if (value == null || value === '' || typeof value === 'boolean') return null
  const number = Number(value)
  return Number.isFinite(number) && number > 0 ? number : null
}

/** The advance status of a stage-9 record's values, or null when it holds nothing (or ended with a stopping exit). */
export function advanceStatusOf(data: Record<string, unknown> | null | undefined): AdvanceStatus | null {
  const d = data ?? {}
  if (d.stop) return null
  if (d.advanceStatus === 'collected' || d.advanceStatus === 'pending') return d.advanceStatus
  if (d.collected === 'yes' || positive(d.amount) !== null) return 'collected'
  return d.collected === 'no' ? 'pending' : null
}

/** The values with `advanceStatus` filled in from an older record (unchanged when it has one, or holds nothing). */
export function withAdvanceStatus<T extends Record<string, unknown>>(data: T): T {
  if (data.advanceStatus === 'collected' || data.advanceStatus === 'pending') return data
  const status = advanceStatusOf(data)
  return status ? { ...data, advanceStatus: status } : data
}

/** 10000 -> '10k', 12500 -> '12.5k'; below 1,000, or not a multiple of 100, the plain number. */
export function shortAmount(value: unknown): string {
  const number = positive(value)
  if (number === null) return ''
  const cents = Math.round(number * 100)
  if (cents >= 100_000 && cents % 10_000 === 0) return `${cents / 100_000}k`
  return String(cents / 100)
}

/**
 * "Advance 10k collected / 50k total" for the read-only history. Null when the stage holds nothing to say. The currency code
 * follows the text only when it is not the agency's default advance currency.
 */
export function advanceSummary(data: Record<string, unknown> | null | undefined, defaultCurrency = ''): string | null {
  const d = data ?? {}
  const status = advanceStatusOf(d)
  if (!status) return null
  const amount = shortAmount(d.amount)
  const total = shortAmount(d.serviceChargeTotal)
  let text: string
  if (status === 'pending') {
    text = 'Advance pending'
    if (amount) text += `, ${amount} expected`
    if (total) text += ` / ${total} total`
  } else {
    text = amount ? `Advance ${amount} collected` : 'Advance collected'
    if (total) text += amount ? ` / ${total} total` : `, ${total} total`
  }
  const code = typeof d.amountCurrency === 'string' ? d.amountCurrency.trim().toUpperCase() : ''
  return (amount || total) && code && code !== defaultCurrency.trim().toUpperCase() ? `${text} ${code}` : text
}
