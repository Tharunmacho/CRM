/** Actual service fees are entered before the confirmed 9% CGST and 9% SGST. */
export function feePaise(value: string): number | null {
  const input = value.trim()
  if (!/^\d+(?:\.\d{1,2})?$/.test(input)) return null
  const [whole, decimal = ''] = input.split('.')
  const amount = Number(whole) * 100 + Number(decimal.padEnd(2, '0'))
  return Number.isSafeInteger(amount) && amount >= 500_000 && amount <= 3_000_000 ? amount : null
}

export function feePreview(value: string, cgstRate = '9.00', sgstRate = '9.00') {
  const subtotal = feePaise(value)
  if (subtotal === null) return null
  const rates = [cgstRate, sgstRate].map(Number)
  if (rates.some((rate) => !Number.isFinite(rate) || rate < 0 || rate > 100)) return null
  const cgst = Math.round(subtotal * Math.round(rates[0] * 100) / 10_000), sgst = Math.round(subtotal * Math.round(rates[1] * 100) / 10_000)
  return { subtotal, cgst, sgst, total: subtotal + cgst + sgst }
}

export const invoiceMoney = (paise: number) => new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(paise / 100)

/** A receipt can be a partial payment; it must be positive and have no fractions of a paise. */
export function paymentPaise(value: string): number | null {
  const input = value.trim()
  if (!/^\d+(?:\.\d{1,2})?$/.test(input)) return null
  const [whole, decimal = ''] = input.split('.')
  const amount = Number(whole) * 100 + Number(decimal.padEnd(2, '0'))
  return Number.isSafeInteger(amount) && amount > 0 ? amount : null
}

export const billingFieldLabel = (field: string) => ({
  companyName: 'company name', company_name: 'company name', address: 'company address', gstin: 'GSTIN',
  invoicePrefix: 'invoice prefix', invoice_prefix: 'invoice prefix', nextNumber: 'next invoice number', next_number: 'next invoice number',
  sacCode: 'SAC code', sac_code: 'SAC code', bankName: 'bank name', bank_name: 'bank name',
  bankAccount: 'bank account', bank_account: 'bank account', bankIfsc: 'IFSC', bank_ifsc: 'IFSC',
} as Record<string, string>)[field] ?? field
