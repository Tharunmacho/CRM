/**
 * WhatsApp message templates of the Lead sheet (pure). The three defaults mirror backend/app/services/lead_templates.py and are
 * only used until (or if) the server's copy cannot be read; an administrator's wording comes from GET /api/leads/wa-templates.
 * Placeholders: {name}, {position}, {company_name}. No imports (tests load this file on its own).
 */
export interface WaTemplate { key: string; title: string; body: string; isDefault?: boolean; defaultBody?: string; defaultTitle?: string }

export const WA_PLACEHOLDERS = ['{name}', '{position}', '{company_name}'] as const
export const WA_BODY_MAX = 255
export const WA_TITLE_MAX = 120

export const DEFAULT_WA_TEMPLATES: WaTemplate[] = [
  { key: 'first_contact', title: 'First contact', body: 'Hello {name}, this is {company_name}. You showed interest in the {position} job abroad. Can we talk today about your details?' },
  { key: 'request_resume', title: 'Request resume', body: 'Hello {name}, thank you for speaking with us. Please send your updated resume (PDF or Word) for the {position} job to this number. - {company_name}' },
  { key: 'follow_up', title: 'Follow-up', body: 'Hello {name}, this is {company_name} following up about the {position} job. Are you still interested? Please reply and we will guide you.' },
]

export interface WaValues { name?: string | null; position?: string | null; companyName?: string | null }

/** The person's first name ("Asha Devi" -> "Asha"); "there" when the lead has no name. */
export function firstName(name?: string | null): string {
  const first = (name ?? '').trim().split(/\s+/)[0] ?? ''
  return first || 'there'
}

/** Fill the placeholders. An unknown {word} is left as typed; a missing position reads "the job" so the sentence still works. */
export function fillTemplate(body: string, values: WaValues): string {
  const position = (values.position ?? '').trim()
  const company = (values.companyName ?? '').trim()
  return body
    .replace(/\{name\}/g, firstName(values.name))
    .replace(/the \{position\} job/g, position ? `the ${position} job` : 'the job')
    .replace(/\{position\}/g, position || 'the job')
    .replace(/\{company_name\}/g, company || 'our agency')
}

/** wa.me link for a phone and message; null when the phone has no usable digits (7 to 15). */
export function waUrl(phone: string | null | undefined, message: string): string | null {
  const digits = (phone ?? '').replace(/^\s*p:\s*/i, '').replace(/\D/g, '')
  if (digits.length < 7 || digits.length > 15) return null
  const text = message.trim()
  return `https://wa.me/${digits}${text ? `?text=${encodeURIComponent(text)}` : ''}`
}

/** The templates to offer: the server's three in order, else the compiled-in defaults. Unknown or empty entries are dropped. */
export function normalizeTemplates(value: unknown): WaTemplate[] {
  const list = (value && typeof value === 'object' && Array.isArray((value as { templates?: unknown }).templates)) ? (value as { templates: unknown[] }).templates : []
  const clean = list.flatMap((item): WaTemplate[] => {
    const entry = item as Partial<WaTemplate> | null
    return entry && typeof entry.key === 'string' && typeof entry.title === 'string' && typeof entry.body === 'string' && entry.body.trim()
      ? [{ key: entry.key, title: entry.title, body: entry.body, isDefault: entry.isDefault, defaultBody: entry.defaultBody, defaultTitle: entry.defaultTitle }]
      : []
  })
  return clean.length ? clean : DEFAULT_WA_TEMPLATES
}

/** Characters left of the 255 the administrator may save (negative = too long). */
export const bodyRoom = (body: string): number => WA_BODY_MAX - body.replace(/\s+/g, ' ').trim().length

/** Unknown {placeholders} in a template body (the server refuses them too). */
export function unknownPlaceholders(body: string): string[] {
  return [...new Set((body.match(/\{[^{}]*\}/g) ?? []).filter((token) => !(WA_PLACEHOLDERS as readonly string[]).includes(token)))]
}
