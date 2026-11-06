import { useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useAgency } from '@/shared/state/use-agency'
import { loadWaTemplates, leadExtrasApi } from '../lead-extras-api'
import { DEFAULT_WA_TEMPLATES, WA_BODY_MAX, fillTemplate, waUrl, type WaTemplate } from '../wa-template-model'
import '@/styles/lead-extras.css'

/**
 * The green WhatsApp action of the Lead sheet. It no longer jumps straight to a blank chat: it opens a small popover next to the
 * button with three templates (first contact, request resume, follow-up) already filled for this person, the text editable for this
 * one message, and "Open WhatsApp" opens wa.me/<number>?text=<message>. The chosen message is written to the lead's history
 * ("WhatsApp message opened"). Administrators change the default wording under Users > Agency settings.
 *
 * `openWhatsApp(lead, anchor)` can be called from a button, a menu item or the drawer; `<WaComposerHost />` is mounted once by the screen.
 */
export interface WaLead { id: string; name?: string | null; phone?: string | null; positionApplied?: string | null }
interface Open { lead: WaLead; rect: { top: number; left: number; right: number; bottom: number } | null }

let current: Open | null = null
const listeners = new Set<() => void>()
const emit = () => listeners.forEach((listener) => listener())
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }

export function openWhatsApp(lead: WaLead, anchor?: Element | null) {
  const box = anchor?.getBoundingClientRect()
  current = { lead, rect: box ? { top: box.top, left: box.left, right: box.right, bottom: box.bottom } : null }
  emit()
}
export function closeWhatsApp() { current = null; emit() }

const GLYPH = 'M17.47 14.38c-.3-.15-1.76-.87-2.03-.97-.27-.1-.47-.15-.67.15-.2.3-.77.97-.94 1.16-.17.2-.35.22-.65.07-.3-.15-1.26-.46-2.39-1.48-.88-.79-1.48-1.76-1.65-2.06-.17-.3-.02-.46.13-.6.13-.14.3-.35.45-.52.15-.17.2-.3.3-.5.1-.2.05-.37-.03-.52-.07-.15-.67-1.61-.92-2.21-.24-.58-.49-.5-.67-.51h-.57c-.2 0-.52.07-.79.37-.27.3-1.04 1.02-1.04 2.48 0 1.46 1.07 2.88 1.21 3.07.15.2 2.1 3.2 5.08 4.49.71.3 1.26.49 1.69.63.71.23 1.36.2 1.87.12.57-.08 1.76-.72 2-1.41.25-.7.25-1.29.17-1.41-.07-.12-.27-.2-.57-.35M12.05 21.78h-.01a9.87 9.87 0 0 1-5.03-1.38l-.36-.21-3.74.98 1-3.65-.24-.37a9.86 9.86 0 0 1-1.51-5.26c0-5.45 4.44-9.88 9.89-9.88 2.64 0 5.12 1.03 6.99 2.9a9.82 9.82 0 0 1 2.89 6.99c0 5.45-4.44 9.88-9.88 9.88M20.46 3.49A11.8 11.8 0 0 0 12.05 0C5.5 0 .16 5.34.16 11.89c0 2.1.55 4.14 1.59 5.95L.06 24l6.3-1.65a11.9 11.9 0 0 0 5.69 1.45h.01c6.55 0 11.89-5.34 11.89-11.89 0-3.18-1.24-6.17-3.48-8.42'

export function WhatsAppGlyph({ size = 22 }: { size?: number }) {
  return <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false"><path fill="currentColor" d={GLYPH} /></svg>
}

/** The green glyph button (same look as the Data Bank's): opens the template popover instead of a blank chat. */
export function WhatsAppTemplateButton({ lead }: { lead: WaLead }) {
  if (!waUrl(lead.phone, '')) return <span className="db-muted" title="No WhatsApp number saved" aria-label="No WhatsApp number">–</span>
  return (
    <button type="button" className="db-wa lwa-wa" title={`Message ${lead.name || 'this lead'} on WhatsApp (choose a template)`} aria-label={`Open WhatsApp message for ${lead.name || 'this lead'}`} aria-haspopup="dialog"
      onClick={(event) => { event.stopPropagation(); openWhatsApp(lead, event.currentTarget) }} onKeyDown={(event) => event.stopPropagation()}>
      <WhatsAppGlyph />
    </button>
  )
}

export function WaComposerHost() {
  const open = useSyncExternalStore(subscribe, () => current, () => current)
  useEffect(() => () => closeWhatsApp(), [])
  if (!open) return null
  return createPortal(<Composer key={open.lead.id} open={open} />, document.body)
}

function Composer({ open }: { open: Open }) {
  const agency = useAgency()
  const { lead, rect } = open
  const [templates, setTemplates] = useState<WaTemplate[]>(DEFAULT_WA_TEMPLATES)
  const [company, setCompany] = useState(agency.name)
  const [key, setKey] = useState(DEFAULT_WA_TEMPLATES[0].key)
  const [text, setText] = useState('')
  const [edited, setEdited] = useState(false)
  const [note, setNote] = useState('')
  const box = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  const [place, setPlace] = useState<{ top: number; left: number } | null>(null)
  const values = useCallback(() => ({ name: lead.name, position: lead.positionApplied, companyName: company }), [lead.name, lead.positionApplied, company])

  useEffect(() => {
    let alive = true
    void loadWaTemplates().then((loaded) => {
      if (!alive) return
      setTemplates(loaded.templates)
      if (loaded.companyName) setCompany(loaded.companyName)
    })
    return () => { alive = false }
  }, [])
  // The text follows the chosen template until the person types something of their own.
  const chosen = templates.find((item) => item.key === key) ?? templates[0]
  useEffect(() => { if (!edited && chosen) setText(fillTemplate(chosen.body, values())) }, [chosen, edited, values])
  useEffect(() => { area.current?.focus({ preventScroll: true }) }, [])

  useLayoutEffect(() => {
    const element = box.current
    if (!element) return
    const width = element.offsetWidth, height = element.offsetHeight
    const margin = 12
    let left = rect ? rect.left : (window.innerWidth - width) / 2
    let top = rect ? rect.bottom + 8 : (window.innerHeight - height) / 3
    if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin
    if (rect && top + height > window.innerHeight - margin) top = Math.max(margin, rect.top - height - 8)
    setPlace({ top: Math.max(margin, top), left: Math.max(margin, left) })
  }, [rect, templates, text.length > 0])

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') closeWhatsApp() }
    const onDown = (event: MouseEvent) => { if (box.current && !box.current.contains(event.target as Node)) closeWhatsApp() }
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('mousedown', onDown) }
  }, [])

  const url = waUrl(lead.phone, text)
  const pick = (next: WaTemplate) => { setKey(next.key); setEdited(false); setNote('') }
  const send = () => {
    if (!url) return
    window.open(url, '_blank', 'noopener,noreferrer')
    void leadExtrasApi.logWhatsApp(lead.id, { template: chosen?.key ?? null, message: text.trim().slice(0, 2000) }).catch(() => { /* viewers who cannot work this lead only open the chat */ })
    closeWhatsApp()
  }
  const copy = () => {
    void navigator.clipboard?.writeText(text).then(() => setNote('Message copied.')).catch(() => setNote('Could not copy. Select the text and copy it.'))
  }

  return (
    <div ref={box} className="lwa-pop" role="dialog" aria-label={`WhatsApp message to ${lead.name || 'lead'}`} style={place ? { top: place.top, left: place.left } : { top: -9999, left: -9999 }}
      onKeyDown={(event) => { if (event.key === 'Tab' || event.key === 'Escape') { event.nativeEvent.stopPropagation(); if (event.key === 'Escape') closeWhatsApp() } }}>
      <div className="lwa-head">
        <div>
          <h3>WhatsApp message</h3>
          <p>{lead.name || 'Lead'}{lead.phone ? ` · ${lead.phone}` : ''}</p>
        </div>
        <button type="button" className="lwa-x" onClick={closeWhatsApp} aria-label="Close"><X size={16} aria-hidden="true" /></button>
      </div>
      <div className="lwa-tabs" role="radiogroup" aria-label="Message template">
        {templates.map((item) => (
          <button key={item.key} type="button" role="radio" aria-checked={item.key === chosen?.key} className="lwa-tab" onClick={() => pick(item)}>{item.title}</button>
        ))}
      </div>
      <textarea ref={area} className="lwa-text" value={text} maxLength={1000} aria-label="Message text" onChange={(event) => { setText(event.target.value); setEdited(true); setNote('') }} />
      <p className="lwa-hint">{edited ? <><b>Edited for this message only.</b> </> : null}Change the wording if you like; the template stays as it is. {text.length > WA_BODY_MAX ? 'Long messages are fine on WhatsApp.' : ''}</p>
      {!url && <p className="lwa-err" role="alert">This lead has no valid phone number.</p>}
      {note && <p className="lwa-hint" role="status">{note}</p>}
      <div className="lwa-foot">
        <button type="button" className="ld-btn" onClick={copy} disabled={!text.trim()}>Copy</button>
        <button type="button" className="lwa-go" onClick={send} disabled={!url}><WhatsAppGlyph size={18} />Open WhatsApp</button>
      </div>
    </div>
  )
}
